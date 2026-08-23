use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    sync::{
        Arc, Mutex, PoisonError,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};

use base64::Engine as _;

use crate::{
    eip::{
        EncodedBytes, OutputCapture, OutputCursor, OutputKind, OutputOverflow, OutputPolicy,
        OutputReadParams, OutputReadResult, OutputReference, OutputSegment,
    },
    operation::ShortIdAllocator,
};

const RESPONSE_RESERVE_BYTES: u64 = 4096;

#[derive(Clone)]
pub(crate) struct RetentionQuota {
    inner: Arc<Mutex<QuotaState>>,
    max_bytes: u64,
    max_objects: usize,
}

#[derive(Default)]
struct QuotaState {
    bytes: u64,
    objects: usize,
}

#[derive(Clone)]
pub(crate) struct RetentionStore {
    inner: Arc<RetentionInner>,
}

struct RetentionInner {
    state: Mutex<RetentionState>,
    quota: RetentionQuota,
    max_objects: usize,
    #[allow(dead_code)] // Used by producer integration; output.read itself does not allocate.
    ttl: Duration,
    max_inline_bytes: u64,
    max_output_bytes: u64,
    max_response_bytes: u64,
    selector_ids: ShortIdAllocator,
}

#[derive(Default)]
struct RetentionState {
    objects: BTreeMap<String, RetainedObject>,
    cursors: BTreeMap<String, RetainedCursor>,
    released: BTreeSet<String>,
    released_order: VecDeque<String>,
}

struct RetainedObject {
    capture: Arc<Mutex<RetainedCapture>>,
    expires_at: Instant,
    expires_at_utc: chrono::DateTime<chrono::Utc>,
}

struct RetainedCapture {
    data: Vec<u8>,
    producer_complete: bool,
    produced_bytes: u64,
    dropped_bytes: u64,
    readable: bool,
}

pub(crate) struct LiveOutput {
    store: RetentionStore,
    reference: OutputReference,
    capture: Arc<Mutex<RetainedCapture>>,
    capture_limit: u64,
    release_on_drop: AtomicBool,
}

struct RetainedCursor {
    reference: String,
    offset: u64,
    expires_at: Instant,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RetentionError {
    Invalid,
    InvalidHandle,
    Gap {
        available_start: u64,
        available_end: u64,
    },
    Busy,
    #[allow(dead_code)] // Constructed by producer-side capture before command producers land.
    OutputLimit,
    Internal,
}

impl RetentionQuota {
    pub(crate) fn new(config: &crate::config::Config) -> Result<Self, RetentionError> {
        Ok(Self {
            inner: Arc::new(Mutex::new(QuotaState::default())),
            max_bytes: config.limits.max_retained_bytes,
            max_objects: usize::try_from(config.limits.max_retained_objects)
                .map_err(|_| RetentionError::Internal)?,
        })
    }

    pub(crate) fn reserve(&self, bytes: u64, objects: usize) -> bool {
        let mut state = self.inner.lock().unwrap_or_else(PoisonError::into_inner);
        let Some(next_bytes) = state.bytes.checked_add(bytes) else {
            return false;
        };
        let Some(next_objects) = state.objects.checked_add(objects) else {
            return false;
        };
        if next_bytes > self.max_bytes || next_objects > self.max_objects {
            return false;
        }
        state.bytes = next_bytes;
        state.objects = next_objects;
        true
    }

    pub(crate) fn release(&self, bytes: u64, objects: usize) {
        let mut state = self.inner.lock().unwrap_or_else(PoisonError::into_inner);
        state.bytes = state.bytes.saturating_sub(bytes);
        state.objects = state.objects.saturating_sub(objects);
    }

