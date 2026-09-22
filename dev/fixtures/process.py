"""Socket ownership and bounded lifecycle for local HTTP fixtures."""

import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx2


@contextmanager
def fixture_process(module: str, *, port: int = 0, arguments: tuple[str, ...] = ()):
    """Own a real fixture subprocess and its bound listener through cleanup."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))
        listener.listen()
        port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-m", module, "--fd", str(listener.fileno()), *arguments],
            pass_fds=(listener.fileno(),),
            cwd=Path(__file__).resolve().parents[2],
            stdout=subprocess.DEVNULL,
        )
        try:
            with httpx2.Client(trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Local fixture process exited during startup")
                    try:
                        response = client.get(f"http://127.0.0.1:{port}/healthz")
                        response.raise_for_status()
                        break
                    except httpx2.HTTPError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Local fixture did not become ready") from None
                        time.sleep(0.1)
            yield f"http://127.0.0.1:{port}"
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
