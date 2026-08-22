use std::{
    collections::{BTreeMap, VecDeque},
    sync::{Arc, Mutex, PoisonError},
    time::{Duration, Instant},
};

use serde::Serialize;
use sha2::{Digest, Sha256};

use crate::eip::{
    EIPCallContext, EIPError, OperationCancelStatus, OperationReceipt, ReceiptOutcome, ReceiptRef,
    ReceiptStage,
};

#[derive(Clone)]
pub(crate) struct OperationRegistry {
    inner: Arc<RegistryInner>,
}

#[derive(Clone, Default)]
pub(crate) struct ShortIdAllocator {
    counters: Arc<Mutex<BTreeMap<&'static str, u64>>>,
}

struct RegistryInner {
    state: Mutex<RegistryState>,
    environment_id: String,
    generation: u64,
    max_records: usize,
    terminal_ttl: Duration,
    max_duration: Duration,
    selector_ids: ShortIdAllocator,
}

#[derive(Default)]
struct RegistryState {
    records: BTreeMap<String, OperationRecord>,
    terminal_order: VecDeque<String>,
}

struct OperationRecord {
    method: String,
    request_digest: String,
    idempotency_key: Option<String>,
    status: RecordStatus,
    cancellation_requested: bool,
    deadline: Instant,
    receipt: Option<OperationReceipt>,
    result: Option<serde_json::Value>,
    failure: Option<EIPError>,
}

enum RecordStatus {
    Active,
    Terminal { completed_at: Instant },
}

pub(crate) enum BeginOutcome {
    New(OperationLease),
    Replay(serde_json::Value),
    ReplayFailure(Box<EIPError>),
}

