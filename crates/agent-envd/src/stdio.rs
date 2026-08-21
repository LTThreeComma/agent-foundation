use std::{collections::HashSet, future::Future, io, sync::Arc, time::Duration};

use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader},
    sync::{Semaphore, mpsc, watch},
    task::JoinSet,
    time::timeout,
};

use crate::{config::Config, daemon::Daemon};

const MAX_HEADER_BYTES: usize = 8 * 1024;
const CONTENT_TYPE: &str = "application/json; charset=utf-8";
const SHUTDOWN_DRAIN_TIMEOUT: Duration = Duration::from_secs(1);

pub(crate) async fn serve(daemon: Arc<Daemon>, config: &Config) -> io::Result<()> {
    let reader = BufReader::new(tokio::io::stdin());
    let writer = tokio::io::stdout();
    serve_io(reader, writer, daemon, config, shutdown_signal()).await
}

async fn serve_io<R, W, S>(
    mut reader: R,
    writer: W,
    daemon: Arc<Daemon>,
    config: &Config,
    shutdown: S,
) -> io::Result<()>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin + Send + 'static,
    S: Future<Output = io::Result<()>>,
{
    let max_request_bytes = usize::try_from(config.limits.max_request_bytes)
        .map_err(|_| invalid_data("max_request_bytes does not fit this platform"))?;
    let max_response_bytes = usize::try_from(config.limits.max_response_bytes)
        .map_err(|_| invalid_data("max_response_bytes does not fit this platform"))?;
    let max_concurrency = usize::try_from(config.limits.max_concurrent_operations)
        .map_err(|_| invalid_data("max_concurrent_operations does not fit this platform"))?;

    let (responses, mut response_rx) = mpsc::channel::<Vec<u8>>(max_concurrency);
    let (writer_stopped, mut writer_stopped_rx) = watch::channel(false);
    let mut writer_task = tokio::spawn(async move {
        let result = async {
            let mut writer = writer;
            while let Some(response) = response_rx.recv().await {
                write_frame(&mut writer, &response, max_response_bytes).await?;
            }
            writer.flush().await
        }
        .await;
        writer_stopped.send_replace(true);
        result
    });

    let admission = Arc::new(Semaphore::new(max_concurrency));
    let mut requests = JoinSet::new();
    let mut closed = daemon.subscribe_closed();
    let mut shutdown = std::pin::pin!(shutdown);
    let mut first_frame = true;

    loop {
        if *closed.borrow() {
            break;
        }
        let permit = tokio::select! {
            biased;
            changed = closed.changed() => {
                match changed {
                    Ok(()) | Err(_) => break,
                }
            }
            changed = writer_stopped_rx.changed() => {
                match changed {
                    Ok(()) | Err(_) => break,
                }
            }
            signal = &mut shutdown => {
                signal?;
                break;
            }
            permit = admission.clone().acquire_owned() => {
                match permit {
                    Ok(permit) => permit,
                    Err(_) => break,
                }
            }
        };
        let read_timeout = if first_frame {
            config.initialization_timeout
        } else {
            config.session_idle_timeout
        };
        let frame = tokio::select! {
            biased;
            changed = closed.changed() => {
                drop(permit);
                match changed {
                    Ok(()) | Err(_) => break,
                }
            }
            changed = writer_stopped_rx.changed() => {
                drop(permit);
                match changed {
                    Ok(()) | Err(_) => break,
                }
            }
            signal = &mut shutdown => {
                drop(permit);
                signal?;
                break;
            }
            frame = timeout(read_timeout, read_frame(&mut reader, max_request_bytes)) => {
                match frame {
                    Ok(frame) => frame?,
                    Err(_) => {
                        drop(permit);
                        break;
                    }
                }
            }
        };
        let Some(frame) = frame else {
            drop(permit);
            break;
        };
        let payload = String::from_utf8(frame)
            .map_err(|_| invalid_data("stdio frame body must be UTF-8 JSON"))?;
        if first_frame {
            first_frame = false;
            let response = daemon.handle_payload(&payload).await;
            if responses.send(response).await.is_err() {
                drop(permit);
                break;
            }
            drop(permit);
            continue;
        }

        let daemon = Arc::clone(&daemon);
        let responses = responses.clone();
        requests.spawn(async move {
            let response = daemon.handle_payload(&payload).await;
            let _ = responses.send(response).await;
            drop(permit);
        });
    }

    let request_result = match timeout(SHUTDOWN_DRAIN_TIMEOUT, async {
        while let Some(result) = requests.join_next().await {
            result
                .map_err(|error| io::Error::other(format!("stdio request task failed: {error}")))?;
        }
        Ok(())
    })
    .await
    {
        Ok(result) => result,
        Err(_) => Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "stdio request drain exceeded its shutdown deadline",
        )),
    };
    if request_result.is_err() {
        requests.abort_all();
        while requests.join_next().await.is_some() {}
    }

    drop(responses);
    let writer_result = match timeout(SHUTDOWN_DRAIN_TIMEOUT, &mut writer_task).await {
        Ok(result) => result
            .map_err(|error| io::Error::other(format!("stdio writer task failed: {error}")))?,
        Err(_) => {
            writer_task.abort();
            Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "stdio response drain exceeded its shutdown deadline",
            ))
        }
    };
    request_result?;
    writer_result
}

