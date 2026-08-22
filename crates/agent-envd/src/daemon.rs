use std::{
    collections::BTreeMap,
    error::Error,
    fmt,
    future::Future,
    net::{IpAddr, Ipv4Addr, SocketAddr},
    sync::{Arc, Mutex, MutexGuard, PoisonError},
    time::{Duration, Instant},
};

use serde::{Serialize, de::DeserializeOwned};
use tokio::sync::{Notify, mpsc, oneshot, watch};

use crate::{
    config::Config,
    eip::{
        self, DispatchError, DispatchStage, EIPError, EIPErrorData, EIPServerInfo, EipHandler,
        EnvironmentDescribeParams, EnvironmentDescribeResult, EnvironmentDescriptor, ErrorType,
        InitializeParams, InitializeResult, IsolationBackend, IsolationCleanupGuarantee,
        IsolationMode, IsolationNetworkPolicy, IsolationPosture, JsonRpcErrorResponse, JsonRpcId,
        JsonRpcRequest, JsonRpcSuccessResponse, ReceiptOutcome, ReceiptStage, RetryHint,
        SessionCloseParams, SessionCloseResult,
    },
    mount::MountRegistry,
    operation::{BeginOutcome, OperationLease, OperationRegistry, RegistryError},
    resource::{ResourceError, ResourceRegistry},
    retention::{RetentionError, RetentionQuota, RetentionStore},
    transfer::{TransferError, TransferRegistry},
};

const MAX_STRING_REQUEST_ID_BYTES: usize = 128;
const BASE_CAPABILITIES: [&str; 2] = ["environment.describe", "session.close"];

macro_rules! unsupported_methods {
    ($($name:ident($params:ty) -> $result:ty;)+) => {
        $(
            async fn $name(&self, _params: $params) -> Result<$result, EIPError> {
                Err(protocol_error(
                    ErrorType::Unsupported,
                    "method capability is not available",
                ))
            }
        )+
    };
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SessionState {
    Uninitialized,
    Initialized,
    Closed,
}

struct SessionAdmission {
    state: Mutex<SessionAdmissionState>,
    idle: Notify,
}

struct SessionAdmissionState {
    lifecycle: SessionState,
    active_session_work: usize,
}

struct SessionWorkGuard<'a> {
    admission: &'a SessionAdmission,
}

#[derive(Clone, Default)]
struct OwnedOperationTasks {
    inner: Arc<OwnedOperationTasksInner>,
}

#[derive(Default)]
struct OwnedOperationTasksInner {
    state: Mutex<OwnedOperationTaskState>,
    idle: Notify,
}

#[derive(Default)]
struct OwnedOperationTaskState {
    tasks: BTreeMap<String, Option<oneshot::Sender<()>>>,
    draining: bool,
}

struct OwnedOperationTaskGuard {
    tasks: OwnedOperationTasks,
    operation_id: String,
}

type OwnedOperationResult<T> = oneshot::Receiver<Option<Result<T, EIPError>>>;

pub(crate) struct Daemon {
    descriptor: EnvironmentDescriptor,
    session: SessionAdmission,
    max_operation_duration: Duration,
    operations: OperationRegistry,
    owned_operations: OwnedOperationTasks,
    mounts: MountRegistry,
    resources: ResourceRegistry,
    retention: RetentionStore,
    transfers: TransferRegistry,
    closed: watch::Sender<bool>,
    max_response_bytes: usize,
}

impl SessionAdmission {
    fn state(&self) -> MutexGuard<'_, SessionAdmissionState> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn admit_work(&self) -> Option<SessionWorkGuard<'_>> {
        let mut state = self.state();
        if state.lifecycle != SessionState::Initialized {
            return None;
        }
        state.active_session_work = state
            .active_session_work
            .checked_add(1)
            .expect("session work accounting overflow");
        Some(SessionWorkGuard { admission: self })
    }

    fn close(&self) {
        self.state().lifecycle = SessionState::Closed;
    }

    async fn wait_until_idle(&self) {
        loop {
            let notified = self.idle.notified();
            if self.state().active_session_work == 0 {
                return;
            }
            notified.await;
        }
    }
}

impl Drop for SessionWorkGuard<'_> {
    fn drop(&mut self) {
        let mut state = self.admission.state();
        state.active_session_work = state
            .active_session_work
            .checked_sub(1)
            .expect("session work guard released exactly once");
        let idle = state.active_session_work == 0;
        drop(state);
        if idle {
            self.admission.idle.notify_waiters();
        }
    }
}

impl OwnedOperationTasks {
    fn spawn<T, F>(&self, operation_id: String, future: F) -> OwnedOperationResult<T>
    where
        T: Send + 'static,
        F: Future<Output = Result<T, EIPError>> + Send + 'static,
    {
        let (reconcile_tx, mut reconcile_rx) = oneshot::channel();
        let (result_tx, result_rx) = oneshot::channel();
        let mut reconcile_tx = Some(reconcile_tx);
        let registered = {
            let mut state = self
                .inner
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            if state.draining {
                false
            } else {
                let replaced = state
                    .tasks
                    .insert(operation_id.clone(), reconcile_tx.take());
                assert!(
                    replaced.is_none(),
                    "operation task IDs are generation-unique"
                );
                true
            }
        };
        if !registered {
            drop(future);
            let _ = result_tx.send(None);
            return result_rx;
        }
        let guard = OwnedOperationTaskGuard {
            tasks: self.clone(),
            operation_id,
        };
        tokio::spawn(async move {
            let _guard = guard;
            let mut future = Box::pin(future);
            let result = tokio::select! {
                biased;
                _ = &mut reconcile_rx => {
                    drop(future);
                    None
                }
                result = future.as_mut() => Some(result),
            };
            let _ = result_tx.send(result);
        });
        result_rx
    }

    #[cfg(test)]
    fn active_ids(&self) -> Vec<String> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .tasks
            .keys()
            .cloned()
            .collect()
    }

    fn begin_drain(&self) -> Vec<String> {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state.draining = true;
        state.tasks.keys().cloned().collect()
    }

    fn request_reconciliation(&self) {
        let senders = {
            let mut state = self
                .inner
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            state.draining = true;
            state
                .tasks
                .values_mut()
                .filter_map(Option::take)
                .collect::<Vec<_>>()
        };
        for sender in senders {
            let _ = sender.send(());
        }
    }

    async fn wait_until_idle(&self) {
        loop {
            let notified = self.inner.idle.notified();
            if self
                .inner
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .tasks
                .is_empty()
            {
                return;
            }
            notified.await;
        }
    }
}

impl Drop for OwnedOperationTaskGuard {
    fn drop(&mut self) {
        let idle = {
            let mut state = self
                .tasks
                .inner
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            state.tasks.remove(&self.operation_id);
            state.tasks.is_empty()
        };
        if idle {
            self.tasks.inner.idle.notify_waiters();
        }
    }
}

impl Daemon {
    pub(crate) fn new(config: &Config) -> Result<Self, DaemonInitError> {
        Self::with_generation(config, fresh_generation()?)
    }

    fn with_generation(config: &Config, generation: u64) -> Result<Self, DaemonInitError> {
        if generation == 0 {
            return Err(DaemonInitError::new("generation must be nonzero"));
        }
        let max_response_bytes = usize::try_from(config.limits.max_response_bytes)
            .map_err(|_| DaemonInitError::new("max_response_bytes does not fit this platform"))?;
        let max_operation_records =
            usize::try_from(config.limits.max_operation_records).map_err(|_| {
                DaemonInitError::new("max_operation_records does not fit this platform")
            })?;
        let operation_record_ttl = Duration::from_millis(config.limits.operation_record_ttl_ms);
        let mounts = MountRegistry::initialize(config).map_err(|error| {
            DaemonInitError::new(format!("mount initialization failed: {error}"))
        })?;
        let transfers = TransferRegistry::new(config)
            .map_err(|_| DaemonInitError::new("transfer registry initialization failed"))?;
        let operations = OperationRegistry::new(
            config.environment_id.clone(),
            generation,
            max_operation_records,
            operation_record_ttl,
            Duration::from_millis(config.limits.max_operation_duration_ms),
        );
        let retention_quota = RetentionQuota::new(config)
            .map_err(|_| DaemonInitError::new("retention quota initialization failed"))?;
        let resources = ResourceRegistry::new(config, operations.clone(), retention_quota.clone())
            .map_err(|_| DaemonInitError::new("resource registry initialization failed"))?;
        let retention = RetentionStore::new(config, generation, retention_quota)
            .map_err(|_| DaemonInitError::new("retention store initialization failed"))?;
        let mut capabilities = BASE_CAPABILITIES
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();
        capabilities.extend([
            "operation.cancel".to_owned(),
            "port.observe".to_owned(),
            "receipt.read".to_owned(),
        ]);
        if mounts.has_complete_read_family() {
            capabilities.push("file.read".to_owned());
        }
        if mounts.has_complete_write_family() {
            capabilities.push("file.write".to_owned());
        }
        if mounts.supports_anywhere("find") {
            capabilities.push("file.find".to_owned());
        }
        if mounts.supports_anywhere("search") {
            capabilities.push("file.search".to_owned());
        }
        capabilities.sort();
        let descriptor = EnvironmentDescriptor {
            environment_id: config.environment_id.clone(),
            generation,
            capabilities,
            mounts: mounts.descriptors(),
            shell_profiles: Vec::new(),
            limits: config.limits.clone(),
            isolation: IsolationPosture {
                mode: IsolationMode::Disabled,
                backend: IsolationBackend::OuterHost,
                filesystem_containment: false,
                process_containment: false,
                network_containment: false,
                network_policy: IsolationNetworkPolicy::Host,
                cleanup_guarantee: IsolationCleanupGuarantee::OuterHost,
            },
        };
        let (closed, _) = watch::channel(false);
        Ok(Self {
            descriptor,
            session: SessionAdmission {
                state: Mutex::new(SessionAdmissionState {
                    lifecycle: SessionState::Uninitialized,
                    active_session_work: 0,
                }),
                idle: Notify::new(),
            },
            max_operation_duration: Duration::from_millis(config.limits.max_operation_duration_ms),
            operations,
            owned_operations: OwnedOperationTasks::default(),
            mounts,
            resources,
            retention,
            transfers,
            closed,
            max_response_bytes,
        })
    }

    pub(crate) fn subscribe_closed(&self) -> watch::Receiver<bool> {
        self.closed.subscribe()
    }

