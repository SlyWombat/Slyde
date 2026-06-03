# memento-core

Local-network client library for the **Memento Smart Frame**, built from a clean-room
reverse-engineering of the discontinued official app. Pure Python, no cloud, LAN-only.

See [`docs/protocol.md`](../../docs/protocol.md) for the wire protocol.

```python
from memento_core import FrameClient, discover

frames = discover()                      # UDP broadcast; returns FrameInfo list
with FrameClient(frames[0].ip) as frame:
    print(frame.get_config()["Name"])
    frame.upload_image("photo.jpg", "vacation01.jpg")
```

Nothing is hardcoded to any particular deployment — the frame is located by discovery or an
explicit host you pass in.