    #[cfg(test)]
    pub(crate) fn usage(&self) -> (u64, usize) {
        let state = self.inner.lock().unwrap_or_else(PoisonError::into_inner);
        (state.bytes, state.objects)
    }
}

impl RetentionStore {
    pub(crate) fn new(
        config: &crate::config::Config,
        generation: u64,
        quota: RetentionQuota,
    ) -> Result<Self, RetentionError> {
        Ok(Self {
            inner: Arc::new(RetentionInner {
                state: Mutex::new(RetentionState::default()),
                quota,
                max_objects: usize::try_from(config.limits.max_retained_objects)
                    .map_err(|_| RetentionError::Internal)?,
                ttl: Duration::from_millis(config.limits.max_retention_ttl_ms),
                max_inline_bytes: config.limits.max_inline_output_bytes,
                max_output_bytes: config.limits.max_output_bytes,
                max_response_bytes: config.limits.max_response_bytes,
                selector_ids: ShortIdAllocator::for_generation(generation),
            }),
        })
    }

    #[cfg(test)]
    pub(crate) fn create_live(
        &self,
        policy: Option<&OutputPolicy>,
    ) -> Result<LiveOutput, RetentionError> {
        self.create_live_many(policy, 1)?
            .pop()
            .ok_or(RetentionError::Internal)
    }

    pub(crate) fn create_live_pair(
        &self,
        policy: Option<&OutputPolicy>,
    ) -> Result<(LiveOutput, LiveOutput), RetentionError> {
        let mut outputs = self.create_live_many(policy, 2)?.into_iter();
        let stdout = outputs.next().ok_or(RetentionError::Internal)?;
        let stderr = outputs.next().ok_or(RetentionError::Internal)?;
        Ok((stdout, stderr))
    }

    fn create_live_many(
        &self,
        policy: Option<&OutputPolicy>,
        count: usize,
    ) -> Result<Vec<LiveOutput>, RetentionError> {
        let policy = self.effective_policy(policy)?;
        if policy.overflow != OutputOverflow::Retain || count == 0 {
            return Err(RetentionError::Invalid);
        }
        let expires_at_utc = chrono::Duration::from_std(self.inner.ttl)
            .map(|ttl| chrono::Utc::now() + ttl)
            .map_err(|_| RetentionError::Internal)?;
        let mut state = self.state();
        state.prune(Instant::now(), self.inner.max_objects, &self.inner.quota);
        if !self.inner.quota.reserve(0, count) {
            return Err(RetentionError::Busy);
        }
        let mut selectors = Vec::with_capacity(count);
        for _ in 0..count {
            match self.inner.selector_ids.next("output") {
                Ok(selector) => selectors.push(selector),
                Err(_) => {
                    self.inner.quota.release(0, count);
                    return Err(RetentionError::Internal);
                }
            }
        }
        let expires_at = Instant::now() + self.inner.ttl;
        let capture_limit = policy.max_output_bytes.min(self.inner.max_output_bytes);
        let mut outputs = Vec::with_capacity(count);
        for selector in selectors {
            let capture = Arc::new(Mutex::new(RetainedCapture {
                data: Vec::new(),
                producer_complete: false,
                produced_bytes: 0,
                dropped_bytes: 0,
                readable: true,
            }));
            state.objects.insert(
                selector.clone(),
                RetainedObject {
                    capture: Arc::clone(&capture),
                    expires_at,
                    expires_at_utc,
                },
            );
            outputs.push(LiveOutput {
                store: self.clone(),
                reference: OutputReference(selector),
                capture,
                capture_limit,
                release_on_drop: AtomicBool::new(true),
            });
        }
        Ok(outputs)
    }