pub(crate) struct OperationLease {
    registry: OperationRegistry,
    operation_id: String,
    failure_on_drop: Option<Box<(OperationReceipt, EIPError)>>,
    finished: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RegistryError {
    Collision,
    DeadlineExpired,
    IdempotencyDisallowed,
    IdempotencyConflict,
    InProgress,
    TerminalFailure,
    Capacity,
    Encoding,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum OperationInterruption {
    Cancelled,
    TimedOut,
}

impl OperationRegistry {
    pub(crate) fn new(
        environment_id: String,
        generation: u64,
        max_records: usize,
        terminal_ttl: Duration,
        max_duration: Duration,
    ) -> Self {
        Self {
            inner: Arc::new(RegistryInner {
                state: Mutex::new(RegistryState::default()),
                environment_id,
                generation,
                max_records,
                terminal_ttl,
                max_duration,
                selector_ids: ShortIdAllocator::default(),
            }),
        }
    }

    pub(crate) fn begin<P: Serialize>(
        &self,
        method: &str,
        context: &EIPCallContext,
        params: &P,
        key_allowed: bool,
    ) -> Result<BeginOutcome, RegistryError> {
        self.begin_with_replay_validation(method, context, params, key_allowed, |_| true)
    }

    pub(crate) fn begin_with_replay_validation<P, F>(
        &self,
        method: &str,
        context: &EIPCallContext,
        params: &P,
        key_allowed: bool,
        replay_is_valid: F,
    ) -> Result<BeginOutcome, RegistryError>
    where
        P: Serialize,
        F: FnOnce(&serde_json::Value) -> bool,
    {
        if context.idempotency_key.is_some() && !key_allowed {
            return Err(RegistryError::IdempotencyDisallowed);
        }
        let request_digest = canonical_request_digest(method, params)?;
        let now = Instant::now();
        let deadline = operation_deadline(context.deadline, now, self.inner.max_duration)?;
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state.prune(now, self.inner.terminal_ttl);
        if state.records.contains_key(&context.operation_id) {
            return Err(RegistryError::Collision);
        }
        if let Some(key) = &context.idempotency_key {
            let replay = state
                .records
                .values()
                .find(|record| {
                    record.method == method && record.idempotency_key.as_ref() == Some(key)
                })
                .map(|record| {
                    if record.request_digest != request_digest {
                        return Err(RegistryError::IdempotencyConflict);
                    }
                    match &record.status {
                        RecordStatus::Active => Err(RegistryError::InProgress),
                        RecordStatus::Terminal { .. } => {
                            if let Some(result) = &record.result {
                                Ok((Some(result.clone()), None, record.receipt.clone()))
                            } else if let Some(failure) = &record.failure {
                                Ok((None, Some(failure.clone()), record.receipt.clone()))
                            } else {
                                Err(RegistryError::TerminalFailure)
                            }
                        }
                    }
                })
                .transpose()?;
            if let Some((result, failure, receipt)) = replay {
                let replay_valid = result.as_ref().is_none_or(replay_is_valid);
                if replay_valid {
                    while state.records.len() >= self.inner.max_records {
                        if !state.reclaim_oldest_terminal() {
                            return Err(RegistryError::Capacity);
                        }
                    }
                    state.records.insert(
                        context.operation_id.clone(),
                        OperationRecord {
                            method: method.to_owned(),
                            request_digest: request_digest.clone(),
                            idempotency_key: context.idempotency_key.clone(),
                            status: RecordStatus::Terminal { completed_at: now },
                            cancellation_requested: false,
                            deadline: now,
                            receipt,
                            result: result.clone(),
                            failure: failure.clone(),
                        },
                    );
                    state.terminal_order.push_back(context.operation_id.clone());
                    return match (result, failure) {
                        (Some(result), None) => Ok(BeginOutcome::Replay(result)),
                        (None, Some(failure)) => Ok(BeginOutcome::ReplayFailure(Box::new(failure))),
                        _ => Err(RegistryError::TerminalFailure),
                    };
                }
                for record in state.records.values_mut().filter(|record| {
                    record.method == method && record.idempotency_key.as_ref() == Some(key)
                }) {
                    record.idempotency_key = None;
                    record.result = None;
                    record.failure = None;
                }
            }
        }
        while state.records.len() >= self.inner.max_records {
            if !state.reclaim_oldest_terminal() {
                return Err(RegistryError::Capacity);
            }
        }
        state.records.insert(
            context.operation_id.clone(),
            OperationRecord {
                method: method.to_owned(),
                request_digest,
                idempotency_key: context.idempotency_key.clone(),
                status: RecordStatus::Active,
                cancellation_requested: false,
                deadline,
                receipt: None,
                result: None,
                failure: None,
            },
        );
        Ok(BeginOutcome::New(OperationLease {
            registry: self.clone(),
            operation_id: context.operation_id.clone(),
            failure_on_drop: None,
            finished: false,
        }))
    }

    pub(crate) fn cancel(&self, target_operation_id: &str) -> OperationCancelStatus {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let Some(record) = state.records.get_mut(target_operation_id) else {
            return OperationCancelStatus::NotFound;
        };
        match record.status {
            RecordStatus::Terminal { .. } => OperationCancelStatus::AlreadyTerminal,
            RecordStatus::Active => {
                record.cancellation_requested = true;
                OperationCancelStatus::CancellationRequested
            }
        }
    }

    pub(crate) fn interruption(&self, operation_id: &str) -> Option<OperationInterruption> {
        let state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let record = state.records.get(operation_id)?;
        if record.cancellation_requested {
            Some(OperationInterruption::Cancelled)
        } else if matches!(record.status, RecordStatus::Active) && Instant::now() >= record.deadline
        {
            Some(OperationInterruption::TimedOut)
        } else {
            None
        }
    }

    pub(crate) fn cancellation_requested(&self, operation_id: &str) -> bool {
        self.interruption(operation_id) == Some(OperationInterruption::Cancelled)
    }

    pub(crate) fn receipt_by_operation(&self, operation_id: &str) -> Option<OperationReceipt> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .records
            .get(operation_id)
            .and_then(|record| record.receipt.clone())
    }

    pub(crate) fn failure_by_operation(&self, operation_id: &str) -> Option<EIPError> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .records
            .get(operation_id)
            .and_then(|record| record.failure.clone())
    }

    pub(crate) fn receipt_by_ref(&self, receipt_ref: &ReceiptRef) -> Option<OperationReceipt> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .records
            .values()
            .filter_map(|record| record.receipt.as_ref())
            .find(|receipt| receipt.receipt_ref == *receipt_ref)
            .cloned()
    }

    #[cfg(test)]
    pub(crate) fn record_stats(&self) -> (usize, usize, usize) {
        let state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let active = state
            .records
            .values()
            .filter(|record| matches!(record.status, RecordStatus::Active))
            .count();
        let identifier_bytes = state.records.keys().map(String::len).sum();
        (active, state.records.len(), identifier_bytes)
    }
}

impl OperationLease {
    pub(crate) fn request_digest(&self) -> String {
        let state = self
            .registry
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state
            .records
            .get(&self.operation_id)
            .expect("operation lease owns a live record")
            .request_digest
            .clone()
    }

    pub(crate) fn receipt(
        &self,
        method: &str,
        stage: ReceiptStage,
        outcome: Option<ReceiptOutcome>,
    ) -> Result<OperationReceipt, RegistryError> {
        Ok(OperationReceipt {
            receipt_ref: ReceiptRef(self.registry.inner.selector_ids.next("receipt")?),
            operation_id: self.operation_id.clone(),
            method: method.to_owned(),
            environment_id: self.registry.inner.environment_id.clone(),
            generation: self.registry.inner.generation,
            request_digest: self.request_digest(),
            stage,
            outcome,
            observed_at: chrono::Utc::now(),
        })
    }

