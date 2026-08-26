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

If Slyde is killed mid-interlude, the frame is left parked. The next startup detects that (the
restore data is stored durably) and puts the frame back before doing anything else. A clean
shutdown restores immediately.

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