    pub(crate) fn has_active_file_transfers(&self) -> bool {
        self.transfers.has_active()
    }

    pub(crate) fn install_data_sender(
        &self,
        sender: mpsc::Sender<eip::DataFrame>,
    ) -> Result<(), DaemonInitError> {
        self.transfers
            .install_outbound(sender)
            .map_err(|_| DaemonInitError::new("stdio data sender is already installed"))
    }

    pub(crate) async fn handle_data_frame(
        &self,
        frame: eip::DataFrame,
    ) -> Result<(), TransferError> {
        let _work = self.session.admit_work().ok_or(TransferError::Protocol)?;
        self.transfers.handle_frame(frame).await
    }

    pub(crate) async fn transport_closed(&self, budget: Duration) -> bool {
        self.session.close();
        self.closed.send_replace(true);
        self.transfers.begin_session_close();
        let admission_budget = budget / 2;
        let admission_idle = tokio::time::timeout(admission_budget, self.session.wait_until_idle())
            .await
            .is_ok();
        let transfers_closed = tokio::time::timeout(
            budget.saturating_sub(admission_budget),
            self.transfers.close_session(),
        )
        .await
        .is_ok();
        admission_idle && transfers_closed
    }

    pub(crate) async fn drain_owned_operations(&self, budget: Duration) -> bool {
        let started = Instant::now();
        for operation_id in self.owned_operations.begin_drain() {
            self.operations.cancel(&operation_id);
        }
        let task_budget = budget / 3;
        let tasks_drained =
            if tokio::time::timeout(task_budget, self.owned_operations.wait_until_idle())
                .await
                .is_ok()
            {
                true
            } else {
                self.owned_operations.request_reconciliation();
                tokio::time::timeout(task_budget, self.owned_operations.wait_until_idle())
                    .await
                    .is_ok()
            };
        let transfers_reconciled = tokio::time::timeout(
            budget.saturating_sub(started.elapsed()),
            self.transfers.reconcile_committing(),
        )
        .await
        .is_ok();
        tasks_drained && transfers_reconciled
    }

    async fn await_owned_operation<T>(
        &self,
        operation_id: &str,
        result: OwnedOperationResult<T>,
    ) -> Result<T, EIPError> {
        match result.await {
            Ok(Some(result)) => result,
            Ok(None) | Err(_) => Err(self
                .operations
                .failure_by_operation(operation_id)
                .unwrap_or_else(|| {
                    protocol_error(
                        ErrorType::InternalError,
                        "owned operation task ended without terminal evidence",
                    )
                })),
        }
    }

    pub(crate) async fn maintenance(&self) {
        self.retention.expire();
        self.resources.expire();
        self.transfers.expire().await;
    }

    pub(crate) async fn handle_payload(&self, payload: &str) -> Vec<u8> {
        let request = match eip::decode::<JsonRpcRequest>(payload) {
            Ok(request) => request,
            Err(error) => {
                let request_id = match &error {
                    eip::DecodeError::Validation(_) => recover_request_id(payload),
                    eip::DecodeError::Json(_) => None,
                };
                let error_type = match error {
                    eip::DecodeError::Json(error)
                        if matches!(
                            error.classify(),
                            serde_json::error::Category::Syntax | serde_json::error::Category::Eof
                        ) =>
                    {
                        ErrorType::ParseError
                    }
                    eip::DecodeError::Json(_) | eip::DecodeError::Validation(_) => {
                        ErrorType::InvalidRequest
                    }
                };
                self.close_if_uninitialized();
                return self.error_response(
                    request_id,
                    protocol_error(error_type, error_type_message(error_type)),
                );
            }
        };

        if !valid_request_id(&request.id) {
            self.close_if_uninitialized();
            return self.error_response(
                None,
                protocol_error(ErrorType::InvalidRequest, "invalid JSON-RPC request ID"),
            );
        }

        let request_id = request.id.clone();
        if let Err(error) = self.preflight(&request.method).await {
            return self.error_response(Some(request_id), error);
        }

        let params_json = match serde_json::to_string(&request.params) {
            Ok(params) => params,
            Err(_) => {
                return self.error_response(
                    Some(request_id),
                    protocol_error(ErrorType::InternalError, "failed to encode request params"),
                );
            }
        };
        let is_initialization = request.method == "initialize";
        let result = eip::dispatch(self, &request.method, &params_json).await;
        if is_initialization && result.is_err() {
            self.close_if_uninitialized();
        }

        match result {
            Ok(serde_json::Value::Object(fields)) => {
                let response = JsonRpcSuccessResponse {
                    jsonrpc: "2.0".to_owned(),
                    id: request_id,
                    result: fields.into_iter().collect(),
                    extensions: BTreeMap::new(),
                };
                match eip::encode(&response) {
                    Ok(encoded) if encoded.len() <= self.max_response_bytes => encoded,
                    Ok(_) | Err(_) => self.error_response(
                        Some(response.id),
                        protocol_error(ErrorType::InternalError, "response encoding failed"),
                    ),
                }
            }
            Ok(_) => self.error_response(
                Some(request_id),
                protocol_error(
                    ErrorType::InternalError,
                    "method returned a non-object result",
                ),
            ),
            Err(error) => {
                self.error_response(Some(request_id), map_dispatch_error(error, &request.method))
            }
        }
    }

    async fn preflight(&self, method: &str) -> Result<(), EIPError> {
        let mut state = self.session.state();
        match state.lifecycle {
            SessionState::Uninitialized if method == "initialize" => Ok(()),
            SessionState::Uninitialized => {
                state.lifecycle = SessionState::Closed;
                self.closed.send_replace(true);
                Err(protocol_error(
                    ErrorType::NotInitialized,
                    "initialize must be the first request",
                ))
            }
            SessionState::Initialized if method == "initialize" => Err(protocol_error(
                ErrorType::AlreadyInitialized,
                "session is already initialized",
            )),
            SessionState::Initialized => {
                if let Some(capability) = method_capability(method)
                    && !self
                        .descriptor
                        .capabilities
                        .iter()
                        .any(|advertised| advertised == capability)
                {
                    return Err(error_with_capability(
                        ErrorType::Unsupported,
                        "method capability is not available",
                        capability,
                    ));
                }
                Ok(())
            }
            SessionState::Closed => Err(protocol_error(
                ErrorType::NotInitialized,
                "session is closed",
            )),
        }
    }

    fn close_if_uninitialized(&self) {
        let mut state = self.session.state();
        if state.lifecycle == SessionState::Uninitialized {
            state.lifecycle = SessionState::Closed;
            self.closed.send_replace(true);
        }
    }

    #[allow(clippy::result_large_err)]
    fn ensure_initialized(&self) -> Result<(), EIPError> {
        if self.session.state().lifecycle == SessionState::Initialized {
            Ok(())
        } else {
            Err(protocol_error(
                ErrorType::NotInitialized,
                "session is not initialized",
            ))
        }
    }

    #[allow(clippy::result_large_err)]
    fn admit_record<P: Serialize>(
        &self,
        method: &str,
        context: &eip::EIPCallContext,
        params: &P,
        key_allowed: bool,
    ) -> Result<BeginOutcome, EIPError> {
        let session = self.session.state();
        if session.lifecycle != SessionState::Initialized {
            return Err(protocol_error(
                ErrorType::NotInitialized,
                "session is not initialized",
            ));
        }
        self.begin_record(method, context, params, key_allowed)
    }

    #[allow(clippy::result_large_err)]
    fn admit_owned_record<'a, P: Serialize>(
        &'a self,
        method: &str,
        context: &eip::EIPCallContext,
        params: &P,
    ) -> Result<(SessionWorkGuard<'a>, BeginOutcome), EIPError> {
        let work = self.session.admit_work().ok_or_else(|| {
            protocol_error(ErrorType::NotInitialized, "session is not initialized")
        })?;
        let operation = self.begin_record(method, context, params, true)?;
        Ok((work, operation))
    }

    #[allow(clippy::result_large_err)]
    fn begin_record<P: Serialize>(
        &self,
        method: &str,
        context: &eip::EIPCallContext,
        params: &P,
        key_allowed: bool,
    ) -> Result<BeginOutcome, EIPError> {
        self.operations
            .begin(method, context, params, key_allowed)
            .map_err(map_registry_error)
    }

    #[allow(clippy::result_large_err)]
    fn decode_replay<R: DeserializeOwned>(&self, value: serde_json::Value) -> Result<R, EIPError> {
        serde_json::from_value(value).map_err(|_| {
            protocol_error(
                ErrorType::InternalError,
                "retained operation result failed validation",
            )
        })
    }

    fn error_response(&self, id: Option<JsonRpcId>, error: EIPError) -> Vec<u8> {
        let response = JsonRpcErrorResponse {
            jsonrpc: "2.0".to_owned(),
            id,
            error,
            extensions: BTreeMap::new(),
        };
        match eip::encode(&response) {
            Ok(encoded) if encoded.len() <= self.max_response_bytes => encoded,
            Ok(_) | Err(_) => {
                b"{\"error\":{\"code\":-32603,\"data\":{\"dispatch_stage\":\"pre_dispatch\",\"error_type\":\"internal_error\",\"retry_hint\":\"never\"},\"message\":\"response encoding failed\"},\"id\":null,\"jsonrpc\":\"2.0\"}".to_vec()
            }
        }
    }
}

impl EipHandler for Daemon {
    async fn environment_describe(
        &self,
        params: EnvironmentDescribeParams,
    ) -> Result<EnvironmentDescribeResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let operation =
            match self.admit_record("environment.describe", &params.context, &params, false)? {
                BeginOutcome::Replay(value) => return self.decode_replay(value),
                BeginOutcome::ReplayFailure(error) => return Err(*error),
                BeginOutcome::New(operation) => operation,
            };
        let result = EnvironmentDescribeResult {
            descriptor: self.descriptor.clone(),
        };
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn initialize(&self, params: InitializeParams) -> Result<InitializeResult, EIPError> {
        let mut state = self.session.state();
        if state.lifecycle != SessionState::Uninitialized {
            return Err(protocol_error(
                ErrorType::AlreadyInitialized,
                "session is already initialized",
            ));
        }

        let failure = if !params
            .supported_protocol_versions
            .iter()
            .any(|version| version == eip::EIP_PROTOCOL_VERSION)
        {
            Some(protocol_error(
                ErrorType::ProtocolIncompatible,
                "no mutually supported EIP protocol version",
            ))
        } else if params.expected_environment_id != self.descriptor.environment_id {
            Some(protocol_error(
                ErrorType::ProtocolIncompatible,
                "expected Environment identity does not match",
            ))
        } else {
            params
                .required_capabilities
                .iter()
                .find(|required| !self.descriptor.capabilities.contains(required))
                .map(|required| {
                    error_with_capability(
                        ErrorType::ProtocolIncompatible,
                        "required capability is not available",
                        required,
                    )
                })
        };

        if let Some(error) = failure {
            state.lifecycle = SessionState::Closed;
            self.closed.send_replace(true);
            return Err(error);
        }

        state.lifecycle = SessionState::Initialized;
        Ok(InitializeResult {
            protocol_version: eip::EIP_PROTOCOL_VERSION.to_owned(),
            server: EIPServerInfo {
                name: "agent-envd".to_owned(),
                version: env!("CARGO_PKG_VERSION").to_owned(),
            },
            descriptor: self.descriptor.clone(),
        })
    }