    /// Applies producer-side output policy to an already bounded byte sequence.
    /// Producers must call this incrementally or provide bytes bounded by max_output_bytes.
    #[allow(dead_code)] // Also retained as the bounded producer API for future structured outputs.
    pub(crate) fn retain_bytes(
        &self,
        bytes: Vec<u8>,
        producer_complete: bool,
        policy: Option<&OutputPolicy>,
    ) -> Result<OutputCapture, RetentionError> {
        let policy = self.effective_policy(policy)?;
        let produced_bytes = bytes.len() as u64;
        if produced_bytes <= policy.max_inline_bytes {
            return Ok(OutputCapture {
                kind: if bytes.is_empty() {
                    OutputKind::Empty
                } else {
                    OutputKind::Inline
                },
                producer_complete,
                content_complete: producer_complete,
                produced_bytes,
                captured_bytes: produced_bytes,
                dropped_bytes: 0,
                inline: (!bytes.is_empty()).then(|| encoded(&bytes)),
                preview: None,
                reference: None,
                cursor: None,
                available_start: 0,
                available_end: produced_bytes,
                expires_at: None,
            });
        }
        match policy.overflow {
            OutputOverflow::Fail => Err(RetentionError::OutputLimit),
            OutputOverflow::Truncate => {
                let captured = policy.max_inline_bytes.min(produced_bytes) as usize;
                Ok(OutputCapture {
                    kind: OutputKind::Truncated,
                    producer_complete,
                    content_complete: false,
                    produced_bytes,
                    captured_bytes: captured as u64,
                    dropped_bytes: produced_bytes.saturating_sub(captured as u64),
                    inline: None,
                    preview: Some(crate::eip::OutputPreview {
                        segments: vec![OutputSegment {
                            start_offset: 0,
                            data: encoded(&bytes[..captured]),
                        }],
                        represented_bytes: captured as u64,
                    }),
                    reference: None,
                    cursor: None,
                    available_start: 0,
                    available_end: 0,
                    expires_at: None,
                })
            }
            OutputOverflow::Retain => self.create_object(bytes, producer_complete, &policy),
        }
    }

    pub(crate) fn read(
        &self,
        params: &OutputReadParams,
    ) -> Result<OutputReadResult, RetentionError> {
        let policy = self.effective_policy(params.output_policy.as_ref())?;
        let mut state = self.state();
        state.prune(Instant::now(), self.inner.max_objects, &self.inner.quota);
        let reference = &params.reference.0;
        let object = state.objects.get(reference).ok_or(RetentionError::Gap {
            available_start: 0,
            available_end: 0,
        })?;
        let capture = object
            .capture
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if !capture.readable {
            return Err(RetentionError::Gap {
                available_start: 0,
                available_end: 0,
            });
        }
        let captured_bytes = capture.data.len() as u64;
        let start = if let Some(cursor) = &params.cursor {
            let cursor = state
                .cursors
                .get(&cursor.0)
                .ok_or(RetentionError::InvalidHandle)?;
            if cursor.reference != *reference || cursor.expires_at <= Instant::now() {
                return Err(RetentionError::Gap {
                    available_start: 0,
                    available_end: captured_bytes,
                });
            }
            cursor.offset
        } else {
            params.start_offset.ok_or(RetentionError::Invalid)?
        };
        if start > captured_bytes {
            return Err(RetentionError::Gap {
                available_start: 0,
                available_end: captured_bytes,
            });
        }
        let maximum = policy
            .max_inline_bytes
            .min(
                self.inner
                    .max_response_bytes
                    .saturating_sub(RESPONSE_RESERVE_BYTES),
            )
            .max(1);
        if policy.overflow == OutputOverflow::Fail && captured_bytes.saturating_sub(start) > maximum
        {
            return Err(RetentionError::OutputLimit);
        }
        let end = start.saturating_add(maximum).min(captured_bytes);
        let data = encoded(&capture.data[start as usize..end as usize]);
        let producer_complete = capture.producer_complete;
        let produced_bytes = capture.produced_bytes;
        let dropped_bytes = capture.dropped_bytes;
        let expires_at = object.expires_at;
        let expires_at_utc = object.expires_at_utc;
        drop(capture);
        let next_cursor = if end < captured_bytes {
            if !self.inner.quota.reserve(0, 1) {
                return Err(RetentionError::Busy);
            }
            match self.inner.selector_ids.next("output-cursor") {
                Ok(selector) => {
                    state.cursors.insert(
                        selector.clone(),
                        RetainedCursor {
                            reference: reference.clone(),
                            offset: end,
                            expires_at,
                        },
                    );
                    Some(OutputCursor(selector))
                }
                Err(_) => {
                    self.inner.quota.release(0, 1);
                    return Err(RetentionError::Internal);
                }
            }
        } else {
            None
        };
        Ok(OutputReadResult {
            chunks: if start == end {
                Vec::new()
            } else {
                vec![OutputSegment {
                    start_offset: start,
                    data,
                }]
            },
            next_cursor: next_cursor.clone(),
            capture: OutputCapture {
                kind: OutputKind::Retained,
                producer_complete,
                content_complete: producer_complete && dropped_bytes == 0,
                produced_bytes,
                captured_bytes,
                dropped_bytes,
                inline: None,
                preview: None,
                reference: Some(params.reference.clone()),
                cursor: next_cursor,
                available_start: 0,
                available_end: captured_bytes,
                expires_at: Some(expires_at_utc),
            },
        })
    }