async fn read_frame<R>(reader: &mut R, max_body_bytes: usize) -> io::Result<Option<Vec<u8>>>
where
    R: AsyncRead + Unpin,
{
    let mut header = Vec::with_capacity(256);
    let mut byte = [0_u8; 1];
    loop {
        let read = reader.read(&mut byte).await?;
        if read == 0 {
            if header.is_empty() {
                return Ok(None);
            }
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "EOF inside stdio frame header",
            ));
        }
        if header.len() == MAX_HEADER_BYTES {
            return Err(invalid_data("stdio frame header exceeds its byte limit"));
        }
        header.push(byte[0]);
        if header.ends_with(b"\r\n\r\n") {
            break;
        }
    }

    let header = std::str::from_utf8(&header)
        .map_err(|_| invalid_data("stdio frame header must be ASCII"))?;
    if !header.is_ascii() {
        return Err(invalid_data("stdio frame header must be ASCII"));
    }

    let mut names = HashSet::new();
    let mut content_length = None;
    for line in header[..header.len() - 4].split("\r\n") {
        let (name, value) = line
            .split_once(':')
            .ok_or_else(|| invalid_data("malformed stdio frame header"))?;
        if !valid_header_name(name) {
            return Err(invalid_data("malformed stdio frame header name"));
        }
        let normalized_name = name.to_ascii_lowercase();
        if !names.insert(normalized_name.clone()) {
            return Err(invalid_data("duplicate stdio frame header"));
        }
        let value = value.trim_matches([' ', '\t']);
        if value.is_empty() || value.bytes().any(|byte| byte.is_ascii_control()) {
            return Err(invalid_data("malformed stdio frame header value"));
        }
        match normalized_name.as_str() {
            "content-length" => {
                if !canonical_decimal(value) {
                    return Err(invalid_data("Content-Length must be canonical decimal"));
                }
                let length = value
                    .parse::<usize>()
                    .map_err(|_| invalid_data("Content-Length does not fit this platform"))?;
                if length > max_body_bytes {
                    return Err(invalid_data("stdio frame body exceeds its byte limit"));
                }
                content_length = Some(length);
            }
            "content-type" => {
                if !valid_content_type(value) {
                    return Err(invalid_data("Content-Type must identify UTF-8 JSON"));
                }
            }
            "authorization" | "content-encoding" | "eip-session" | "transfer-encoding" => {
                return Err(invalid_data("security-sensitive stdio header is forbidden"));
            }
            _ if normalized_name.starts_with("eip-") => {
                return Err(invalid_data("reserved stdio header is forbidden"));
            }
            _ => {}
        }
    }

    let content_length =
        content_length.ok_or_else(|| invalid_data("Content-Length header is required"))?;
    let mut body = vec![0_u8; content_length];
    reader.read_exact(&mut body).await?;
    Ok(Some(body))
}

async fn write_frame<W>(writer: &mut W, payload: &[u8], max_body_bytes: usize) -> io::Result<()>
where
    W: AsyncWrite + Unpin,
{
    if payload.len() > max_body_bytes {
        return Err(invalid_data("stdio response exceeds its byte limit"));
    }
    let header = format!(
        "Content-Length: {}\r\nContent-Type: {CONTENT_TYPE}\r\n\r\n",
        payload.len()
    );
    writer.write_all(header.as_bytes()).await?;
    writer.write_all(payload).await?;
    writer.flush().await
}

