"""A small, dependency-free web interface that visualizes the emulated frame.

Shows what the physical frame would display — the current image, the albums and the photos on
the frame, and the config — and auto-refreshes so uploads appear live. Uses only the stdlib so
importing the emulator (e.g. as a test fixture) stays lightweight.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from .state import FrameState

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Memento Frame Emulator</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; background:#0b0e14; color:#e2e8f0;
         font-family: ui-sans-serif, system-ui, sans-serif; }
  header { padding:14px 20px; border-bottom:1px solid #222a3a; display:flex;
           align-items:center; gap:12px; }
  .dot { width:10px; height:10px; border-radius:50%; background:#34d399; }
  h1 { font-size:16px; margin:0; font-weight:700; }
  .sub { color:#94a3b8; font-size:12px; }
  main { padding:20px; max-width:1100px; margin:0 auto; }
  .frame { aspect-ratio:3/2; background:#000; border:1px solid #222a3a; border-radius:12px;
           overflow:hidden; display:flex; align-items:center; justify-content:center; }
  .frame img { width:100%; height:100%; object-fit:contain; }
  .empty { color:#475569; font-size:14px; }
  h2 { font-size:13px; color:#94a3b8; text-transform:uppercase; letter-spacing:.05em;
       margin:24px 0 8px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(110px,1fr)); gap:8px; }
  .grid img { width:100%; aspect-ratio:1; object-fit:cover; background:#141925;
              border-radius:8px; }
  .pill { display:inline-block; background:#141925; border:1px solid #222a3a; border-radius:999px;
          padding:3px 10px; margin:0 6px 6px 0; font-size:12px; }
</style></head>
<body>
<header><span class="dot"></span>
  <div><h1 id="name">Memento Frame Emulator</h1>
  <div class="sub" id="meta"></div></div>
</header>
<main>
  <div class="frame" id="frame"><span class="empty">No image displayed</span></div>
  <h2>Albums</h2><div id="albums"></div>
  <h2>On the frame (<span id="count">0</span>)</h2><div class="grid" id="photos"></div>
</main>
<script>
async function refresh() {
  const s = await (await fetch('/api/state')).json();
  document.getElementById('name').textContent = s.name;
  document.getElementById('meta').textContent =
    `fw ${s.config.SoftwareVersion} · ${s.config.ScreenSize}" ${s.config.Orientation}`;
  const frame = document.getElementById('frame');
  frame.innerHTML = s.current_image
    ? `<img src="/photo/${encodeURIComponent(s.current_image)}" alt=""/>`
    : '<span class="empty">No image displayed</span>';
  document.getElementById('albums').innerHTML = s.albums
    .map(a => `<span class="pill">${a.display_name} (${a.images.length})</span>`).join('');
  document.getElementById('count').textContent = s.photos.length;
  document.getElementById('photos').innerHTML = s.photos
    .map(n => `<img src="/photo/${encodeURIComponent(n)}" alt="" loading="lazy"/>`).join('');
}
refresh(); setInterval(refresh, 2000);
</script>
</body></html>
"""


def _make_handler(state: FrameState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # silence default logging
            pass

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/" or self.path.startswith("/index"):
                self._send(200, _PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._send(200, json.dumps(_state_json(state)).encode(), "application/json")
            elif self.path.startswith("/photo/"):
                name = unquote(self.path[len("/photo/") :])
                data = state.photos.get(name.lower())
                if data is None:
                    self._send(404, b"not found", "text/plain")
                else:
                    self._send(200, data, "image/jpeg")
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


def _state_json(state: FrameState) -> dict[str, object]:
    config = {k: v for k, v in state.config.items() if not k.startswith("WiFi")}
    return {
        "name": state.name,
        "config": config,
        "current_image": state.current_image,
        "photos": state.photo_names(),
        "albums": [
            {
                "name": a.name,
                "display_name": a.display_name,
                "reserved": a.reserved,
                "images": a.images,
            }
            for a in state.albums.albums
        ],
    }


class EmulatorWeb:
    """A background HTTP server visualizing a :class:`FrameState`."""

    def __init__(self, state: FrameState, *, host: str = "0.0.0.0", port: int = 8099) -> None:
        self._server = ThreadingHTTPServer((host, port), _make_handler(state))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> EmulatorWeb:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
