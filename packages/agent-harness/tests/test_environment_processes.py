from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from converge_agent_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    ArgvCommand,
    CommandEnvironment,
    CommandLimits,
    CommandRequest,
    DirectLocalEnvironmentConfiguration,
    DirectLocalEnvironmentProviderBinding,
    DirectLocalPortPolicy,
    DirectLocalProcessPolicy,
    DirectLocalRootConfiguration,
    DirectLocalShellProfile,
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentError,
    EnvironmentOutputPolicy,
    EnvironmentPermissionSet,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    PortTarget,
    ShellCommand,
    create_environment_run_binding,
)

pytestmark = pytest.mark.anyio


def _instance() -> AgentInstanceContext:
    return AgentInstanceContext(
        identity=AgentIdentityRef(issuer="test", subject="agent"),
        agent_instance_id="agent-1",
    )


def _binding(
    root: Path,
    *,
    executables: frozenset[Path] = frozenset(),
    environment_keys: frozenset[str] = frozenset(),
    shell_profiles: tuple[DirectLocalShellProfile, ...] = (),
    ports: DirectLocalPortPolicy | None = None,
):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="local-process",
            root=DirectLocalRootConfiguration(path=root, ownership="caller_owned"),
            shell_profiles=shell_profiles,
            processes=DirectLocalProcessPolicy(
                allowed_executables=executables,
                allowed_environment_keys=environment_keys,
                max_concurrent_processes=2,
                max_wall_time_seconds=2,
                terminate_grace_seconds=0.02,
            ),
            ports=ports or DirectLocalPortPolicy(),
        )
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=1,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=provider,
            ),
        ),
        default_binding_id="binding-1",
    )
    return create_environment_run_binding(
        initial_topology=request,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


def _output_policy(*, overflow: str = "truncate") -> EnvironmentOutputPolicy:
    return EnvironmentOutputPolicy(
        max_inline_bytes=64,
        max_output_bytes=1024,
        overflow=overflow,
    )


async def test_foreground_argv_uses_minimal_environment_and_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path(sys.executable).resolve()
    monkeypatch.setenv("AMBIENT_SECRET", "must-not-leak")
    binding = _binding(
        tmp_path,
        executables=frozenset({executable}),
        environment_keys=frozenset({"EXPLICIT"}),
    )
    script = (
        "import os,sys; "
        "sys.stdout.write(os.getenv('AMBIENT_SECRET', 'missing') + '|' + os.getenv('EXPLICIT', '')); "
        "sys.exit(7)"
    )
    request = CommandRequest(
        command=ArgvCommand(executable=str(executable), arguments=("-c", script)),
        environment=CommandEnvironment(set={"EXPLICIT": "visible"}),
        output_policy=_output_policy(),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        result = await environment.shell.exec(request)
        assert result.status.phase == "exited"
        assert result.status.exit_code == 7
        assert result.output.stdout.inline == b"missing|visible"


async def test_shell_profile_is_explicit_and_unlisted_executable_is_denied(tmp_path: Path) -> None:
    shell = Path("/bin/sh").resolve()
    binding = _binding(
        tmp_path,
        executables=frozenset({shell}),
        shell_profiles=(DirectLocalShellProfile(profile_id="posix", executable=shell),),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        result = await environment.shell.exec(
            CommandRequest(
                command=ShellCommand(profile_id="posix", script="printf profile"),
                output_policy=_output_policy(),
            )
        )
        assert result.output.stdout.inline == b"profile"

        with pytest.raises(EnvironmentError) as denied:
            await environment.shell.exec(
                CommandRequest(
                    command=ArgvCommand(executable=str(Path(sys.executable).resolve())),
                    output_policy=_output_policy(),
                )
            )
        assert denied.value.code == "environment_denied"


async def test_foreground_retained_output_remains_readable(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))
    policy = EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=64, overflow="retain")
    request = CommandRequest(
        command=ArgvCommand(
            executable=str(executable),
            arguments=("-c", "import sys; sys.stdout.buffer.write(b'x' * 20)"),
        ),
        output_policy=policy,
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        result = await environment.shell.exec(request)
        reference = result.output.stdout.reference
        assert reference is not None
        assert result.output.stdout.captured_bytes == 20
        read = await environment.outputs.read(reference, policy=policy)
        assert read.chunks[0].data == b"xxxx"
        await environment.outputs.release(reference=reference)


async def test_background_stdin_wait_kill_and_release(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        echo = await environment.processes.start(
            CommandRequest(
                command=ArgvCommand(
                    executable=str(executable),
                    arguments=("-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
                ),
                initial_stdin=b"input",
                output_policy=_output_policy(),
            )
        )
        completed = await environment.processes.wait(
            echo.process.handle,
            condition="tree_cleaned",
            timeout_seconds=1,
        )
        assert completed.output.stdout.inline == b"input"
        await environment.processes.release(echo.process.handle)

        long_running = await environment.processes.start(
            CommandRequest(
                command=ArgvCommand(
                    executable=str(executable),
                    arguments=("-c", "import time; time.sleep(30)"),
                ),
                keep_stdin_open=True,
                output_policy=_output_policy(),
            )
        )
        killed = await environment.processes.kill(long_running.process.handle)
        assert killed.process.status.cleanup == "complete"
        assert killed.process.status.phase == "signaled"
        await environment.processes.release(long_running.process.handle)


async def test_command_wall_timeout_is_provider_owned_and_finite(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))
    request = CommandRequest(
        command=ArgvCommand(
            executable=str(executable),
            arguments=("-c", "import time; time.sleep(30)"),
        ),
        limits=CommandLimits(wall_time_seconds=0.05),
        output_policy=_output_policy(),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        result = await environment.shell.exec(request)
        assert result.status.phase == "timed_out"
        assert result.status.termination_reason == "timeout"


async def test_active_process_handle_fences_topology_retirement(tmp_path: Path) -> None:
    executable = Path(sys.executable).resolve()
    binding = _binding(tmp_path, executables=frozenset({executable}))
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        started = await environment.processes.start(
            CommandRequest(
                command=ArgvCommand(
                    executable=str(executable),
                    arguments=("-c", "import time; time.sleep(30)"),
                ),
                limits=CommandLimits(wall_time_seconds=2),
                output_policy=_output_policy(),
            )
        )
        removal = EnvironmentTopologyRequest(topology_version=2, bindings=(), default_binding_id=None)
        with pytest.raises(EnvironmentError) as in_use:
            await binding.controller.apply(removal)
        assert in_use.value.code == "topology_in_use"
        assert environment.topology.topology_version == 1

        await environment.processes.kill(started.process.handle)
        await environment.processes.release(started.process.handle)
        change = await binding.controller.apply(removal)
        assert change.current_version == 2
        assert environment.topology.bindings == ()


async def test_loopback_port_observation_is_explicitly_allowlisted(tmp_path: Path) -> None:
    server = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0)
    socket = server.sockets[0]
    port = socket.getsockname()[1]
    binding = _binding(
        tmp_path,
        ports=DirectLocalPortPolicy(enabled=True, allowed_ports=frozenset({port})),
    )
    try:
        async with binding.bind(run_id="run-1", instance=_instance()) as environment:
            observed = await environment.ports.inspect(PortTarget(port=port))
            assert observed.status == "listening"
            with pytest.raises(EnvironmentError) as denied:
                await environment.ports.inspect(PortTarget(port=port + 1))
            assert denied.value.code == "environment_denied"
    finally:
        server.close()
        await server.wait_closed()
