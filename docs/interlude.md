# Interlude — put your own image in the rotation, every other photo

> **What this is.** Slyde can show one **extra image between the photos** on a Memento frame, so
> the frame reads `photo → your image → the next photo → your image → …`. Slyde does not draw that
> image. **Another program does** — anything that can write a file. Write new bytes whenever you
> like (every minute, every second, on an event) and the frame picks them up on its next turn.
> Delete the file and the frame goes straight back to being an ordinary photo frame.
>
> Applies to the **Memento LAN frame** (and the Pi soft-frame). Not e-paper frames — see
> [Why not e-paper](#why-not-e-paper). Design + rationale: issue #70.

---

## The two-minute version

```bash
# 1. Ask Slyde where to write (per frame). The frame id is what /api/frames returns.
curl -s http://slyde:8080/api/frames/192.168.10.113/interlude | jq -r .image_path
# -> /data/cache/interlude/192.168.10.113.img

# 2. Turn it on.
curl -X PUT http://slyde:8080/api/frames/192.168.10.113/interlude \
     -H 'content-type: application/json' -d '{"enabled": true}'

# 3. Have your program write that path. Whenever. As often as it likes.
my-dashboard-renderer --out /tmp/board.png && mv /tmp/board.png /data/cache/interlude/192.168.10.113.img

# 4. Done — the frame now alternates photo / your image / photo / your image.
#    To stop:
rm /data/cache/interlude/192.168.10.113.img
```

Step 4 is the important one: **removing the file is the off switch.** The frame restores its own
slide time and shuffle and resumes its normal slideshow, on its own, within a few seconds. Put the
file back and interludes resume. Your program never has to tell Slyde anything.

---

## The contract

| | |
|---|---|
| **Where** | `GET /api/frames/{frame_id}/interlude` → `image_path`. Default `<CACHE_DIR>/interlude/<frame-id>.img`; override the directory with `INTERLUDE_DIR`, or point at your own path per frame (`source_ref`). |
| **What** | Any image Pillow can open — PNG, JPEG, WebP, BMP, GIF. Extension and dimensions don't matter; Slyde fits it to the panel. |
| **When** | Whenever you want. Slyde re-reads on a poll (`INTERLUDE_POLL_SECONDS`, default 5s) and uploads to the frame only when the bytes actually changed. |
| **Removing** | Delete the file (or `DELETE …/interlude/image`). Normal slideshow resumes. |
| **Size** | Fitted to the frame's canvas (3240×2160 on a Memento). Send at least that, or it'll be upscaled. |

### Write atomically

Slyde may read the file at any moment, including the moment you're rewriting it. **Write to a temp
file in the same directory and `rename()` it over the target** — rename is atomic, so a reader sees
either the whole old image or the whole new one:

```python
import os, tempfile


def publish(path: str, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)  # atomic
```

```bash
# shell equivalent
render > "$IMG.tmp" && mv -f "$IMG.tmp" "$IMG"
```

If you don't, Slyde is still safe — it decode-checks every read and treats a partial image exactly
like a missing one, keeping the last good picture on the frame — but you'll occasionally skip a
refresh for no reason.

---

## Running the producer in its own container

The usual shape: your producer is a **sibling container on the same Docker host**. Share one
directory between it and Slyde, and the file drop works unchanged.

```yaml
services:
  slyde:
    image: slyde:latest
    env_file: .env                 # INTERLUDE_DIR=/interlude
    volumes:
      - slyde-data:/data
      - interlude:/interlude       # <- shared

  my-board:                        # your renderer, whatever it is
    image: my-board:latest
    restart: unless-stopped
    volumes:
      - interlude:/out             # <- same volume, its own mount point
    environment:
      # ask Slyde once for the exact filename; it is the frame's id, not its IP (see below)
      TARGET: /out/b3f1c2de-4a55-4f0e-9a11-7c2d5e8f0a12.img

volumes:
  slyde-data:
  interlude:
```

A bind mount (`- /data/interlude:/interlude`) works just as well and is easier to inspect from the
host. **If the producer runs on a different host from Slyde**, don't try to share a filesystem —
use the HTTP `PUT` below, or have Slyde pull with a `url` source.

## Two other ways in

**Push over HTTP** — no shared filesystem needed (handy from another host or container). Slyde
writes the drop file for you, atomically, and rejects anything that isn't a decodable image:

```bash
curl -X PUT --data-binary @board.png \
     -H 'content-type: image/png' \
     http://slyde:8080/api/frames/192.168.10.113/interlude/image
```

**Let Slyde pull** — if your producer already serves the image over HTTP:

```bash
curl -X PUT http://slyde:8080/api/frames/192.168.10.113/interlude \
  -H 'content-type: application/json' \
  -d '{"enabled": true, "source_kind": "url", "source_ref": "http://grafana:3000/render/d/abc?width=3240&height=2160"}'
```

A URL that stops answering behaves the same as a deleted file: normal slideshow resumes.

---

## Settings

`PUT /api/frames/{frame_id}/interlude`, all optional:

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Master switch for this frame. |
| `source_kind` | `"file"` | `file` (a path you write) or `url` (Slyde GETs it). |
| `source_ref` | `""` | The path or URL. Empty = Slyde's managed drop path (`image_path`). |
| `every_n_photos` | `1` | Interlude after every N photos. `1` = every other image. |
| `dwell_seconds` | `0` | How long it stays up. `0` = the same as a photo. |
| `fit` | `"contain"` | `contain` / `cover` / `blur` / `smart`. **Defaults to `contain`** — a dashboard or clock must not be cropped, unlike a photo. |

`GET` returns those plus live state:

| Field | Meaning |
|---|---|
| `state` | `idle` (off) · `engaged` (interludes are running) · `standby` (on, but no usable image — **the frame is running its own slideshow**) · `unsupported` |
| `detail` | Why, in words — e.g. `no interlude image at /data/…`, `panel is off / night mode` |
| `image_present` | Whether the source is there right now |
| `last_photo` | The photo the rotation will resume from |

`GET …/interlude/preview` renders the current image exactly as the frame will show it (fitted to
the panel) — useful while you're developing a producer, and it displays nothing on the frame.

---

## What Slyde does behind the scenes

Worth knowing, because it explains the failure modes.

A Memento frame runs its slideshow **itself**, on its own timer, and offers no "tell me when you
change picture" event. So to insert anything between photos, Slyde has to take the wheel: while
interludes are running it **parks the frame's own slide timer** and drives every transition
explicitly. Two consequences you can see from the outside:

- **Slyde owns the order while engaged.** The frame's own shuffle is switched off (it would fight
  the cursor) and restored when interludes stop. Photo order comes from your Library.
