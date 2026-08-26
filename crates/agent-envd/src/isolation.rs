use std::{
    ffi::OsString,
    fmt, fs, io,
    net::TcpStream,
    path::{Path, PathBuf},
    process::{Command as StdCommand, Stdio},
    time::{Duration, Instant},
};

#[cfg(target_os = "macos")]
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener};

use serde::Serialize;
use tokio::process::Command;

use crate::{
    config::{
        Config, ExecutionConfig, ExecutionIsolationMode, ExecutionNetworkMode, TransportConfig,
    },
    eip::{
        IsolationBackend, IsolationCleanupGuarantee, IsolationMode, IsolationNetworkPolicy,
        IsolationPosture,
    },
};

#[cfg(target_os = "macos")]
const SANDBOX_EXEC: &str = "/usr/bin/sandbox-exec";
const PROBE_EXIT_CODE: i32 = 37;
#[cfg(target_os = "macos")]
const PROBE_TIMEOUT: Duration = Duration::from_secs(5);
const PROBE_SENTINEL: &[u8] = b"agent-envd-isolation-probe";

#[cfg(target_os = "macos")]
const DEFAULT_MACOS_RUNTIME_PATHS: &[&str] = &[
    "/System",
    "/usr/bin",
    "/usr/lib",
    "/bin",
    "/sbin",
    "/private/etc/ssl",
    "/private/var/db/timezone",
    "/private/var/select",
];

