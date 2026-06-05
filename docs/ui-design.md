# UI design — the multi-frame fleet manager

Slyde's backend is multi-frame (registry, connected + served backends, guaranteed delivery,
per-frame processing); the **UI is still single-frame** and is the gap. This is the design for a
world-class layout that manages a *fleet* of 3–10 heterogeneous frames (some offline for days),
grounded in the real components and endpoints. Tracked by the UI epic; build order at the end.

Stack: React + TS + Vite + Tailwind + TanStack Query. Palette: ink `#0b0e14`, panel `#141925`, edge
`#222a3a`, accent `#5b8cff`.

## 1. Why the current UI doesn't scale

The app is a **single-frame console bolted onto a discovery list** (`App.tsx` holds one
`host: string`; `FramePicker` → `FrameView`). For a fleet it breaks:

- The only fleet-aware view (`StatusPanel`) is a muted footer that **hides itself** on 0 frames or
  any error — the one screen that works for offline/served frames is the one you never land on.
- `FramePicker` lists/selects by **LAN IP** (`GET /frames`), so a **served** (cloud) frame — no host,
  possibly asleep 3 days — can't even appear, and the empty state misleads its owner.
- Everything downstream is `host`-keyed; the registry/library/status plane is `id`-keyed. There's
  **no bridge** — a served frame literally can't be opened (every panel 503s with no host to connect).
- The whole model is "push to a live, reachable frame" (`FramePanel` online pill, Prev/Next, live
  current-image, "Frame unavailable" hard-fail). For a served e-ink frame that's the *normal* state,
  not an error. It ignores the `PUT /frames/{id}/library` + guaranteed-delivery contract entirely.
- Two contradictory "what's on the frame" models — live device albums (`FrameAlbums`/`AddPhotos`)
  vs the declared **library** (the transport-agnostic, offline-tolerant one that has *no UI*).
- No frame heterogeneity in the UI (LCD vs e-ink treated identically), no per-frame **processing
  profile** affordance, fleet OTA, search/filter/group, multi-frame curation, router/deep-links, or
  fleet health roll-up.

**The inversion:** become **fleet-first**, built on the `id`-keyed registry/status/library plane,
where the connected-frame live console is *one capability of one kind of frame*, not the whole app.

## 2. Information architecture

**Identity:** `frame.id` (from `/frames/status`) is canonical everywhere. A connected frame's `id`
*is* its host, so `id`-keyed routes call the existing host endpoints transparently; served frames
carry only an `id` and never call host endpoints. A client helper gates capabilities off
`interaction` (connected vs served).

**Persistent shell** (introduce react-router) — left rail (desktop) / bottom tabs (mobile):

```
Fleet     /            fleet health + frame cards + activity strip   (home)
Frames    /frames      all frames: search, filter by kind/backend/health; onboard
          /frames/:id  frame detail (tabs: Overview · Library · Albums · Settings · Firmware)
Curate    /curate      Immich-first: pick photos/album → target frame(s) → set library
Activity  /activity    delivery queue, subscriptions, sync/OTA log (read-only)
Settings  /settings    Immich, scheduler, firmware registry, fleet OTA, app
```