    pub(crate) fn expire(&self) {
        self.state()
            .prune(Instant::now(), self.inner.max_objects, &self.inner.quota);
    }

    pub(crate) fn reserve_external_cursor(&self) -> bool {
        self.inner.quota.reserve(0, 1)
    }

    pub(crate) fn release_external_cursors(&self, count: usize) {
        self.inner.quota.release(0, count);
    }

    pub(crate) fn release_reference(&self, reference: &OutputReference) -> bool {
        let mut state = self.state();
        state.prune(Instant::now(), self.inner.max_objects, &self.inner.quota);
        if let Some(object) = state.objects.remove(&reference.0) {
            let cursor_count = state
                .cursors
                .values()
                .filter(|cursor| cursor.reference == reference.0)
                .count();
            state
                .cursors
                .retain(|_, cursor| cursor.reference != reference.0);
            let captured_bytes = {
                let mut capture = object
                    .capture
                    .lock()
                    .unwrap_or_else(PoisonError::into_inner);
                capture.readable = false;
                std::mem::take(&mut capture.data).len() as u64
            };
            self.inner.quota.release(captured_bytes, 1 + cursor_count);
            state.remember_release(format!("reference:{}", reference.0), self.inner.max_objects);
            true
        } else {
            state
                .released
                .contains(&format!("reference:{}", reference.0))
        }
    }

    pub(crate) fn release_cursor(&self, cursor: &OutputCursor) -> bool {
        let mut state = self.state();
        state.prune(Instant::now(), self.inner.max_objects, &self.inner.quota);
        if state.cursors.remove(&cursor.0).is_some() {
            self.inner.quota.release(0, 1);
            state.remember_release(format!("cursor:{}", cursor.0), self.inner.max_objects);
            true
        } else {
            state.released.contains(&format!("cursor:{}", cursor.0))
        }
    }

    #[cfg(test)]
    pub(crate) fn quota(&self) -> (u64, usize) {
        self.inner.quota.usage()
    }

    fn create_object(
        &self,
        mut bytes: Vec<u8>,
        producer_complete: bool,
        policy: &OutputPolicy,
    ) -> Result<OutputCapture, RetentionError> {
        let produced_bytes = bytes.len() as u64;
        let capture_limit = policy.max_output_bytes.min(self.inner.max_output_bytes);
        if bytes.len() as u64 > capture_limit {
            bytes.truncate(capture_limit as usize);
        }
        let captured_bytes = bytes.len() as u64;
        let dropped_bytes = produced_bytes.saturating_sub(captured_bytes);
        let mut state = self.state();
        state.prune(Instant::now(), self.inner.max_objects, &self.inner.quota);
        if !self.inner.quota.reserve(captured_bytes, 1) {
            drop(state);
            let preview_bytes = policy.max_inline_bytes.min(bytes.len() as u64) as usize;
            return Ok(OutputCapture {
                kind: OutputKind::Truncated,
                producer_complete,
                content_complete: false,
                produced_bytes,
                captured_bytes: preview_bytes as u64,
                dropped_bytes: produced_bytes.saturating_sub(preview_bytes as u64),
                inline: None,
                preview: Some(crate::eip::OutputPreview {
                    segments: vec![OutputSegment {
                        start_offset: 0,
                        data: encoded(&bytes[..preview_bytes]),
                    }],
                    represented_bytes: preview_bytes as u64,
                }),
                reference: None,
                cursor: None,
                available_start: 0,
                available_end: 0,
                expires_at: None,
            });
        }
        let selector = match self.inner.selector_ids.next("output") {
            Ok(selector) => selector,
            Err(_) => {
                self.inner.quota.release(captured_bytes, 1);
                return Err(RetentionError::Internal);
            }
        };
        let expires_at = Instant::now() + self.inner.ttl;
        let expires_at_utc = match chrono::Duration::from_std(self.inner.ttl) {
            Ok(ttl) => chrono::Utc::now() + ttl,
            Err(_) => {
                self.inner.quota.release(captured_bytes, 1);
                return Err(RetentionError::Internal);
            }
        };
        state.objects.insert(
            selector.clone(),
            RetainedObject {
                capture: Arc::new(Mutex::new(RetainedCapture {
                    data: bytes,
                    producer_complete,
                    produced_bytes,
                    dropped_bytes,
                    readable: true,
                })),
                expires_at,
                expires_at_utc,
            },
        );
        Ok(OutputCapture {
            kind: OutputKind::Retained,
            producer_complete,
            content_complete: producer_complete && dropped_bytes == 0,
            produced_bytes,
            captured_bytes,
            dropped_bytes,
            inline: None,
            preview: None,
            reference: Some(OutputReference(selector)),
            cursor: None,
            available_start: 0,
            available_end: captured_bytes,
            expires_at: Some(expires_at_utc),
        })
    }

