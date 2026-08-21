use std::{
    collections::{BTreeMap, HashSet, VecDeque},
    error::Error,
    fmt,
    sync::{Mutex as StdMutex, PoisonError},
    time::{Duration, Instant},
};

use tokio::sync::{Mutex, watch};

use crate::{
    config::Config,
    eip::{
        self, DispatchError, DispatchStage, EIPError, EIPErrorData, EIPServerInfo, EipHandler,
        EnvironmentDescribeParams, EnvironmentDescribeResult, EnvironmentDescriptor, ErrorType,
        InitializeParams, InitializeResult, IsolationBackend, IsolationCleanupGuarantee,
        IsolationMode, IsolationNetworkPolicy, IsolationPosture, JsonRpcErrorResponse, JsonRpcId,
        JsonRpcRequest, JsonRpcSuccessResponse, RetryHint, SessionCloseParams, SessionCloseResult,
    },
};

const MAX_STRING_REQUEST_ID_BYTES: usize = 128;
const CAPABILITIES: [&str; 2] = ["environment.describe", "session.close"];

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

#[derive(Default)]
struct OperationIds {
    live: HashSet<String>,
    terminal: VecDeque<(String, Instant)>,
}

impl OperationIds {
    fn prune_expired(&mut self, now: Instant, ttl: Duration) {
        while self
            .terminal
            .front()
            .is_some_and(|(_, completed_at)| now.duration_since(*completed_at) >= ttl)
        {
            self.terminal.pop_front();
        }
    }

    fn contains(&self, operation_id: &str) -> bool {
        self.live.contains(operation_id)
            || self
                .terminal
                .iter()
                .any(|(retained_id, _)| retained_id == operation_id)
    }
}