    pub(crate) fn preserve_failure_on_drop(
        &mut self,
        receipt: OperationReceipt,
        failure: EIPError,
    ) {
        self.failure_on_drop = Some(Box::new((receipt, failure)));
    }

    pub(crate) fn finish<T: Serialize>(
        mut self,
        result: &T,
        receipt: Option<OperationReceipt>,
    ) -> Result<(), RegistryError> {
        let result = serde_json::to_value(result).map_err(|_| RegistryError::Encoding)?;
        self.finish_value(Some(result), None, receipt);
        Ok(())
    }

    pub(crate) fn finish_failure(mut self, receipt: OperationReceipt, failure: EIPError) {
        self.finish_value(None, Some(failure), Some(receipt));
    }

    fn finish_value(
        &mut self,
        result: Option<serde_json::Value>,
        failure: Option<EIPError>,
        receipt: Option<OperationReceipt>,
    ) {
        let now = Instant::now();
        let mut state = self
            .registry
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if let Some(record) = state.records.get_mut(&self.operation_id) {
            record.status = RecordStatus::Terminal { completed_at: now };
            record.result = result;
            record.failure = failure;
            record.receipt = receipt;
            state.terminal_order.push_back(self.operation_id.clone());
        }
        self.finished = true;
    }
}

impl Drop for OperationLease {
    fn drop(&mut self) {
        if self.finished {
            return;
        }
        if let Some(failure) = self.failure_on_drop.take() {
            let (receipt, failure) = *failure;
            self.finish_value(None, Some(failure), Some(receipt));
        } else {
            self.finish_value(None, None, None);
        }
    }
}

impl RegistryState {
    fn prune(&mut self, now: Instant, ttl: Duration) {
        while let Some(operation_id) = self.terminal_order.front() {
            let expired = self.records.get(operation_id).is_none_or(|record| {
                matches!(
                    record.status,
                    RecordStatus::Terminal { completed_at }
                        if now.duration_since(completed_at) >= ttl
                )
            });
            if !expired {
                break;
            }
            if let Some(operation_id) = self.terminal_order.pop_front() {
                self.records.remove(&operation_id);
            }
        }
    }

    fn reclaim_oldest_terminal(&mut self) -> bool {
        while let Some(operation_id) = self.terminal_order.pop_front() {
            if self.records.remove(&operation_id).is_some() {
                return true;
            }
        }
        false
    }
}

fn operation_deadline(
    requested: Option<chrono::DateTime<chrono::Utc>>,
    now: Instant,
    max_duration: Duration,
) -> Result<Instant, RegistryError> {
    let hard = now + max_duration;
    let Some(requested) = requested else {
        return Ok(hard);
    };
    let remaining = (requested - chrono::Utc::now())
        .to_std()
        .map_err(|_| RegistryError::DeadlineExpired)?;
    if remaining.is_zero() {
        return Err(RegistryError::DeadlineExpired);
    }
    Ok(hard.min(now + remaining))
}

pub(crate) fn canonical_request_digest<P: Serialize>(
    method: &str,
    params: &P,
) -> Result<String, RegistryError> {
    let mut value = serde_json::to_value(params).map_err(|_| RegistryError::Encoding)?;
    let object = value.as_object_mut().ok_or(RegistryError::Encoding)?;
    if let Some(context) = object
        .get_mut("context")
        .and_then(serde_json::Value::as_object_mut)
    {
        context.remove("operation_id");
        context.remove("deadline");
        context.remove("idempotency_key");
    }
    let canonical = serde_json::to_vec(&value).map_err(|_| RegistryError::Encoding)?;
    let mut hasher = Sha256::new();
    hasher.update(crate::eip::EIP_PROTOCOL_VERSION.as_bytes());
    hasher.update([0]);
    hasher.update(method.as_bytes());
    hasher.update([0]);
    hasher.update(canonical);
    Ok(format!("{:x}", hasher.finalize()))
}

impl ShortIdAllocator {
    pub(crate) fn next(&self, prefix: &'static str) -> Result<String, RegistryError> {
        let mut counters = self.counters.lock().unwrap_or_else(PoisonError::into_inner);
        let counter = counters.entry(prefix).or_default();
        *counter = counter.checked_add(1).ok_or(RegistryError::Encoding)?;
        Ok(format!("{prefix}-{counter}"))
    }
}

