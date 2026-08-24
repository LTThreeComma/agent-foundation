use std::{
    collections::{BTreeMap, VecDeque},
    path::{Path, PathBuf},
    process::Stdio,
    sync::{
        Arc, Mutex, PoisonError, Weak,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};

use base64::Engine as _;
use tokio::{
    io::BufReader,
    process::{Child, ChildStdin, Command},
    sync::{Mutex as AsyncMutex, Notify, oneshot},
};

use crate::{
    config::{CommandConfig, Config, reserved_environment_name, valid_environment_name},
    eip::{
        self, CleanupOutcome, CommandNetwork, CommandRequest, CommandSpec, EncodedBytes,
        OutputCapture, OutputCursor, OutputKind, OutputOverflow, OutputPolicy, OutputPreview,
        OutputSegment, ProcessHandle, ProcessInfo, ProcessOutputSnapshot, ProcessPhase,
        ProcessSignal, ProcessStatus, ProcessStream, ProcessStreamRead, ProcessStreamSnapshot,
        ProcessWaitCondition, RequestedProcessSignal, TerminationReason,
    },
    mount::{CommandCwd, MountPathError, MountRegistry},
    operation::ShortIdAllocator,
    retention::{LiveOutput, RetentionError, RetentionStore},
    supervisor::{
        self, ControlSignal, LaunchPlan, OutputStream, StopReason, SupervisorEvent,
        SupervisorRequest,
    },
};

const SUPERVISOR_HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(5);
const PROCESS_CONTROL_TIMEOUT: Duration = Duration::from_secs(5);
const PROCESS_CURSOR_TTL: Duration = Duration::from_secs(300);
const RESPONSE_RESERVE_BYTES: u64 = 4096;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ProcessError {
    Invalid,
    Unsupported,
    Denied,
    NotFound,
    InvalidHandle,
    Busy,
    Conflict,
    OutputLimit,
    Timeout,
    Cancelled,
    PreDispatchTimeout,
    PreDispatchCancelled,
    UnknownOutcome,
    StartFailed,
    CleanupFailed,
    Internal,
}

#[derive(Clone)]
pub(crate) struct ExecutionManager {
    inner: Arc<ExecutionInner>,
}

struct ExecutionInner {
    state: Mutex<ManagerState>,
    retention: RetentionStore,
    command: CommandConfig,
    environment_id: String,
    generation: u64,
    max_active: usize,
    max_records: usize,
    terminal_ttl: Duration,
    max_operation_duration: Duration,
    max_inline_bytes: u64,
    max_output_bytes: u64,
    max_response_bytes: u64,
    ids: ShortIdAllocator,
}

struct ManagerState {
    records: BTreeMap<String, Arc<ProcessRecord>>,
    terminal_order: VecDeque<(Instant, String)>,
    released: BTreeMap<String, Instant>,
    released_order: VecDeque<String>,
    cursors: BTreeMap<String, ProcessCursor>,
    active: usize,
    starts_in_progress: usize,
    draining: bool,
}

struct ProcessCursor {
    handle: String,
    stream: ProcessStream,
    offset: u64,
    expires_at: Instant,
}

struct ProcessRecord {
    handle: ProcessHandle,
    exposed: bool,
    state: Mutex<RecordState>,
    stdout: ProcessOutput,
    stderr: ProcessOutput,
    control: Arc<AsyncMutex<ChildStdin>>,
    stdin_operation: AsyncMutex<()>,
    signal_operation: AsyncMutex<()>,
    changed: Notify,
    manager: Weak<ExecutionInner>,
}

struct RecordState {
    status: ProcessStatus,
    stdin_open: bool,
    stdin_bytes: u64,
    stdin_limit: u64,
    terminal_recorded: bool,
    active_released: bool,
    stdin_ack: u64,
    stdin_result: (u64, bool),
    signal_ack: u64,
    signal_result: bool,
    output_limit_crossed: bool,
}

struct ProcessOutput {
    stream: ProcessStream,
    sink: OutputSink,
}

enum OutputSink {
    Retained {
        output: LiveOutput,
        shared_remaining: Arc<Mutex<u64>>,
    },
    Bounded {
        state: Mutex<BoundedCapture>,
        overflow: OutputOverflow,
        local_limit: u64,
        shared_remaining: Arc<Mutex<u64>>,
        shared_failed: Arc<AtomicBool>,
    },
}

struct BoundedCapture {
    data: Vec<u8>,
    produced: u64,
    dropped: u64,
    complete: bool,
}

pub(crate) struct StartedProcess {
    record: Arc<ProcessRecord>,
}

pub(crate) struct StartedRecordRelease {
    record: Option<Arc<ProcessRecord>>,
    retention: RetentionStore,
}

struct PreparedCommand {
    plan: LaunchPlan,
    policy: OutputPolicy,
    stdin_limit: u64,
}

struct ResolvedCommand {
    executable: PathBuf,
    arguments: Vec<String>,
    roots: Vec<PathBuf>,
    environment: BTreeMap<String, String>,
}

struct StartReservation {
    manager: Arc<ExecutionInner>,
    active: bool,
}

impl ExecutionManager {
    pub(crate) fn new(
        config: &Config,
        generation: u64,
        retention: RetentionStore,
    ) -> Result<Option<Self>, ProcessError> {
        let Some(command) = config.command.clone() else {
            return Ok(None);
        };
        let max_active =
            usize::try_from(config.limits.max_processes).map_err(|_| ProcessError::Internal)?;
        let max_records = usize::try_from(config.limits.max_process_records)
            .map_err(|_| ProcessError::Internal)?;
        if max_active == 0 || max_records < max_active {
            return Err(ProcessError::Internal);
        }
        Ok(Some(Self {
            inner: Arc::new(ExecutionInner {
                state: Mutex::new(ManagerState {
                    records: BTreeMap::new(),
                    terminal_order: VecDeque::new(),
                    released: BTreeMap::new(),
                    released_order: VecDeque::new(),
                    cursors: BTreeMap::new(),
                    active: 0,
                    starts_in_progress: 0,
                    draining: false,
                }),
                retention,
                command,
                environment_id: config.environment_id.clone(),
                generation,
                max_active,
                max_records,
                terminal_ttl: Duration::from_millis(config.limits.terminal_process_record_ttl_ms),
                max_operation_duration: Duration::from_millis(
                    config.limits.max_operation_duration_ms,
                ),
                max_inline_bytes: config.limits.max_inline_output_bytes,
                max_output_bytes: config.limits.max_output_bytes,
                max_response_bytes: config.limits.max_response_bytes,
                ids: ShortIdAllocator::for_generation(generation),
            }),
        }))
    }

    pub(crate) fn shell_profiles(&self) -> Vec<eip::ShellProfileDescriptor> {
        self.inner
            .command
            .shell_profiles
            .iter()
            .map(|profile| eip::ShellProfileDescriptor {
                profile_id: profile.profile_id.clone(),
                display_name: profile.display_name.clone(),
                supports_login_mode: profile.allow_login_mode,
                max_script_bytes: profile.max_script_bytes,
            })
            .collect()
    }

    pub(crate) async fn start<F>(
        &self,
        mounts: &MountRegistry,
        request: &CommandRequest,
        exposed: bool,
        dispatch_guard: F,
    ) -> Result<StartedProcess, ProcessError>
    where
        F: Fn() -> Result<(), ProcessError>,
    {
        let prepared = self.prepare_request(mounts, request)?;
        let mut reservation = self.reserve_start()?;
        self.start_reserved(prepared, exposed, &mut reservation, dispatch_guard)
            .await
    }

    async fn start_reserved<F>(
        &self,
        prepared: PreparedCommand,
        exposed: bool,
        reservation: &mut StartReservation,
        dispatch_guard: F,
    ) -> Result<StartedProcess, ProcessError>
    where
        F: Fn() -> Result<(), ProcessError>,
    {
        let executable = std::env::current_exe().map_err(|_| ProcessError::Internal)?;
        let mut supervisor = Command::new(executable)
            .arg("--internal-supervisor")
            .env_clear()
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .map_err(|_| ProcessError::StartFailed)?;
        let mut supervisor_stdin = supervisor.stdin.take().ok_or(ProcessError::Internal)?;
        let supervisor_stdout = supervisor.stdout.take().ok_or(ProcessError::Internal)?;
        let mut reader = BufReader::new(supervisor_stdout);

        let booted = tokio::time::timeout(
            SUPERVISOR_HANDSHAKE_TIMEOUT,
            supervisor::read_event(&mut reader),
        )
        .await
        .map_err(|_| ProcessError::StartFailed)?
        .map_err(|_| ProcessError::StartFailed)?;
        if !matches!(booted, Some(SupervisorEvent::Booted { version: 1 })) {
            return Err(ProcessError::StartFailed);
        }
        supervisor::write_request(
            &mut supervisor_stdin,
            &SupervisorRequest::Prepare {
                version: 1,
                plan: prepared.plan.clone(),
            },
        )
        .await
        .map_err(|_| ProcessError::StartFailed)?;
        let ready = tokio::time::timeout(
            SUPERVISOR_HANDSHAKE_TIMEOUT,
            supervisor::read_event(&mut reader),
        )
        .await
        .map_err(|_| ProcessError::StartFailed)?
        .map_err(|_| ProcessError::StartFailed)?;
        if !matches!(ready, Some(SupervisorEvent::Prepared)) {
            return Err(ProcessError::StartFailed);
        }

        let handle = ProcessHandle(
            self.inner
                .ids
                .next("process")
                .map_err(|_| ProcessError::Internal)?,
        );
        let shared_output_remaining = Arc::new(Mutex::new(
            if prepared.policy.overflow == OutputOverflow::Retain {
                prepared.policy.max_output_bytes
            } else {
                prepared.policy.max_inline_bytes
            },
        ));
        let shared_output_failed = Arc::new(AtomicBool::new(false));
        let (retained_stdout, retained_stderr) =
            if prepared.policy.overflow == OutputOverflow::Retain {
                match self
                    .inner
                    .retention
                    .create_live_pair(Some(&prepared.policy))
                {
                    Ok((stdout, stderr)) => (Some(stdout), Some(stderr)),
                    Err(RetentionError::Busy) => (None, None),
                    Err(_) => return Err(ProcessError::Internal),
                }
            } else {
                (None, None)
            };
        let stdout = ProcessOutput::new(
            ProcessStream::Stdout,
            &prepared.policy,
            retained_stdout,
            Arc::clone(&shared_output_remaining),
            Arc::clone(&shared_output_failed),
        );
        let stderr = ProcessOutput::new(
            ProcessStream::Stderr,
            &prepared.policy,
            retained_stderr,
            shared_output_remaining,
            shared_output_failed,
        );
        let control = Arc::new(AsyncMutex::new(supervisor_stdin));
        let record = Arc::new(ProcessRecord {
            handle: handle.clone(),
            exposed,
            state: Mutex::new(RecordState {
                status: ProcessStatus {
                    phase: ProcessPhase::Starting,
                    termination_reason: None,
                    exit_code: None,
                    signal: None,
                    started_at: None,
                    ended_at: None,
                    cleanup: CleanupOutcome::Pending,
                },
                stdin_open: false,
                stdin_bytes: 0,
                stdin_limit: prepared.stdin_limit,
                terminal_recorded: false,
                active_released: false,
                stdin_ack: 0,
                stdin_result: (0, false),
                signal_ack: 0,
                signal_result: false,
                output_limit_crossed: false,
            }),
            stdout,
            stderr,
            control: Arc::clone(&control),
            stdin_operation: AsyncMutex::new(()),
            signal_operation: AsyncMutex::new(()),
            changed: Notify::new(),
            manager: Arc::downgrade(&self.inner),
        });

        {
            let mut state = self.state();
            if state.draining || state.records.len() >= self.inner.max_records {
                return Err(ProcessError::Busy);
            }
            state.active = state.active.checked_add(1).ok_or(ProcessError::Internal)?;
            state.records.insert(handle.0.clone(), Arc::clone(&record));
        }
        reservation.commit();
        if let Err(error) = dispatch_guard() {
            self.rollback_pre_dispatch_start(&handle);
            return Err(error);
        }

        let started = StartedProcess {
            record: Arc::clone(&record),
        };
        let (confirmation_tx, confirmation_rx) = oneshot::channel();
        tokio::spawn(run_event_pump(
            supervisor,
            reader,
            Arc::clone(&record),
            confirmation_tx,
        ));

        if supervisor::write_request(&mut *control.lock().await, &SupervisorRequest::Start)
            .await
            .is_err()
        {
            let _ = self.stop_started(&started, StopReason::Shutdown).await;
            return Err(ProcessError::UnknownOutcome);
        }
        let handshake_deadline = Instant::now() + SUPERVISOR_HANDSHAKE_TIMEOUT;
        let mut confirmation_rx = confirmation_rx;
        loop {
            let remaining = handshake_deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(self
                    .finish_interrupted_start(&started, ProcessError::UnknownOutcome)
                    .await);
            }
            let slice = remaining.min(Duration::from_millis(25));
            match tokio::time::timeout(slice, &mut confirmation_rx).await {
                Ok(Ok(Ok(()))) => return Ok(started),
                Ok(Ok(Err(ProcessError::StartFailed))) => {
                    self.release_started(&started);
                    return Err(ProcessError::StartFailed);
                }
                Ok(Ok(Err(error))) => {
                    return Err(self.finish_interrupted_start(&started, error).await);
                }
                Ok(Err(_)) => {
                    return Err(self
                        .finish_interrupted_start(&started, ProcessError::UnknownOutcome)
                        .await);
                }
                Err(_) => {
                    if let Err(error) = dispatch_guard() {
                        return Err(self.finish_interrupted_start(&started, error).await);
                    }
                }
            }
        }
    }

    fn prepare_request(
        &self,
        mounts: &MountRegistry,
        request: &CommandRequest,
    ) -> Result<PreparedCommand, ProcessError> {
        if request.network == CommandNetwork::Deny
            || request.limits.memory_bytes.is_some()
            || request.limits.cpu_time_ms.is_some()
            || request.limits.process_count.is_some()
        {
            return Err(ProcessError::Unsupported);
        }
        let cwd = mounts
            .resolve_command_cwd(&request.cwd)
            .map_err(map_mount_error)?;
        let ResolvedCommand {
            executable,
            arguments,
            roots,
            mut environment,
        } = self.resolve_command(&request.command, &cwd)?;
        validate_arguments(
            &arguments,
            self.inner.command.max_arguments,
            self.inner.command.max_argument_bytes,
        )?;
        apply_request_environment(
            &mut environment,
            request,
            self.inner.command.max_environment_entries,
            self.inner.command.max_environment_bytes,
        )?;
        let path = std::env::join_paths(&roots).map_err(|_| ProcessError::Invalid)?;
        environment.insert("PATH".to_owned(), path.to_string_lossy().into_owned());
        environment.insert(
            "HOME".to_owned(),
            path_string(&self.inner.command.private_home)?,
        );
        let private_temp = path_string(&self.inner.command.private_temp)?;
        for name in ["TMPDIR", "TMP", "TEMP"] {
            environment.insert(name.to_owned(), private_temp.clone());
        }
        validate_final_environment(
            &environment,
            self.inner.command.max_environment_entries,
            self.inner.command.max_environment_bytes,
        )?;

        let policy = effective_policy(
            request.output_policy.as_ref(),
            self.inner.max_inline_bytes,
            self.inner.max_output_bytes,
        )?;
        let wall_time_ms = request
            .limits
            .wall_time_ms
            .unwrap_or(self.inner.max_operation_duration.as_millis() as u64);
        if wall_time_ms == 0 || wall_time_ms > self.inner.max_operation_duration.as_millis() as u64
        {
            return Err(ProcessError::Invalid);
        }
        let stdin_limit = request
            .limits
            .stdin_bytes
            .unwrap_or(self.inner.max_output_bytes)
            .min(self.inner.max_output_bytes);
        if stdin_limit == 0 {
            return Err(ProcessError::Invalid);
        }
        let initial_stdin = request
            .initial_stdin
            .as_ref()
            .map(decode_eip_bytes)
            .transpose()?;
        if initial_stdin.as_ref().map_or(0, Vec::len) as u64 > stdin_limit {
            return Err(ProcessError::Invalid);
        }
        Ok(PreparedCommand {
            plan: LaunchPlan {
                executable,
                arguments,
                cwd: cwd.native_path,
                environment,
                initial_stdin: initial_stdin
                    .map(|bytes| base64::engine::general_purpose::STANDARD.encode(bytes)),
                keep_stdin_open: request.keep_stdin_open,
                wall_time_ms,
            },
            policy,
            stdin_limit,
        })
    }

    fn resolve_command(
        &self,
        command: &CommandSpec,
        cwd: &CommandCwd,
    ) -> Result<ResolvedCommand, ProcessError> {
        match command {
            CommandSpec::Argv(argv) => {
                if argv.executable.is_empty() || argv.executable.contains('\0') {
                    return Err(ProcessError::Invalid);
                }
                let executable = if is_bare_executable(&argv.executable) {
                    resolve_bare_executable(
                        &argv.executable,
                        &self.inner.command.trusted_executable_roots,
                    )?
                } else {
                    cwd.resolve_relative_executable(&argv.executable)
                        .map_err(map_mount_error)?
                };
                Ok(ResolvedCommand {
                    executable,
                    arguments: argv.arguments.clone(),
                    roots: self.inner.command.trusted_executable_roots.clone(),
                    environment: self.inner.command.base_environment.clone(),
                })
            }
            CommandSpec::Shell(shell) => {
                let profile = self
                    .inner
                    .command
                    .shell_profiles
                    .iter()
                    .find(|profile| profile.profile_id == shell.profile_id)
                    .ok_or(ProcessError::Unsupported)?;
                if shell.script.contains('\0')
                    || shell.script.len() as u64 > profile.max_script_bytes
                    || (shell.login && !profile.allow_login_mode)
                {
                    return Err(ProcessError::Invalid);
                }
                let mut arguments = profile.fixed_arguments.clone();
                if shell.login {
                    arguments.insert(0, "-l".to_owned());
                }
                arguments.push(shell.script.clone());
                let mut environment = self.inner.command.base_environment.clone();
                environment.extend(profile.safe_base_environment.clone());
                Ok(ResolvedCommand {
                    executable: profile.native_executable.clone(),
                    arguments,
                    roots: profile.executable_search_roots.clone(),
                    environment,
                })
            }
        }
    }

    pub(crate) fn inspect(&self, handle: &ProcessHandle) -> Result<ProcessInfo, ProcessError> {
        let record = self.record(handle)?;
        Ok(record.info(&self.inner))
    }

    pub(crate) async fn wait(
        &self,
        handle: &ProcessHandle,
        condition: ProcessWaitCondition,
        deadline: Instant,
    ) -> Result<ProcessInfo, ProcessError> {
        let record = self.record(handle)?;
        loop {
            let notified = record.changed.notified();
            let info = record.info(&self.inner);
            if wait_satisfied(&info.status, condition) {
                return Ok(info);
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(ProcessError::Timeout);
            }
            tokio::time::timeout(remaining, notified)
                .await
                .map_err(|_| ProcessError::Timeout)?;
        }
    }

    pub(crate) async fn write_stdin(
        &self,
        handle: &ProcessHandle,
        data: &EncodedBytes,
        close_after_write: bool,
    ) -> Result<(u64, bool), ProcessError> {
        let record = self.record(handle)?;
        let bytes = decode_eip_bytes(data)?;
        let _operation = record.stdin_operation.lock().await;
        let previous_ack = {
            let state = record.record_state();
            if !state.stdin_open {
                return Err(ProcessError::Conflict);
            }
            if state.stdin_bytes.saturating_add(bytes.len() as u64) > state.stdin_limit {
                return Err(ProcessError::OutputLimit);
            }
            state.stdin_ack
        };
        {
            let mut control = record.control.lock().await;
            supervisor::write_request(
                &mut *control,
                &SupervisorRequest::WriteStdin {
                    data: base64::engine::general_purpose::STANDARD.encode(&bytes),
                    close_after_write,
                },
            )
            .await
            .map_err(|_| ProcessError::Internal)?;
        }
        self.await_stdin_ack(&record, previous_ack).await
    }

    pub(crate) async fn close_stdin(&self, handle: &ProcessHandle) -> Result<bool, ProcessError> {
        let record = self.record(handle)?;
        let _operation = record.stdin_operation.lock().await;
        let previous_ack = {
            let state = record.record_state();
            if !state.stdin_open {
                return Ok(false);
            }
            state.stdin_ack
        };
        {
            let mut control = record.control.lock().await;
            supervisor::write_request(&mut *control, &SupervisorRequest::CloseStdin)
                .await
                .map_err(|_| ProcessError::Internal)?;
        }
        self.await_stdin_ack(&record, previous_ack).await?;
        Ok(false)
    }

    pub(crate) async fn signal(
        &self,
        handle: &ProcessHandle,
        signal: RequestedProcessSignal,
    ) -> Result<(bool, ProcessInfo), ProcessError> {
        let record = self.record(handle)?;
        let _operation = record.signal_operation.lock().await;
        let previous_ack = {
            let state = record.record_state();
            if is_terminal_phase(state.status.phase) {
                return Ok((false, record.info(&self.inner)));
            }
            state.signal_ack
        };
        let signal = match signal {
            RequestedProcessSignal::Interrupt => ControlSignal::Interrupt,
            RequestedProcessSignal::Terminate => ControlSignal::Terminate,
        };
        {
            let mut control = record.control.lock().await;
            supervisor::write_request(&mut *control, &SupervisorRequest::Signal { signal })
                .await
                .map_err(|_| ProcessError::Internal)?;
        }
        let accepted = self.await_signal_ack(&record, previous_ack).await?;
        Ok((accepted, record.info(&self.inner)))
    }

    pub(crate) async fn kill(
        &self,
        handle: &ProcessHandle,
        reason: StopReason,
        deadline: Instant,
    ) -> Result<ProcessInfo, ProcessError> {
        let record = self.record(handle)?;
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero()
            || !matches!(
                tokio::time::timeout(remaining, async {
                    if !record.cleanup_terminal() {
                        let mut control = record.control.lock().await;
                        supervisor::write_request(
                            &mut *control,
                            &SupervisorRequest::Kill { reason },
                        )
                        .await
                        .map_err(|_| ProcessError::Internal)?;
                    }
                    Ok::<(), ProcessError>(())
                })
                .await,
                Ok(Ok(()))
            )
        {
            return Err(ProcessError::UnknownOutcome);
        }
        let process = self
            .wait(handle, ProcessWaitCondition::TreeCleaned, deadline)
            .await
            .map_err(|_| ProcessError::UnknownOutcome)?;
        if process.status.cleanup == CleanupOutcome::Failed {
            Err(ProcessError::CleanupFailed)
        } else {
            Ok(process)
        }
    }

    pub(crate) fn release(&self, handle: &ProcessHandle) -> Result<bool, ProcessError> {
        let record = {
            let mut state = self.state();
            self.prune_locked(&mut state);
            if state.released.contains_key(&handle.0) {
                return Ok(true);
            }
            let Some(record) = state.records.get(&handle.0) else {
                return Err(ProcessError::NotFound);
            };
            if !record.fully_cleaned() {
                return Err(ProcessError::Conflict);
            }
            let record = Arc::clone(record);
            state.records.remove(&handle.0);
            state
                .terminal_order
                .retain(|(_, terminal_handle)| terminal_handle != &handle.0);
            let released_cursors = remove_cursors_for_handle(&mut state, &handle.0);
            self.inner
                .retention
                .release_external_cursors(released_cursors);
            if state
                .released
                .insert(handle.0.clone(), Instant::now())
                .is_none()
            {
                state.released_order.push_back(handle.0.clone());
            }
            while state.released_order.len() > self.inner.max_records {
                if let Some(expired) = state.released_order.pop_front() {
                    state.released.remove(&expired);
                }
            }
            record
        };
        record.release_output(&self.inner.retention);
        Ok(true)
    }

    pub(crate) async fn read_output(
        &self,
        handle: &ProcessHandle,
        stdout_cursor: Option<&OutputCursor>,
        stderr_cursor: Option<&OutputCursor>,
        wait_ms: u64,
        policy: Option<&OutputPolicy>,
        deadline: Instant,
    ) -> Result<(ProcessInfo, ProcessStreamRead, ProcessStreamRead), ProcessError> {
        let record = self.record(handle)?;
        let policy = effective_read_policy(
            policy,
            self.inner.max_inline_bytes,
            self.inner.max_output_bytes,
            self.inner.max_response_bytes,
        )?;
        let stdout_start = self.cursor_offset(&record, ProcessStream::Stdout, stdout_cursor)?;
        let stderr_start = self.cursor_offset(&record, ProcessStream::Stderr, stderr_cursor)?;
        let initial_stdout_end = record.stdout.snapshot().available_end;
        let initial_stderr_end = record.stderr.snapshot().available_end;
        if wait_ms > 0 && !record.is_terminal() {
            let wait_until = deadline.min(Instant::now() + Duration::from_millis(wait_ms));
            loop {
                let notified = record.changed.notified();
                let stdout_end = record.stdout.snapshot().available_end;
                let stderr_end = record.stderr.snapshot().available_end;
                if stdout_end > initial_stdout_end
                    || stderr_end > initial_stderr_end
                    || record.is_terminal()
                {
                    break;
                }
                let remaining = wait_until.saturating_duration_since(Instant::now());
                if remaining.is_zero() {
                    break;
                }
                if tokio::time::timeout(remaining, notified).await.is_err() {
                    break;
                }
            }
        }
        let stdout = self.read_stream(&record, ProcessStream::Stdout, stdout_start, &policy)?;
        let stderr = match self.read_stream(&record, ProcessStream::Stderr, stderr_start, &policy) {
            Ok(stderr) => stderr,
            Err(error) => {
                if let Some(cursor) = &stdout.next_cursor {
                    self.release_cursor(cursor);
                }
                return Err(error);
            }
        };
        Ok((record.info(&self.inner), stdout, stderr))
    }

    pub(crate) async fn wait_started(
        &self,
        started: &StartedProcess,
        condition: ProcessWaitCondition,
        deadline: Instant,
    ) -> Result<ProcessInfo, ProcessError> {
        loop {
            let notified = started.record.changed.notified();
            let info = started.record.info(&self.inner);
            if wait_satisfied(&info.status, condition) {
                return Ok(info);
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(ProcessError::Timeout);
            }
            tokio::time::timeout(remaining, notified)
                .await
                .map_err(|_| ProcessError::Timeout)?;
        }
    }

    pub(crate) async fn stop_started(
        &self,
        started: &StartedProcess,
        reason: StopReason,
    ) -> Result<(), ProcessError> {
        if started.record.cleanup_terminal() {
            return Ok(());
        }
        let mut control = started.record.control.lock().await;
        supervisor::write_request(&mut *control, &SupervisorRequest::Kill { reason })
            .await
            .map_err(|_| ProcessError::Internal)
    }

    pub(crate) async fn interrupt_started(
        &self,
        started: &StartedProcess,
        reason: StopReason,
        deadline: Instant,
    ) -> Result<ProcessInfo, ProcessError> {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero()
            || !matches!(
                tokio::time::timeout(remaining, self.stop_started(started, reason)).await,
                Ok(Ok(()))
            )
        {
            return Err(ProcessError::UnknownOutcome);
        }
        match self
            .wait_started(started, ProcessWaitCondition::TreeCleaned, deadline)
            .await
        {
            Ok(process) if process.status.cleanup == CleanupOutcome::Complete => Ok(process),
            Ok(_) | Err(_) => Err(ProcessError::UnknownOutcome),
        }
    }

    async fn finish_interrupted_start(
        &self,
        started: &StartedProcess,
        error: ProcessError,
    ) -> ProcessError {
        let (reason, reported_error) = match error {
            ProcessError::Cancelled | ProcessError::PreDispatchCancelled => {
                (StopReason::Cancelled, ProcessError::Cancelled)
            }
            ProcessError::Timeout | ProcessError::PreDispatchTimeout => {
                (StopReason::Timeout, ProcessError::Timeout)
            }
            _ => (StopReason::Shutdown, error),
        };
        match self
            .interrupt_started(started, reason, Instant::now() + Duration::from_secs(3))
            .await
        {
            Ok(_) => {
                self.release_started(started);
                reported_error
            }
            Err(_) => ProcessError::UnknownOutcome,
        }
    }

    pub(crate) fn release_started(&self, started: &StartedProcess) {
        drop(self.prepare_started_release(started));
    }

    pub(crate) fn prepare_started_release(
        &self,
        started: &StartedProcess,
    ) -> Option<StartedRecordRelease> {
        if !started.record.fully_cleaned() {
            return None;
        }
        let record = {
            let mut state = self.state();
            state
                .terminal_order
                .retain(|(_, handle)| handle != &started.record.handle.0);
            state.records.remove(&started.record.handle.0)
        }?;
        Some(StartedRecordRelease {
            record: Some(record),
            retention: self.inner.retention.clone(),
        })
    }

    pub(crate) async fn drain(&self, budget: Duration) -> bool {
        let deadline = Instant::now() + budget;
        let records = {
            let mut state = self.state();
            state.draining = true;
            state.records.values().cloned().collect::<Vec<_>>()
        };
        for record in &records {
            if !record.cleanup_terminal() {
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero()
                    || tokio::time::timeout(remaining, async {
                        let mut control = record.control.lock().await;
                        supervisor::write_request(
                            &mut *control,
                            &SupervisorRequest::Kill {
                                reason: StopReason::Shutdown,
                            },
                        )
                        .await
                    })
                    .await
                    .is_err()
                {
                    return false;
                }
            }
        }
        for record in records {
            while !record.cleanup_terminal() {
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero()
                    || tokio::time::timeout(remaining, record.changed.notified())
                        .await
                        .is_err()
                {
                    return false;
                }
            }
            if !record.fully_cleaned() {
                return false;
            }
        }
        true
    }

    pub(crate) fn release_cursor(&self, cursor: &OutputCursor) -> bool {
        let selector = format!("cursor:{}", cursor.0);
        let mut state = self.state();
        self.prune_locked(&mut state);
        if state.cursors.remove(&cursor.0).is_some() {
            self.inner.retention.release_external_cursors(1);
            if state
                .released
                .insert(selector.clone(), Instant::now())
                .is_none()
            {
                state.released_order.push_back(selector);
            }
            while state.released_order.len() > self.inner.max_records {
                if let Some(expired) = state.released_order.pop_front() {
                    state.released.remove(&expired);
                }
            }
            true
        } else {
            state.released.contains_key(&selector)
        }
    }

    pub(crate) fn maintenance(&self) {
        let mut state = self.state();
        self.prune_locked(&mut state);
    }

    async fn await_stdin_ack(
        &self,
        record: &Arc<ProcessRecord>,
        previous: u64,
    ) -> Result<(u64, bool), ProcessError> {
        let deadline = Instant::now() + PROCESS_CONTROL_TIMEOUT;
        loop {
            let notified = record.changed.notified();
            {
                let state = record.record_state();
                if state.stdin_ack != previous {
                    return Ok(state.stdin_result);
                }
                if is_terminal_phase(state.status.phase) {
                    return Err(ProcessError::UnknownOutcome);
                }
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            tokio::time::timeout(remaining, notified)
                .await
                .map_err(|_| ProcessError::UnknownOutcome)?;
        }
    }

    async fn await_signal_ack(
        &self,
        record: &Arc<ProcessRecord>,
        previous: u64,
    ) -> Result<bool, ProcessError> {
        let deadline = Instant::now() + PROCESS_CONTROL_TIMEOUT;
        loop {
            let notified = record.changed.notified();
            {
                let state = record.record_state();
                if state.signal_ack != previous {
                    return Ok(state.signal_result);
                }
                if is_terminal_phase(state.status.phase) {
                    return Err(ProcessError::UnknownOutcome);
                }
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            tokio::time::timeout(remaining, notified)
                .await
                .map_err(|_| ProcessError::UnknownOutcome)?;
        }
    }

    fn read_stream(
        &self,
        record: &Arc<ProcessRecord>,
        stream: ProcessStream,
        start: u64,
        policy: &OutputPolicy,
    ) -> Result<ProcessStreamRead, ProcessError> {
        let output = match stream {
            ProcessStream::Stdout => &record.stdout,
            ProcessStream::Stderr => &record.stderr,
        };
        let (data, capture) = output.read(start, policy.max_inline_bytes)?;
        if policy.overflow == OutputOverflow::Fail
            && capture.available_end.saturating_sub(start) > policy.max_inline_bytes
        {
            return Err(ProcessError::OutputLimit);
        }
        let end = start.saturating_add(data.len() as u64);
        let next_cursor = if end < capture.available_end || !capture.producer_complete {
            Some(self.create_cursor(record, stream, end)?)
        } else {
            None
        };
        let capture_cursor = (capture.kind == OutputKind::Retained)
            .then(|| next_cursor.clone())
            .flatten();
        Ok(ProcessStreamRead {
            chunks: if data.is_empty() {
                Vec::new()
            } else {
                vec![OutputSegment {
                    start_offset: start,
                    data: encoded(&data),
                }]
            },
            next_cursor,
            capture: OutputCapture {
                cursor: capture_cursor,
                ..capture
            },
        })
    }

    fn create_cursor(
        &self,
        record: &ProcessRecord,
        stream: ProcessStream,
        offset: u64,
    ) -> Result<OutputCursor, ProcessError> {
        let selector = self
            .inner
            .ids
            .next("process-cursor")
            .map_err(|_| ProcessError::Internal)?;
        let mut state = self.state();
        self.prune_locked(&mut state);
        let maximum = self.inner.max_records.saturating_mul(8).max(8);
        while state.cursors.len() >= maximum {
            let Some(oldest) = state.cursors.keys().next().cloned() else {
                break;
            };
            if state.cursors.remove(&oldest).is_some() {
                self.inner.retention.release_external_cursors(1);
            }
        }
        if !self.inner.retention.reserve_external_cursor() {
            return Err(ProcessError::Busy);
        }
        state.cursors.insert(
            selector.clone(),
            ProcessCursor {
                handle: record.handle.0.clone(),
                stream,
                offset,
                expires_at: Instant::now() + PROCESS_CURSOR_TTL,
            },
        );
        Ok(OutputCursor(selector))
    }

    fn cursor_offset(
        &self,
        record: &ProcessRecord,
        stream: ProcessStream,
        cursor: Option<&OutputCursor>,
    ) -> Result<u64, ProcessError> {
        let Some(cursor) = cursor else {
            return Ok(0);
        };
        let mut state = self.state();
        self.prune_locked(&mut state);
        let cursor = state
            .cursors
            .get(&cursor.0)
            .ok_or(ProcessError::InvalidHandle)?;
        if cursor.handle != record.handle.0 || cursor.stream != stream {
            return Err(ProcessError::InvalidHandle);
        }
        Ok(cursor.offset)
    }

    fn record(&self, handle: &ProcessHandle) -> Result<Arc<ProcessRecord>, ProcessError> {
        let mut state = self.state();
        self.prune_locked(&mut state);
        let record = state
            .records
            .get(&handle.0)
            .filter(|record| record.exposed)
            .cloned()
            .ok_or(ProcessError::NotFound)?;
        Ok(record)
    }

    fn reserve_start(&self) -> Result<StartReservation, ProcessError> {
        let mut state = self.state();
        self.prune_locked(&mut state);
        if state.draining
            || state.active.saturating_add(state.starts_in_progress) >= self.inner.max_active
        {
            return Err(ProcessError::Busy);
        }
        while state.records.len().saturating_add(state.starts_in_progress) >= self.inner.max_records
        {
            if !self.reclaim_oldest_locked(&mut state) {
                return Err(ProcessError::Busy);
            }
        }
        state.starts_in_progress = state
            .starts_in_progress
            .checked_add(1)
            .ok_or(ProcessError::Internal)?;
        Ok(StartReservation {
            manager: Arc::clone(&self.inner),
            active: true,
        })
    }

    fn rollback_pre_dispatch_start(&self, handle: &ProcessHandle) {
        let record = {
            let mut state = self.state();
            state.active = state.active.saturating_sub(1);
            state.records.remove(&handle.0)
        };
        if let Some(record) = record {
            record.release_output(&self.inner.retention);
        }
    }

    fn prune_locked(&self, state: &mut ManagerState) {
        let now = Instant::now();
        let cursor_count = state.cursors.len();
        state.cursors.retain(|_, cursor| cursor.expires_at > now);
        self.inner
            .retention
            .release_external_cursors(cursor_count.saturating_sub(state.cursors.len()));
        while let Some(handle) = state.released_order.front() {
            let expired = state.released.get(handle).is_none_or(|released_at| {
                now.duration_since(*released_at) >= self.inner.terminal_ttl
            });
            if !expired {
                break;
            }
            if let Some(handle) = state.released_order.pop_front() {
                state.released.remove(&handle);
            }
        }
        while let Some((terminal_at, handle)) = state.terminal_order.front().cloned() {
            if now.duration_since(terminal_at) < self.inner.terminal_ttl {
                break;
            }
            state.terminal_order.pop_front();
            if let Some(record) = state.records.get(&handle)
                && record.fully_cleaned()
            {
                let record = state.records.remove(&handle).expect("record exists");
                let released_cursors = remove_cursors_for_handle(state, &handle);
                self.inner
                    .retention
                    .release_external_cursors(released_cursors);
                record.release_output(&self.inner.retention);
            }
        }
    }

    fn reclaim_oldest_locked(&self, state: &mut ManagerState) -> bool {
        while let Some((_, handle)) = state.terminal_order.pop_front() {
            if let Some(record) = state.records.get(&handle)
                && record.fully_cleaned()
            {
                let record = state.records.remove(&handle).expect("record exists");
                let released_cursors = remove_cursors_for_handle(state, &handle);
                self.inner
                    .retention
                    .release_external_cursors(released_cursors);
                record.release_output(&self.inner.retention);
                return true;
            }
        }
        false
    }

    fn state(&self) -> std::sync::MutexGuard<'_, ManagerState> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }
}

fn remove_cursors_for_handle(state: &mut ManagerState, handle: &str) -> usize {
    let before = state.cursors.len();
    state.cursors.retain(|_, cursor| cursor.handle != handle);
    before.saturating_sub(state.cursors.len())
}

impl StartReservation {
    fn commit(&mut self) {
        if self.active {
            let mut state = self
                .manager
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            state.starts_in_progress = state.starts_in_progress.saturating_sub(1);
            self.active = false;
        }
    }
}

impl Drop for StartReservation {
    fn drop(&mut self) {
        self.commit();
    }
}

impl StartedProcess {
    pub(crate) fn info(&self, manager: &ExecutionManager) -> ProcessInfo {
        self.record.info(&manager.inner)
    }
}

impl StartedRecordRelease {
    pub(crate) fn preserve_output(mut self) {
        if let Some(record) = self.record.take() {
            record.detach_output();
        }
    }
}

impl Drop for StartedRecordRelease {
    fn drop(&mut self) {
        if let Some(record) = self.record.take() {
            record.release_output(&self.retention);
        }
    }
}

impl ProcessRecord {
    fn info(&self, manager: &ExecutionInner) -> ProcessInfo {
        let state = self.record_state();
        ProcessInfo {
            handle: self.handle.clone(),
            environment_id: manager.environment_id.clone(),
            generation: manager.generation,
            status: state.status.clone(),
            stdin_open: state.stdin_open,
            output: ProcessOutputSnapshot {
                stdout: self.stdout.stream_snapshot(),
                stderr: self.stderr.stream_snapshot(),
            },
        }
    }

    fn record_state(&self) -> std::sync::MutexGuard<'_, RecordState> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn is_terminal(&self) -> bool {
        is_terminal_phase(self.record_state().status.phase)
    }

    fn cleanup_terminal(&self) -> bool {
        let state = self.record_state();
        is_terminal_phase(state.status.phase) && state.status.cleanup != CleanupOutcome::Pending
    }

    fn fully_cleaned(&self) -> bool {
        let state = self.record_state();
        is_terminal_phase(state.status.phase) && state.status.cleanup == CleanupOutcome::Complete
    }

    fn release_output(&self, retention: &RetentionStore) {
        for output in [&self.stdout, &self.stderr] {
            if let Some(reference) = output.reference() {
                retention.release_reference(&reference);
            }
        }
    }

    fn detach_output(&self) {
        self.stdout.detach_retained();
        self.stderr.detach_retained();
    }
}

impl ProcessOutput {
    fn new(
        stream: ProcessStream,
        policy: &OutputPolicy,
        retained: Option<LiveOutput>,
        shared_remaining: Arc<Mutex<u64>>,
        shared_failed: Arc<AtomicBool>,
    ) -> Self {
        let sink = if let Some(output) = retained {
            OutputSink::Retained {
                output,
                shared_remaining,
            }
        } else {
            OutputSink::Bounded {
                state: Mutex::new(BoundedCapture {
                    data: Vec::new(),
                    produced: 0,
                    dropped: 0,
                    complete: false,
                }),
                overflow: if policy.overflow == OutputOverflow::Retain {
                    OutputOverflow::Truncate
                } else {
                    policy.overflow
                },
                local_limit: policy.max_inline_bytes,
                shared_remaining,
                shared_failed,
            }
        };
        Self { stream, sink }
    }

    fn append(&self, bytes: &[u8]) -> Result<bool, ProcessError> {
        match &self.sink {
            OutputSink::Retained {
                output,
                shared_remaining,
            } => {
                let mut remaining = shared_remaining
                    .lock()
                    .unwrap_or_else(PoisonError::into_inner);
                let retained = output
                    .append_limited(bytes, *remaining)
                    .map_err(|_| ProcessError::Internal)?;
                *remaining = remaining.saturating_sub(retained);
                Ok(false)
            }
            OutputSink::Bounded {
                state,
                overflow,
                local_limit,
                shared_remaining,
                shared_failed,
            } => {
                let mut shared_remaining = shared_remaining
                    .lock()
                    .unwrap_or_else(PoisonError::into_inner);
                let mut state = state.lock().unwrap_or_else(PoisonError::into_inner);
                state.produced = state
                    .produced
                    .checked_add(bytes.len() as u64)
                    .ok_or(ProcessError::Internal)?;
                let local_remaining = local_limit.saturating_sub(state.data.len() as u64);
                let captured = local_remaining
                    .min(*shared_remaining)
                    .min(bytes.len() as u64) as usize;
                state.data.extend_from_slice(&bytes[..captured]);
                *shared_remaining = shared_remaining.saturating_sub(captured as u64);
                let newly_dropped = (bytes.len() - captured) as u64;
                state.dropped = state
                    .dropped
                    .checked_add(newly_dropped)
                    .ok_or(ProcessError::Internal)?;
                let crossed = *overflow == OutputOverflow::Fail
                    && newly_dropped > 0
                    && !shared_failed.swap(true, Ordering::AcqRel);
                Ok(crossed)
            }
        }
    }

    fn complete(&self) {
        match &self.sink {
            OutputSink::Retained { output, .. } => output.complete(),
            OutputSink::Bounded { state, .. } => {
                state
                    .lock()
                    .unwrap_or_else(PoisonError::into_inner)
                    .complete = true;
            }
        }
    }

    fn snapshot(&self) -> OutputCapture {
        match &self.sink {
            OutputSink::Retained { output, .. } => output.snapshot(),
            OutputSink::Bounded { state, .. } => {
                let state = state.lock().unwrap_or_else(PoisonError::into_inner);
                bounded_snapshot(&state)
            }
        }
    }

    fn read(&self, start: u64, maximum: u64) -> Result<(Vec<u8>, OutputCapture), ProcessError> {
        let capture = self.snapshot();
        if start < capture.available_start || start > capture.available_end {
            return Err(ProcessError::InvalidHandle);
        }
        match &self.sink {
            OutputSink::Retained { output, .. } => output
                .read_range(start, maximum)
                .map_err(|_| ProcessError::InvalidHandle),
            OutputSink::Bounded { state, .. } => {
                let state = state.lock().unwrap_or_else(PoisonError::into_inner);
                let end = start.saturating_add(maximum).min(state.data.len() as u64);
                Ok((state.data[start as usize..end as usize].to_vec(), capture))
            }
        }
    }

    fn stream_snapshot(&self) -> ProcessStreamSnapshot {
        ProcessStreamSnapshot {
            stream: self.stream,
            capture: self.snapshot(),
        }
    }

    fn reference(&self) -> Option<eip::OutputReference> {
        match &self.sink {
            OutputSink::Retained { output, .. } => Some(output.reference()),
            OutputSink::Bounded { .. } => None,
        }
    }

    fn detach_retained(&self) {
        if let OutputSink::Retained { output, .. } = &self.sink {
            output.detach();
        }
    }
}

async fn run_event_pump(
    mut supervisor_process: Child,
    mut reader: BufReader<tokio::process::ChildStdout>,
    record: Arc<ProcessRecord>,
    confirmation: oneshot::Sender<Result<(), ProcessError>>,
) {
    let mut confirmation = Some(confirmation);
    let mut saw_cleaned = false;
    loop {
        let event = match supervisor::read_event(&mut reader).await {
            Ok(Some(event)) => event,
            Ok(None) | Err(_) => break,
        };
        match event {
            SupervisorEvent::Output { stream, data } => {
                let bytes = match base64::engine::general_purpose::STANDARD.decode(data) {
                    Ok(bytes) => bytes,
                    Err(_) => break,
                };
                let output = match stream {
                    OutputStream::Stdout => &record.stdout,
                    OutputStream::Stderr => &record.stderr,
                };
                match output.append(&bytes) {
                    Ok(true) => {
                        mark_output_limit(&record);
                        let control = Arc::clone(&record.control);
                        tokio::spawn(async move {
                            let mut control = control.lock().await;
                            let _ = supervisor::write_request(
                                &mut *control,
                                &SupervisorRequest::Kill {
                                    reason: StopReason::OutputLimit,
                                },
                            )
                            .await;
                        });
                    }
                    Ok(false) => {}
                    Err(_) => break,
                }
            }
            SupervisorEvent::StreamClosed { stream } => match stream {
                OutputStream::Stdout => record.stdout.complete(),
                OutputStream::Stderr => record.stderr.complete(),
            },
            SupervisorEvent::StdinResult {
                accepted_bytes,
                open,
            } => {
                let mut state = record.record_state();
                state.stdin_open = open;
                state.stdin_bytes = state.stdin_bytes.saturating_add(accepted_bytes);
                state.stdin_result = (accepted_bytes, open);
                state.stdin_ack = state.stdin_ack.wrapping_add(1);
            }
            SupervisorEvent::SignalResult { accepted } => {
                let mut state = record.record_state();
                state.signal_result = accepted;
                state.signal_ack = state.signal_ack.wrapping_add(1);
            }
            SupervisorEvent::Started {
                stdin_open,
                accepted_stdin_bytes,
                initial_stdin_complete,
            } => {
                {
                    let mut state = record.record_state();
                    if state.status.phase == ProcessPhase::Starting {
                        state.status.phase = ProcessPhase::Running;
                        state.status.started_at = Some(chrono::Utc::now());
                        state.stdin_open = stdin_open;
                        state.stdin_bytes = accepted_stdin_bytes;
                    }
                }
                if let Some(confirmation) = confirmation.take() {
                    let result = if initial_stdin_complete {
                        Ok(())
                    } else {
                        Err(ProcessError::UnknownOutcome)
                    };
                    let _ = confirmation.send(result);
                }
            }
            SupervisorEvent::StartFailed { .. } => {
                {
                    let mut state = record.record_state();
                    state.status.phase = ProcessPhase::Failed;
                    state.status.termination_reason = Some(TerminationReason::BackendLost);
                    state.status.ended_at = Some(chrono::Utc::now());
                    state.status.cleanup = CleanupOutcome::Complete;
                    state.stdin_open = false;
                }
                record.stdout.complete();
                record.stderr.complete();
                release_active_and_mark_terminal(&record);
                saw_cleaned = true;
                record.changed.notify_waiters();
                if let Some(confirmation) = confirmation.take() {
                    let _ = confirmation.send(Err(ProcessError::StartFailed));
                }
                break;
            }
            SupervisorEvent::Terminal {
                exit_code,
                signal,
                stop_reason,
            } => {
                let mut state = record.record_state();
                apply_terminal_status(&mut state, exit_code, signal, stop_reason);
                state.stdin_open = false;
                if !state.terminal_recorded {
                    state.terminal_recorded = true;
                }
            }
            SupervisorEvent::Cleaned {
                complete,
                output_complete,
            } => {
                {
                    let mut state = record.record_state();
                    state.status.cleanup = if complete {
                        CleanupOutcome::Complete
                    } else {
                        CleanupOutcome::Failed
                    };
                    if !output_complete {
                        state.status.phase = ProcessPhase::Failed;
                        state.status.termination_reason = Some(TerminationReason::BackendLost);
                        state.status.exit_code = None;
                        state.status.signal = None;
                    }
                    state.stdin_open = false;
                }
                if output_complete {
                    record.stdout.complete();
                    record.stderr.complete();
                }
                release_active_and_mark_terminal(&record);
                saw_cleaned = true;
                record.changed.notify_waiters();
                break;
            }
            SupervisorEvent::Booted { .. }
            | SupervisorEvent::Prepared
            | SupervisorEvent::ProtocolError { .. } => break,
        }
        record.changed.notify_waiters();
    }
    let mut supervisor_reaped = false;
    if !saw_cleaned {
        {
            let mut state = record.record_state();
            if !is_terminal_phase(state.status.phase) {
                state.status.phase = ProcessPhase::Failed;
                state.status.termination_reason = Some(TerminationReason::BackendLost);
                state.status.ended_at = Some(chrono::Utc::now());
            }
            state.status.cleanup = CleanupOutcome::Failed;
            state.stdin_open = false;
        }
        release_active_and_mark_terminal(&record);
        record.changed.notify_waiters();
        {
            let mut control = record.control.lock().await;
            let _ = supervisor::write_request(
                &mut *control,
                &SupervisorRequest::Kill {
                    reason: StopReason::Shutdown,
                },
            )
            .await;
        }
        supervisor_reaped =
            tokio::time::timeout(PROCESS_CONTROL_TIMEOUT, supervisor_process.wait())
                .await
                .is_ok();
    }
    if let Some(confirmation) = confirmation.take() {
        let _ = confirmation.send(Err(ProcessError::UnknownOutcome));
    }
    if !supervisor_reaped {
        let _ = supervisor_process.start_kill();
        let _ = supervisor_process.wait().await;
    }
}

fn release_active_and_mark_terminal(record: &ProcessRecord) {
    if !record.fully_cleaned() {
        return;
    }
    let Some(manager) = record.manager.upgrade() else {
        return;
    };
    let should_release = {
        let mut state = record.record_state();
        if state.active_released {
            false
        } else {
            state.active_released = true;
            true
        }
    };
    if should_release {
        let mut state = manager.state.lock().unwrap_or_else(PoisonError::into_inner);
        state.active = state.active.saturating_sub(1);
        state
            .terminal_order
            .push_back((Instant::now(), record.handle.0.clone()));
    }
}

fn mark_output_limit(record: &ProcessRecord) {
    let mut state = record.record_state();
    state.output_limit_crossed = true;
    if is_terminal_phase(state.status.phase)
        && state.status.termination_reason != Some(TerminationReason::BackendLost)
    {
        state.status.phase = ProcessPhase::Failed;
        state.status.termination_reason = Some(TerminationReason::OutputLimit);
        state.status.exit_code = None;
        state.status.signal = None;
    }
}

fn apply_terminal_status(
    state: &mut RecordState,
    exit_code: Option<i32>,
    signal: Option<ControlSignal>,
    stop_reason: Option<StopReason>,
) {
    let output_limited = state.output_limit_crossed;
    state.status.ended_at = Some(chrono::Utc::now());
    state.status.exit_code = exit_code;
    state.status.cleanup = CleanupOutcome::Pending;
    if output_limited {
        state.status.phase = ProcessPhase::Failed;
        state.status.termination_reason = Some(TerminationReason::OutputLimit);
        state.status.exit_code = None;
        state.status.signal = None;
        return;
    }
    match stop_reason {
        Some(StopReason::Timeout) => {
            state.status.phase = ProcessPhase::TimedOut;
            state.status.termination_reason = Some(TerminationReason::Timeout);
            state.status.exit_code = None;
        }
        Some(StopReason::Cancelled) => {
            state.status.phase = ProcessPhase::Cancelled;
            state.status.termination_reason = Some(TerminationReason::Cancelled);
            state.status.exit_code = None;
        }
        Some(StopReason::OutputLimit) => {
            state.status.phase = ProcessPhase::Failed;
            state.status.termination_reason = Some(TerminationReason::OutputLimit);
            state.status.exit_code = None;
        }
        Some(StopReason::Kill | StopReason::Shutdown) => {
            state.status.phase = ProcessPhase::Signaled;
            state.status.termination_reason = Some(TerminationReason::Signal);
            state.status.signal = Some(ProcessSignal::Kill);
            state.status.exit_code = None;
        }
        None if signal.is_some() => {
            state.status.phase = ProcessPhase::Signaled;
            state.status.termination_reason = Some(TerminationReason::Signal);
            state.status.signal = signal.map(|signal| match signal {
                ControlSignal::Interrupt => ProcessSignal::Interrupt,
                ControlSignal::Terminate => ProcessSignal::Terminate,
                ControlSignal::Kill => ProcessSignal::Kill,
            });
            state.status.exit_code = None;
        }
        None => {
            state.status.phase = ProcessPhase::Exited;
            state.status.termination_reason = Some(TerminationReason::Exit);
        }
    }
}

fn bounded_snapshot(state: &BoundedCapture) -> OutputCapture {
    let captured = state.data.len() as u64;
    if state.produced == 0 {
        return OutputCapture {
            kind: OutputKind::Empty,
            producer_complete: state.complete,
            content_complete: state.complete,
            produced_bytes: 0,
            captured_bytes: 0,
            dropped_bytes: 0,
            inline: None,
            preview: None,
            reference: None,
            cursor: None,
            available_start: 0,
            available_end: 0,
            expires_at: None,
        };
    }
    if state.dropped == 0 {
        OutputCapture {
            kind: OutputKind::Inline,
            producer_complete: state.complete,
            content_complete: state.complete,
            produced_bytes: state.produced,
            captured_bytes: captured,
            dropped_bytes: 0,
            inline: Some(encoded(&state.data)),
            preview: None,
            reference: None,
            cursor: None,
            available_start: 0,
            available_end: captured,
            expires_at: None,
        }
    } else {
        OutputCapture {
            kind: OutputKind::Truncated,
            producer_complete: state.complete,
            content_complete: false,
            produced_bytes: state.produced,
            captured_bytes: captured,
            dropped_bytes: state.dropped,
            inline: None,
            preview: Some(OutputPreview {
                segments: if state.data.is_empty() {
                    Vec::new()
                } else {
                    vec![OutputSegment {
                        start_offset: 0,
                        data: encoded(&state.data),
                    }]
                },
                represented_bytes: captured,
            }),
            reference: None,
            cursor: None,
            available_start: 0,
            available_end: captured,
            expires_at: None,
        }
    }
}

fn effective_policy(
    requested: Option<&OutputPolicy>,
    hard_inline: u64,
    hard_output: u64,
) -> Result<OutputPolicy, ProcessError> {
    let policy = requested.cloned().unwrap_or(OutputPolicy {
        max_inline_bytes: hard_inline,
        max_output_bytes: hard_output,
        overflow: OutputOverflow::Truncate,
    });
    if policy.max_inline_bytes == 0
        || policy.max_output_bytes == 0
        || policy.max_inline_bytes > policy.max_output_bytes
        || policy.max_inline_bytes > hard_inline
        || policy.max_output_bytes > hard_output
    {
        return Err(ProcessError::Invalid);
    }
    Ok(policy)
}

fn effective_read_policy(
    requested: Option<&OutputPolicy>,
    hard_inline: u64,
    hard_output: u64,
    hard_response: u64,
) -> Result<OutputPolicy, ProcessError> {
    let mut policy = effective_policy(requested, hard_inline, hard_output)?;
    policy.max_inline_bytes = policy
        .max_inline_bytes
        .min(hard_response.saturating_sub(RESPONSE_RESERVE_BYTES) / 2)
        .max(1);
    Ok(policy)
}

fn apply_request_environment(
    environment: &mut BTreeMap<String, String>,
    request: &CommandRequest,
    max_entries: usize,
    max_bytes: usize,
) -> Result<(), ProcessError> {
    if request
        .environment
        .set
        .len()
        .saturating_add(request.environment.unset.len())
        > max_entries
        || request
            .environment
            .set
            .iter()
            .map(|(name, value)| name.len().saturating_add(value.len()))
            .sum::<usize>()
            > max_bytes
    {
        return Err(ProcessError::Invalid);
    }
    for name in &request.environment.unset {
        validate_request_environment_name(name)?;
        environment.remove(name);
    }
    for (name, value) in &request.environment.set {
        validate_request_environment_name(name)?;
        if value.contains('\0') {
            return Err(ProcessError::Invalid);
        }
        environment.insert(name.clone(), value.clone());
    }
    Ok(())
}

fn validate_final_environment(
    environment: &BTreeMap<String, String>,
    max_entries: usize,
    max_bytes: usize,
) -> Result<(), ProcessError> {
    if environment.len() > max_entries
        || environment
            .iter()
            .map(|(name, value)| name.len().saturating_add(value.len()))
            .sum::<usize>()
            > max_bytes
    {
        return Err(ProcessError::Invalid);
    }
    Ok(())
}

fn validate_request_environment_name(name: &str) -> Result<(), ProcessError> {
    if !valid_environment_name(name) || reserved_environment_name(name) {
        return Err(ProcessError::Invalid);
    }
    Ok(())
}

fn validate_arguments(
    arguments: &[String],
    max_arguments: usize,
    max_bytes: usize,
) -> Result<(), ProcessError> {
    if arguments.len() > max_arguments
        || arguments.iter().map(String::len).sum::<usize>() > max_bytes
        || arguments.iter().any(|argument| argument.contains('\0'))
    {
        return Err(ProcessError::Invalid);
    }
    Ok(())
}

fn resolve_bare_executable(name: &str, roots: &[PathBuf]) -> Result<PathBuf, ProcessError> {
    for root in roots {
        let candidate = root.join(name);
        let Ok(canonical) = std::fs::canonicalize(&candidate) else {
            continue;
        };
        if !canonical.starts_with(root) || !canonical.is_file() {
            continue;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if std::fs::metadata(&canonical)
                .map_err(|_| ProcessError::Denied)?
                .permissions()
                .mode()
                & 0o111
                == 0
            {
                continue;
            }
        }
        return Ok(canonical);
    }
    Err(ProcessError::Denied)
}

fn is_bare_executable(value: &str) -> bool {
    !value.contains('/') && !value.contains('\\') && Path::new(value).components().count() == 1
}

fn path_string(path: &Path) -> Result<String, ProcessError> {
    path.to_str()
        .map(ToOwned::to_owned)
        .ok_or(ProcessError::Invalid)
}

fn decode_eip_bytes(value: &EncodedBytes) -> Result<Vec<u8>, ProcessError> {
    if value.encoding != "base64" {
        return Err(ProcessError::Invalid);
    }
    base64::engine::general_purpose::STANDARD_NO_PAD
        .decode(&value.data)
        .map_err(|_| ProcessError::Invalid)
}

fn encoded(bytes: &[u8]) -> EncodedBytes {
    EncodedBytes {
        encoding: "base64".to_owned(),
        data: base64::engine::general_purpose::STANDARD_NO_PAD.encode(bytes),
    }
}

fn wait_satisfied(status: &ProcessStatus, condition: ProcessWaitCondition) -> bool {
    match condition {
        ProcessWaitCondition::InitialTerminal => is_terminal_phase(status.phase),
        ProcessWaitCondition::TreeCleaned => {
            is_terminal_phase(status.phase) && status.cleanup != CleanupOutcome::Pending
        }
    }
}

fn is_terminal_phase(phase: ProcessPhase) -> bool {
    !matches!(phase, ProcessPhase::Starting | ProcessPhase::Running)
}

fn map_mount_error(error: MountPathError) -> ProcessError {
    match error {
        MountPathError::Unsupported => ProcessError::Unsupported,
        MountPathError::Quota | MountPathError::Limit => ProcessError::Busy,
        MountPathError::Denied | MountPathError::NotFound | MountPathError::NotRegular => {
            ProcessError::Denied
        }
        MountPathError::UnknownOutcome | MountPathError::Io | MountPathError::Internal => {
            ProcessError::Internal
        }
        MountPathError::Invalid | MountPathError::AlreadyExists => ProcessError::Invalid,
    }
}