pub(crate) struct Daemon {
    descriptor: EnvironmentDescriptor,
    session: Mutex<SessionState>,
    operation_ids: StdMutex<OperationIds>,
    max_operation_records: usize,
    operation_record_ttl: Duration,
    closed: watch::Sender<bool>,
    max_response_bytes: usize,
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
        let descriptor = EnvironmentDescriptor {
            environment_id: config.environment_id.clone(),
            generation,
            capabilities: CAPABILITIES.iter().map(ToString::to_string).collect(),
            mounts: Vec::new(),
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
            session: Mutex::new(SessionState::Uninitialized),
            operation_ids: StdMutex::new(OperationIds::default()),
            max_operation_records,
            operation_record_ttl,
            closed,
            max_response_bytes,
        })
    }

    pub(crate) fn subscribe_closed(&self) -> watch::Receiver<bool> {
        self.closed.subscribe()
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
                self.close_if_uninitialized().await;
                return self.error_response(
                    request_id,
                    protocol_error(error_type, error_type_message(error_type)),
                );
            }
        };

        if !valid_request_id(&request.id) {
            self.close_if_uninitialized().await;
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
            self.close_if_uninitialized().await;
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
        let mut state = self.session.lock().await;
        match *state {
            SessionState::Uninitialized if method == "initialize" => Ok(()),
            SessionState::Uninitialized => {
                *state = SessionState::Closed;
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

    async fn close_if_uninitialized(&self) {
        let mut state = self.session.lock().await;
        if *state == SessionState::Uninitialized {
            *state = SessionState::Closed;
            self.closed.send_replace(true);
        }
    }

    async fn ensure_initialized(&self) -> Result<(), EIPError> {
        if *self.session.lock().await == SessionState::Initialized {
            Ok(())
        } else {
            Err(protocol_error(
                ErrorType::NotInitialized,
                "session is not initialized",
            ))
        }
    }

    // EIPError is generated and intentionally carries complete bounded evidence.
    #[allow(clippy::result_large_err)]
    fn begin_operation<'a>(
        &'a self,
        operation_id: &str,
        idempotency_key: Option<&str>,
    ) -> Result<OperationGuard<'a>, EIPError> {
        if idempotency_key.is_some() {
            return Err(protocol_error(
                ErrorType::InvalidParams,
                "idempotency_key is not allowed for this method",
            ));
        }
        let now = Instant::now();
        let mut operation_ids = self
            .operation_ids
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        operation_ids.prune_expired(now, self.operation_record_ttl);
        if operation_ids.contains(operation_id) {
            return Err(protocol_error(
                ErrorType::Conflict,
                "operation_id is already active or retained",
            ));
        }
        while operation_ids.live.len() + operation_ids.terminal.len() >= self.max_operation_records
        {
            if operation_ids.terminal.pop_front().is_none() {
                let mut error = protocol_error(
                    ErrorType::Busy,
                    "operation record capacity is currently exhausted",
                );
                error.data.retry_hint = RetryHint::AfterCapacity;
                return Err(error);
            }
        }
        operation_ids.live.insert(operation_id.to_owned());
        Ok(OperationGuard {
            daemon: self,
            operation_id: operation_id.to_owned(),
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

struct OperationGuard<'a> {
    daemon: &'a Daemon,
    operation_id: String,
}

impl Drop for OperationGuard<'_> {
    fn drop(&mut self) {
        let mut operation_ids = self
            .daemon
            .operation_ids
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        operation_ids.live.remove(&self.operation_id);
        operation_ids
            .terminal
            .push_back((self.operation_id.clone(), Instant::now()));
        while operation_ids.live.len() + operation_ids.terminal.len()
            > self.daemon.max_operation_records
        {
            operation_ids.terminal.pop_front();
        }
    }
}

impl EipHandler for Daemon {
    async fn environment_describe(
        &self,
        params: EnvironmentDescribeParams,
    ) -> Result<EnvironmentDescribeResult, EIPError> {
        self.ensure_initialized().await?;
        let _operation = self.begin_operation(
            &params.context.operation_id,
            params.context.idempotency_key.as_deref(),
        )?;
        if params
            .context
            .deadline
            .is_some_and(|deadline| deadline <= chrono::Utc::now())
        {
            return Err(protocol_error(
                ErrorType::Timeout,
                "operation deadline has expired",
            ));
        }
        Ok(EnvironmentDescribeResult {
            descriptor: self.descriptor.clone(),
        })
    }

    async fn initialize(&self, params: InitializeParams) -> Result<InitializeResult, EIPError> {
        let mut state = self.session.lock().await;
        if *state != SessionState::Uninitialized {
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
            *state = SessionState::Closed;
            self.closed.send_replace(true);
            return Err(error);
        }

        *state = SessionState::Initialized;
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
        self.ensure_initialized().await?;
        let _operation = self.begin_operation(
            &params.context.operation_id,
            params.context.idempotency_key.as_deref(),
        )?;
        if params
            .context
            .deadline
            .is_some_and(|deadline| deadline <= chrono::Utc::now())
        {
            return Err(protocol_error(
                ErrorType::Timeout,
                "operation deadline has expired",
            ));
        }
        *self.session.lock().await = SessionState::Closed;
        self.closed.send_replace(true);
        Ok(SessionCloseResult { closed: true })
    }

    unsupported_methods! {
        file_copy(eip::FileCopyParams) -> eip::FileCopyResult;
        file_find(eip::FileFindParams) -> eip::FileFindResult;
        file_list(eip::FileListParams) -> eip::FileListResult;
        file_mkdir(eip::FileMkdirParams) -> eip::FileMkdirResult;
        file_move(eip::FileMoveParams) -> eip::FileMoveResult;
        file_patch(eip::FilePatchParams) -> eip::FilePatchResult;
        file_read(eip::FileReadParams) -> eip::FileReadResult;
        file_remove(eip::FileRemoveParams) -> eip::FileRemoveResult;
        file_search(eip::FileSearchParams) -> eip::FileSearchResult;
        file_stat(eip::FileStatParams) -> eip::FileStatResult;
        file_write(eip::FileWriteParams) -> eip::FileWriteResult;
        operation_cancel(eip::OperationCancelParams) -> eip::OperationCancelResult;
        output_read(eip::OutputReadParams) -> eip::OutputReadResult;
        output_release(eip::OutputReleaseParams) -> eip::OutputReleaseResult;
        port_inspect(eip::PortInspectParams) -> eip::PortInspectResult;
        port_wait(eip::PortWaitParams) -> eip::PortWaitResult;
        process_close_stdin(eip::ProcessCloseStdinParams) -> eip::ProcessCloseStdinResult;
        process_inspect(eip::ProcessInspectParams) -> eip::ProcessInspectResult;
        process_kill(eip::ProcessKillParams) -> eip::ProcessKillResult;
        process_read_output(eip::ProcessReadOutputParams) -> eip::ProcessReadOutputResult;
        process_release(eip::ProcessReleaseParams) -> eip::ProcessReleaseResult;
        process_signal(eip::ProcessSignalParams) -> eip::ProcessSignalResult;
        process_start(eip::ProcessStartParams) -> eip::ProcessStartResult;
        process_wait(eip::ProcessWaitParams) -> eip::ProcessWaitResult;
        process_write_stdin(eip::ProcessWriteStdinParams) -> eip::ProcessWriteStdinResult;
        receipt_get(eip::ReceiptGetParams) -> eip::ReceiptGetResult;
        shell_exec(eip::ShellExecParams) -> eip::ShellExecResult;
    }
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
    use std::{sync::PoisonError, time::Duration};

    use serde_json::{Value, json};

    use crate::config::Config;

    use super::{Daemon, fresh_generation};

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
            json!(["environment.describe", "session.close"])
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

        let operation_ids = daemon
            .operation_ids
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        assert!(operation_ids.live.is_empty());
        assert_eq!(operation_ids.terminal.len(), 2);
        assert!(
            operation_ids
                .terminal
                .iter()
                .map(|(operation_id, _)| operation_id.len())
                .sum::<usize>()
                <= 2 * 128 * 4
        );
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