    async fn session_close(
        &self,
        params: SessionCloseParams,
    ) -> Result<SessionCloseResult, EIPError> {
        ensure_deadline(&params.context)?;
        let operation = {
            let mut session = self.session.state();
            if session.lifecycle != SessionState::Initialized {
                return Err(protocol_error(
                    ErrorType::NotInitialized,
                    "session is not initialized",
                ));
            }
            let operation = self.begin_record("session.close", &params.context, &params, false)?;
            session.lifecycle = SessionState::Closed;
            operation
        };
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        self.closed.send_replace(true);
        self.transfers.begin_session_close();
        self.session.wait_until_idle().await;
        self.transfers.close_session().await;
        let result = SessionCloseResult { closed: true };
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn file_open_reader(
        &self,
        params: eip::FileReaderOpenParams,
    ) -> Result<eip::FileReaderOpenResult, EIPError> {
        self.transfers.expire().await;
        let work = self.session.admit_work().ok_or_else(|| {
            protocol_error(ErrorType::NotInitialized, "session is not initialized")
        })?;
        let operation = self
            .operations
            .begin_with_replay_validation(
                "file.open_reader",
                &params.context,
                &params,
                true,
                |value| {
                    serde_json::from_value::<eip::FileReaderOpenResult>(value.clone())
                        .is_ok_and(|result| self.transfers.is_live_reader(&result.reader.0))
                },
            )
            .map_err(map_registry_error)?;
        drop(work);
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let result = self
            .transfers
            .open_reader(&self.mounts, &params)
            .await
            .map_err(map_transfer_error)?;
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn file_close_reader(
        &self,
        params: eip::FileReaderCloseParams,
    ) -> Result<eip::FileReaderCloseResult, EIPError> {
        self.ensure_initialized()?;
        let operation =
            match self.admit_record("file.close_reader", &params.context, &params, true)? {
                BeginOutcome::Replay(value) => return self.decode_replay(value),
                BeginOutcome::ReplayFailure(error) => return Err(*error),
                BeginOutcome::New(operation) => operation,
            };
        let result = self
            .transfers
            .close_reader(&params.reader, params.accept_complete)
            .await
            .map_err(map_transfer_error)?;
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn file_open_writer(
        &self,
        params: eip::FileWriterOpenParams,
    ) -> Result<eip::FileWriterOpenResult, EIPError> {
        self.transfers.expire().await;
        let work = self.session.admit_work().ok_or_else(|| {
            protocol_error(ErrorType::NotInitialized, "session is not initialized")
        })?;
        let operation = self
            .operations
            .begin_with_replay_validation(
                "file.open_writer",
                &params.context,
                &params,
                true,
                |value| {
                    serde_json::from_value::<eip::FileWriterOpenResult>(value.clone())
                        .is_ok_and(|result| self.transfers.is_live_writer(&result.writer.0))
                },
            )
            .map_err(map_registry_error)?;
        drop(work);
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let result = self
            .transfers
            .open_writer(&self.mounts, &params)
            .await
            .map_err(map_transfer_error)?;
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn file_abort_writer(
        &self,
        params: eip::FileWriterAbortParams,
    ) -> Result<eip::FileWriterAbortResult, EIPError> {
        self.ensure_initialized()?;
        let operation =
            match self.admit_record("file.abort_writer", &params.context, &params, true)? {
                BeginOutcome::Replay(value) => return self.decode_replay(value),
                BeginOutcome::ReplayFailure(error) => return Err(*error),
                BeginOutcome::New(operation) => operation,
            };
        let result = eip::FileWriterAbortResult {
            status: self
                .transfers
                .abort_writer(&params.writer)
                .await
                .map_err(map_transfer_error)?,
        };
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn file_commit_writer(
        &self,
        params: eip::FileWriterCommitParams,
    ) -> Result<eip::FileWriterCommitResult, EIPError> {
        let work = self.session.admit_work().ok_or_else(|| {
            protocol_error(ErrorType::NotInitialized, "session is not initialized")
        })?;
        let operation =
            match self.begin_record("file.commit_writer", &params.context, &params, true)? {
                BeginOutcome::Replay(value) => return self.decode_replay(value),
                BeginOutcome::ReplayFailure(error) => return Err(*error),
                BeginOutcome::New(operation) => operation,
            };
        let (operation, receipt) = mutation_receipt(operation, "file.commit_writer")?;
        let commit = match self.transfers.prepare_commit(&params).await {
            Ok(commit) => commit,
            Err(error) => {
                return Err(mutation_failure(
                    operation,
                    "file.commit_writer",
                    map_transfer_error(error),
                ));
            }
        };
        let operation_id = params.context.operation_id.clone();
        let operations = self.operations.clone();
        let commit_operation_id = operation_id.clone();
        let owned = self
            .owned_operations
            .spawn(operation_id.clone(), async move {
                let committed = match commit.execute(operations, commit_operation_id).await {
                    Ok(committed) => committed,
                    Err(error) => {
                        return Err(mutation_failure(
                            operation,
                            "file.commit_writer",
                            map_transfer_error(error),
                        ));
                    }
                };
                let result = eip::FileWriterCommitResult {
                    info: committed.info,
                    transferred_bytes: committed.transferred_bytes,
                    transfer_digest: committed.transfer_digest,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_registry_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn file_stat(
        &self,
        params: eip::FileStatParams,
    ) -> Result<eip::FileStatResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let operation = match self.admit_record("file.stat", &params.context, &params, false)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let resources = self.resources.clone();
        let mounts = self.mounts.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.stat(&mounts, &call))
            .await
            .map_err(|_| protocol_error(ErrorType::InternalError, "resource worker failed"))?
            .map_err(map_resource_error)?;
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn file_read_text(
        &self,
        params: eip::FileReadTextParams,
    ) -> Result<eip::FileReadTextResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let operation =
            match self.admit_record("file.read_text", &params.context, &params, false)? {
                BeginOutcome::Replay(value) => return self.decode_replay(value),
                BeginOutcome::ReplayFailure(error) => return Err(*error),
                BeginOutcome::New(operation) => operation,
            };
        let resources = self.resources.clone();
        let mounts = self.mounts.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.read_text(&mounts, &call))
            .await
            .map_err(|_| protocol_error(ErrorType::InternalError, "resource worker failed"))?
            .map_err(map_resource_error)?;
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn file_list(
        &self,
        params: eip::FileListParams,
    ) -> Result<eip::FileListResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let operation = match self.admit_record("file.list", &params.context, &params, false)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let resources = self.resources.clone();
        let mounts = self.mounts.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.list(&mounts, &call))
            .await
            .map_err(|_| protocol_error(ErrorType::InternalError, "resource worker failed"))?
            .map_err(map_resource_error)?;
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn file_find(
        &self,
        params: eip::FileFindParams,
    ) -> Result<eip::FileFindResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let operation = match self.admit_record("file.find", &params.context, &params, false)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let resources = self.resources.clone();
        let mounts = self.mounts.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.find(&mounts, &call))
            .await
            .map_err(|_| protocol_error(ErrorType::InternalError, "resource worker failed"))?
            .map_err(map_resource_error)?;
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn file_search(
        &self,
        params: eip::FileSearchParams,
    ) -> Result<eip::FileSearchResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let operation = match self.admit_record("file.search", &params.context, &params, false)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let resources = self.resources.clone();
        let mounts = self.mounts.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.search(&mounts, &call))
            .await
            .map_err(|_| protocol_error(ErrorType::InternalError, "resource worker failed"))?
            .map_err(map_resource_error)?;
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn file_write_text(
        &self,
        params: eip::FileWriteTextParams,
    ) -> Result<eip::FileWriteTextResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        if params.mode == eip::FileWriteMode::Append && params.context.idempotency_key.is_none() {
            return Err(protocol_error(
                ErrorType::InvalidParams,
                "append requires an idempotency_key",
            ));
        }
        let (work, operation) =
            self.admit_owned_record("file.write_text", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.write_text")?;
        let operation_id = params.context.operation_id.clone();
        let resources = self.resources.clone();
        let mounts = self.mounts.clone();
        let owned = self
            .owned_operations
            .spawn(operation_id.clone(), async move {
                let write =
                    tokio::task::spawn_blocking(move || resources.write_text(&mounts, &params))
                        .await;
                let (info, bytes_written) = match write {
                    Ok(Ok(result)) => result,
                    Ok(Err(error)) => {
                        return Err(mutation_failure(
                            operation,
                            "file.write_text",
                            map_resource_error(error),
                        ));
                    }
                    Err(_) => {
                        return Err(mutation_failure(
                            operation,
                            "file.write_text",
                            protocol_error(ErrorType::InternalError, "resource worker failed"),
                        ));
                    }
                };
                let result = eip::FileWriteTextResult {
                    info,
                    bytes_written,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_registry_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn file_mkdir(
        &self,
        params: eip::FileMkdirParams,
    ) -> Result<eip::FileMkdirResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let (work, operation) = self.admit_owned_record("file.mkdir", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.mkdir")?;
        let operation_id = params.context.operation_id.clone();
        let resources = self.resources.clone();
        let mounts = self.mounts.clone();
        let owned = self
            .owned_operations
            .spawn(operation_id.clone(), async move {
                let mkdir =
                    tokio::task::spawn_blocking(move || resources.mkdir(&mounts, &params)).await;
                let (info, created_directories) = match mkdir {
                    Ok(Ok(result)) => result,
                    Ok(Err(error)) => {
                        return Err(mutation_failure(
                            operation,
                            "file.mkdir",
                            map_resource_error(error),
                        ));
                    }
                    Err(_) => {
                        return Err(mutation_failure(
                            operation,
                            "file.mkdir",
                            protocol_error(ErrorType::InternalError, "resource worker failed"),
                        ));
                    }
                };
                let result = eip::FileMkdirResult {
                    info,
                    created_directories,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_registry_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn file_patch_text(
        &self,
        params: eip::FilePatchTextParams,
    ) -> Result<eip::FilePatchTextResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let (work, operation) =
            self.admit_owned_record("file.patch_text", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.patch_text")?;
        let operation_id = params.context.operation_id.clone();
        let resources = self.resources.clone();
        let mounts = self.mounts.clone();
        let owned = self
            .owned_operations
            .spawn(operation_id.clone(), async move {
                let patch =
                    tokio::task::spawn_blocking(move || resources.patch_text(&mounts, &params))
                        .await;
                let (info, hunks_applied) = match patch {
                    Ok(Ok(result)) => result,
                    Ok(Err(error)) => {
                        return Err(mutation_failure(
                            operation,
                            "file.patch_text",
                            map_resource_error(error),
                        ));
                    }
                    Err(_) => {
                        return Err(mutation_failure(
                            operation,
                            "file.patch_text",
                            protocol_error(ErrorType::InternalError, "resource worker failed"),
                        ));
                    }
                };
                let result = eip::FilePatchTextResult {
                    info,
                    hunks_applied,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_registry_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn file_copy(
        &self,
        params: eip::FileCopyParams,
    ) -> Result<eip::FileCopyResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let (work, operation) = self.admit_owned_record("file.copy", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.copy")?;
        let operation_id = params.context.operation_id.clone();
        let resources = self.resources.clone();
        let mounts = self.mounts.clone();
        let owned = self
            .owned_operations
            .spawn(operation_id.clone(), async move {
                let copy =
                    tokio::task::spawn_blocking(move || resources.copy(&mounts, &params)).await;
                let (destination, bytes_copied, source_stability) = match copy {
                    Ok(Ok(result)) => result,
                    Ok(Err(error)) => {
                        return Err(mutation_failure(
                            operation,
                            "file.copy",
                            map_resource_error(error),
                        ));
                    }
                    Err(_) => {
                        return Err(mutation_failure(
                            operation,
                            "file.copy",
                            protocol_error(ErrorType::InternalError, "resource worker failed"),
                        ));
                    }
                };
                let result = eip::FileCopyResult {
                    destination,
                    bytes_copied,
                    atomic_destination: true,
                    receipt: receipt.clone(),
                    source_stability,
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_registry_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn file_move(
        &self,
        params: eip::FileMoveParams,
    ) -> Result<eip::FileMoveResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let (work, operation) = self.admit_owned_record("file.move", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.move")?;
        let operation_id = params.context.operation_id.clone();
        let resources = self.resources.clone();
        let mounts = self.mounts.clone();
        let owned = self
            .owned_operations
            .spawn(operation_id.clone(), async move {
                let moved =
                    tokio::task::spawn_blocking(move || resources.move_path(&mounts, &params))
                        .await;
                let destination = match moved {
                    Ok(Ok(result)) => result,
                    Ok(Err(error)) => {
                        return Err(mutation_failure(
                            operation,
                            "file.move",
                            map_resource_error(error),
                        ));
                    }
                    Err(_) => {
                        return Err(mutation_failure(
                            operation,
                            "file.move",
                            protocol_error(ErrorType::InternalError, "resource worker failed"),
                        ));
                    }
                };
                let result = eip::FileMoveResult {
                    destination,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_registry_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn file_remove(
        &self,
        params: eip::FileRemoveParams,
    ) -> Result<eip::FileRemoveResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let (work, operation) = self.admit_owned_record("file.remove", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.remove")?;
        let operation_id = params.context.operation_id.clone();
        let resources = self.resources.clone();
        let mounts = self.mounts.clone();
        let owned = self
            .owned_operations
            .spawn(operation_id.clone(), async move {
                let removed =
                    tokio::task::spawn_blocking(move || resources.remove(&mounts, &params)).await;
                let removed_entries = match removed {
                    Ok(Ok(result)) => result,
                    Ok(Err(error)) => {
                        return Err(mutation_failure(
                            operation,
                            "file.remove",
                            map_resource_error(error),
                        ));
                    }
                    Err(_) => {
                        return Err(mutation_failure(
                            operation,
                            "file.remove",
                            protocol_error(ErrorType::InternalError, "resource worker failed"),
                        ));
                    }
                };
                let result = eip::FileRemoveResult {
                    removed_entries,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_registry_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn operation_cancel(
        &self,
        params: eip::OperationCancelParams,
    ) -> Result<eip::OperationCancelResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let operation =
            match self.admit_record("operation.cancel", &params.context, &params, true)? {
                BeginOutcome::Replay(value) => return self.decode_replay(value),
                BeginOutcome::ReplayFailure(error) => return Err(*error),
                BeginOutcome::New(operation) => operation,
            };
        let result = eip::OperationCancelResult {
            status: self.operations.cancel(&params.target_operation_id),
        };
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn receipt_get(
        &self,
        params: eip::ReceiptGetParams,
    ) -> Result<eip::ReceiptGetResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let operation = match self.admit_record("receipt.get", &params.context, &params, false)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let receipt = if let Some(reference) = &params.receipt_ref {
            self.operations.receipt_by_ref(reference)
        } else if let Some(operation_id) = &params.operation_id {
            self.operations.receipt_by_operation(operation_id)
        } else {
            None
        }
        .ok_or_else(|| protocol_error(ErrorType::NotFoundOrDenied, "receipt was not found"))?;
        let result = eip::ReceiptGetResult { receipt };
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn output_read(
        &self,
        params: eip::OutputReadParams,
    ) -> Result<eip::OutputReadResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let operation = match self.admit_record("output.read", &params.context, &params, false)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let result = self.retention.read(&params).map_err(map_retention_error)?;
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn output_release(
        &self,
        params: eip::OutputReleaseParams,
    ) -> Result<eip::OutputReleaseResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let operation = match self.admit_record("output.release", &params.context, &params, true)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "output.release")?;
        let released = if let Some(reference) = &params.reference {
            self.retention.release_reference(reference)
        } else if let Some(cursor) = &params.cursor {
            self.retention.release_cursor(cursor) || self.resources.release_cursor(cursor)
        } else {
            false
        };
        let result = eip::OutputReleaseResult {
            released,
            receipt: receipt.clone(),
        };
        operation
            .finish(&result, Some(receipt))
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn port_inspect(
        &self,
        params: eip::PortInspectParams,
    ) -> Result<eip::PortInspectResult, EIPError> {
        self.ensure_initialized()?;
        ensure_deadline(&params.context)?;
        let operation = match self.admit_record("port.inspect", &params.context, &params, false)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let deadline = effective_deadline(params.context.deadline, self.max_operation_duration)?;
        let observation = inspect_port(&params.target, deadline).await;
        let result = eip::PortInspectResult { observation };
        operation
            .finish(&result, None)
            .map_err(map_registry_error)?;
        Ok(result)
    }

    async fn port_wait(
        &self,
        params: eip::PortWaitParams,
    ) -> Result<eip::PortWaitResult, EIPError> {
        self.ensure_initialized()?;
        let requested_deadline = params.context.deadline.ok_or_else(|| {
            protocol_error(
                ErrorType::InvalidParams,
                "port.wait requires a finite context deadline",
            )
        })?;
        ensure_deadline(&params.context)?;
        let operation = match self.admit_record("port.wait", &params.context, &params, false)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let deadline = effective_deadline(Some(requested_deadline), self.max_operation_duration)?;
        loop {
            if self
                .operations
                .cancellation_requested(&params.context.operation_id)
            {
                return Err(protocol_error(
                    ErrorType::Cancelled,
                    "port wait was cancelled",
                ));
            }
            let observation = inspect_port(&params.target, deadline).await;
            let desired = match params.desired_status {
                eip::DesiredPortStatus::Listening => eip::PortStatus::Listening,
                eip::DesiredPortStatus::NotListening => eip::PortStatus::NotListening,
            };
            if observation.status == desired {
                let result = eip::PortWaitResult { observation };
                operation
                    .finish(&result, None)
                    .map_err(map_registry_error)?;
                return Ok(result);
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(protocol_error(
                    ErrorType::Timeout,
                    "port wait deadline has expired",
                ));
            }
            tokio::time::sleep(remaining.min(Duration::from_millis(25))).await;
        }
    }

    unsupported_methods! {
        process_close_stdin(eip::ProcessCloseStdinParams) -> eip::ProcessCloseStdinResult;
        process_inspect(eip::ProcessInspectParams) -> eip::ProcessInspectResult;
        process_kill(eip::ProcessKillParams) -> eip::ProcessKillResult;
        process_read_output(eip::ProcessReadOutputParams) -> eip::ProcessReadOutputResult;
        process_release(eip::ProcessReleaseParams) -> eip::ProcessReleaseResult;
        process_signal(eip::ProcessSignalParams) -> eip::ProcessSignalResult;
        process_start(eip::ProcessStartParams) -> eip::ProcessStartResult;
        process_wait(eip::ProcessWaitParams) -> eip::ProcessWaitResult;
        process_write_stdin(eip::ProcessWriteStdinParams) -> eip::ProcessWriteStdinResult;
        shell_exec(eip::ShellExecParams) -> eip::ShellExecResult;
    }
}

#[allow(clippy::result_large_err)]
fn mutation_failure(operation: OperationLease, method: &str, mut error: EIPError) -> EIPError {
    let (stage, outcome) = match error.data.error_type {
        ErrorType::UnknownOutcome => (ReceiptStage::Unknown, ReceiptOutcome::Unknown),
        ErrorType::Cancelled => (ReceiptStage::Completed, ReceiptOutcome::Cancelled),
        ErrorType::Timeout => (ReceiptStage::Completed, ReceiptOutcome::TimedOut),
        _ => (ReceiptStage::Completed, ReceiptOutcome::Failed),
    };
    if let Ok(receipt) = operation.receipt(method, stage, Some(outcome)) {
        error.data.dispatch_stage = if stage == ReceiptStage::Unknown {
            DispatchStage::Unknown
        } else {
            DispatchStage::Completed
        };
        error.data.operation_id = Some(receipt.operation_id.clone());
        error.data.environment_id = Some(receipt.environment_id.clone());
        error.data.generation = Some(receipt.generation);
        error.data.receipt = Some(receipt.clone());
        operation.finish_failure(receipt, error.clone());
    }
    error
}

#[allow(clippy::result_large_err)]
fn mutation_receipt(
    mut operation: OperationLease,
    method: &str,
) -> Result<(OperationLease, eip::OperationReceipt), EIPError> {
    let receipt = operation
        .receipt(
            method,
            ReceiptStage::Completed,
            Some(ReceiptOutcome::Succeeded),
        )
        .map_err(map_registry_error)?;
    let mut unknown_receipt = receipt.clone();
    unknown_receipt.stage = ReceiptStage::Unknown;
    unknown_receipt.outcome = Some(ReceiptOutcome::Unknown);
    unknown_receipt.observed_at = chrono::Utc::now();
    let mut failure = protocol_error(
        ErrorType::UnknownOutcome,
        "mutation completion evidence became unavailable before terminal recording",
    );
    failure.data.retry_hint = RetryHint::ReconcileFirst;
    failure.data.dispatch_stage = DispatchStage::Unknown;
    failure.data.operation_id = Some(unknown_receipt.operation_id.clone());
    failure.data.environment_id = Some(unknown_receipt.environment_id.clone());
    failure.data.generation = Some(unknown_receipt.generation);
    failure.data.receipt = Some(unknown_receipt.clone());
    operation.preserve_failure_on_drop(unknown_receipt, failure);
    Ok((operation, receipt))
}

#[allow(clippy::result_large_err)]
fn ensure_deadline(context: &eip::EIPCallContext) -> Result<(), EIPError> {
    if context
        .deadline
        .is_some_and(|deadline| deadline <= chrono::Utc::now())
    {
        Err(protocol_error(
            ErrorType::Timeout,
            "operation deadline has expired",
        ))
    } else {
        Ok(())
    }
}

#[allow(clippy::result_large_err)]
fn effective_deadline(
    requested: Option<chrono::DateTime<chrono::Utc>>,
    hard_duration: Duration,
) -> Result<Instant, EIPError> {
    let now = Instant::now();
    let hard = now + hard_duration;
    let Some(requested) = requested else {
        return Ok(hard);
    };
    let remaining = (requested - chrono::Utc::now())
        .to_std()
        .map_err(|_| protocol_error(ErrorType::Timeout, "operation deadline has expired"))?;
    Ok(hard.min(now + remaining))
}

async fn inspect_port(target: &eip::PortTarget, deadline: Instant) -> eip::PortObservation {
    let ip = match target.address {
        eip::PortAddress::Loopback => IpAddr::V4(Ipv4Addr::LOCALHOST),
        eip::PortAddress::Any => IpAddr::V4(Ipv4Addr::UNSPECIFIED),
    };
    let timeout = deadline
        .saturating_duration_since(Instant::now())
        .min(Duration::from_millis(100));
    let status = if timeout.is_zero() {
        eip::PortStatus::Unknown
    } else {
        match tokio::time::timeout(
            timeout,
            tokio::net::TcpStream::connect(SocketAddr::new(ip, target.port as u16)),
        )
        .await
        {
            Ok(Ok(_)) => eip::PortStatus::Listening,
            Ok(Err(error)) if error.kind() == std::io::ErrorKind::ConnectionRefused => {
                eip::PortStatus::NotListening
            }
            Ok(Err(_)) | Err(_) => eip::PortStatus::Unknown,
        }
    };
    eip::PortObservation {
        target: target.clone(),
        status,
        managed_process: None,
        observed_at: chrono::Utc::now(),
    }
}

fn map_resource_error(error: ResourceError) -> EIPError {
    let error = match error {
        ResourceError::PartialRemove {
            removed_entries,
            cause,
        } => {
            let mut mapped = map_resource_error(*cause);
            mapped.data.emitted_items = Some(removed_entries);
            mapped.data.retry_hint = RetryHint::ReconcileFirst;
            return mapped;
        }
        error => error,
    };
    let (error_type, message, retry_hint) = match error {
        ResourceError::Invalid => (
            ErrorType::InvalidParams,
            "invalid resource operation parameters",
            RetryHint::Never,
        ),
        ResourceError::Denied => (
            ErrorType::Denied,
            "resource access is denied",
            RetryHint::Never,
        ),
        ResourceError::NotFound => (
            ErrorType::NotFoundOrDenied,
            "resource was not found or is not visible",
            RetryHint::Never,
        ),
        ResourceError::Conflict => (
            ErrorType::Conflict,
            "resource identity or revision changed",
            RetryHint::ReconcileFirst,
        ),
        ResourceError::Unsupported => (
            ErrorType::Unsupported,
            "resource operation is unsupported",
            RetryHint::Never,
        ),
        ResourceError::Limit => (
            ErrorType::QuotaExceeded,
            "resource operation exceeded a finite limit",
            RetryHint::Never,
        ),
        ResourceError::OutputLimit => (
            ErrorType::OutputLimitExceeded,
            "resource output exceeded the selected policy",
            RetryHint::Never,
        ),
        ResourceError::InvalidHandle => (
            ErrorType::InvalidHandle,
            "resource cursor is invalid or expired",
            RetryHint::Never,
        ),
        ResourceError::Busy => (
            ErrorType::Busy,
            "resource cursor capacity is exhausted",
            RetryHint::AfterCapacity,
        ),
        ResourceError::Cancelled => (
            ErrorType::Cancelled,
            "resource operation was cancelled",
            RetryHint::Never,
        ),
        ResourceError::Timeout => (
            ErrorType::Timeout,
            "resource operation exceeded its deadline",
            RetryHint::Never,
        ),
        ResourceError::UnknownOutcome => (
            ErrorType::UnknownOutcome,
            "resource commit completed with uncertain durability evidence",
            RetryHint::ReconcileFirst,
        ),
        ResourceError::Io => (
            ErrorType::ProviderUnavailable,
            "resource provider I/O failed",
            RetryHint::SameRequest,
        ),
        ResourceError::Internal => (
            ErrorType::InternalError,
            "resource operation failed internally",
            RetryHint::Never,
        ),
        ResourceError::PartialRemove { .. } => unreachable!("handled before error mapping"),
    };
    let mut mapped = protocol_error(error_type, message);
    mapped.data.retry_hint = retry_hint;
    mapped
}

fn map_retention_error(error: RetentionError) -> EIPError {
    let gap_bounds = match error {
        RetentionError::Gap {
            available_start,
            available_end,
        } => Some((available_start, available_end)),
        _ => None,
    };
    let (error_type, message, retry_hint) = match error {
        RetentionError::Invalid => (
            ErrorType::InvalidParams,
            "invalid output policy or selector",
            RetryHint::Never,
        ),
        RetentionError::InvalidHandle => (
            ErrorType::InvalidHandle,
            "output cursor is invalid or expired",
            RetryHint::Never,
        ),
        RetentionError::Gap { .. } => (
            ErrorType::RetentionGap,
            "retained output is unavailable at the requested offset",
            RetryHint::Never,
        ),
        RetentionError::Busy => (
            ErrorType::Busy,
            "retained output cursor capacity is exhausted",
            RetryHint::AfterCapacity,
        ),
        RetentionError::OutputLimit => (
            ErrorType::OutputLimitExceeded,
            "output exceeded the selected policy",
            RetryHint::Never,
        ),
        RetentionError::Internal => (
            ErrorType::InternalError,
            "retained output operation failed internally",
            RetryHint::Never,
        ),
    };
    let mut mapped = protocol_error(error_type, message);
    mapped.data.retry_hint = retry_hint;
    if let Some((available_start, available_end)) = gap_bounds {
        mapped.data.available_start = Some(available_start);
        mapped.data.available_end = Some(available_end);
    }
    mapped
}

fn method_capability(method: &str) -> Option<&'static str> {
    eip::METHODS
        .iter()
        .find(|spec| spec.name == method)
        .and_then(|spec| spec.capability)
}

fn map_dispatch_error(error: DispatchError, method: &str) -> EIPError {
    let mut error = match error {
        DispatchError::MethodNotFound => {
            protocol_error(ErrorType::MethodNotFound, "method not found")
        }
        DispatchError::InvalidParams(_) => {
            protocol_error(ErrorType::InvalidParams, "invalid method params")
        }
        DispatchError::InvalidResult(_) | DispatchError::Encode(_) => {
            protocol_error(ErrorType::InternalError, "method result encoding failed")
        }
        DispatchError::Method(error) => error,
    };
    if error.data.capability.is_none() {
        error.data.capability = method_capability(method).map(ToOwned::to_owned);
    }
    error
}

fn map_registry_error(error: RegistryError) -> EIPError {
    let (error_type, message, retry_hint) = match error {
        RegistryError::Collision => (
            ErrorType::Conflict,
            "operation_id is already active or retained",
            RetryHint::Never,
        ),
        RegistryError::DeadlineExpired => (
            ErrorType::Timeout,
            "operation deadline has expired",
            RetryHint::Never,
        ),
        RegistryError::IdempotencyDisallowed => (
            ErrorType::InvalidParams,
            "idempotency_key is not allowed for this method",
            RetryHint::Never,
        ),
        RegistryError::IdempotencyConflict => (
            ErrorType::IdempotencyConflict,
            "idempotency key was reused for a different request",
            RetryHint::Never,
        ),
        RegistryError::InProgress => (
            ErrorType::OperationInProgress,
            "matching operation is still in progress",
            RetryHint::ReconcileFirst,
        ),
        RegistryError::TerminalFailure => (
            ErrorType::Conflict,
            "matching operation completed without a replayable success result",
            RetryHint::ReconcileFirst,
        ),
        RegistryError::Capacity => (
            ErrorType::Busy,
            "operation record capacity is currently exhausted",
            RetryHint::AfterCapacity,
        ),
        RegistryError::Encoding => (
            ErrorType::InternalError,
            "operation evidence encoding failed",
            RetryHint::Never,
        ),
    };
    let mut mapped = protocol_error(error_type, message);
    mapped.data.retry_hint = retry_hint;
    mapped
}

fn map_transfer_error(error: TransferError) -> EIPError {
    let (error_type, message, retry_hint) = match error {
        TransferError::InvalidHandle | TransferError::WrongKind | TransferError::WrongState => (
            ErrorType::InvalidHandle,
            "file transfer handle or state is invalid",
            RetryHint::Never,
        ),
        TransferError::Conflict => (
            ErrorType::Conflict,
            "file transfer precondition changed",
            RetryHint::AfterRefresh,
        ),
        TransferError::IntegrityMismatch => (
            ErrorType::IntegrityMismatch,
            "file transfer count or digest did not match",
            RetryHint::Never,
        ),
        TransferError::Expired => (
            ErrorType::Timeout,
            "file transfer expired",
            RetryHint::Never,
        ),
        TransferError::Busy => (
            ErrorType::Busy,
            "file transfer capacity is exhausted",
            RetryHint::AfterCapacity,
        ),
        TransferError::Quota | TransferError::Limit => (
            ErrorType::QuotaExceeded,
            "file transfer quota is exhausted",
            RetryHint::AfterCapacity,
        ),
        TransferError::Unsupported => (
            ErrorType::Unsupported,
            "file transfer operation is unsupported",
            RetryHint::Never,
        ),
        TransferError::Denied => (
            ErrorType::Denied,
            "file transfer is denied",
            RetryHint::Never,
        ),
        TransferError::NotFound => (
            ErrorType::NotFoundOrDenied,
            "file resource was not found or is denied",
            RetryHint::Never,
        ),
        TransferError::Source => (
            ErrorType::ProviderUnavailable,
            "native file source became unavailable",
            RetryHint::AfterRefresh,
        ),
        TransferError::Protocol => (
            ErrorType::InvalidParams,
            "file transfer request is invalid",
            RetryHint::Never,
        ),
        TransferError::Cancelled => (
            ErrorType::Cancelled,
            "file transfer operation was cancelled",
            RetryHint::Never,
        ),
        TransferError::Timeout => (
            ErrorType::Timeout,
            "file transfer operation exceeded its deadline",
            RetryHint::Never,
        ),
        TransferError::UnknownOutcome => (
            ErrorType::UnknownOutcome,
            "file commit completed with uncertain durability evidence",
            RetryHint::ReconcileFirst,
        ),
        TransferError::SessionClosed => (
            ErrorType::NotInitialized,
            "file transfer session is closed",
            RetryHint::Never,
        ),
        TransferError::Internal => (
            ErrorType::InternalError,
            "file transfer internal failure",
            RetryHint::Never,
        ),
    };
    let mut mapped = protocol_error(error_type, message);
    mapped.data.retry_hint = retry_hint;
    mapped
}

fn protocol_error(error_type: ErrorType, message: impl Into<String>) -> EIPError {
    EIPError {
        code: error_type.code(),
        message: message.into(),
        data: EIPErrorData {
            error_type,
            retry_hint: RetryHint::Never,
            dispatch_stage: DispatchStage::PreDispatch,
            operation_id: None,
            environment_id: None,
            generation: None,
            capability: None,
            field: None,
            handle_kind: None,
            produced_bytes: None,
            captured_bytes: None,
            dropped_bytes: None,
            emitted_items: None,
            dropped_items: None,
            process_status: None,
            receipt: None,
            safe_detail: None,
            available_start: None,
            available_end: None,
        },
    }
}

fn error_with_capability(
    error_type: ErrorType,
    message: impl Into<String>,
    capability: &str,
) -> EIPError {
    let mut error = protocol_error(error_type, message);
    error.data.capability = Some(capability.to_owned());
    error
}

fn error_type_message(error_type: ErrorType) -> &'static str {
    match error_type {
        ErrorType::ParseError => "invalid JSON payload",
        ErrorType::InvalidRequest => "invalid JSON-RPC request",
        _ => "protocol error",
    }
}

fn valid_request_id(id: &JsonRpcId) -> bool {
    match id {
        JsonRpcId::String(value) => !value.is_empty() && value.len() <= MAX_STRING_REQUEST_ID_BYTES,
        JsonRpcId::Integer(_) => true,
    }
}

fn recover_request_id(payload: &str) -> Option<JsonRpcId> {
    let value = serde_json::from_str::<serde_json::Value>(payload).ok()?;
    let id = value.as_object()?.get("id")?.clone();
    let id = serde_json::from_value::<JsonRpcId>(id).ok()?;
    valid_request_id(&id).then_some(id)
}

fn fresh_generation() -> Result<u64, DaemonInitError> {
    loop {
        let mut bytes = [0_u8; 8];
        getrandom::fill(&mut bytes)
            .map_err(|_| DaemonInitError::new("secure random generation failed"))?;
        let generation = u64::from_ne_bytes(bytes);
        if generation != 0 {
            return Ok(generation);
        }
    }
}

#[derive(Debug)]
pub(crate) struct DaemonInitError {
    message: String,
}

impl DaemonInitError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for DaemonInitError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl Error for DaemonInitError {}

#[cfg(test)]
mod tests {
    use std::{
        fs,
        path::PathBuf,
        sync::{
            Arc, Condvar, Mutex, PoisonError,
            atomic::{AtomicBool, Ordering},
        },
        time::Duration,
    };

    use serde_json::{Value, json};

    use crate::{
        config::{Config, TrustedMountConfig},
        eip::{
            EIPCallContext, EIPPath, EipHandler, FileWriteMode, FileWriteTextParams,
            FileWriterOpenParams,
        },
        operation::{BeginOutcome, random_selector},
    };

    use super::{Daemon, fresh_generation, mutation_receipt};

    struct TempTree(PathBuf);

    impl TempTree {
        fn new() -> Self {
            let path = std::env::temp_dir().join(
                random_selector("agent-envd-daemon-test").expect("random temporary directory"),
            );
            fs::create_dir(&path).expect("creates temporary directory");
            Self(path)
        }

        fn child(&self, name: &str) -> PathBuf {
            self.0.join(name)
        }
    }

    impl Drop for TempTree {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn request(id: Value, method: &str, params: Value) -> String {
        json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}).to_string()
    }

    fn initialize_params(required_capabilities: Value) -> Value {
        json!({
            "supported_protocol_versions": ["1.0"],
            "client": {"name": "test", "version": "1"},
            "expected_environment_id": "env-test",
            "required_capabilities": required_capabilities
        })
    }

    async fn initialize(daemon: &Daemon) -> Value {
        let bytes = daemon
            .handle_payload(&request(
                json!(1),
                "initialize",
                initialize_params(json!([])),
            ))
            .await;
        serde_json::from_slice(&bytes).expect("response is JSON")
    }

    #[test]
    fn generations_are_nonzero_and_fresh() {
        let first = fresh_generation().expect("secure randomness is available");
        let second = fresh_generation().expect("secure randomness is available");
        assert_ne!(first, 0);
        assert_ne!(second, 0);
        assert_ne!(first, second);
    }

    #[tokio::test]
    async fn initialize_describe_and_close_follow_session_lifecycle() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 7).expect("daemon builds");

        let initialized = initialize(&daemon).await;
        assert_eq!(initialized["result"]["protocol_version"], "1.0");
        assert_eq!(initialized["result"]["descriptor"]["generation"], 7);
        assert_eq!(
            initialized["result"]["descriptor"]["capabilities"],
            json!([
                "environment.describe",
                "operation.cancel",
                "port.observe",
                "receipt.read",
                "session.close"
            ])
        );

        let described: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(2),
                    "environment.describe",
                    json!({"context": {"operation_id": "describe-1"}}),
                ))
                .await,
        )
        .expect("response is JSON");
        assert_eq!(described["result"]["descriptor"]["generation"], 7);

        let closed: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(3),
                    "session.close",
                    json!({"context": {"operation_id": "close-1"}}),
                ))
                .await,
        )
        .expect("response is JSON");
        assert_eq!(closed["result"]["closed"], true);
        assert!(*daemon.subscribe_closed().borrow());
    }

    #[tokio::test]
    async fn session_teardown_serializes_transfer_and_mutation_admission() {
        let tree = TempTree::new();
        let native = tree.child("native");
        let staging = tree.child("staging");
        fs::create_dir(&native).expect("native root");
        fs::create_dir(&staging).expect("staging root");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&staging, fs::Permissions::from_mode(0o700))
                .expect("private staging root");
        }
        let mut config = Config::for_test("env-test");
        config.mounts.push(TrustedMountConfig {
            mount_id: "workspace".to_owned(),
            native_root: native.clone(),
            staging_root: Some(staging.clone()),
            writable: true,
            exclusive_mutation_control: true,
            allow_command_execution: false,
            max_file_bytes: 1024 * 1024,
            allowed_operations: Vec::new(),
        });
        let daemon = Arc::new(Daemon::with_generation(&config, 71).expect("daemon builds"));
        let _ = initialize(&daemon).await;
        EipHandler::file_open_writer(
            daemon.as_ref(),
            FileWriterOpenParams {
                context: EIPCallContext {
                    operation_id: "open-before-close".to_owned(),
                    deadline: None,
                    idempotency_key: None,
                },
                path: EIPPath {
                    mount_id: "workspace".to_owned(),
                    path: "/candidate.bin".to_owned(),
                },
                mode: FileWriteMode::Create,
                expected_revision: None,
                executable: None,
                transfer_deadline: None,
            },
        )
        .await
        .expect("opens session-owned writer");
        let in_flight = daemon
            .session
            .admit_work()
            .expect("admits work before teardown");
        let closing_daemon = Arc::clone(&daemon);
        let closing = tokio::spawn(async move {
            closing_daemon
                .transport_closed(Duration::from_secs(1))
                .await
        });
        tokio::task::yield_now().await;
        assert!(!closing.is_finished());
        drop(in_flight);
        assert!(closing.await.expect("close waits for admitted work"));
        assert!(!daemon.has_active_file_transfers());
        assert_eq!(
            fs::read_dir(&staging).expect("staging directory").count(),
            0
        );

        let second = Arc::new(Daemon::with_generation(&config, 72).expect("daemon builds"));
        let _ = initialize(&second).await;
        assert!(second.transport_closed(Duration::from_secs(1)).await);
        let rejected = EipHandler::file_write_text(
            second.as_ref(),
            FileWriteTextParams {
                context: EIPCallContext {
                    operation_id: "write-after-close".to_owned(),
                    deadline: None,
                    idempotency_key: Some("write-after-close-key".to_owned()),
                },
                path: EIPPath {
                    mount_id: "workspace".to_owned(),
                    path: "/too-late.txt".to_owned(),
                },
                mode: FileWriteMode::Create,
                text: "too late".to_owned(),
                expected_revision: None,
                executable: None,
            },
        )
        .await
        .expect_err("post-close mutation is rejected before operation admission");
        assert_eq!(
            rejected.data.error_type,
            crate::eip::ErrorType::NotInitialized
        );
        assert!(!native.join("too-late.txt").exists());
    }

    #[tokio::test]
    async fn transport_teardown_bounds_stalled_admission_handoffs() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 73).expect("daemon builds");
        let _ = initialize(&daemon).await;
        let stalled = daemon
            .session
            .admit_work()
            .expect("admits a handoff before close");
        let closed = tokio::time::timeout(
            Duration::from_secs(1),
            daemon.transport_closed(Duration::from_millis(20)),
        )
        .await
        .expect("transport teardown stays bounded");
        assert!(!closed);
        drop(stalled);
    }

    #[tokio::test]
    async fn first_non_initialize_request_fails_and_closes_session() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 8).expect("daemon builds");

        let response: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(1),
                    "environment.describe",
                    json!({"context": {"operation_id": "describe-1"}}),
                ))
                .await,
        )
        .expect("response is JSON");

        assert_eq!(response["error"]["code"], -32001);
        assert!(*daemon.subscribe_closed().borrow());
    }

    #[tokio::test]
    async fn completed_operation_ids_remain_reserved() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 9).expect("daemon builds");
        let _ = initialize(&daemon).await;
        let describe = request(
            json!(2),
            "environment.describe",
            json!({"context": {"operation_id": "describe-once"}}),
        );

        let first: Value = serde_json::from_slice(&daemon.handle_payload(&describe).await)
            .expect("response is JSON");
        assert_eq!(first["result"]["descriptor"]["generation"], 9);
        let repeated: Value = serde_json::from_slice(&daemon.handle_payload(&describe).await)
            .expect("response is JSON");
        assert_eq!(repeated["error"]["code"], -32060);
    }

    #[tokio::test]
    async fn operation_registry_memory_is_bounded_by_id_and_record_limits() {
        let mut config = Config::for_test("env-test");
        config.limits.max_concurrent_operations = 1;
        config.limits.max_operation_records = 2;
        let daemon = Daemon::with_generation(&config, 10).expect("daemon builds");
        let _ = initialize(&daemon).await;

        for (request_id, suffix) in [(2, 'a'), (3, 'b')] {
            let operation_id = format!("{}{suffix}", "🧪".repeat(127));
            let response: Value = serde_json::from_slice(
                &daemon
                    .handle_payload(&request(
                        json!(request_id),
                        "environment.describe",
                        json!({"context": {"operation_id": operation_id}}),
                    ))
                    .await,
            )
            .expect("response is JSON");
            assert_eq!(response["result"]["descriptor"]["generation"], 10);
        }

        let too_long: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(4),
                    "environment.describe",
                    json!({"context": {"operation_id": "🧪".repeat(129)}}),
                ))
                .await,
        )
        .expect("response is JSON");
        assert_eq!(too_long["error"]["code"], -32602);

        let (active, records, identifier_bytes) = daemon.operations.record_stats();
        assert_eq!(active, 0);
        assert_eq!(records, 2);
        assert!(identifier_bytes <= 2 * 128 * 4);
    }

    #[tokio::test]
    async fn terminal_operation_ids_reclaim_by_capacity_and_ttl() {
        let mut capacity_config = Config::for_test("env-test");
        capacity_config.limits.max_concurrent_operations = 1;
        capacity_config.limits.max_operation_records = 1;
        let capacity_daemon = Daemon::with_generation(&capacity_config, 10).expect("daemon builds");
        let _ = initialize(&capacity_daemon).await;
        for (request_id, operation_id) in [(2, "first"), (3, "second"), (4, "first")] {
            let response: Value = serde_json::from_slice(
                &capacity_daemon
                    .handle_payload(&request(
                        json!(request_id),
                        "environment.describe",
                        json!({"context": {"operation_id": operation_id}}),
                    ))
                    .await,
            )
            .expect("response is JSON");
            assert_eq!(response["result"]["descriptor"]["generation"], 10);
        }

        let mut ttl_config = Config::for_test("env-test");
        ttl_config.limits.operation_record_ttl_ms = 1;
        let ttl_daemon = Daemon::with_generation(&ttl_config, 11).expect("daemon builds");
        let _ = initialize(&ttl_daemon).await;
        let describe = request(
            json!(2),
            "environment.describe",
            json!({"context": {"operation_id": "expires"}}),
        );
        let _ = ttl_daemon.handle_payload(&describe).await;
        tokio::time::sleep(Duration::from_millis(5)).await;
        let reused: Value = serde_json::from_slice(&ttl_daemon.handle_payload(&describe).await)
            .expect("response is JSON");
        assert_eq!(reused["result"]["descriptor"]["generation"], 11);
    }

    #[tokio::test]
    async fn known_unadvertised_method_is_unsupported() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 9).expect("daemon builds");
        let _ = initialize(&daemon).await;

        let response: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(2),
                    "file.stat",
                    json!({"context": {"operation_id": "stat-1"}, "path": {"mount_id": "workspace", "path": "/"}}),
                ))
                .await,
        )
        .expect("response is JSON");

        assert_eq!(response["error"]["code"], -32012);
        assert_eq!(response["error"]["data"]["capability"], "file.read");
    }

    #[tokio::test]
    async fn operation_namespace_and_session_resource_replay_are_unified() {
        let tree = TempTree::new();
        let native = tree.child("native");
        let staging = tree.child("staging");
        fs::create_dir(&native).expect("native root");
        fs::create_dir(&staging).expect("staging root");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&staging, fs::Permissions::from_mode(0o700))
                .expect("private staging root");
        }
        fs::write(native.join("replay.txt"), "replay").expect("replay source");
        let mut config = Config::for_test("env-test");
        config.mounts.push(TrustedMountConfig {
            mount_id: "workspace".to_owned(),
            native_root: native.clone(),
            staging_root: Some(staging),
            writable: true,
            exclusive_mutation_control: true,
            allow_command_execution: false,
            max_file_bytes: 1024 * 1024,
            allowed_operations: Vec::new(),
        });
        let daemon = Daemon::with_generation(&config, 12).expect("daemon builds");
        let _ = initialize(&daemon).await;

        let described: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(10),
                    "environment.describe",
                    json!({"context": {"operation_id": "cross-method"}}),
                ))
                .await,
        )
        .expect("describe response");
        assert_eq!(described["result"]["descriptor"]["generation"], 12);
        let collided: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(11),
                    "file.stat",
                    json!({
                        "context": {"operation_id": "cross-method"},
                        "path": {"mount_id": "workspace", "path": "/"}
                    }),
                ))
                .await,
        )
        .expect("collision response");
        assert_eq!(collided["error"]["code"], -32060);

        let open_params = |operation_id: &str| {
            json!({
                "context": {"operation_id": operation_id, "idempotency_key": "reader-key"},
                "path": {"mount_id": "workspace", "path": "/replay.txt"}
            })
        };
        let opened: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(12),
                    "file.open_reader",
                    open_params("reader-open-1"),
                ))
                .await,
        )
        .expect("reader open response");
        let reader = opened["result"]["reader"].clone();
        assert_eq!(reader, "reader-1");
        let replayed: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(13),
                    "file.open_reader",
                    open_params("reader-open-2"),
                ))
                .await,
        )
        .expect("reader replay response");
        assert_eq!(replayed["result"]["reader"], reader);
        let closed: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(14),
                    "file.close_reader",
                    json!({
                        "context": {"operation_id": "reader-close"},
                        "reader": reader,
                        "accept_complete": false
                    }),
                ))
                .await,
        )
        .expect("reader close response");
        assert_eq!(closed["result"]["completion"]["complete"], false);
        let reopened: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(15),
                    "file.open_reader",
                    open_params("reader-open-3"),
                ))
                .await,
        )
        .expect("reader reopen response");
        assert_eq!(reopened["result"]["reader"], "reader-2");
    }

    #[tokio::test]
    async fn resource_and_transfer_candidates_share_one_staging_quota() {
        let tree = TempTree::new();
        let native = tree.child("native");
        let staging = tree.child("staging");
        fs::create_dir(&native).expect("native root");
        fs::create_dir(&staging).expect("staging root");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&staging, fs::Permissions::from_mode(0o700))
                .expect("private staging root");
        }
        let mut config = Config::for_test("env-test");
        config.limits.max_staged_file_objects = 1;
        config.limits.max_staged_file_bytes = 1024;
        config.mounts.push(TrustedMountConfig {
            mount_id: "workspace".to_owned(),
            native_root: native.clone(),
            staging_root: Some(staging),
            writable: true,
            exclusive_mutation_control: true,
            allow_command_execution: false,
            max_file_bytes: 1024,
            allowed_operations: Vec::new(),
        });
        let daemon = Daemon::with_generation(&config, 73).expect("daemon builds");
        let _ = initialize(&daemon).await;

        let opened: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(2),
                    "file.open_writer",
                    json!({
                        "context": {"operation_id": "quota-writer-open"},
                        "path": {"mount_id": "workspace", "path": "/writer.bin"},
                        "mode": "create"
                    }),
                ))
                .await,
        )
        .expect("writer response");
        let writer = opened["result"]["writer"].clone();
        assert_eq!(writer, "writer-1");

        let blocked: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(3),
                    "file.write_text",
                    json!({
                        "context": {"operation_id": "quota-write-blocked", "idempotency_key": "quota-blocked-key"},
                        "path": {"mount_id": "workspace", "path": "/inline.txt"},
                        "mode": "create",
                        "text": "blocked"
                    }),
                ))
                .await,
        )
        .expect("blocked write response");
        assert_eq!(blocked["error"]["data"]["error_type"], "quota_exceeded");
        assert!(!native.join("inline.txt").exists());

        let aborted: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(4),
                    "file.abort_writer",
                    json!({
                        "context": {"operation_id": "quota-writer-abort"},
                        "writer": writer
                    }),
                ))
                .await,
        )
        .expect("abort response");
        assert_eq!(aborted["result"]["status"], "aborted");

        let written: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(5),
                    "file.write_text",
                    json!({
                        "context": {"operation_id": "quota-write-after-abort", "idempotency_key": "quota-after-key"},
                        "path": {"mount_id": "workspace", "path": "/inline.txt"},
                        "mode": "create",
                        "text": "released"
                    }),
                ))
                .await,
        )
        .expect("write response");
        assert_eq!(written["result"]["bytes_written"], 8);
        assert_eq!(
            fs::read_to_string(native.join("inline.txt")).expect("inline target"),
            "released"
        );
    }

    #[tokio::test]
    async fn configured_resource_handlers_return_receipts_and_reconcile() {
        let tree = TempTree::new();
        let native = tree.child("native");
        let staging = tree.child("staging");
        fs::create_dir(&native).expect("native root");
        fs::create_dir(&staging).expect("staging root");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&staging, fs::Permissions::from_mode(0o700))
                .expect("private staging root");
        }
        let mut config = Config::for_test("env-test");
        config.mounts.push(TrustedMountConfig {
            mount_id: "workspace".to_owned(),
            native_root: native.clone(),
            staging_root: Some(staging),
            writable: true,
            exclusive_mutation_control: true,
            allow_command_execution: false,
            max_file_bytes: 1024 * 1024,
            allowed_operations: Vec::new(),
        });
        let daemon = Daemon::with_generation(&config, 12).expect("daemon builds");
        let initialized = initialize(&daemon).await;
        let capabilities = initialized["result"]["descriptor"]["capabilities"]
            .as_array()
            .expect("capabilities array");
        for capability in ["file.read", "file.write", "file.find", "file.search"] {
            assert!(capabilities.iter().any(|value| value == capability));
        }

        let write: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(2),
                    "file.write_text",
                    json!({
                        "context": {"operation_id": "write-e2e", "idempotency_key": "write-key"},
                        "path": {"mount_id": "workspace", "path": "/block2.txt"},
                        "mode": "create",
                        "text": "block2\n"
                    }),
                ))
                .await,
        )
        .expect("write response");
        assert_eq!(write["result"]["bytes_written"], 7);
        assert_eq!(
            fs::read_to_string(native.join("block2.txt")).expect("file"),
            "block2\n"
        );
        let receipt_ref = write["result"]["receipt"]["receipt_ref"].clone();

        let receipt: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(3),
                    "receipt.get",
                    json!({
                        "context": {"operation_id": "receipt-e2e"},
                        "receipt_ref": receipt_ref
                    }),
                ))
                .await,
        )
        .expect("receipt response");
        assert_eq!(receipt["result"]["receipt"]["operation_id"], "write-e2e");
        assert_eq!(receipt["result"]["receipt"]["outcome"], "succeeded");

        let read: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(4),
                    "file.read_text",
                    json!({
                        "context": {"operation_id": "read-e2e"},
                        "path": {"mount_id": "workspace", "path": "/block2.txt"},
                        "max_bytes": 64
                    }),
                ))
                .await,
        )
        .expect("read response");
        assert_eq!(read["result"]["text"], "block2\n");
        assert_eq!(read["result"]["content_complete"], true);

        let failed: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(41),
                    "file.write_text",
                    json!({
                        "context": {"operation_id": "failed-write", "idempotency_key": "failed-key"},
                        "path": {"mount_id": "workspace", "path": "/missing.txt"},
                        "mode": "replace",
                        "text": "never committed"
                    }),
                ))
                .await,
        )
        .expect("failed mutation response");
        assert_eq!(
            failed["error"]["data"]["receipt"]["operation_id"],
            "failed-write"
        );
        assert_eq!(failed["error"]["data"]["receipt"]["outcome"], "failed");
        let failed_receipt: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(42),
                    "receipt.get",
                    json!({
                        "context": {"operation_id": "failed-receipt"},
                        "operation_id": "failed-write"
                    }),
                ))
                .await,
        )
        .expect("failed receipt response");
        assert_eq!(failed_receipt["result"]["receipt"]["outcome"], "failed");
        let failed_replay: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(43),
                    "file.write_text",
                    json!({
                        "context": {"operation_id": "failed-write-retry", "idempotency_key": "failed-key"},
                        "path": {"mount_id": "workspace", "path": "/missing.txt"},
                        "mode": "replace",
                        "text": "never committed"
                    }),
                ))
                .await,
        )
        .expect("failed replay response");
        assert_eq!(
            failed_replay["error"]["data"]["error_type"],
            failed["error"]["data"]["error_type"]
        );
        assert_eq!(
            failed_replay["error"]["data"]["receipt"],
            failed["error"]["data"]["receipt"]
        );
        assert_eq!(
            failed_replay["error"]["data"]["operation_id"],
            "failed-write"
        );

        let cancelled: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(5),
                    "operation.cancel",
                    json!({
                        "context": {"operation_id": "cancel-e2e"},
                        "target_operation_id": "write-e2e"
                    }),
                ))
                .await,
        )
        .expect("cancel response");
        assert_eq!(cancelled["result"]["status"], "already_terminal");
    }

    #[tokio::test]
    async fn aborted_mutation_owner_preserves_unknown_outcome_replay() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 13).expect("daemon builds");
        let _ = initialize(&daemon).await;
        let params = FileWriteTextParams {
            context: EIPCallContext {
                operation_id: "drain-write".to_owned(),
                deadline: None,
                idempotency_key: Some("drain-write-key".to_owned()),
            },
            path: EIPPath {
                mount_id: "workspace".to_owned(),
                path: "/drain.txt".to_owned(),
            },
            mode: FileWriteMode::Create,
            text: "drain".to_owned(),
            expected_revision: None,
            executable: None,
        };
        let operation = match daemon
            .begin_record("file.write_text", &params.context, &params, true)
            .expect("operation is admitted")
        {
            BeginOutcome::New(operation) => operation,
            BeginOutcome::Replay(_) | BeginOutcome::ReplayFailure(_) => {
                panic!("new operation expected")
            }
        };
        let (operation, receipt) =
            mutation_receipt(operation, "file.write_text").expect("receipt is armed");
        let gate = Arc::new((Mutex::new(false), Condvar::new()));
        let blocking_gate = Arc::clone(&gate);
        let (started_tx, started_rx) = tokio::sync::oneshot::channel();
        let owned =
            daemon
                .owned_operations
                .spawn(params.context.operation_id.clone(), async move {
                    tokio::task::spawn_blocking(move || {
                        let _ = started_tx.send(());
                        let (released, changed) = &*blocking_gate;
                        let mut released = released.lock().unwrap_or_else(PoisonError::into_inner);
                        while !*released {
                            released = changed
                                .wait(released)
                                .unwrap_or_else(PoisonError::into_inner);
                        }
                    })
                    .await
                    .expect("blocking worker exits");
                    operation
                        .finish(&json!({"completed": true}), Some(receipt))
                        .expect("operation finishes");
                    Ok(json!({"completed": true}))
                });
        let response_waiter = tokio::spawn(async move {
            let _ = owned.await;
        });
        started_rx.await.expect("blocking worker starts");
        response_waiter.abort();
        assert!(
            response_waiter
                .await
                .expect_err("response waiter is cancelled")
                .is_cancelled()
        );
        assert_eq!(daemon.operations.record_stats().0, 1);
        assert_eq!(
            daemon.owned_operations.active_ids(),
            vec!["drain-write".to_owned()]
        );
        daemon.owned_operations.request_reconciliation();
        daemon.owned_operations.wait_until_idle().await;
        let (released, changed) = &*gate;
        *released.lock().unwrap_or_else(PoisonError::into_inner) = true;
        changed.notify_all();

        let mut retry = params;
        retry.context.operation_id = "drain-write-retry".to_owned();
        let replay = daemon
            .begin_record("file.write_text", &retry.context, &retry, true)
            .expect("matching idempotency key replays");
        let BeginOutcome::ReplayFailure(error) = replay else {
            panic!("unknown-outcome failure replay expected");
        };
        assert_eq!(error.data.error_type, crate::eip::ErrorType::UnknownOutcome);
        assert_eq!(error.data.operation_id.as_deref(), Some("drain-write"));
        let replayed_receipt = error.data.receipt.expect("unknown receipt is retained");
        assert_eq!(replayed_receipt.operation_id, "drain-write");
        assert_eq!(
            replayed_receipt.outcome,
            Some(crate::eip::ReceiptOutcome::Unknown)
        );
    }

    #[tokio::test]
    async fn mutation_registered_after_drain_is_reconciled_before_dispatch() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 14).expect("daemon builds");
        let _ = initialize(&daemon).await;
        daemon.owned_operations.begin_drain();
        let params = FileWriteTextParams {
            context: EIPCallContext {
                operation_id: "late-write".to_owned(),
                deadline: None,
                idempotency_key: Some("late-write-key".to_owned()),
            },
            path: EIPPath {
                mount_id: "workspace".to_owned(),
                path: "/late.txt".to_owned(),
            },
            mode: FileWriteMode::Create,
            text: "late".to_owned(),
            expected_revision: None,
            executable: None,
        };
        let operation = match daemon
            .begin_record("file.write_text", &params.context, &params, true)
            .expect("operation is admitted")
        {
            BeginOutcome::New(operation) => operation,
            BeginOutcome::Replay(_) | BeginOutcome::ReplayFailure(_) => {
                panic!("new operation expected")
            }
        };
        let (operation, receipt) =
            mutation_receipt(operation, "file.write_text").expect("receipt is armed");
        let dispatched = Arc::new(AtomicBool::new(false));
        let worker_dispatched = Arc::clone(&dispatched);
        let owned =
            daemon
                .owned_operations
                .spawn(params.context.operation_id.clone(), async move {
                    worker_dispatched.store(true, Ordering::SeqCst);
                    operation
                        .finish(&json!({"completed": true}), Some(receipt))
                        .expect("operation finishes");
                    Ok(json!({"completed": true}))
                });
        let error = daemon
            .await_owned_operation(&params.context.operation_id, owned)
            .await
            .expect_err("late registration is reconciled");
        assert_eq!(error.data.error_type, crate::eip::ErrorType::UnknownOutcome);
        assert!(!dispatched.load(Ordering::SeqCst));
        daemon.owned_operations.wait_until_idle().await;
    }

    #[tokio::test]
    async fn failed_required_capability_negotiation_is_terminal() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 10).expect("daemon builds");

        let response: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(1),
                    "initialize",
                    initialize_params(json!(["file.read"])),
                ))
                .await,
        )
        .expect("response is JSON");

        assert_eq!(response["error"]["code"], -32003);
        assert_eq!(response["error"]["data"]["capability"], "file.read");
        assert!(*daemon.subscribe_closed().borrow());
    }
}