- **Slyde must be running.** That's exactly why "no image" means *stand down*, not *pause*: any
  time there's nothing to show — file deleted, URL down, panel off, night mode, no photos delivered
  yet — Slyde restores the frame's own slide time and shuffle, leaves a real photo on screen, and
  lets the frame run itself. It keeps watching, and re-engages when the image comes back.

**If Slyde dies mid-interlude, the frame recovers by itself.** The frame's timer isn't switched off
— it's set to a value comfortably longer than Slyde's own cadence, and every command Slyde sends
restarts that countdown. So while Slyde is alive the timer never fires; if Slyde stops, nothing
re-arms it, and a couple of minutes later the frame simply resumes its own slideshow. Nothing to
restart, nothing stuck on the wall. (Both facts this relies on — that a display command restarts
the countdown, and that the frame accepts any number of seconds rather than just the app's 15
choices — were measured on a real fw 6.02 frame; see `protocol.md`.)

On top of that, Slyde writes the frame's original settings down before touching them, so a restart
restores them exactly, and a clean shutdown restores immediately. Set `INTERLUDE_PARK_SECONDS=2419200`
if you'd rather the frame hold the last image until Slyde comes back.

**The image is uploaded while a photo is on screen**, into one of two alternating buffer files
(`slyde-interlude-a.jpg` / `-b.jpg`), so an upload never overwrites the file the frame is
displaying, and what appears is the newest bytes you published — not a copy fetched a slide ago.
Identical bytes are never re-uploaded.

