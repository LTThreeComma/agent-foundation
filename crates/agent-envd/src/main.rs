use std::time::Duration;

fn main() {
    let runtime = match tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
    {
        Ok(runtime) => runtime,
        Err(error) => {
            eprintln!("agent-envd failed to start its async runtime: {error}");
            std::process::exit(1);
        }
    };
    let result = runtime.block_on(converge_agent_envd::run_from_environment());
    // Tokio's portable stdin adapter may have one blocking read in progress when
    // an operator signal wins the shutdown race. Keep process shutdown bounded.
    runtime.shutdown_timeout(Duration::from_secs(1));
    if let Err(error) = result {
        eprintln!("agent-envd failed: {error}");
        std::process::exit(1);
    }
}