    fn effective_policy(
        &self,
        policy: Option<&OutputPolicy>,
    ) -> Result<OutputPolicy, RetentionError> {
        let policy = policy.cloned().unwrap_or(OutputPolicy {
            max_inline_bytes: self.inner.max_inline_bytes,
            max_output_bytes: self.inner.max_output_bytes,
            overflow: OutputOverflow::Truncate,
        });
        if policy.max_inline_bytes == 0
            || policy.max_output_bytes == 0
            || policy.max_inline_bytes > policy.max_output_bytes
            || policy.max_inline_bytes > self.inner.max_inline_bytes
            || policy.max_output_bytes > self.inner.max_output_bytes
        {
            return Err(RetentionError::Invalid);
        }
        Ok(policy)
    }

    fn state(&self) -> std::sync::MutexGuard<'_, RetentionState> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }
}

impl LiveOutput {
    pub(crate) fn append_limited(
        &self,
        bytes: &[u8],
        shared_allowance: u64,
    ) -> Result<u64, RetentionError> {
        let mut capture = self.capture.lock().unwrap_or_else(PoisonError::into_inner);
        capture.produced_bytes = capture
            .produced_bytes
            .checked_add(bytes.len() as u64)
            .ok_or(RetentionError::Internal)?;
        if !capture.readable {
            capture.dropped_bytes = capture
                .dropped_bytes
                .checked_add(bytes.len() as u64)
                .ok_or(RetentionError::Internal)?;
            return Ok(0);
        }
        let remaining = self.capture_limit.saturating_sub(capture.data.len() as u64);
        let requested = remaining.min(shared_allowance).min(bytes.len() as u64);
        let retained = if requested > 0 && self.store.inner.quota.reserve(requested, 0) {
            capture.data.extend_from_slice(&bytes[..requested as usize]);
            requested
        } else {
            0
        };
        capture.dropped_bytes = capture
            .dropped_bytes
            .checked_add((bytes.len() as u64).saturating_sub(retained))
            .ok_or(RetentionError::Internal)?;
        Ok(retained)
    }

    pub(crate) fn complete(&self) {
        self.capture
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .producer_complete = true;
    }

    pub(crate) fn snapshot(&self) -> OutputCapture {
        let (expires_at, retained) = {
            let state = self.store.state();
            state
                .objects
                .get(&self.reference.0)
                .map(|object| (Some(object.expires_at_utc), true))
                .unwrap_or((None, false))
        };
        let capture = self.capture.lock().unwrap_or_else(PoisonError::into_inner);
        let captured_bytes = capture.data.len() as u64;
        if retained && capture.readable {
            OutputCapture {
                kind: OutputKind::Retained,
                producer_complete: capture.producer_complete,
                content_complete: capture.producer_complete && capture.dropped_bytes == 0,
                produced_bytes: capture.produced_bytes,
                captured_bytes,
                dropped_bytes: capture.dropped_bytes,
                inline: None,
                preview: None,
                reference: Some(self.reference.clone()),
                cursor: None,
                available_start: 0,
                available_end: captured_bytes,
                expires_at,
            }
        } else {
            OutputCapture {
                kind: if capture.produced_bytes == 0 {
                    OutputKind::Empty
                } else {
                    OutputKind::Truncated
                },
                producer_complete: capture.producer_complete,
                content_complete: capture.producer_complete && capture.produced_bytes == 0,
                produced_bytes: capture.produced_bytes,
                captured_bytes: 0,
                dropped_bytes: capture.produced_bytes,
                inline: None,
                preview: None,
                reference: None,
                cursor: None,
                available_start: 0,
                available_end: 0,
                expires_at: None,
            }
        }
    }

