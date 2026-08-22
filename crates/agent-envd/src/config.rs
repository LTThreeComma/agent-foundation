use std::{
    env,
    error::Error,
    fmt, fs,
    path::{Path, PathBuf},
    time::Duration,
};

use serde::Deserialize;

use crate::eip::EIPLimits;

const DEFAULT_MAX_REQUEST_BYTES: u64 = 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES: u64 = 1024 * 1024;
const DEFAULT_MAX_CONCURRENT_OPERATIONS: u64 = 32;
const DEFAULT_MAX_TRANSFER_FRAME_BYTES: u64 = 256 * 1024;
const DEFAULT_MAX_CONCURRENT_FILE_TRANSFERS: u64 = 16;
const DEFAULT_SESSION_IDLE_TTL_MS: u64 = 5 * 60 * 1000;
const DEFAULT_STAGING_SCAVENGE_TIMEOUT_MS: u64 = 5_000;
const INITIALIZATION_TIMEOUT: Duration = Duration::from_secs(10);

const KNOWN_ENVIRONMENT_VARIABLES: &[&str] = &[
    "AGENT_ENVD_API_KEY",
    "AGENT_ENVD_TRANSPORT",
    "AGENT_ENVD_LISTEN_ADDRESS",
    "AGENT_ENVD_HTTP_ENABLED",
    "AGENT_ENVD_WEBSOCKET_ENABLED",
    "AGENT_ENVD_ENVIRONMENT_ID",
    "AGENT_ENVD_RUNTIME_DIR",
    "AGENT_ENVD_EXECUTION_ISOLATION",
    "AGENT_ENVD_EXECUTION_NETWORK",
    "AGENT_ENVD_EXECUTION_EXTRA_READ_ONLY_PATHS",
    "AGENT_ENVD_EXECUTION_UID",
    "AGENT_ENVD_EXECUTION_GID",
];

const NETWORK_ONLY_VARIABLES: &[&str] = &[
    "AGENT_ENVD_API_KEY",
    "AGENT_ENVD_LISTEN_ADDRESS",
    "AGENT_ENVD_HTTP_ENABLED",
    "AGENT_ENVD_WEBSOCKET_ENABLED",
];

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct TrustedMountConfig {
    pub(crate) mount_id: String,
    pub(crate) native_root: PathBuf,
    #[serde(default)]
    pub(crate) staging_root: Option<PathBuf>,
    pub(crate) writable: bool,
    pub(crate) exclusive_mutation_control: bool,
    #[serde(default = "default_allow_command_execution")]
    pub(crate) allow_command_execution: bool,
    pub(crate) max_file_bytes: u64,
    #[serde(default)]
    pub(crate) allowed_operations: Vec<String>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct FileConfig {
    #[serde(default)]
    mounts: Vec<TrustedMountConfig>,
}

#[derive(Debug, Clone)]
pub(crate) struct Config {
    pub(crate) environment_id: String,
    pub(crate) limits: EIPLimits,
    pub(crate) initialization_timeout: Duration,
    pub(crate) session_idle_timeout: Duration,
    pub(crate) staging_scavenge_timeout: Duration,
    pub(crate) mounts: Vec<TrustedMountConfig>,
}

impl Config {
    pub(crate) fn from_environment() -> Result<Self, ConfigError> {
        reject_unknown_environment_variables()?;
        let file = load_file_config(config_file_argument()?)?;

        let transport =
            optional_unicode("AGENT_ENVD_TRANSPORT")?.unwrap_or_else(|| "stdio".to_owned());
        if transport != "stdio" {
            return Err(ConfigError::new(
                "AGENT_ENVD_TRANSPORT must be stdio in this release",
            ));
        }
        for name in NETWORK_ONLY_VARIABLES {
            if env::var_os(name).is_some() {
                return Err(ConfigError::new(format!(
                    "{name} is not valid with stdio transport"
                )));
            }
        }

        let isolation = optional_unicode("AGENT_ENVD_EXECUTION_ISOLATION")?
            .unwrap_or_else(|| "required".to_owned());
        match isolation.as_str() {
            "disabled" => {}
            "required" => {
                return Err(ConfigError::new(
                    "required execution isolation is not available yet; explicitly set AGENT_ENVD_EXECUTION_ISOLATION=disabled only inside an outer sandbox",
                ));
            }
            _ => {
                return Err(ConfigError::new(
                    "AGENT_ENVD_EXECUTION_ISOLATION must be required or disabled",
                ));
            }
        }

        if let Some(network) = optional_unicode("AGENT_ENVD_EXECUTION_NETWORK")?
            && network != "host"
        {
            return Err(ConfigError::new(
                "disabled execution isolation supports only AGENT_ENVD_EXECUTION_NETWORK=host",
            ));
        }
        if let Some(paths) = optional_unicode("AGENT_ENVD_EXECUTION_EXTRA_READ_ONLY_PATHS")? {
            let paths = serde_json::from_str::<Vec<String>>(&paths).map_err(|_| {
                ConfigError::new(
                    "AGENT_ENVD_EXECUTION_EXTRA_READ_ONLY_PATHS must be a JSON array of strings",
                )
            })?;
            if !paths.is_empty() {
                return Err(ConfigError::new(
                    "extra read-only execution paths require native isolation",
                ));
            }
        }
        if env::var_os("AGENT_ENVD_EXECUTION_UID").is_some()
            || env::var_os("AGENT_ENVD_EXECUTION_GID").is_some()
        {
            return Err(ConfigError::new(
                "execution UID and GID require native isolation",
            ));
        }

        if let Some(runtime_dir) = optional_unicode("AGENT_ENVD_RUNTIME_DIR")?
            && !Path::new(&runtime_dir).is_absolute()
        {
            return Err(ConfigError::new(
                "AGENT_ENVD_RUNTIME_DIR must be an absolute path",
            ));
        }

        let environment_id = required_unicode("AGENT_ENVD_ENVIRONMENT_ID")?;
        if environment_id.trim() != environment_id
            || environment_id.is_empty()
            || environment_id.len() > 256
            || environment_id.chars().any(char::is_control)
        {
            return Err(ConfigError::new(
                "AGENT_ENVD_ENVIRONMENT_ID must be 1..=256 non-control characters without surrounding whitespace",
            ));
        }

        let limits = default_limits();
        Ok(Self {
            environment_id,
            initialization_timeout: INITIALIZATION_TIMEOUT,
            session_idle_timeout: Duration::from_millis(DEFAULT_SESSION_IDLE_TTL_MS),
            staging_scavenge_timeout: Duration::from_millis(DEFAULT_STAGING_SCAVENGE_TIMEOUT_MS),
            mounts: file.mounts,
            limits,
        })
    }

    #[cfg(test)]
    pub(crate) fn for_test(environment_id: &str) -> Self {
        Self {
            environment_id: environment_id.to_owned(),
            limits: default_limits(),
            initialization_timeout: Duration::from_millis(20),
            session_idle_timeout: Duration::from_secs(1),
            staging_scavenge_timeout: Duration::from_secs(1),
            mounts: Vec::new(),
        }
    }
}

fn default_allow_command_execution() -> bool {
    true
}

fn config_file_argument() -> Result<Option<PathBuf>, ConfigError> {
    let mut arguments = env::args_os().skip(1);
    let Some(flag) = arguments.next() else {
        return Ok(None);
    };
    if flag != "--config" {
        return Err(ConfigError::new(
            "the only supported argument is --config <absolute-json-path>",
        ));
    }
    let path = arguments
        .next()
        .ok_or_else(|| ConfigError::new("--config requires a path"))?;
    if arguments.next().is_some() {
        return Err(ConfigError::new("unexpected arguments after --config path"));
    }
    let path = PathBuf::from(path);
    if !path.is_absolute() {
        return Err(ConfigError::new("--config path must be absolute"));
    }
    Ok(Some(path))
}

fn load_file_config(path: Option<PathBuf>) -> Result<FileConfig, ConfigError> {
    let Some(path) = path else {
        return Ok(FileConfig::default());
    };
    let metadata = fs::symlink_metadata(&path)
        .map_err(|error| ConfigError::new(format!("cannot inspect config file: {error}")))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(ConfigError::new(
            "--config must identify a regular file, not a symlink",
        ));
    }
    let bytes = fs::read(&path)
        .map_err(|error| ConfigError::new(format!("cannot read config file: {error}")))?;
    serde_json::from_slice(&bytes)
        .map_err(|error| ConfigError::new(format!("invalid config file JSON: {error}")))
}