#[cfg(test)]
pub(crate) fn random_selector(prefix: &str) -> Result<String, RegistryError> {
    let mut bytes = [0_u8; 18];
    getrandom::fill(&mut bytes).map_err(|_| RegistryError::Encoding)?;
    use base64::Engine as _;
    Ok(format!(
        "{prefix}-{}",
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
    ))
}

#[cfg(test)]
mod tests {
    use crate::eip::EIPCallContext;

    use super::{
        BeginOutcome, OperationInterruption, OperationRegistry, RegistryError, ShortIdAllocator,
        canonical_request_digest,
    };

    #[test]
    fn short_ids_are_kind_prefixed_and_concurrently_unique() {
        let ids = ShortIdAllocator::default();
        assert_eq!(ids.next("reader").expect("reader ID"), "reader-1");
        assert_eq!(ids.next("writer").expect("writer ID"), "writer-1");

        let workers = (0..8)
            .map(|_| {
                let ids = ids.clone();
                std::thread::spawn(move || {
                    (0..64)
                        .map(|_| ids.next("receipt").expect("receipt ID"))
                        .collect::<Vec<_>>()
                })
            })
            .collect::<Vec<_>>();
        let mut generated = workers
            .into_iter()
            .flat_map(|worker| worker.join().expect("worker succeeds"))
            .collect::<Vec<_>>();
        generated.sort();
        generated.dedup();
        assert_eq!(generated.len(), 512);
        assert!(generated.iter().all(|selector| selector.len() <= 11));
    }

    #[test]
    fn canonical_digest_omits_context_correlation_fields() {
        let first = serde_json::json!({
            "context": {"operation_id": "one", "deadline": "2026-08-21T00:00:00Z", "idempotency_key": "key"},
            "path": {"mount_id": "workspace", "path": "/file"}
        });
        let second = serde_json::json!({
            "path": {"path": "/file", "mount_id": "workspace"},
            "context": {"operation_id": "two", "idempotency_key": "other"}
        });
        assert_eq!(
            canonical_request_digest("file.stat", &first).expect("digest"),
            canonical_request_digest("file.stat", &second).expect("digest")
        );
    }

    #[test]
    fn registry_retains_terminal_ids_and_replays_matching_keys() {
        let registry = OperationRegistry::new(
            "env".to_owned(),
            7,
            2,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
        );
        let params = serde_json::json!({
            "context": {"operation_id": "one", "idempotency_key": "key"},
            "value": 1
        });
        let context = EIPCallContext {
            operation_id: "one".to_owned(),
            deadline: None,
            idempotency_key: Some("key".to_owned()),
        };
        let BeginOutcome::New(lease) = registry
            .begin("method", &context, &params, true)
            .expect("accepted")
        else {
            panic!("new operation expected")
        };
        lease
            .finish(&serde_json::json!({"ok": true}), None)
            .expect("finishes");

        assert!(matches!(
            registry.begin("method", &context, &params, true),
            Err(RegistryError::Collision)
        ));
        let replay_context = EIPCallContext {
            operation_id: "two".to_owned(),
            deadline: None,
            idempotency_key: Some("key".to_owned()),
        };
        assert!(matches!(
            registry
                .begin("method", &replay_context, &params, true)
                .expect("replays"),
            BeginOutcome::Replay(_)
        ));
        assert!(matches!(
            registry.begin("method", &replay_context, &params, true),
            Err(RegistryError::Collision)
        ));
    }

    #[test]
    fn operation_deadlines_are_finite_and_observable_by_workers() {
        let registry = OperationRegistry::new(
            "env".to_owned(),
            7,
            4,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_millis(1),
        );
        let context = EIPCallContext {
            operation_id: "timed".to_owned(),
            deadline: None,
            idempotency_key: None,
        };
        let params = serde_json::json!({"context": {"operation_id": "timed"}});
        let _lease = match registry
            .begin("method", &context, &params, false)
            .expect("operation begins")
        {
            BeginOutcome::New(lease) => lease,
            BeginOutcome::Replay(_) | BeginOutcome::ReplayFailure(_) => {
                panic!("new operation expected")
            }
        };
        std::thread::sleep(std::time::Duration::from_millis(5));
        assert_eq!(
            registry.interruption("timed"),
            Some(OperationInterruption::TimedOut)
        );

        let expired = EIPCallContext {
            operation_id: "expired".to_owned(),
            deadline: Some(chrono::Utc::now() - chrono::Duration::seconds(1)),
            idempotency_key: None,
        };
        assert!(matches!(
            registry.begin("method", &expired, &params, false),
            Err(RegistryError::DeadlineExpired)
        ));
    }
}