    pub(crate) fn read_range(
        &self,
        start: u64,
        maximum: u64,
    ) -> Result<(Vec<u8>, OutputCapture), RetentionError> {
        let snapshot = self.snapshot();
        if start < snapshot.available_start || start > snapshot.available_end {
            return Err(RetentionError::Gap {
                available_start: snapshot.available_start,
                available_end: snapshot.available_end,
            });
        }
        let capture = self.capture.lock().unwrap_or_else(PoisonError::into_inner);
        if !capture.readable {
            return Err(RetentionError::Gap {
                available_start: 0,
                available_end: 0,
            });
        }
        let end = start.saturating_add(maximum).min(capture.data.len() as u64);
        Ok((
            capture.data[start as usize..end as usize].to_vec(),
            snapshot,
        ))
    }

    pub(crate) fn reference(&self) -> OutputReference {
        self.reference.clone()
    }

    pub(crate) fn detach(&self) {
        self.release_on_drop.store(false, Ordering::Release);
    }
}

impl Drop for LiveOutput {
    fn drop(&mut self) {
        if self.release_on_drop.swap(false, Ordering::AcqRel) {
            self.store.release_reference(&self.reference);
        }
    }
}

impl RetentionState {
    fn prune(&mut self, now: Instant, max_tombstones: usize, quota: &RetentionQuota) {
        let expired_objects = self
            .objects
            .iter()
            .filter(|(_, object)| object.expires_at <= now)
            .map(|(reference, _)| reference.clone())
            .collect::<Vec<_>>();
        for reference in expired_objects {
            let cursor_count = self
                .cursors
                .values()
                .filter(|cursor| cursor.reference == reference)
                .count();
            if let Some(object) = self.objects.remove(&reference) {
                let captured_bytes = {
                    let mut capture = object
                        .capture
                        .lock()
                        .unwrap_or_else(PoisonError::into_inner);
                    capture.readable = false;
                    std::mem::take(&mut capture.data).len() as u64
                };
                quota.release(captured_bytes, 1 + cursor_count);
            }
            self.cursors
                .retain(|_, cursor| cursor.reference != reference);
        }
        let expired_cursors = self
            .cursors
            .iter()
            .filter(|(_, cursor)| cursor.expires_at <= now)
            .map(|(selector, _)| selector.clone())
            .collect::<Vec<_>>();
        for selector in expired_cursors {
            if self.cursors.remove(&selector).is_some() {
                quota.release(0, 1);
            }
        }
        while self.released_order.len() > max_tombstones {
            if let Some(selector) = self.released_order.pop_front() {
                self.released.remove(&selector);
            }
        }
    }

    fn remember_release(&mut self, selector: String, max_tombstones: usize) {
        if self.released.insert(selector.clone()) {
            self.released_order.push_back(selector);
        }
        while self.released_order.len() > max_tombstones {
            if let Some(selector) = self.released_order.pop_front() {
                self.released.remove(&selector);
            }
        }
    }
}

fn encoded(bytes: &[u8]) -> EncodedBytes {
    EncodedBytes {
        encoding: "base64".to_owned(),
        data: base64::engine::general_purpose::STANDARD_NO_PAD.encode(bytes),
    }
}

#[cfg(test)]
mod tests {
    use crate::{
        config::Config,
        eip::{OutputOverflow, OutputPolicy, OutputReadParams},
    };

    use super::{RetentionError, RetentionQuota, RetentionStore};