fn canonical_decimal(value: &str) -> bool {
    !value.is_empty()
        && value.bytes().all(|byte| byte.is_ascii_digit())
        && (value == "0" || !value.starts_with('0'))
}

fn valid_header_name(name: &str) -> bool {
    !name.is_empty()
        && name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
}

fn valid_content_type(value: &str) -> bool {
    let mut parts = value.split(';').map(str::trim);
    if !parts
        .next()
        .is_some_and(|media_type| media_type.eq_ignore_ascii_case("application/json"))
    {
        return false;
    }
    match (parts.next(), parts.next()) {
        (None, None) => true,
        (Some(charset), None) => charset.eq_ignore_ascii_case("charset=utf-8"),
        _ => false,
    }
}

fn invalid_data(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(unix)]
async fn shutdown_signal() -> io::Result<()> {
    use tokio::signal::unix::{SignalKind, signal};

    let mut terminate = signal(SignalKind::terminate())?;
    tokio::select! {
        result = tokio::signal::ctrl_c() => result,
        _ = terminate.recv() => Ok(()),
    }
}

#[cfg(not(unix))]
async fn shutdown_signal() -> io::Result<()> {
    tokio::signal::ctrl_c().await
}

#[cfg(test)]
mod tests {
    use std::{future::pending, io, sync::Arc};

    use tokio::io::{AsyncReadExt, AsyncWriteExt, duplex};

    use crate::{config::Config, daemon::Daemon};

    use super::{read_frame, serve_io, write_frame};

    #[tokio::test]
    async fn initialization_timeout_closes_an_idle_transport() {
        let config = Config::for_test("env-test");
        let daemon = Arc::new(Daemon::new(&config).expect("daemon builds"));
        let (_input_client, input_server) = duplex(1024);
        let (output_server, mut output_client) = duplex(1024);

        serve_io(
            input_server,
            output_server,
            daemon,
            &config,
            pending::<io::Result<()>>(),
        )
        .await
        .expect("idle initialization timeout is a clean session close");

        let mut response = Vec::new();
        output_client
            .read_to_end(&mut response)
            .await
            .expect("output pipe closes");
        assert!(response.is_empty());
    }

    #[tokio::test]
    async fn reads_content_length_frame() {
        let (mut client, mut server) = duplex(1024);
        client
            .write_all(
                b"Content-Length: 2\r\nContent-Type: application/json; charset=utf-8\r\n\r\n{}",
            )
            .await
            .expect("fixture writes");

        let body = read_frame(&mut server, 16)
            .await
            .expect("frame is valid")
            .expect("frame is present");
        assert_eq!(body, b"{}");
    }

    #[tokio::test]
    async fn rejects_noncanonical_or_duplicate_lengths() {
        for header in [
            b"Content-Length: 02\r\n\r\n{}".as_slice(),
            b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}".as_slice(),
        ] {
            let (mut client, mut server) = duplex(1024);
            client.write_all(header).await.expect("fixture writes");
            assert!(read_frame(&mut server, 16).await.is_err());
        }
    }

    #[tokio::test]
    async fn rejects_body_length_above_limit_before_body_allocation() {
        let (mut client, mut server) = duplex(1024);
        client
            .write_all(b"Content-Length: 17\r\n\r\n")
            .await
            .expect("fixture writes");

        assert!(read_frame(&mut server, 16).await.is_err());
    }

    #[tokio::test]
    async fn writes_canonical_frame() {
        let (mut client, mut server) = duplex(1024);
        write_frame(&mut client, b"{}", 16)
            .await
            .expect("response writes");
        client.shutdown().await.expect("writer shuts down");

        let mut bytes = Vec::new();
        tokio::io::AsyncReadExt::read_to_end(&mut server, &mut bytes)
            .await
            .expect("response reads");
        assert_eq!(
            bytes,
            b"Content-Length: 2\r\nContent-Type: application/json; charset=utf-8\r\n\r\n{}"
        );
    }
}
