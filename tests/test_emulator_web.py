"""The emulator's visual web interface."""

from __future__ import annotations

import json
import urllib.request

from memento_emulator import EmulatorWeb, FrameState


def test_web_serves_state_and_photos() -> None:
    state = FrameState(name="WebFrame", ip="127.0.0.1")
    state.add_photo("a.jpg", b"\xff\xd8\xffDATA\xff\xd9")
    web = EmulatorWeb(state, host="127.0.0.1", port=0).start()
    try:
        base = f"http://127.0.0.1:{web.port}"
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "Memento Frame Emulator" in page

        state_json = json.load(urllib.request.urlopen(base + "/api/state"))
        assert state_json["name"] == "WebFrame"
        assert "a.jpg" in state_json["photos"]
        assert "WiFiPSWD" not in state_json["config"]

        photo = urllib.request.urlopen(base + "/photo/a.jpg").read()
        assert photo.startswith(b"\xff\xd8\xff")
    finally:
        web.stop()
