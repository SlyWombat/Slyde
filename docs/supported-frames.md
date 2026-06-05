# Supported frames

Slyde is a **frame-revival toolkit**: it drives digital photo frames from your own
[Immich](https://immich.app) library, with no cloud. Every frame is added behind a pluggable
`FrameBackend` (see [`frame-backends.md`](frame-backends.md)), so support grows over time without
forking. This page tracks which frames work today — and how to add yours.

> **Got a dead or cloud-locked frame?** [Open an issue](https://github.com/SlyWombat/Slyde/issues/new)
> with the make/model and how it talks to its app. Even a teardown photo or a packet capture helps.

## Status

| Frame | Status | How Slyde drives it | Panel |
|---|---|---|---|
| **Memento Smart Frame** (Electric Objects) | ✅ **Supported** | Reverse-engineered **LAN protocol** (connected backend) — no cloud, no account | Full-colour LCD |
| **DIY Pi soft-frame** | ✅ **Supported** | The emulator run fullscreen on a Raspberry Pi (SDL/KMS); Slyde treats it like a real frame, incl. OTA | Any HDMI display |
| **Aluratek 13.3" ePaper** (model `AEINK13F`, a **Sungale** white-label) | 🟡 **In progress** | **Cloud impersonation** (served backend) — the frame polls a server Slyde runs, fed from Immich | Colour e-paper (Spectra-6) |
| **Other Sungale / "xiaowooya"-app frames** | 🟡 **Likely** | Same served backend, region/brand-parameterized | varies |
| **Your frame?** | ⬜ **Wanted** | — | — |

## Is my frame revivable?

Most consumer frames fall into one of four buckets. Find yours, and you'll know the path (and which
backend it becomes):

1. **It runs Android and exposes ADB** (many cheap WiFi touchscreen frames — Frameo, Nexfoto, some
   Aluratek). **Easiest:** enable developer mode → ADB → sideload an app / run the Slyde soft-frame.
   No protocol work needed.
2. **It talks a proprietary LAN protocol to a desktop/mobile app** (Memento). **Reverse-engineer the
   LAN protocol** → a *connected* backend (the manager pushes photos to the frame). This is the
   hardest but most rewarding path — see [`protocol.md`](protocol.md) for how the Memento one was done.
3. **It polls a vendor cloud over HTTP** (battery e-paper frames; many "send from anywhere" frames —
   Aluratek/Sungale eFrame). **Impersonate the cloud**: redirect its hostname to a Slyde server (DNS
   rewrite) → a *served* backend. Works even for frames offline for days. See
   [`../experiments/aluratek-eframe/`](../experiments/aluratek-eframe/).
4. **Sealed MCU / e-ink with no ADB and TLS-pinned cloud.** If it polls a cloud you can intercept,
   path 3 still applies. Otherwise it's a firmware/hardware project (dump + reflash) — case by case.

**Quick triage:** does a phone app drive it? MITM the app (it reveals the API). Does it have a USB
data port + settings? Try ADB. Is it battery/e-ink? It almost certainly polls a cloud (path 3).

## Adding a frame

1. Read [`frame-backends.md`](frame-backends.md) — the `FrameBackend` interface and the
   connected-vs-served split.
2. Implement a backend for your frame; register it; add a conformance test.
3. Open a PR and add your frame to the table above.

The **Memento** backend is the reference *connected* frame; the **Sungale/eFrame** work is the
reference *served* (cloud-impersonation) frame.

## Communities & prior art

Owners of discontinued/cloud-locked frames who've been trying to revive them by hand — good places
to ask, share, and find collaborators:

- **Memento:** [DPReview "Anyone here own a Memento Smart Frame?"](https://www.dpreview.com/forums/thread/4471198),
  the OpenFrame Discourse "ever hack a Memento?" thread.
- **General frame hacking / e-waste:** Hackaday, r/digitalframe, r/selfhosted, r/ReverseEngineering.
- Nixplay / Pix-Star / Aura jailbreak communities (different vendors, same "the cloud died" problem).

Not affiliated with or endorsed by any frame manufacturer. Interoperability work for hardware you own.
