"""Process-local hints for the durable Run queue; polling remains authoritative."""

from anyio import Event


class QueueWakeups:
    """Coalesce committed work notifications without losing a scan/wait race."""

    def __init__(self) -> None:
        self._changed = Event()

    def watch(self) -> Event:
        """Capture before scanning, so a commit during the scan also wakes the wait."""
        return self._changed

    def notify(self) -> None:
        changed, self._changed = self._changed, Event()
        changed.set()
