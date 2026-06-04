# Memento Soft Frame (Pi display appliance)

Runs the Memento frame **in display mode**: fullscreen slideshow on the attached screen, speaking
the real Memento LAN protocol so the Manager treats it exactly like a hardware frame. Same codebase
as the test emulator — just `--mode display` (which adds the SDL renderer).

## What you need
- A Raspberry Pi (or any Linux SBC) running a **console-only** OS (Raspberry Pi OS **Lite** — no
  desktop), with a screen on HDMI.
- Network access to your LAN (so the Manager can reach it) and to Immich (indirectly, via syncs the
  Manager pushes).

## Install
```sh
sudo ./install.sh                 # uses the public repo
# or: sudo ./install.sh https://github.com/SlyWombat/memento-manager.git
```
This creates a `memento` service user, a venv at `/opt/memento-frame`, installs
`memento-emulator[display]` (pulls pygame/SDL), persists state under `/var/lib/memento-frame`, and
enables the `memento-frame` systemd service (autostart on boot, restart on crash).

Logs: `journalctl -u memento-frame -f`

## How it behaves
- **Fullscreen render** straight to KMS/DRM (`SDL_VIDEODRIVER=kmsdrm`) — no X/Wayland.
- On start it **detects the panel resolution** and reports it (Width/Height) so the Manager prepares
  photos to the exact size/aspect (see the per-frame-canvas behaviour).
- Runs the slideshow (DisplayTime / Shuffle / DisplayOn) and reflects Next/Previous/DisplayImage.
- Persists config + photos, so a reboot keeps your library and settings.

## Orientation
Mount portrait? Set **Orientation = Portrait** (or PortraitMode) from the Manager's settings. The
renderer rotates the image 90° and reports the swapped Width/Height, so subsequent syncs arrive
correctly proportioned. (No app restart needed.)

## Notes / tuning
- If the screen blanks, the unit already tries `setterm --blank 0`; on some setups you may also want
  `consoleblank=0` on the kernel cmdline.
- The web debug UI is still available on `:8099` (disable with `--web-port 0` in the unit).
- Updates are managed from the Manager (Firmware tab) — see the main docs.
