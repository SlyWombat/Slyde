"""Fullscreen 'display' mode — render the frame's current image on a real panel (e.g. a Pi).

Uses pygame/SDL, which talks to KMS/DRM directly so it runs on a console with no desktop. pygame
is an optional dependency (the ``display`` extra) and is imported lazily, so the headless emulator
and CI never need it. The pure geometry helpers below are unit-tested without pygame.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from memento_core.protocol import JsonDict

from .state import FrameState

if TYPE_CHECKING:
    import pygame


def is_portrait(config: JsonDict) -> bool:
    return str(config.get("Orientation", "")).lower().startswith("portrait") or bool(
        config.get("PortraitMode")
    )


def effective_canvas(native: tuple[int, int], *, portrait: bool) -> tuple[int, int]:
    """The logical canvas a panel presents given its native size and mount orientation.

    A 1920x1080 panel is (1920,1080) landscape, (1080,1920) portrait — this is what the frame
    reports as Width x Height so the manager prepares images to match.
    """
    low, high = sorted(native)
    return (low, high) if portrait else (high, low)


def fit_size(src: tuple[int, int], dst: tuple[int, int]) -> tuple[int, int]:
    """Largest size that fits ``src`` inside ``dst`` preserving aspect ratio (contain)."""
    sw, sh = src
    dw, dh = dst
    if sw <= 0 or sh <= 0:
        return dst
    scale = min(dw / sw, dh / sh)
    return max(1, round(sw * scale)), max(1, round(sh * scale))


class Renderer:
    """Blits ``state.current_image`` fullscreen, reflecting the slideshow as it advances."""

    def __init__(self, state: FrameState, *, fps: int = 10) -> None:
        self.state = state
        self._fps = fps
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        import pygame  # lazy: only needed in display mode

        pygame.init()
        pygame.mouse.set_visible(False)
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        native = screen.get_size()
        width, height = effective_canvas(native, portrait=is_portrait(self.state.config))
        # Report the real panel resolution so the manager prepares images to match.
        self.state.update_config({"Width": width, "Height": height})

        clock = pygame.time.Clock()
        shown: str | None = None
        surface: pygame.Surface | None = None
        while not self._stop:
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (
                    event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
                ):
                    self._stop = True
            current = self.state.current_image
            if current != shown:
                shown = current
                surface = self._render_image(current, native)
            screen.fill((0, 0, 0))
            if surface is not None:
                offset = (
                    (native[0] - surface.get_width()) // 2,
                    (native[1] - surface.get_height()) // 2,
                )
                screen.blit(surface, offset)
            pygame.display.flip()
            clock.tick(self._fps)
        pygame.quit()

    def _render_image(self, name: str, screen_size: tuple[int, int]) -> pygame.Surface | None:
        import pygame

        data = self.state.get_photo(name) if name else None
        if not data:
            return None
        try:
            image = pygame.image.load(io.BytesIO(data))
        except Exception:  # a non-image / undecodable blob — show black rather than crash
            return None
        if is_portrait(self.state.config):
            image = pygame.transform.rotate(image, 90)
        return pygame.transform.smoothscale(image, fit_size(image.get_size(), screen_size))