fn default_limits() -> EIPLimits {
    EIPLimits {
        max_request_bytes: DEFAULT_MAX_REQUEST_BYTES,
        max_response_bytes: DEFAULT_MAX_RESPONSE_BYTES,
        max_concurrent_operations: DEFAULT_MAX_CONCURRENT_OPERATIONS,
        max_processes: 32,
        max_operation_duration_ms: 5 * 60 * 1000,
        max_inline_output_bytes: 64 * 1024,
        max_output_bytes: 16 * 1024 * 1024,
        max_retained_bytes: 256 * 1024 * 1024,
        max_retained_objects: 1024,
        max_retention_ttl_ms: 60 * 60 * 1000,
        max_operation_records: 4096,
        operation_record_ttl_ms: 60 * 60 * 1000,
        session_idle_ttl_ms: DEFAULT_SESSION_IDLE_TTL_MS,
        max_process_records: 128,
        terminal_process_record_ttl_ms: 60 * 60 * 1000,
        max_transfer_frame_bytes: DEFAULT_MAX_TRANSFER_FRAME_BYTES,
        max_concurrent_file_transfers: DEFAULT_MAX_CONCURRENT_FILE_TRANSFERS,
        max_file_transfer_records: 128,
        file_transfer_record_ttl_ms: 60 * 60 * 1000,
        max_staged_file_bytes: 512 * 1024 * 1024,
        max_staged_file_objects: 64,
        file_transfer_idle_ttl_ms: 60 * 1000,
        max_file_transfer_duration_ms: 15 * 60 * 1000,
    }
}

fn reject_unknown_environment_variables() -> Result<(), ConfigError> {
    for (name, _) in env::vars_os() {
        let Some(name) = name.to_str() else {
            continue;
        };
        if name.starts_with("AGENT_ENVD_") && !KNOWN_ENVIRONMENT_VARIABLES.contains(&name) {
            return Err(ConfigError::new(format!(
                "unknown agent-envd environment variable: {name}"
            )));
        }
    }
    Ok(())
}

fn required_unicode(name: &str) -> Result<String, ConfigError> {
    optional_unicode(name)?.ok_or_else(|| ConfigError::new(format!("{name} is required")))
}

fn optional_unicode(name: &str) -> Result<Option<String>, ConfigError> {
    env::var_os(name)
        .map(|value| {
            value
                .into_string()
                .map_err(|_| ConfigError::new(format!("{name} must be valid UTF-8")))
        })
        .transpose()
}

#[derive(Debug)]
pub(crate) struct ConfigError {
    message: String,
}

impl ConfigError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for ConfigError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl Error for ConfigError {}

#[cfg(test)]
mod tests {
    use crate::eip::EipValidate;

    use super::Config;

    #[test]
    fn test_configuration_has_finite_valid_limits() {
        let config = Config::for_test("env-test");

        config.limits.validate().expect("limits are valid");
        assert_eq!(config.environment_id, "env-test");
    }
}