    #[test]
    fn retained_reads_are_non_draining_and_release_returns_quota() {
        let config = Config::for_test("env");
        let store = RetentionStore::new(&config, 7, RetentionQuota::new(&config).expect("quota"))
            .expect("store");
        let capture = store
            .retain_bytes(
                b"abcdefgh".to_vec(),
                true,
                Some(&OutputPolicy {
                    max_inline_bytes: 2,
                    max_output_bytes: 8,
                    overflow: OutputOverflow::Retain,
                }),
            )
            .expect("retained");
        let reference = capture.reference.expect("reference");
        let params = OutputReadParams {
            context: crate::eip::EIPCallContext {
                operation_id: "read-one".to_owned(),
                deadline: None,
                idempotency_key: None,
            },
            reference: reference.clone(),
            cursor: None,
            start_offset: Some(0),
            output_policy: Some(OutputPolicy {
                max_inline_bytes: 2,
                max_output_bytes: 8,
                overflow: OutputOverflow::Truncate,
            }),
        };
        let first = store.read(&params).expect("first read");
        let repeated = store.read(&params).expect("non-draining repeated read");
        assert_eq!(first.chunks, repeated.chunks);
        let cursor = first.next_cursor.expect("continuation cursor");
        assert!(store.release_cursor(&cursor));
        assert!(store.release_cursor(&cursor));
        assert!(
            store.read(&params).is_ok(),
            "cursor release preserves object"
        );
        assert_eq!(store.quota().0, 8);
        assert!(store.release_reference(&reference));
        assert_eq!(store.quota().0, 0);
        assert!(store.release_reference(&reference));
    }

    #[test]
    fn live_retention_reserves_before_growth_and_reports_exact_drops() {
        let config = Config::for_test("env");
        let store = RetentionStore::new(&config, 7, RetentionQuota::new(&config).expect("quota"))
            .expect("store");
        let live = store
            .create_live(Some(&OutputPolicy {
                max_inline_bytes: 1,
                max_output_bytes: 4,
                overflow: OutputOverflow::Retain,
            }))
            .expect("live output");
        assert_eq!(live.append_limited(b"abc", 2).expect("append"), 2);
        assert_eq!(live.append_limited(b"def", 2).expect("append"), 2);
        live.complete();
        let capture = live.snapshot();
        assert_eq!(capture.produced_bytes, 6);
        assert_eq!(capture.captured_bytes, 4);
        assert_eq!(capture.dropped_bytes, 2);
        assert!(capture.producer_complete);
        assert!(!capture.content_complete);
        assert_eq!(store.quota(), (4, 1));

        let reference = live.reference();
        assert!(store.release_reference(&reference));
        assert_eq!(store.quota(), (0, 0));
        assert_eq!(
            live.capture.lock().expect("capture lock").data.capacity(),
            0,
            "quota is returned only after the retained allocation is dropped",
        );
        assert_eq!(live.append_limited(b"gh", 2).expect("dropped append"), 0);
        let released = live.snapshot();
        assert_eq!(released.produced_bytes, 8);
        assert_eq!(released.captured_bytes, 0);
        assert_eq!(released.dropped_bytes, 8);
    }

    #[test]
    fn detached_live_output_remains_readable_until_explicit_release() {
        let config = Config::for_test("env");
        let store = RetentionStore::new(&config, 7, RetentionQuota::new(&config).expect("quota"))
            .expect("store");
        let live = store
            .create_live(Some(&OutputPolicy {
                max_inline_bytes: 1,
                max_output_bytes: 3,
                overflow: OutputOverflow::Retain,
            }))
            .expect("live output");
        assert_eq!(live.append_limited(b"abc", 3).expect("append"), 3);
        live.complete();
        let reference = live.reference();
        live.detach();
        drop(live);

        assert_eq!(store.quota(), (3, 1));
        assert!(
            store
                .read(&OutputReadParams {
                    context: crate::eip::EIPCallContext {
                        operation_id: "detached-read".to_owned(),
                        deadline: None,
                        idempotency_key: None,
                    },
                    reference: reference.clone(),
                    cursor: None,
                    start_offset: Some(0),
                    output_policy: None,
                })
                .is_ok()
        );
        assert!(store.release_reference(&reference));
        assert_eq!(store.quota(), (0, 0));
    }