Those two buffer files are **Slyde-reserved**: they never enter your Library, never appear as the
frame's picture in the UI, and are never treated as photos to prune. They are also deleted from the
device on *every* stand-down — withdrawn image, panel off, manager restart — because anything Slyde
uploads joins the frame's own "all photos" album, which is exactly the album the frame's own
slideshow cycles. Leaving a buffer behind would put a stale dashboard among your pictures with
nothing running to explain it. So whenever interludes aren't active, the frame is exactly as if it
had never had one.

---

## On the Pi soft-frame

The soft-frame is the emulator running fullscreen on a Pi, driven by the same `memento-lan`
backend — so interludes work there identically, and it's the easy place to try this before pointing
it at the real frame. Two practical differences:

- **Its id is usually its IP**, not a GUID, because the emulator reports a placeholder GUID. So the
  drop file is typically `192.168.x.y.img`. As always, don't construct it — read `image_path` from
  `GET /api/frames/{id}/interlude`.
- **The canvas is whatever you configured the soft-frame to render at**, not 3240x2160, so size
  your source image to that panel.

## Why not e-paper

The Aluratek/Sungale and SwitchBot frames declare `interludes: false` and the API refuses to enable
it (`409`). Two independent reasons:

1. **The panel.** A Spectra-6 e-paper refresh is ~15–30 seconds of visible flashing and consumes a
   slice of a finite lifetime refresh budget. Doubling the redraw rate to show a clock is the wrong
   trade on a panel that exists to sit still for hours.
2. **The transport.** Those frames *poll us* (or are pushed to through a vendor cloud) on their own
   schedule — hours or days apart. There is no "show this image now" command to conduct with.

A future full-colour LAN frame gets interludes by setting one capability flag; nothing else in the
engine changes.

---

## Recipes

**A clock, every minute, with cron:**

```cron
* * * * * /usr/local/bin/render-clock --size 3240x2160 --out /tmp/c.png && mv -f /tmp/c.png /data/cache/interlude/192.168.10.113.img
```

**A weather board from a Python service:**

```python
import time, os, tempfile
from PIL import Image, ImageDraw

TARGET = "/data/cache/interlude/192.168.10.113.img"


def publish(img: Image.Image) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(TARGET), suffix=".png")
    with os.fdopen(fd, "wb") as f:
        img.save(f, format="PNG")
    os.replace(tmp, TARGET)


while True:
    board = Image.new("RGB", (3240, 2160), "black")
    ImageDraw.Draw(board).text((160, 160), forecast_text(), fill="white")
    publish(board)
    time.sleep(60)
```

**Only during the day** — no special support needed; it falls out of the contract:

```bash
if [ "$(date +%H)" -ge 8 ] && [ "$(date +%H)" -lt 22 ]; then
  render > "$IMG.tmp" && mv -f "$IMG.tmp" "$IMG"     # interludes on
else
  rm -f "$IMG"                                       # plain photo slideshow overnight
fi
```

---

## Troubleshooting

| Symptom | What it means |
|---|---|
| `state: "standby"`, `detail: "no interlude image at …"` | Slyde can't see your file. Check the path from `GET …/interlude` and that the Slyde container can read it (bind-mount `INTERLUDE_DIR`). |
| `state: "standby"`, `detail: "not a decodable image"` | Partial write, or a format Pillow can't open. Write atomically (above). |
| `state: "standby"`, `detail: "no photos delivered to this frame yet"` | There's nothing to alternate *with*. Curate some photos first. |
| `state: "standby"`, `detail: "panel is off / night mode"` | Deliberate — Slyde won't push at a frame the owner switched off. |
| `state: "engaged"` but nothing changes on the frame | The frame may be asleep/unreachable; `detail` says `frame unreachable`. |
| Interlude looks squashed or cropped | Set `fit`. `contain` (the default) never crops; `cover` fills and crops. |
| The frame stopped changing picture at all | Slyde was killed while engaged. Restart it — startup restores the frame. |

## See also

- [`architecture.md`](architecture.md) — ADR-011, where this sits in the design
- [`protocol.md`](protocol.md) — `DisplayImage`, `ChangePictureDuration`, and the duration ladder
- Issue **#70** — the full design, the alternatives considered, and the open hardware questions
