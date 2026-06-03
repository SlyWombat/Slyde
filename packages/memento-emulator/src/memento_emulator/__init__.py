"""memento-emulator — a faithful server-side emulator of the Memento Smart Frame."""

from .server import EmulatedFrame
from .state import DEFAULT_CONFIG, FrameState
from .web import EmulatorWeb

__all__ = ["DEFAULT_CONFIG", "EmulatedFrame", "EmulatorWeb", "FrameState"]
__version__ = "0.1.0"
