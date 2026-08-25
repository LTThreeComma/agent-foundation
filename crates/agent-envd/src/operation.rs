use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    future::Future,
    sync::{Arc, Mutex, PoisonError},
    time::{Duration, Instant},
};

use serde::Serialize;
use sha2::{Digest, Sha256};

use crate::eip::{
    EIPCallContext, EIPError, OperationCancelStatus, OperationReceipt, ReceiptOutcome, ReceiptStage,
};

tokio::task_local! {
    static CARRIER_ATTEMPT: u64;
}

pub(crate) async fn scope_carrier_attempt<F: Future>(attempt: u64, future: F) -> F::Output {
    CARRIER_ATTEMPT.scope(attempt, future).await
}

#[derive(Clone)]
pub(crate) struct OperationRegistry {
    inner: Arc<RegistryInner>,
}

#[derive(Clone)]
pub(crate) struct ShortIdAllocator {
    namespace: Arc<str>,
    counters: Arc<Mutex<BTreeMap<&'static str, u64>>>,
}

struct RegistryInner {
    state: Mutex<RegistryState>,
    environment_id: String,
    generation: u64,
    max_records: usize,
    terminal_ttl: Duration,
    max_duration: Duration,
}

#[derive(Default)]
struct RegistryState {
    records: BTreeMap<String, OperationRecord>,
    terminal_order: VecDeque<String>,
    next_attempt: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ReplayClass {
    ActiveOnly,
    TerminalEvidence,
}

struct OperationRecord {
    method: String,
    request_digest: String,
    replay_class: ReplayClass,
    reconciliation: bool,
    attempt: u64,
    carrier_attempt: Option<u64>,
    status: RecordStatus,
    cancellation_requested: bool,
    deadline: Instant,
    receipt: Option<OperationReceipt>,
    result: Option<serde_json::Value>,
    failure: Option<EIPError>,
    pins: BTreeSet<String>,
}

enum RecordStatus {
    Active,
    Completing,
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
    replay_class: ReplayClass,
    failure_on_drop: Option<Box<(OperationReceipt, EIPError)>>,
    finished: bool,
}

pub(crate) struct ActiveResponseHandoff {
    registry: OperationRegistry,
    operation_id: String,
    attempt: u64,
    completed: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RegistryError {
    Collision,
    DeadlineExpired,
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
            }),
        }
    }

    pub(crate) fn begin<P: Serialize>(
        &self,
        method: &str,
        context: &EIPCallContext,
        params: &P,
    ) -> Result<BeginOutcome, RegistryError> {
        let replay_class = replay_class(method)?;
        let reconciliation = is_reconciliation_method(method);
        let request_digest = canonical_request_digest(method, params)?;
        let now = Instant::now();
        let deadline = operation_deadline(context.timeout_ms, now, self.inner.max_duration)?;
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state.prune(now, self.inner.terminal_ttl);
        if let Some(record) = state.records.get(&context.operation_id) {
            if record.method != method || record.request_digest != request_digest {
                return Err(RegistryError::Collision);
            }
            return match &record.status {
                RecordStatus::Active | RecordStatus::Completing => Err(RegistryError::InProgress),
                RecordStatus::Terminal { .. } => {
                    if record.replay_class != ReplayClass::TerminalEvidence {
                        return Err(RegistryError::TerminalFailure);
                    }
                    if let Some(result) = &record.result {
                        Ok(BeginOutcome::Replay(result.clone()))
                    } else if let Some(failure) = &record.failure {
                        Ok(BeginOutcome::ReplayFailure(Box::new(failure.clone())))
                    } else {
                        Err(RegistryError::TerminalFailure)
                    }
                }
            };
        }
        let capacity = if reconciliation {
            1
        } else {
            self.inner.max_records
        };
        while state
            .records
            .values()
            .filter(|record| record.reconciliation == reconciliation)
            .count()
            >= capacity
        {
            if !state.reclaim_oldest_terminal(reconciliation) {
                return Err(RegistryError::Capacity);
            }
        }
        state.next_attempt = state
            .next_attempt
            .checked_add(1)
            .ok_or(RegistryError::Capacity)?;
        let attempt = state.next_attempt;
        state.records.insert(
            context.operation_id.clone(),
            OperationRecord {
                method: method.to_owned(),
                request_digest,
                replay_class,
                reconciliation,
                attempt,
                carrier_attempt: CARRIER_ATTEMPT.try_with(|attempt| *attempt).ok(),
                status: RecordStatus::Active,
                cancellation_requested: false,
                deadline,
                receipt: None,
                result: None,
                failure: None,
                pins: BTreeSet::new(),
            },
        );
        Ok(BeginOutcome::New(OperationLease {
            registry: self.clone(),
            operation_id: context.operation_id.clone(),
            replay_class,
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
            RecordStatus::Completing | RecordStatus::Terminal { .. } => {
                OperationCancelStatus::AlreadyTerminal
            }
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

    pub(crate) fn finish_dispatched_failure(
        &self,
        method: &str,
        params: &serde_json::Value,
        failure: EIPError,
    ) {
        let Some(operation_id) = params
            .as_object()
            .and_then(|params| params.get("context"))
            .and_then(serde_json::Value::as_object)
            .and_then(|context| context.get("operation_id"))
            .and_then(serde_json::Value::as_str)
        else {
            return;
        };
        let Ok(request_digest) = canonical_request_digest(method, params) else {
            return;
        };
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let mut pins = selector_pins_from_failure(method, &failure);
        let published = if let Some(record) = state.records.get_mut(operation_id) {
            if record.method == method
                && record.request_digest == request_digest
                && matches!(record.status, RecordStatus::Completing)
                && record.result.is_none()
                && record.failure.is_none()
            {
                if record.replay_class == ReplayClass::TerminalEvidence {
                    record.status = RecordStatus::Terminal {
                        completed_at: Instant::now(),
                    };
                    record.receipt = failure.data.receipt.clone();
                    record.failure = Some(failure);
                    record.pins = std::mem::take(&mut pins);
                    true
                } else {
                    false
                }
            } else {
                false
            }
        } else {
            false
        };
        if published {
            state.terminal_order.push_back(operation_id.to_owned());
        }
    }

    pub(crate) fn active_response_handoff<P: Serialize>(
        &self,
        method: &str,
        params: &P,
    ) -> Option<ActiveResponseHandoff> {
        let params_value = serde_json::to_value(params).ok()?;
        let operation_id = operation_id_from_params(&params_value)?;
        let request_digest = canonical_request_digest(method, params).ok()?;
        let state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let record = state.records.get(operation_id)?;
        if record.method != method
            || record.request_digest != request_digest
            || record.replay_class != ReplayClass::ActiveOnly
            || record.carrier_attempt != CARRIER_ATTEMPT.try_with(|attempt| *attempt).ok()
            || !matches!(record.status, RecordStatus::Completing)
        {
            return None;
        }
        Some(ActiveResponseHandoff {
            registry: self.clone(),
            operation_id: operation_id.to_owned(),
            attempt: record.attempt,
            completed: false,
        })
    }

    pub(crate) fn release_selector(&self, kind: &str, value: &str) {
        let selector = format!("{kind}:{value}");
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        for record in state.records.values_mut() {
            record.pins.remove(&selector);
        }
    }

    fn complete_active_handoff(&self, operation_id: &str, attempt: u64) {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let should_remove = state.records.get(operation_id).is_some_and(|record| {
            record.attempt == attempt
                && record.replay_class == ReplayClass::ActiveOnly
                && matches!(record.status, RecordStatus::Completing)
        });
        if should_remove {
            state.remove_record(operation_id);
        }
    }

    #[cfg(test)]
    pub(crate) fn complete_active_for_test(&self, operation_id: &str) {
        let attempt = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .records
            .get(operation_id)
            .map(|record| record.attempt);
        if let Some(attempt) = attempt {
            self.complete_active_handoff(operation_id, attempt);
        }
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
            .filter(|record| {
                matches!(
                    record.status,
                    RecordStatus::Active | RecordStatus::Completing
                )
            })
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
        if self.replay_class == ReplayClass::ActiveOnly {
            self.finish_active();
            return Ok(());
        }
        let result = serde_json::to_value(result).map_err(|_| RegistryError::Encoding)?;
        self.finish_value(Some(result), None, receipt);
        Ok(())
    }

    pub(crate) fn finish_failure(mut self, receipt: OperationReceipt, failure: EIPError) {
        if self.replay_class == ReplayClass::ActiveOnly {
            self.finish_active();
        } else {
            self.finish_value(None, Some(failure), Some(receipt));
        }
    }

    fn finish_active(&mut self) {
        let mut state = self
            .registry
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if let Some(record) = state.records.get_mut(&self.operation_id) {
            record.status = RecordStatus::Completing;
            record.receipt = None;
            record.result = None;
            record.failure = None;
        }
        self.finished = true;
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
        let method = state
            .records
            .get(&self.operation_id)
            .map(|record| record.method.clone());
        let pins = method.as_deref().map_or_else(BTreeSet::new, |method| {
            selector_pins(method, result.as_ref(), failure.as_ref())
        });
        if let Some(record) = state.records.get_mut(&self.operation_id) {
            record.status = RecordStatus::Terminal { completed_at: now };
            record.result = result;
            record.failure = failure;
            record.receipt = receipt;
            record.pins = pins;
            state.terminal_order.push_back(self.operation_id.clone());
        }
        self.finished = true;
    }
}

impl ActiveResponseHandoff {
    pub(crate) fn complete(mut self) {
        self.registry
            .complete_active_handoff(&self.operation_id, self.attempt);
        self.completed = true;
    }
}

impl Drop for ActiveResponseHandoff {
    fn drop(&mut self) {
        if !self.completed {
            self.registry
                .complete_active_handoff(&self.operation_id, self.attempt);
            self.completed = true;
        }
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
            return;
        }
        let mut state = self
            .registry
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if let Some(record) = state.records.get_mut(&self.operation_id)
            && matches!(record.status, RecordStatus::Active)
        {
            record.status = RecordStatus::Completing;
        }
        self.finished = true;
    }
}

impl RegistryState {
    fn prune(&mut self, now: Instant, ttl: Duration) {
        let mut retained = VecDeque::with_capacity(self.terminal_order.len());
        while let Some(operation_id) = self.terminal_order.pop_front() {
            let expired = self.records.get(&operation_id).is_some_and(|record| {
                record.pins.is_empty()
                    && matches!(
                        record.status,
                        RecordStatus::Terminal { completed_at }
                            if now.duration_since(completed_at) >= ttl
                    )
            });
            if expired {
                self.remove_record(&operation_id);
            } else if self.records.contains_key(&operation_id) {
                retained.push_back(operation_id);
            }
        }
        self.terminal_order = retained;
    }

    fn reclaim_oldest_terminal(&mut self, reconciliation: bool) -> bool {
        let mut retained = VecDeque::with_capacity(self.terminal_order.len());
        let mut reclaimed = false;
        while let Some(operation_id) = self.terminal_order.pop_front() {
            let eligible = !reclaimed
                && self.records.get(&operation_id).is_some_and(|record| {
                    record.reconciliation == reconciliation
                        && record.pins.is_empty()
                        && matches!(record.status, RecordStatus::Terminal { .. })
                });
            if eligible {
                self.remove_record(&operation_id);
                reclaimed = true;
            } else if self.records.contains_key(&operation_id) {
                retained.push_back(operation_id);
            }
        }
        self.terminal_order = retained;
        reclaimed
    }

    fn remove_record(&mut self, operation_id: &str) -> Option<OperationRecord> {
        self.records.remove(operation_id)
    }
}

fn replay_class(method: &str) -> Result<ReplayClass, RegistryError> {
    let metadata = crate::eip::METHODS
        .iter()
        .find(|metadata| metadata.name == method)
        .ok_or(RegistryError::Encoding)?;
    match metadata.replay_class {
        "active_only" => Ok(ReplayClass::ActiveOnly),
        "terminal_evidence" => Ok(ReplayClass::TerminalEvidence),
        _ => Err(RegistryError::Encoding),
    }
}

fn is_reconciliation_method(method: &str) -> bool {
    matches!(
        method,
        "operation.cancel"
            | "receipt.get"
            | "process.inspect"
            | "process.kill"
            | "process.release"
            | "output.read"
            | "output.release"
    )
}

fn operation_id_from_params(params: &serde_json::Value) -> Option<&str> {
    params
        .as_object()?
        .get("context")?
        .as_object()?
        .get("operation_id")?
        .as_str()
}

fn selector_pins(
    method: &str,
    result: Option<&serde_json::Value>,
    failure: Option<&EIPError>,
) -> BTreeSet<String> {
    if !matches!(method, "process.start" | "shell.exec") {
        return BTreeSet::new();
    }
    let mut pins = BTreeSet::new();
    if let Some(result) = result {
        collect_command_pins(method, result, &mut pins);
    }
    if let Some(failure) = failure
        && let Ok(value) = serde_json::to_value(failure)
    {
        let data = &value["data"];
        if let Some(process) = data.get("process") {
            collect_process_pins(process, &mut pins);
        }
        if let Some(output) = data.get("output") {
            collect_output_pins(output, &mut pins);
        }
    }
    pins
}

fn selector_pins_from_failure(method: &str, failure: &EIPError) -> BTreeSet<String> {
    selector_pins(method, None, Some(failure))
}

fn collect_command_pins(method: &str, value: &serde_json::Value, pins: &mut BTreeSet<String>) {
    if method == "process.start" {
        collect_process_pins(&value["process"], pins);
    } else {
        collect_output_pins(&value["output"], pins);
    }
}

fn collect_process_pins(value: &serde_json::Value, pins: &mut BTreeSet<String>) {
    if let Some(handle) = value.get("handle").and_then(serde_json::Value::as_str) {
        pins.insert(format!("process:{handle}"));
    }
    if let Some(output) = value.get("output") {
        collect_output_pins(output, pins);
    }
}

fn collect_output_pins(value: &serde_json::Value, pins: &mut BTreeSet<String>) {
    for stream in ["stdout", "stderr"] {
        if let Some(reference) = value
            .get(stream)
            .and_then(|stream| stream.get("reference"))
            .and_then(serde_json::Value::as_str)
        {
            pins.insert(format!("output:{reference}"));
        }
    }
}

fn operation_deadline(
    requested_ms: Option<u64>,
    now: Instant,
    max_duration: Duration,
) -> Result<Instant, RegistryError> {
    let hard = now + max_duration;
    let Some(requested_ms) = requested_ms else {
        return Ok(hard);
    };
    let requested = Duration::from_millis(requested_ms);
    if requested.is_zero() {
        return Err(RegistryError::DeadlineExpired);
    }
    Ok(hard.min(now + requested))
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
        context.remove("timeout_ms");
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
    pub(crate) fn for_generation(generation: u64) -> Self {
        Self {
            namespace: encode_base36(generation).into(),
            counters: Arc::new(Mutex::new(BTreeMap::new())),
        }
    }

    pub(crate) fn next(&self, prefix: &'static str) -> Result<String, RegistryError> {
        let mut counters = self.counters.lock().unwrap_or_else(PoisonError::into_inner);
        let counter = counters.entry(prefix).or_default();
        *counter = counter.checked_add(1).ok_or(RegistryError::Encoding)?;
        Ok(format!("{prefix}-{}-{counter}", self.namespace))
    }
}

fn encode_base36(mut value: u64) -> String {
    const DIGITS: &[u8; 36] = b"0123456789abcdefghijklmnopqrstuvwxyz";
    let mut buffer = [0_u8; 13];
    let mut cursor = buffer.len();
    loop {
        cursor -= 1;
        buffer[cursor] = DIGITS[(value % 36) as usize];
        value /= 36;
        if value == 0 {
            break;
        }
    }
    String::from_utf8(buffer[cursor..].to_vec()).expect("base36 digits are UTF-8")
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
    use crate::eip::{EIPCallContext, EIPError, ErrorType};

    use super::{
        BeginOutcome, OperationInterruption, OperationRegistry, RegistryError, ShortIdAllocator,
        canonical_request_digest,
    };

    #[test]
    fn short_ids_are_kind_prefixed_and_concurrently_unique() {
        let ids = ShortIdAllocator::for_generation(42);
        assert_eq!(ids.next("reader").expect("reader ID"), "reader-16-1");
        assert_eq!(ids.next("writer").expect("writer ID"), "writer-16-1");

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
        assert!(generated.iter().all(|selector| selector.len() <= 32));
    }

    #[test]
    fn canonical_digest_omits_context_correlation_fields() {
        let first = serde_json::json!({
            "context": {"operation_id": "one", "timeout_ms": 1_000},
            "path": {"mount_id": "workspace", "path": "/file"}
        });
        let second = serde_json::json!({
            "path": {"path": "/file", "mount_id": "workspace"},
            "context": {"operation_id": "two"}
        });
        assert_eq!(
            canonical_request_digest("file.stat", &first).expect("digest"),
            canonical_request_digest("file.stat", &second).expect("digest")
        );
    }

    #[test]
    fn registry_replays_only_the_same_operation_identity_and_request() {
        let registry = OperationRegistry::new(
            "env".to_owned(),
            7,
            2,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
        );
        let params = serde_json::json!({
            "context": {"operation_id": "one"},
            "value": 1
        });
        let context = EIPCallContext {
            operation_id: "one".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(lease) = registry
            .begin("file.write_text", &context, &params)
            .expect("accepted")
        else {
            panic!("new operation expected")
        };
        lease
            .finish(&serde_json::json!({"ok": true}), None)
            .expect("finishes");

        assert!(matches!(
            registry
                .begin("file.write_text", &context, &params)
                .expect("same operation replays"),
            BeginOutcome::Replay(_)
        ));
        let mismatched = serde_json::json!({
            "context": {"operation_id": "one"},
            "value": 2
        });
        assert!(matches!(
            registry.begin("file.write_text", &context, &mismatched),
            Err(RegistryError::Collision)
        ));
        assert!(matches!(
            registry.begin("file.mkdir", &context, &params),
            Err(RegistryError::Collision)
        ));
    }

    #[test]
    fn dispatched_failure_is_published_atomically_after_lease_completion() {
        let registry = OperationRegistry::new(
            "env".to_owned(),
            7,
            2,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
        );
        let params = serde_json::json!({
            "context": {"operation_id": "failed"},
            "path": {"mount_id": "workspace", "path": "/missing"}
        });
        let context = EIPCallContext {
            operation_id: "failed".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(lease) = registry
            .begin("file.write_text", &context, &params)
            .expect("operation begins")
        else {
            panic!("new operation expected")
        };
        drop(lease);

        assert!(matches!(
            registry.begin("file.write_text", &context, &params),
            Err(RegistryError::InProgress)
        ));

        let failure = serde_json::from_value::<EIPError>(serde_json::json!({
            "code": -32011,
            "message": "missing",
            "data": {
                "error_type": "not_found_or_denied",
                "retry_hint": "never",
                "dispatch_stage": "completed"
            }
        }))
        .expect("typed failure");
        registry.finish_dispatched_failure("file.write_text", &params, failure);

        let BeginOutcome::ReplayFailure(replayed) = registry
            .begin("file.write_text", &context, &params)
            .expect("failure replays")
        else {
            panic!("failure replay expected")
        };
        assert_eq!(replayed.data.error_type, ErrorType::NotFoundOrDenied);
        assert_eq!(replayed.message, "missing");
    }

    #[test]
    fn active_only_records_live_until_the_exact_response_handoff() {
        let registry = OperationRegistry::new(
            "env".to_owned(),
            7,
            2,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
        );
        let params = serde_json::json!({
            "context": {"operation_id": "page"},
            "reference": "output-1",
            "start_offset": 0
        });
        let context = EIPCallContext {
            operation_id: "page".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(lease) = registry
            .begin("output.read", &context, &params)
            .expect("active-only operation begins")
        else {
            panic!("new operation expected")
        };
        lease
            .finish(&serde_json::json!({"next_offset": 0}), None)
            .expect("active-only operation completes handler work");
        assert!(matches!(
            registry.begin("output.read", &context, &params),
            Err(RegistryError::InProgress)
        ));

        let first = registry
            .active_response_handoff("output.read", &params)
            .expect("handoff token exists");
        let stale = registry
            .active_response_handoff("output.read", &params)
            .expect("duplicate waiter observes the same admission attempt");
        first.complete();

        let BeginOutcome::New(second) = registry
            .begin("output.read", &context, &params)
            .expect("operation ID is reusable after handoff")
        else {
            panic!("new operation expected")
        };
        second
            .finish(&serde_json::json!({"next_offset": 0}), None)
            .expect("second attempt completes handler work");
        drop(stale);
        assert!(matches!(
            registry.begin("output.read", &context, &params),
            Err(RegistryError::InProgress)
        ));
        registry
            .active_response_handoff("output.read", &params)
            .expect("second handoff token exists")
            .complete();
        assert_eq!(registry.record_stats().1, 0);
    }

    #[test]
    fn session_resource_creation_stays_active_until_response_handoff() {
        let registry = OperationRegistry::new(
            "env".to_owned(),
            7,
            2,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
        );
        let params = serde_json::json!({
            "context": {"operation_id": "open-reader"},
            "path": {"mount_id": "workspace", "path": "/file"}
        });
        let context = EIPCallContext {
            operation_id: "open-reader".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(lease) = registry
            .begin("file.open_reader", &context, &params)
            .expect("session resource operation begins")
        else {
            panic!("new operation expected")
        };
        lease
            .finish(&serde_json::json!({"reader": "reader-1"}), None)
            .expect("session resource handler completes");
        assert!(matches!(
            registry.begin("file.open_reader", &context, &params),
            Err(RegistryError::InProgress)
        ));
        registry
            .active_response_handoff("file.open_reader", &params)
            .expect("session resource response has a handoff")
            .complete();
        assert_eq!(registry.record_stats().1, 0);
    }

    #[test]
    fn pinned_command_origin_cannot_consume_the_reconciliation_reserve() {
        let registry = OperationRegistry::new(
            "env".to_owned(),
            7,
            1,
            std::time::Duration::from_secs(60),
            std::time::Duration::from_secs(60),
        );
        let start_params = serde_json::json!({
            "context": {"operation_id": "start"},
            "request": {"command": {"kind": "argv"}}
        });
        let start_context = EIPCallContext {
            operation_id: "start".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(start) = registry
            .begin("process.start", &start_context, &start_params)
            .expect("command origin begins")
        else {
            panic!("new operation expected")
        };
        start
            .finish(
                &serde_json::json!({
                    "process": {
                        "handle": "process-1",
                        "output": {
                            "stdout": {"reference": "output-1"},
                            "stderr": {"reference": "output-2"}
                        }
                    }
                }),
                None,
            )
            .expect("command origin publishes");

        let ordinary_params = serde_json::json!({
            "context": {"operation_id": "ordinary"},
            "path": {"mount_id": "workspace", "path": "/file"},
            "text": "value"
        });
        let ordinary_context = EIPCallContext {
            operation_id: "ordinary".to_owned(),
            timeout_ms: None,
        };
        assert!(matches!(
            registry.begin("file.write_text", &ordinary_context, &ordinary_params),
            Err(RegistryError::Capacity)
        ));

        let reconcile_params = serde_json::json!({
            "context": {"operation_id": "inspect"},
            "handle": "process-1"
        });
        let reconcile_context = EIPCallContext {
            operation_id: "inspect".to_owned(),
            timeout_ms: None,
        };
        let BeginOutcome::New(inspect) = registry
            .begin("process.inspect", &reconcile_context, &reconcile_params)
            .expect("reconciliation reserve remains available")
        else {
            panic!("new reconciliation operation expected")
        };
        inspect
            .finish(&serde_json::json!({"process": {}}), None)
            .expect("reconciliation handler completes");
        registry
            .active_response_handoff("process.inspect", &reconcile_params)
            .expect("reconciliation response has a handoff")
            .complete();

        registry.release_selector("process", "process-1");
        registry.release_selector("output", "output-1");
        registry.release_selector("output", "output-2");
        assert!(matches!(
            registry
                .begin("file.write_text", &ordinary_context, &ordinary_params)
                .expect("unpinned origin becomes reclaimable"),
            BeginOutcome::New(_)
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
            timeout_ms: None,
        };
        let params = serde_json::json!({"context": {"operation_id": "timed"}});
        let _lease = match registry
            .begin("environment.describe", &context, &params)
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
            timeout_ms: Some(0),
        };
        assert!(matches!(
            registry.begin("environment.describe", &expired, &params),
            Err(RegistryError::DeadlineExpired)
        ));
    }
}