#[cfg(target_os = "macos")]
const CURATED_MACOS_TOOLCHAIN_PATHS: &[(&str, &str)] = &[
    (
        "/Library/Developer/CommandLineTools",
        "/Library/Developer/CommandLineTools",
    ),
    ("/opt/homebrew/bin", "/opt/homebrew"),
    ("/opt/homebrew/sbin", "/opt/homebrew"),
    ("/opt/homebrew/include", "/opt/homebrew"),
    ("/opt/homebrew/lib", "/opt/homebrew"),
    ("/opt/homebrew/share", "/opt/homebrew"),
    ("/opt/homebrew/Cellar", "/opt/homebrew"),
    ("/opt/homebrew/opt", "/opt/homebrew"),
    ("/usr/local/bin", "/usr/local"),
    ("/usr/local/sbin", "/usr/local"),
    ("/usr/local/Cellar", "/usr/local"),
    ("/usr/local/opt", "/usr/local"),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum LaunchIsolation {
    Disabled,
    MacosSeatbelt,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct IsolationPathGrant {
    pub(crate) root: PathBuf,
    pub(crate) writable: bool,
}

#[derive(Debug, Clone)]
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub(crate) struct IsolationPolicy {
    pub(crate) grants: Vec<IsolationPathGrant>,
    pub(crate) read_only_roots: Vec<PathBuf>,
    pub(crate) read_only_files: Vec<PathBuf>,
    pub(crate) protected_paths: Vec<PathBuf>,
    pub(crate) protected_mutation_paths: Vec<PathBuf>,
    pub(crate) private_home: PathBuf,
    pub(crate) private_temp: PathBuf,
    pub(crate) network: ExecutionNetworkMode,
}

#[derive(Debug, Clone)]
pub(crate) enum IsolationRuntime {
    Disabled,
    #[cfg(target_os = "macos")]
    MacosSeatbelt(MacosSeatbeltRuntime),
}

#[cfg(target_os = "macos")]
#[derive(Debug, Clone)]
pub(crate) struct MacosSeatbeltRuntime {
    network: ExecutionNetworkMode,
    runtime_read_only_paths: Vec<PathBuf>,
    extra_read_only_paths: Vec<PathBuf>,
    protected_paths: Vec<PathBuf>,
    protected_mutation_paths: Vec<PathBuf>,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct IsolationProbeReport {
    pub(crate) ready: bool,
    pub(crate) isolation: bool,
    pub(crate) backend: &'static str,
    pub(crate) network_isolation: bool,
    pub(crate) filesystem_containment: bool,
    pub(crate) process_containment: bool,
    pub(crate) cleanup: &'static str,
}

#[derive(Debug)]
pub(crate) struct IsolationError {
    message: String,
}

impl IsolationError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for IsolationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for IsolationError {}

impl IsolationRuntime {
    pub(crate) fn initialize(config: &Config) -> Result<Self, IsolationError> {
        match config.execution.isolation {
            ExecutionIsolationMode::Disabled => Ok(Self::Disabled),
            ExecutionIsolationMode::Required => {
                let (protected_paths, protected_mutation_paths) = protected_paths(config)?;
                validate_extra_paths(&config.execution.extra_read_only_paths, &protected_paths)?;
                #[cfg(target_os = "macos")]
                {
                    let runtime = MacosSeatbeltRuntime::new(
                        &config.execution,
                        protected_paths,
                        protected_mutation_paths,
                    )?;
                    runtime.run_production_probe()?;
                    Ok(Self::MacosSeatbelt(runtime))
                }
                #[cfg(not(target_os = "macos"))]
                {
                    let _ = (protected_paths, protected_mutation_paths);
                    Err(IsolationError::new(
                        "required execution isolation is not implemented for this platform",
                    ))
                }
            }
        }
    }

    pub(crate) fn probe_config(
        execution: &ExecutionConfig,
    ) -> Result<IsolationProbeReport, IsolationError> {
        match execution.isolation {
            ExecutionIsolationMode::Disabled => Ok(IsolationProbeReport {
                ready: true,
                isolation: false,
                backend: "outer_host",
                network_isolation: false,
                filesystem_containment: false,
                process_containment: false,
                cleanup: "outer_host",
            }),
            ExecutionIsolationMode::Required => {
                #[cfg(target_os = "macos")]
                {
                    let runtime = MacosSeatbeltRuntime::new(execution, Vec::new(), Vec::new())?;
                    runtime.run_production_probe()?;
                    Ok(runtime.probe_report())
                }
                #[cfg(not(target_os = "macos"))]
                {
                    Err(IsolationError::new(
                        "required execution isolation is not implemented for this platform",
                    ))
                }
            }
        }
    }

    pub(crate) fn posture(&self) -> IsolationPosture {
        match self {
            Self::Disabled => IsolationPosture {
                mode: IsolationMode::Disabled,
                backend: IsolationBackend::OuterHost,
                filesystem_containment: false,
                process_containment: false,
                network_containment: false,
                network_policy: IsolationNetworkPolicy::Host,
                cleanup_guarantee: IsolationCleanupGuarantee::OuterHost,
            },
            #[cfg(target_os = "macos")]
            Self::MacosSeatbelt(runtime) => IsolationPosture {
                mode: IsolationMode::Required,
                backend: IsolationBackend::MacosSeatbelt,
                filesystem_containment: true,
                process_containment: true,
                network_containment: runtime.network == ExecutionNetworkMode::Deny,
                network_policy: match runtime.network {
                    ExecutionNetworkMode::Host => IsolationNetworkPolicy::Host,
                    ExecutionNetworkMode::Deny => IsolationNetworkPolicy::Deny,
                },
                cleanup_guarantee: IsolationCleanupGuarantee::ResidualConfinedPossible,
            },
        }
    }

    pub(crate) fn supports_per_command_network_deny(&self) -> bool {
        match self {
            Self::Disabled => false,
            #[cfg(target_os = "macos")]
            Self::MacosSeatbelt(_) => true,
        }
    }

    pub(crate) fn configured_network(&self) -> ExecutionNetworkMode {
        match self {
            Self::Disabled => ExecutionNetworkMode::Host,
            #[cfg(target_os = "macos")]
            Self::MacosSeatbelt(runtime) => runtime.network,
        }
    }

    pub(crate) fn runtime_read_only_paths(&self) -> &[PathBuf] {
        match self {
            Self::Disabled => &[],
            #[cfg(target_os = "macos")]
            Self::MacosSeatbelt(runtime) => &runtime.runtime_read_only_paths,
        }
    }

    pub(crate) fn extra_read_only_paths(&self) -> &[PathBuf] {
        match self {
            Self::Disabled => &[],
            #[cfg(target_os = "macos")]
            Self::MacosSeatbelt(runtime) => &runtime.extra_read_only_paths,
        }
    }

    pub(crate) fn protected_paths(&self) -> &[PathBuf] {
        match self {
            Self::Disabled => &[],
            #[cfg(target_os = "macos")]
            Self::MacosSeatbelt(runtime) => &runtime.protected_paths,
        }
    }

    pub(crate) fn protected_mutation_paths(&self) -> &[PathBuf] {
        match self {
            Self::Disabled => &[],
            #[cfg(target_os = "macos")]
            Self::MacosSeatbelt(runtime) => &runtime.protected_mutation_paths,
        }
    }

    pub(crate) fn launch_isolation(&self) -> LaunchIsolation {
        match self {
            Self::Disabled => LaunchIsolation::Disabled,
            #[cfg(target_os = "macos")]
            Self::MacosSeatbelt(_) => LaunchIsolation::MacosSeatbelt,
        }
    }

    pub(crate) fn supervisor_command(
        &self,
        supervisor_executable: &Path,
        policy: &IsolationPolicy,
    ) -> Result<Command, IsolationError> {
        #[cfg(not(target_os = "macos"))]
        let _ = policy;
        match self {
            Self::Disabled => {
                let mut command = Command::new(supervisor_executable);
                command.arg("--internal-supervisor");
                Ok(command)
            }
            #[cfg(target_os = "macos")]
            Self::MacosSeatbelt(_) => {
                validate_policy(policy)?;
                let (profile, parameters) = build_macos_profile(policy, supervisor_executable);
                let mut command = Command::new(SANDBOX_EXEC);
                command.arg("-p").arg(profile);
                for (name, value) in parameters {
                    command.arg(profile_parameter_argument(&name, &value));
                }
                command
                    .arg("--")
                    .arg(supervisor_executable)
                    .arg("--internal-supervisor");
                Ok(command)
            }
        }
    }
}

#[cfg(target_os = "macos")]
impl MacosSeatbeltRuntime {
    fn new(
        execution: &ExecutionConfig,
        protected_paths: Vec<PathBuf>,
        protected_mutation_paths: Vec<PathBuf>,
    ) -> Result<Self, IsolationError> {
        validate_sandbox_exec()?;
        let runtime_read_only_paths = macos_runtime_read_only_paths()?;
        Ok(Self {
            network: execution.network,
            runtime_read_only_paths,
            extra_read_only_paths: execution.extra_read_only_paths.clone(),
            protected_paths,
            protected_mutation_paths,
        })
    }

    fn run_production_probe(&self) -> Result<(), IsolationError> {
        run_macos_probe(
            self.network,
            &self.runtime_read_only_paths,
            &self.extra_read_only_paths,
        )
    }

    fn probe_report(&self) -> IsolationProbeReport {
        IsolationProbeReport {
            ready: true,
            isolation: true,
            backend: "macos_seatbelt",
            network_isolation: self.network == ExecutionNetworkMode::Deny,
            filesystem_containment: true,
            process_containment: true,
            cleanup: "residual_confined_possible",
        }
    }
}

fn protected_paths(config: &Config) -> Result<(Vec<PathBuf>, Vec<PathBuf>), IsolationError> {
    let mut paths = Vec::new();
    let mut mutation_paths = Vec::new();
    if let Some(runtime) = &config.runtime {
        let canonical = fs::canonicalize(runtime.parent()).map_err(|error| {
            IsolationError::new(format!(
                "cannot canonicalize protected execution path: {error}"
            ))
        })?;
        append_mutation_anchors(&mut mutation_paths, &canonical);
        paths.push(canonical);
    }
    let mut files = Vec::new();
    if let Some(config_file) = &config.config_file {
        files.push(config_file.clone());
    }
    match &config.transport {
        TransportConfig::Stdio => {}
        TransportConfig::Http(http) => {
            files.push(http.credential_file.clone());
            files.extend(http.tls_certificate_file.iter().cloned());
            files.extend(http.tls_private_key_file.iter().cloned());
        }
        TransportConfig::ReverseWebSocket(websocket) => {
            files.push(websocket.credential_file.clone());
            files.extend(websocket.tls_ca_file.iter().cloned());
        }
    }
    for file in files {
        let canonical = fs::canonicalize(&file).map_err(|error| {
            IsolationError::new(format!(
                "cannot canonicalize protected execution file: {error}"
            ))
        })?;
        let metadata = fs::metadata(&canonical).map_err(|error| {
            IsolationError::new(format!("cannot inspect protected execution file: {error}"))
        })?;
        if !metadata.is_file() {
            return Err(IsolationError::new(
                "protected execution files must be regular files",
            ));
        }
        #[cfg(target_os = "macos")]
        {
            use std::os::unix::fs::MetadataExt as _;
            if metadata.nlink() != 1 {
                return Err(IsolationError::new(
                    "protected execution files must not have hard-link aliases",
                ));
            }
        }
        append_mutation_anchors(&mut mutation_paths, &canonical);
        paths.push(canonical);
    }
    paths.sort();
    paths.dedup();
    mutation_paths.sort();
    mutation_paths.dedup();
    Ok((paths, mutation_paths))
}

fn append_mutation_anchors(paths: &mut Vec<PathBuf>, path: &Path) {
    let mut current = Some(path);
    while let Some(value) = current {
        paths.push(value.to_path_buf());
        current = value.parent();
    }
}

fn validate_extra_paths(
    extra_paths: &[PathBuf],
    protected_paths: &[PathBuf],
) -> Result<(), IsolationError> {
    for extra in extra_paths {
        if protected_paths
            .iter()
            .any(|protected| paths_overlap(extra, protected))
        {
            return Err(IsolationError::new(
                "an execution extra read-only path overlaps protected envd state",
            ));
        }
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn validate_policy(policy: &IsolationPolicy) -> Result<(), IsolationError> {
    for path in policy
        .grants
        .iter()
        .map(|grant| &grant.root)
        .chain(policy.read_only_roots.iter())
        .chain(policy.read_only_files.iter())
        .chain(std::iter::once(&policy.private_home))
        .chain(std::iter::once(&policy.private_temp))
        .chain(policy.protected_paths.iter())
        .chain(policy.protected_mutation_paths.iter())
    {
        if !path.is_absolute() {
            return Err(IsolationError::new(
                "execution isolation policy paths must be absolute",
            ));
        }
    }
    for extra in &policy.read_only_roots {
        if policy
            .protected_paths
            .iter()
            .any(|protected| paths_overlap(extra, protected))
        {
            return Err(IsolationError::new(
                "an execution runtime root overlaps protected envd state",
            ));
        }
    }
    Ok(())
}

fn paths_overlap(left: &Path, right: &Path) -> bool {
    left == right || left.starts_with(right) || right.starts_with(left)
}

#[cfg(target_os = "macos")]
fn validate_sandbox_exec() -> Result<(), IsolationError> {
    let metadata = fs::symlink_metadata(SANDBOX_EXEC)
        .map_err(|error| IsolationError::new(format!("cannot inspect {SANDBOX_EXEC}: {error}")))?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err(IsolationError::new(
            "/usr/bin/sandbox-exec must be a non-symlink regular system file",
        ));
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn macos_runtime_read_only_paths() -> Result<Vec<PathBuf>, IsolationError> {
    let mut paths = Vec::new();
    for value in DEFAULT_MACOS_RUNTIME_PATHS {
        append_existing_canonical_path(&mut paths, Path::new(value), None)?;
    }
    for (value, allowed_prefix) in CURATED_MACOS_TOOLCHAIN_PATHS {
        append_existing_canonical_path(
            &mut paths,
            Path::new(value),
            Some(Path::new(allowed_prefix)),
        )?;
    }
    paths.sort();
    paths.dedup();
    Ok(paths)
}

#[cfg(target_os = "macos")]
fn append_existing_canonical_path(
    paths: &mut Vec<PathBuf>,
    path: &Path,
    allowed_prefix: Option<&Path>,
) -> Result<(), IsolationError> {
    if !path.exists() {
        return Ok(());
    }
    let canonical = fs::canonicalize(path).map_err(|error| {
        IsolationError::new(format!(
            "cannot canonicalize macOS runtime path {}: {error}",
            path.display()
        ))
    })?;
    if !canonical.is_dir() {
        return Ok(());
    }
    if let Some(prefix) = allowed_prefix
        && !canonical.starts_with(prefix)
    {
        return Ok(());
    }
    if !paths.contains(&canonical) {
        paths.push(canonical);
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn build_macos_profile(
    policy: &IsolationPolicy,
    supervisor_executable: &Path,
) -> (String, Vec<(String, PathBuf)>) {
    let mut profile = String::from(
        r#"(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow signal (target same-sandbox))
(allow process-info* (target same-sandbox))
(allow sysctl-read)
(allow ipc-posix-sem)
(allow pseudo-tty)
(allow file-read* file-test-existence (literal "/"))
(allow file-read* file-test-existence file-write-data (subpath "/dev/fd"))
(allow file-read-metadata (literal "/dev") (regex #"^/dev/.*$"))
(allow file-read-metadata file-test-existence
  (literal "/etc") (literal "/tmp") (literal "/var") (literal "/private"))
(allow file-read* file-write* file-ioctl (literal "/dev/null"))
(allow file-read* (literal "/dev/random") (literal "/dev/urandom"))
(allow file-read* file-write* file-ioctl (literal "/dev/ptmx"))
(allow file-ioctl (regex #"^/dev/ttys[0-9]+"))
(allow system-mac-syscall (mac-policy-name "vnguard"))
(allow mach-lookup
  (global-name "com.apple.system.opendirectoryd.libinfo")
  (global-name "com.apple.system.opendirectoryd.membership")
  (global-name "com.apple.PowerManagement.control")
  (global-name "com.apple.cfprefsd.daemon")
  (global-name "com.apple.cfprefsd.agent"))
"#,
    );
    let mut parameters = Vec::new();

    parameters.push((
        "SUPERVISOR_EXECUTABLE".to_owned(),
        supervisor_executable.to_path_buf(),
    ));
    profile.push_str(
        "(allow file-read* file-map-executable (literal (param \"SUPERVISOR_EXECUTABLE\")))\n",
    );

    for (index, path) in policy.protected_paths.iter().enumerate() {
        parameters.push((format!("PROTECTED_{index}"), path.clone()));
    }
    for (index, root) in policy.read_only_roots.iter().enumerate() {
        let name = format!("READ_ONLY_ROOT_{index}");
        parameters.push((name.clone(), root.clone()));
        append_path_rule(
            &mut profile,
            "file-read* file-map-executable",
            "subpath",
            &name,
            policy.protected_paths.len(),
        );
    }
    for (index, path) in policy.read_only_files.iter().enumerate() {
        let name = format!("READ_ONLY_FILE_{index}");
        parameters.push((name.clone(), path.clone()));
        append_path_rule(
            &mut profile,
            "file-read* file-map-executable",
            "literal",
            &name,
            policy.protected_paths.len(),
        );
    }
    for (index, grant) in policy.grants.iter().enumerate() {
        let name = format!("MOUNT_{index}");
        parameters.push((name.clone(), grant.root.clone()));
        append_path_rule(
            &mut profile,
            if grant.writable {
                "file-read* file-write* file-map-executable"
            } else {
                "file-read* file-map-executable"
            },
            "subpath",
            &name,
            policy.protected_paths.len(),
        );
    }

    for (name, path) in [
        ("PRIVATE_HOME", &policy.private_home),
        ("PRIVATE_TEMP", &policy.private_temp),
    ] {
        parameters.push((name.to_owned(), path.clone()));
        profile.push_str(&format!(
            "(allow file-read* file-write* (subpath (param \"{name}\")))\n"
        ));
    }
    for (index, path) in policy.protected_mutation_paths.iter().enumerate() {
        let name = format!("PROTECTED_MUTATION_{index}");
        parameters.push((name.clone(), path.clone()));
        profile.push_str(&format!(
            "(deny file-write-unlink file-write-create (literal (param \"{name}\")))\n"
        ));
    }

    if policy.network == ExecutionNetworkMode::Host {
        profile.push_str(
            r#"(allow system-socket
  (require-all (socket-domain AF_SYSTEM) (socket-protocol 2)))
(allow network-outbound (remote ip "*:*"))
(allow network-bind (local ip "*:*"))
(allow network-inbound (local ip "*:*"))
(allow mach-lookup
  (global-name "com.apple.bsd.dirhelper")
  (global-name "com.apple.networkd")
  (global-name "com.apple.ocspd")
  (global-name "com.apple.trustd.agent")
  (global-name "com.apple.SystemConfiguration.DNSConfiguration")
  (global-name "com.apple.SystemConfiguration.configd"))
"#,
        );
    }
    (profile, parameters)
}

#[cfg(target_os = "macos")]
fn append_path_rule(
    profile: &mut String,
    operations: &str,
    matcher: &str,
    parameter: &str,
    protected_count: usize,
) {
    profile.push_str(&format!(
        "(allow {operations} (require-all ({matcher} (param \"{parameter}\"))"
    ));
    for index in 0..protected_count {
        profile.push_str(&format!(
            " (require-not (subpath (param \"PROTECTED_{index}\")))"
        ));
    }
    profile.push_str("))\n");
}

#[cfg(target_os = "macos")]
fn profile_parameter_argument(name: &str, value: &Path) -> OsString {
    let mut argument = OsString::from("-D");
    argument.push(name);
    argument.push("=");
    argument.push(value.as_os_str());
    argument
}

#[cfg(target_os = "macos")]
fn run_macos_probe(
    network: ExecutionNetworkMode,
    runtime_roots: &[PathBuf],
    extra_roots: &[PathBuf],
) -> Result<(), IsolationError> {
    for writable in [false, true] {
        let root = create_probe_root()?;
        let result = run_macos_probe_in(&root, network, runtime_roots, extra_roots, writable);
        let _ = fs::remove_dir_all(&root);
        result?;
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn run_macos_probe_in(
    root: &Path,
    network: ExecutionNetworkMode,
    runtime_roots: &[PathBuf],
    extra_roots: &[PathBuf],
    writable: bool,
) -> Result<(), IsolationError> {
    let workspace = create_probe_directory(root, "workspace")?;
    let protected = create_probe_directory(root, "protected")?;
    let home = create_probe_directory(&protected, "home")?;
    let temp = create_probe_directory(&protected, "temp")?;
    let allowed_file = workspace.join("allowed");
    let protected_file = protected.join("sentinel");
    let sleeper_pid_file = temp.join("sleeper.pid");
    fs::write(&allowed_file, PROBE_SENTINEL)
        .map_err(|error| IsolationError::new(format!("cannot write isolation probe: {error}")))?;
    fs::write(&protected_file, b"protected").map_err(|error| {
        IsolationError::new(format!(
            "cannot write isolation protected sentinel: {error}"
        ))
    })?;

    let listener =
        TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0)).map_err(|error| {
            IsolationError::new(format!("cannot bind isolation probe listener: {error}"))
        })?;
    let address = listener.local_addr().map_err(|error| {
        IsolationError::new(format!("cannot inspect isolation probe listener: {error}"))
    })?;
    let executable = std::env::current_exe().map_err(|error| {
        IsolationError::new(format!(
            "cannot resolve isolation probe executable: {error}"
        ))
    })?;
    let mut protected_mutation_paths = Vec::new();
    append_mutation_anchors(&mut protected_mutation_paths, &protected);
    let policy = IsolationPolicy {
        grants: vec![IsolationPathGrant {
            root: root.to_path_buf(),
            writable,
        }],
        read_only_roots: runtime_roots.iter().chain(extra_roots).cloned().collect(),
        read_only_files: Vec::new(),
        protected_paths: vec![protected],
        protected_mutation_paths,
        private_home: home,
        private_temp: temp,
        network,
    };
    let (profile, parameters) = build_macos_profile(&policy, &executable);
    let mut command = StdCommand::new(SANDBOX_EXEC);
    command.arg("-p").arg(profile);
    for (name, value) in parameters {
        command.arg(profile_parameter_argument(&name, &value));
    }
    command
        .arg("--")
        .arg(&executable)
        .arg("--internal-isolation-probe")
        .arg(&allowed_file)
        .arg(if writable { "writable" } else { "read_only" })
        .arg(&protected_file)
        .arg(address.to_string())
        .arg(match network {
            ExecutionNetworkMode::Host => "reachable",
            ExecutionNetworkMode::Deny => "denied",
        })
        .arg(&sleeper_pid_file)
        .env_clear()
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_probe_process_group(&mut command);
    let mut child = command.spawn().map_err(|error| {
        IsolationError::new(format!("cannot start macOS isolation probe: {error}"))
    })?;
    let process_group = child.id();
    let deadline = Instant::now() + PROBE_TIMEOUT;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => {
                std::thread::sleep(Duration::from_millis(10));
            }
            Ok(None) => {
                kill_probe_group(process_group);
                let _ = child.wait();
                return Err(IsolationError::new("macOS isolation probe timed out"));
            }
            Err(error) => {
                kill_probe_group(process_group);
                let _ = child.wait();
                return Err(IsolationError::new(format!(
                    "cannot wait for macOS isolation probe: {error}"
                )));
            }
        }
    };
    let stdout = child
        .stdout
        .take()
        .map(read_bounded_probe_output)
        .transpose()?;
    let stderr = child
        .stderr
        .take()
        .map(read_bounded_probe_output)
        .transpose()?;
    kill_probe_group(process_group);
    drop(listener);

    if status.code() != Some(PROBE_EXIT_CODE) {
        return Err(IsolationError::new(format!(
            "macOS isolation probe failed with status {:?}: {}{}",
            status.code(),
            String::from_utf8_lossy(stdout.as_deref().unwrap_or_default()),
            String::from_utf8_lossy(stderr.as_deref().unwrap_or_default())
        )));
    }
    let sleeper_pid = read_probe_pid(&sleeper_pid_file)?;
    if !wait_for_process_exit(sleeper_pid, deadline) {
        return Err(IsolationError::new(
            "macOS isolation probe could not clean its confined descendant",
        ));
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn create_probe_root() -> Result<PathBuf, IsolationError> {
    let parent = fs::canonicalize(std::env::temp_dir()).map_err(|error| {
        IsolationError::new(format!(
            "cannot canonicalize isolation probe parent: {error}"
        ))
    })?;
    for _ in 0..32 {
        let mut random = [0_u8; 16];
        getrandom::fill(&mut random)
            .map_err(|_| IsolationError::new("cannot generate isolation probe identity"))?;
        let name = random
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        let root = parent.join(format!("agent-envd-isolation-probe-{name}"));
        match fs::create_dir(&root) {
            Ok(()) => {
                protect_probe_directory(&root)?;
                return fs::canonicalize(root).map_err(|error| {
                    IsolationError::new(format!("cannot canonicalize isolation probe: {error}"))
                });
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(error) => {
                return Err(IsolationError::new(format!(
                    "cannot create isolation probe: {error}"
                )));
            }
        }
    }
    Err(IsolationError::new(
        "cannot allocate unique isolation probe directory",
    ))
}

#[cfg(target_os = "macos")]
fn create_probe_directory(root: &Path, name: &str) -> Result<PathBuf, IsolationError> {
    let path = root.join(name);
    fs::create_dir(&path).map_err(|error| {
        IsolationError::new(format!("cannot create isolation probe directory: {error}"))
    })?;
    protect_probe_directory(&path)?;
    Ok(path)
}

#[cfg(target_os = "macos")]
fn protect_probe_directory(path: &Path) -> Result<(), IsolationError> {
    use std::os::unix::fs::PermissionsExt as _;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|error| {
        IsolationError::new(format!("cannot protect isolation probe directory: {error}"))
    })
}

#[cfg(target_os = "macos")]
fn configure_probe_process_group(command: &mut StdCommand) {
    use std::os::unix::process::CommandExt as _;
    unsafe {
        command.pre_exec(|| {
            if libc::setpgid(0, 0) == 0 {
                Ok(())
            } else {
                Err(io::Error::last_os_error())
            }
        });
    }
}

#[cfg(target_os = "macos")]
fn kill_probe_group(process_group: u32) {
    if let Ok(group) = i32::try_from(process_group) {
        unsafe {
            libc::kill(-group, libc::SIGKILL);
        }
    }
}

#[cfg(target_os = "macos")]
fn read_bounded_probe_output<R: io::Read>(reader: R) -> Result<Vec<u8>, IsolationError> {
    use io::Read as _;
    let mut output = Vec::new();
    reader
        .take(64 * 1024)
        .read_to_end(&mut output)
        .map_err(|error| {
            IsolationError::new(format!("cannot read isolation probe output: {error}"))
        })?;
    Ok(output)
}

#[cfg(target_os = "macos")]
fn read_probe_pid(path: &Path) -> Result<i32, IsolationError> {
    fs::read_to_string(path)
        .map_err(|error| {
            IsolationError::new(format!("cannot read isolation probe descendant: {error}"))
        })?
        .parse::<i32>()
        .map_err(|_| IsolationError::new("isolation probe descendant PID is invalid"))
}

#[cfg(target_os = "macos")]
fn wait_for_process_exit(pid: i32, deadline: Instant) -> bool {
    loop {
        let result = unsafe { libc::kill(pid, 0) };
        if result != 0 && io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

pub(crate) fn run_internal_probe(arguments: &[OsString]) -> Result<i32, IsolationError> {
    if arguments.len() != 6 {
        return Err(IsolationError::new(
            "internal isolation probe received an invalid argument count",
        ));
    }
    let allowed = Path::new(&arguments[0]);
    if fs::read(allowed).map_err(|_| IsolationError::new("allowed probe read failed"))?
        != PROBE_SENTINEL
    {
        return Err(IsolationError::new("allowed probe sentinel is invalid"));
    }
    let writable = match arguments[1].to_str() {
        Some("writable") => true,
        Some("read_only") => false,
        _ => return Err(IsolationError::new("invalid probe write expectation")),
    };
    let write_result = fs::OpenOptions::new()
        .append(true)
        .open(allowed)
        .and_then(|mut file| {
            use io::Write as _;
            file.write_all(b"-write-check")
        });
    if write_result.is_ok() != writable {
        return Err(IsolationError::new(
            "isolation probe observed an incorrect write policy",
        ));
    }
    let protected_file = Path::new(&arguments[2]);
    if fs::read(protected_file).is_ok() {
        return Err(IsolationError::new("isolation probe read protected state"));
    }
    if writable {
        let protected_parent = protected_file
            .parent()
            .ok_or_else(|| IsolationError::new("protected probe file has no parent"))?;
        let moved_parent = protected_parent.with_file_name("moved-protected");
        if fs::rename(protected_parent, moved_parent).is_ok() {
            return Err(IsolationError::new(
                "isolation probe moved a protected directory through a broad grant",
            ));
        }
    }
    let address = arguments[3]
        .to_str()
        .ok_or_else(|| IsolationError::new("isolation probe address is not UTF-8"))?
        .parse()
        .map_err(|_| IsolationError::new("isolation probe address is invalid"))?;
    let reachable = TcpStream::connect_timeout(&address, Duration::from_millis(500)).is_ok();
    let expected_reachable = match arguments[4].to_str() {
        Some("reachable") => true,
        Some("denied") => false,
        _ => return Err(IsolationError::new("invalid probe network expectation")),
    };
    if reachable != expected_reachable {
        return Err(IsolationError::new(
            "isolation probe observed an incorrect network policy",
        ));
    }

    let executable = std::env::current_exe().map_err(|error| {
        IsolationError::new(format!("cannot resolve probe executable: {error}"))
    })?;
    let child_status = StdCommand::new(&executable)
        .arg("--internal-isolation-probe-child")
        .arg(&arguments[2])
        .env_clear()
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|error| IsolationError::new(format!("cannot start probe child: {error}")))?;
    if !child_status.success() {
        return Err(IsolationError::new(
            "isolation probe descendant did not inherit protected-path denial",
        ));
    }
    StdCommand::new(executable)
        .arg("--internal-isolation-probe-sleeper")
        .arg(&arguments[5])
        .env_clear()
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| {
            IsolationError::new(format!("cannot start isolation probe descendant: {error}"))
        })?;
    let deadline = Instant::now() + Duration::from_secs(1);
    while !Path::new(&arguments[5]).exists() {
        if Instant::now() >= deadline {
            return Err(IsolationError::new(
                "isolation probe descendant did not become observable",
            ));
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    Ok(PROBE_EXIT_CODE)
}

pub(crate) fn run_internal_probe_child(arguments: &[OsString]) -> Result<i32, IsolationError> {
    if arguments.len() != 1 {
        return Err(IsolationError::new(
            "internal isolation probe child received invalid arguments",
        ));
    }
    Ok(if fs::read(Path::new(&arguments[0])).is_err() {
        0
    } else {
        70
    })
}

pub(crate) fn run_internal_probe_sleeper(arguments: &[OsString]) -> Result<i32, IsolationError> {
    if arguments.len() != 1 {
        return Err(IsolationError::new(
            "internal isolation probe sleeper received invalid arguments",
        ));
    }
    fs::write(Path::new(&arguments[0]), std::process::id().to_string()).map_err(|error| {
        IsolationError::new(format!(
            "cannot publish isolation probe descendant: {error}"
        ))
    })?;
    std::thread::sleep(Duration::from_secs(30));
    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disabled_posture_truthfully_delegates_to_outer_host() {
        let posture = IsolationRuntime::Disabled.posture();
        assert_eq!(posture.mode, IsolationMode::Disabled);
        assert_eq!(posture.backend, IsolationBackend::OuterHost);
        assert!(!posture.filesystem_containment);
        assert!(!posture.process_containment);
        assert!(!posture.network_containment);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn seatbelt_profile_parameterizes_every_native_path() {
        let policy = IsolationPolicy {
            grants: vec![IsolationPathGrant {
                root: PathBuf::from("/private/project"),
                writable: true,
            }],
            read_only_roots: vec![PathBuf::from("/System")],
            read_only_files: vec![PathBuf::from("/private/tool")],
            protected_paths: vec![PathBuf::from("/private/project/.envd")],
            protected_mutation_paths: vec![PathBuf::from("/private/project")],
            private_home: PathBuf::from("/private/home"),
            private_temp: PathBuf::from("/private/temp"),
            network: ExecutionNetworkMode::Deny,
        };
        let (profile, parameters) = build_macos_profile(&policy, Path::new("/private/agent-envd"));

        for path in [
            "/private/project",
            "/private/project/.envd",
            "/private/home",
            "/private/temp",
            "/private/tool",
            "/private/agent-envd",
        ] {
            assert!(!profile.contains(path));
            assert!(parameters.iter().any(|(_, value)| value == Path::new(path)));
        }
        assert!(profile.contains("(param \"MOUNT_0\")"));
        assert!(profile.contains("(require-not (subpath (param \"PROTECTED_0\")))"));
        assert!(!profile.contains("allow network-outbound"));
    }
}
