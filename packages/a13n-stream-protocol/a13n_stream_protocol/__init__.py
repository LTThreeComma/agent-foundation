"""Shared stream-protocol boundary for Agent Foundation surfaces."""

from importlib.metadata import version

from a13n_stream_protocol.fragments import CustomEventAssembler, fragment_custom_event
from a13n_stream_protocol.messages import ContentMetadata, InputSource, input_source
from a13n_stream_protocol.observer import (
    AguiEventProcessor,
    AguiObservationError,
    HarnessAguiObserver,
)

__version__ = version("a13n-stream-protocol")

__all__ = [
    "AguiEventProcessor",
    "AguiObservationError",
    "ContentMetadata",
    "CustomEventAssembler",
    "HarnessAguiObserver",
    "InputSource",
    "__version__",
    "fragment_custom_event",
    "input_source",
]