**Core journeys:** fleet at a glance · onboard a frame (LAN scan / cloud frame-code) · curate an
album to one *or several* frames · monitor delivery/health incl. offline frames · per-frame
settings/orientation/**processing** · firmware/OTA across the fleet · manage a served frame you never
connect to.

## 3. The layout (wireframes)

### (a) Fleet dashboard — `/`
```
┌────────┬──────────────────────────────────────────────────────────────────────┐
│ ◈ Slyde│  Fleet                                                  [＋ Add frame] │
│ ◈Fleet │  4 frames · 3 healthy · 1 needs attention            Immich ● connected│
│ ▣Frames│ ┌ ⚠  "Aluratek e-ink" has 3 failed deliveries.       [View] [Retry] ┐ │ ← only if needed
│ ✦Curate│ └──────────────────────────────────────────────────────────────────┘ │
│ ↻Activ.│  ┌─ Living Room ───────────┐  ┌─ Aluratek e-ink ────────┐             │
│ ⚙Settin│  │ [   live preview img  ] │  │ [  last render (dith.) ] │             │
│ ─────  │  │  ● online · LAN         │  │  ◐ asleep · Cloud        │             │
│ ✓Health│  │  Memento 13" · fw 6.02  │  │  e-ink Spectra-6 · 13.3" │             │
│        │  │  ▓▓▓▓░ 124 delivered    │  │  3 pending · ⚠ 3 failed  │             │
│        │  │  [Open]  [Curate ＋]    │  │  seen 2d ago (~3d cycle) │             │
│        │  └─────────────────────────┘  └──────────────────────────┘            │
│        │  Recent activity ────────────────────────────────────────────────────│
│        │   12:04 Living Room ✓ delivered 6   11:58 Aluratek ⚠ retry in 4m …    │
└────────┴──────────────────────────────────────────────────────────────────────┘
```
Each card is **kind-aware**: connected → live preview (`/current`) + "online"; served → last prepared
render + "asleep — healthy". Attention banner renders only when something `failed`. Quietly confident:
clean when healthy, loud only when needed.

### (b) Frame detail — `/frames/:id` (tabbed, capability-gated)
```
‹ Frames   Living Room        ● online · LAN · Memento 13" · fw 6.02
┌Overview─┬─Library─┬─Albums─┬─Settings─┬─Firmware─┐
│  [ live preview ]   At a glance: Showing IMG_2841 · Library 124 · ▓▓▓▓▓ 124✓ 0pend │
│  ‹ Previous  Next ›  Slide 60s · Shuffle on · Profile Full-colour LCD · seen now   │
└ (served variant: last render; no Prev/Next; "polls ~every 3 days"; profile e-ink)  ┘
```
**Library tab** (the new transport-agnostic surface, works for served frames): tiles of the desired
set, each with a delivery dot (✓ delivered / ◐ pending / ⚠ failed); drag to reorder, ✕ to remove →
re-`PUT /frames/{id}/library`. Albums/Settings/Firmware tabs are **present for connected, hidden for
served** (wrap existing `FrameAlbums`/`SettingsPanel`/`FramePanel`). Header status is always honest —
never a false "Frame unavailable".

### (c) Curate — `/curate` (Immich-first, multi-target)
```
Immich (search/virtualized albums)  →  Selection (grid)  →  Targets: ◼Living Room ◻Kitchen ◼Aluratek
                                       preview AS IT RENDERS on target:  ◻ LCD   ◼ e-ink dithered
                                       [ Set library on 2 frames (queued) ]  + Keep in sync (LAN)
```
Browse Immich → build selection → choose **one or several** targets → commit `PUT …/library` per
target, **non-blocking** ("queued"). Killer feature: the selection **previews per target processing
profile** — see the e-ink Spectra-6 dithered render vs LCD *before* committing. Drag a photo/album
onto a frame chip to curate.

### (d) Activity — `/activity`
`StatusPanel` promoted to a destination: delivery queue per frame, subscriptions, sync health
(`/health/sync`), event log, filters. Pure read-only mirror (#25). One action: **Retry** a failed
frame.

## 4. Design system & conventions
- **Status semantics everywhere:** emerald = delivered/healthy · amber = pending/attention-soon ·
  red = failed/needs-action · slate = idle/asleep-OK · accent = active/selected. Promote to
  `<StatusDot>`/`<StatusPill>`/`<HealthBadge>`, `<FrameKindBadge>` (LAN vs Cloud).
- **Frame-as-object:** a `<FrameCard>`/`<FrameAvatar>` (render thumbnail in a bezel) used in
  dashboard, rail, curation targets, activity — one identity throughout.
- **Every surface has explicit loading (skeleton) / empty (illustrated CTA) / error (retry) /
  offline-asleep (kind-aware, never red) states.** No more self-hiding panels or hard-fails.
- **Real-time:** a `usePoll` wrapper over TanStack Query `refetchInterval` (status 5s, current 10s,
  config 30s) that **pauses on hidden tabs**.
- **Accessibility:** landmarks, real buttons, focus-visible accent rings, ARIA live region for
  delivery/sync changes, status never colour-only, 44px touch targets, full keyboard nav.
- **Motion (reduced-motion safe):** count-up on delivered counts, soft "breathing" ring while
  pending, thumbnail crossfade on advance.

## 5. Differentiators that earn the reviews
Frame-as-object identity · **per-target processing preview** (e-ink dithered vs LCD before commit —
the screenshot feature) · drag-to-curate · delivery as ambient, delightful state · honest kind-aware
health ("asleep — healthy") · multi-frame curation in one gesture · fleet OTA roll-up · live
current-image crossfade.

## 6. Backend prerequisites (filed as issues)
- `GET /frames/{id}` registry detail + `GET /frames/{id}/library` read-back (frame detail from id;
  Library tab).
- Register-a-served-frame endpoint (cloud-frame onboarding).
- Render-preview-for-frame endpoint over the prepared-image cache / processing profile (per-target
  preview).
- (Optional) `POST /frames/{id}/deliveries/retry` for the Activity retry action.

## 7. Build order
`A1 design-system → A2 shell/routing → A3 id-keyed client → A4 fleet dashboard →
{A5 frames list/onboard, A6 frame detail, A10 activity}` ; `A6 → A7 library tab → A8 curation →
A9 processing preview → A11 settings tab` ; `A6 → A12 fleet OTA → A14 settings screen` ;
`A13 real-time/a11y/polish` last. The app is usable after **A4**, gains real multi-frame curation
after **A7/A8**, and reaches award-worthy after **A9** + **A13**. Connected-frame flows are never
regressed (ADR-009) — old `host`-keyed paths keep working behind the shell until replaced.
