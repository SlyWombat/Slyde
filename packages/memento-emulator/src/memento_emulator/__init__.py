"""memento-emulator — a faithful server-side emulator of the Memento Smart Frame."""

from .renderer import Renderer, effective_canvas, fit_size
from .server import EmulatedFrame
from .state import DEFAULT_CONFIG, FrameState
from .web import EmulatorWeb

__all__ = [
    "DEFAULT_CONFIG",
    "EmulatedFrame",
    "EmulatorWeb",
    "FrameState",
    "Renderer",
    "effective_canvas",
    "fit_size",
]
__version__ = "0.1.0"
