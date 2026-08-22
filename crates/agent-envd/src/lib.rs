use std::{error::Error, sync::Arc};

mod config;
mod daemon;
pub mod eip;
mod mount;
mod operation;
mod resource;
mod retention;
mod stdio;
mod transfer;

/// Runs one stdio agent-envd instance from trusted process configuration.
pub async fn run_from_environment() -> Result<(), Box<dyn Error + Send + Sync>> {
    let config = config::Config::from_environment()?;
    let daemon = Arc::new(daemon::Daemon::new(&config)?);
    let warning = serde_json::json!({
        "level": "warning",
        "event": "agent-envd.execution_isolation.disabled",
        "message": "envd-native execution isolation is disabled; the outer host owns containment",
    });
    eprintln!("{warning}");
    stdio::serve(daemon, &config).await?;
    Ok(())
}
