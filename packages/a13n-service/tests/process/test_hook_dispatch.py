import anyio
import pytest
from a13n_service.app import create_app
from a13n_service.background import Sweep
from a13n_service.settings import ProcessRole


@pytest.mark.anyio
@pytest.mark.parametrize("role", tuple(ProcessRole))
async def test_hook_dispatch_runs_only_in_control_capable_roles(local_settings, tmp_path, monkeypatch, role):
    started = anyio.Event()
    calls = 0

    async def scan(dispatcher):
        nonlocal calls
        calls += 1
        started.set()
        return Sweep()

    monkeypatch.setattr("a13n_service.hooks.dispatcher.HookDispatcher.scan", scan)
    app = create_app(local_settings(tmp_path / role.value, role=role))
    async with app.router.lifespan_context(app):
        if role in {ProcessRole.all, ProcessRole.control}:
            with anyio.fail_after(10):
                await started.wait()
        else:
            assert not started.is_set()
        app.state.runtime.begin_drain()
        await anyio.wait_all_tasks_blocked()
    assert calls == int(role in {ProcessRole.all, ProcessRole.control})