    #[test]
    fn live_output_pair_reserves_object_slots_atomically() {
        let mut config = Config::for_test("env");
        config.limits.max_retained_objects = 1;
        let store = RetentionStore::new(&config, 7, RetentionQuota::new(&config).expect("quota"))
            .expect("store");
        let policy = OutputPolicy {
            max_inline_bytes: 1,
            max_output_bytes: 4,
            overflow: OutputOverflow::Retain,
        };
        assert!(matches!(
            store.create_live_pair(Some(&policy)),
            Err(RetentionError::Busy)
        ));
        assert_eq!(store.quota(), (0, 0));
        assert!(store.reserve_external_cursor());
        assert_eq!(store.quota(), (0, 1));
        store.release_external_cursors(1);
        assert_eq!(store.quota(), (0, 0));
    }

    #[test]
    fn retention_quota_falls_back_to_explicit_truncation_without_losing_counts() {
        let mut config = Config::for_test("env");
        config.limits.max_retained_bytes = 1;
        let store = RetentionStore::new(&config, 7, RetentionQuota::new(&config).expect("quota"))
            .expect("store");
        let capture = store
            .retain_bytes(
                b"abcdefgh".to_vec(),
                true,
                Some(&OutputPolicy {
                    max_inline_bytes: 2,
                    max_output_bytes: 8,
                    overflow: OutputOverflow::Retain,
                }),
            )
            .expect("bounded fallback");
        assert_eq!(capture.kind, crate::eip::OutputKind::Truncated);
        assert_eq!(capture.produced_bytes, 8);
        assert_eq!(capture.captured_bytes, 2);
        assert_eq!(capture.dropped_bytes, 6);
        assert!(capture.reference.is_none());
        assert_eq!(store.quota(), (0, 0));
    }

    #[test]
    fn expiry_drops_live_capture_allocation_while_the_producer_handle_survives() {
        let mut config = Config::for_test("env");
        config.limits.max_retention_ttl_ms = 1;
        let store = RetentionStore::new(&config, 7, RetentionQuota::new(&config).expect("quota"))
            .expect("store");
        let live = store
            .create_live(Some(&OutputPolicy {
                max_inline_bytes: 1,
                max_output_bytes: 3,
                overflow: OutputOverflow::Retain,
            }))
            .expect("live output");
        assert_eq!(live.append_limited(b"abc", 3).expect("append"), 3);
        assert_eq!(store.quota(), (3, 1));

        std::thread::sleep(std::time::Duration::from_millis(5));
        store.expire();

        assert_eq!(store.quota(), (0, 0));
        let capture = live.capture.lock().expect("capture lock");
        assert!(!capture.readable);
        assert_eq!(capture.data.len(), 0);
        assert_eq!(capture.data.capacity(), 0);
    }

    #[test]
    fn expired_references_report_a_retention_gap_and_return_quota() {
        let mut config = Config::for_test("env");
        config.limits.max_retention_ttl_ms = 1;
        let store = RetentionStore::new(&config, 7, RetentionQuota::new(&config).expect("quota"))
            .expect("store");
        let capture = store
            .retain_bytes(
                b"abc".to_vec(),
                true,
                Some(&OutputPolicy {
                    max_inline_bytes: 1,
                    max_output_bytes: 3,
                    overflow: OutputOverflow::Retain,
                }),
            )
            .expect("retained");
        let reference = capture.reference.expect("reference");
        std::thread::sleep(std::time::Duration::from_millis(5));
        let result = store.read(&OutputReadParams {
            context: crate::eip::EIPCallContext {
                operation_id: "expired-read".to_owned(),
                deadline: None,
                idempotency_key: None,
            },
            reference,
            cursor: None,
            start_offset: Some(0),
            output_policy: None,
        });
        assert_eq!(
            result,
            Err(RetentionError::Gap {
                available_start: 0,
                available_end: 0,
            })
        );
        assert_eq!(store.quota(), (0, 0));
    }
}
