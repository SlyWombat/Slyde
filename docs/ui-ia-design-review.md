# UX / IA design review — Slyde frame management

*Scope: the frame-management surface (frame detail tabs + Curate), grounded in the code as it exists
today. Evaluates the product owner's proposed mental model and recommends a target information
architecture. Review only — no code is changed by this document.*

Owner's model under review (verbatim):

> "I would think that the 'Library' tab would be the current state of what is on a given frame,
> organized by folders. You can add a new folder manually, and then in that folder import images from
> Immich (i.e. keep in sync or one time), or you can select an Immich folder to sync to the library.
> The Albums tab is not needed? But all this needs an expert to review based on the features and
> functions."

**Verdict up front:** the owner's instinct is right and should be adopted. Library should become the
single per-frame "what this frame shows" surface, organized into folders, with each folder bound to a
source (Immich keep-in-sync / Immich one-time / uploads / frame-import). Albums should be retired as a
top-level tab once its three unique capabilities (folders, keep-in-sync, upload) are absorbed into
Library **on the curation/delivery engine**. The main work is not UI — it's finishing the long-planned
unification of the two sync engines (`docs/framework-design.md` §2.4/§2.6) so folders and keep-in-sync
ride the delivery queue instead of the legacy push path.

---

## 1. Current IA map

### 1.1 Where frame management lives

| Route / component | Purpose | Engine |
| --- | --- | --- |
| `/frames` (`FramesList.tsx`) | Fleet list + onboarding (LAN scan / cloud frame-code) | — |
| `/frames/:id` (`FrameDetail.tsx`) | Tabbed frame detail | — |
| → Overview (`OverviewTab`) | Live preview + Prev/Next (connected) or "cloud, pulls on its own" (served); delivery roll-up | reads delivery state |
| → **Library** (`LibraryTab.tsx`) | The frame's curated set + per-photo delivery state + "Import photos on the frame" | **curation/delivery** |
| → **Albums** (`AlbumsTab.tsx` + `AddToFolder.tsx` + `FolderSyncStatus.tsx`) | Device folders, From-Immich (once/selected/keep-in-sync), upload, delete | **legacy SyncService** |
| → Settings (`SettingsTab.tsx`) | Rename, device toggles (connected), processing summary, detach | — |
| → Firmware | OTA (connected only) | — |
| `/curate` (`Curate.tsx`) | Immich-first, multi-target curation → `PUT /library` per frame | **curation/delivery** |

Tab gating (`FrameDetail.tsx` lines 68-74): **Library** and **Settings** always show; **Albums** shows
only when `capabilities.albums` is true; **Firmware** only when `capabilities.ota`. From the backends:

- Memento LAN (`backends/memento_lan.py`): `interaction=connected`, `albums=True`, `upload=True`,
  `delete=True`, `ota=True` → shows every tab.
- Sungale cloud (`backends/sungale_cloud.py`): `interaction=served`, `albums=False`, `upload=True`,
  `delete=True`, `ota=False` → shows **Library + Settings only**.

So **served frames have no Albums tab at all**, which means today they have no folders, no
keep-in-sync, and no upload-from-disk in the UI — even though the served backend's capabilities say
`upload=True` and the curation engine fully supports served delivery. Those features are not missing
because served frames can't do them; they're missing because they are trapped inside an Albums tab
that is gated off for served frames.

### 1.2 The two engines, made explicit

**Engine A — Curation library + delivery queue** (the intended north star).

- Stores: `library_item` (frame_id, asset_id, dest_name, position, **source** ∈ immich|upload|frame)
  and `delivery` (per-photo state pending|delivered|failed) — `store.py`.
- Write path: `PUT /frames/{id}/library` → `FrameLibrary.set_desired` + `DeliveryService.enqueue_desired`
  → background `drain` (`routers/frames.py` `set_library`, `delivery_service.py`).
- Delivery is transport-aware (`DeliveryService._deliver`): **served** → prepared image written to
  `ImageCache`, frame pulls on wake; **connected** → pushed over LAN, offline = transient retry.
- Read path: `GET /frames/{id}/library` joins desired set with delivery state → `LibraryTab`.
- Sources that feed it: Immich curation (`Curate.tsx`), **frame-import** (`frame_import.py`, source
  `frame`, recorded already-delivered), and **served-device uploads** (`uploads.py::ingest_upload`,
  source `upload`, called only from `sungale_cloud.py` when the eFrame's own app pushes a photo).
- Works for offline + served frames; no host call needed to read or queue.

**Engine B — Legacy folder sync (`SyncService`)** (connected-only).

- Stores: `synced_photo` (host, asset_id, dest_name, album_id) and `album_sync` (subscriptions),
  both keyed by **host**, entirely separate from `library_item`/`delivery` — `store.py`, `sync.py`.
- Surfaces: the **Albums tab**. Folder CRUD (`/{host}/albums*`) reads/writes the **device's own**
  album manifest live; From-Immich = `POST /{host}/sync(/jobs)`; keep-in-sync = `POST
  /{host}/subscriptions` (mirrors an Immich album 1:1, re-run by the scheduler via
  `run_due_subscriptions`); upload = `POST /{host}/upload` → `SyncService.upload_files`.
- Two-phase, push-only, talks directly to the device. Folder data is read **live from the frame**, so
  when the frame is asleep the whole tab errors into an "asleep / off the LAN" banner
  (`AlbumsTab.tsx` lines 69-84).
- No per-photo delivery state; progress is a transient job result + a subscription `last_result`
  string.

**The bridge** is one-directional: `frame_import.py` reads the device's albums (Engine B's world) and
ingests them into the library (Engine A) as already-delivered. Nothing goes the other way.

---

## 2. Findings

**F1 — Two engines put Immich photos on a connected frame with zero cross-visibility.** A photo added
through Albums → "Add once" lands in `synced_photo` and shows as a device-folder thumbnail, but never
appears in the Library tab (different table; `library_item` is never written — `sync.py` only calls
`store.upsert(SyncedPhoto…)`). Conversely a Library/Curate photo is pushed with `album=None`
(`delivery_service.py` line 122) into the device's default set, so it *does* eventually show in Albums
because Albums reads the device live — but it never lands in any named folder. The result is an
asymmetric, confusing split: **Library is a subset-or-superset of Albums depending on which tool you
used**, and the same physical photo can be "in the library" or not for no reason the user can see.

**F2 — "Current state" is split across three incompatible notions.**
- Library = the *desired/managed* set + delivery state (offline-tolerant, the honest answer).
- Albums = a *live device read* of folders + files (connected + awake only; errors otherwise).
- Overview = the *single image currently on screen* (`/current`).
None of them is labelled as such, and the first two disagree (F1). The owner's "current state of what
is on a given frame" maps cleanly onto Library's delivery state (`delivered` = on the frame) — that is
already the most truthful, always-available view; Albums' live read is the fragile one.

**F3 — Folders exist only on the device, only for connected frames.** `library_item` has no
folder/group column (`store.py` lines 59-66); grouping lives entirely in the device's `FrameAlbum`
manifest, read live. So folders are connected-only, require the frame awake, and are invisible to the
curation engine. The owner's "organized by folders" has **no representation in Engine A today**.

**F4 — Uploads are incoherent across sources.** Three upload paths, two outcomes:
- Web UI "Upload files" (`AddToFolder`→`DirectUpload`→`/{host}/upload`) → `SyncService.upload_files`
  → `synced_photo` only. **Invisible to Library.** Connected-only.
- Served eFrame's own app upload (`ingest_upload`) → `library_item` (source `upload`) + delivery.
  **Visible in Library.**
- Frame-import (`frame_import.py`) → `library_item` (source `frame`), already-delivered.
So whether an uploaded photo is a first-class library member depends on which door it came through.
The `library.py` docstrings and `store.set_library` already anticipate `source='upload'` library items
("kept… not part of the Immich curation a PUT manages"), but the **web upload route never creates
them** — the plumbing for unified uploads is half-built.

**F5 — Keep-in-sync is connected-only by accident of placement.** Subscriptions (`album_sync`) live in
Engine B and are reached only through the Albums tab, so they can't apply to served frames — yet the
curation engine delivers to served frames fine. Keep-in-sync is exactly the feature most valuable for a
cloud frame you rarely touch, and it's the one served frames can't have.

**F6 — Naming schemes don't reconcile, so dedup across engines is best-effort.** Engine A dest_names
are `asset_id.jpg` or server-derived; Engine B uses `dest_name_for()` hashed slugs; device albums use
raw filenames; frame-import dedupes by raw filename against `library_item.dest_name`. A photo present
via two engines can carry two different dest_names and be counted twice.

**F7 — Delivery feedback is first-class in A, absent in B.** Library shows per-photo
delivered/pending/failed dots and a roll-up (`LibraryTab` `STATE_TONE`); Albums shows only a job
summary and a subscription `last_result` string. Migrating folders onto Engine A is a strict feedback
upgrade.

**F8 — Albums is a dead-end when the frame sleeps.** Because it reads live, the entire management
surface (including *creating* a folder or starting keep-in-sync, which don't inherently need the device
awake) is unavailable whenever a connected frame is asleep — a normal state for low-power frames.

**Net:** Albums and Library overlap heavily (both "put Immich photos on this connected frame"); the
*non*-overlap is exactly three things Library lacks — **folders, keep-in-sync, upload** — plus
device-truth operations (delete-from-frame, live thumbnails). Everything else Albums does, Library does
better and more universally.

---

## 3. The owner's three questions, answered

### (a) Should Library become the current-state-of-the-frame view, organized by folders? — **Yes.**

Library is already the only view that is honest for every frame type and every power state: it is the
managed set plus delivery state, and `delivered` already means "on the frame." Make it *the* per-frame
home. Two refinements to the owner's wording:

- It should remain the **managed/desired set with delivery state**, not a live device scrape. "Current
  state" is best expressed as desired + per-photo delivery (✓ on frame / ◐ delivering / ⚠ failed),
  which works offline and for served frames. Reserve the literal live device read for a small
  diagnostic, not the primary surface (it's the fragile part — F2, F8).
- Add a real **folder/group** concept to the curation model (a column on `library_item` + a per-folder
  source binding). Folders today are device-only and connected-only (F3); to deliver the owner's model
  they must become a first-class, transport-agnostic property of the library.

### (b) Is the Albums tab redundant and removable? — **Yes, after absorbing three capabilities.**

Albums is ~80% redundant with Library (both curate Immich photos to a connected frame, via parallel
engines — F1). Its only non-redundant functions are **folder grouping, keep-in-sync subscriptions, and
upload-from-disk**, plus device-truth ops (delete-from-frame, live thumbnails, remove-from-folder).
Fold the first three into Library on Engine A; expose delete-from-frame as a per-photo action in
Library (the capability already exists: `capabilities.delete`); and demote the live device read to an
optional "what's literally on the device now" diagnostic (e.g. a collapsible under Overview for
connected frames). Then **remove Albums as a top-level tab.** Keeping it is what produces the
two-sources-of-truth confusion (F1/F2) and the asleep dead-end (F8).

Do **not** simply delete Albums before the absorption — that would regress folders, keep-in-sync, and
upload for connected frames. Sequence matters (see §5).

### (c) How should folders + Immich sync + import-from-frame fit into one model? — One library, folders with source bindings, one delivery queue.

```
Frame
└── Library (the managed set, on the curation/delivery engine)
    ├── Folder "Family"      ← source binding: Immich album X · keep-in-sync
    ├── Folder "Italy 2024"  ← source binding: Immich album Y · one-time import
    ├── Folder "Scans"       ← source: uploads
    └── Folder "Already on frame" ← source: frame-import (delivered)
```

Each folder is a row-set in `library_item` (add a `folder` column) with a **source binding** describing
how it is filled:

- **Immich keep-in-sync** — bind the folder to an Immich album; the scheduler reconciles the folder's
  desired set from the album (add new / drop departed) and feeds `enqueue_desired`. This is today's
  subscription logic (`sync.py::sync_subscription`) re-pointed from "push to device album" to "set this
  folder's library rows," so delivery is shared and works for served frames too.
- **Immich one-time** — copy the album's assets into the folder once, no binding retained (today's
  `Curate` / "Add once," scoped to a folder).
- **Uploads** — `ingest_upload` for *every* upload source (web UI included — fix F4), tagged to the
  folder.
- **Frame-import** — `frame_import.py`, tagged into a folder (e.g. "Already on frame"), recorded
  delivered.

All four feed the **same** `library_item` + `delivery` tables, so delivery state, offline tolerance,
and served support come for free and uniformly. `album_sync` (host-keyed subscriptions) is replaced by
per-folder bindings keyed by `frame_id`.

For **connected** frames, a Slyde folder can optionally project onto the device's own album manifest
(the existing `mirror_album`/`create_album` ops, driven from the delivery side instead of from
`SyncService`), so the grouping is also visible on the device. For **served** frames, folders are
purely Slyde-side organization (the device has no album concept) — and that's fine, because the value
is in *how Slyde curates and reports*, not in a device feature.

---

## 4. Recommended target IA

**Frame detail tabs:** `Overview · Library · Settings · Firmware*` (Albums removed; Firmware stays
capability-gated). Library is identical in shape for connected and served frames; only per-photo
actions differ by capability (Prev/Next and device-folder projection are connected-only; everything
else is universal).

**Library tab — proposed wireframe:**

```
‹ Frames   Living Room        ● online · LAN · Memento 13"            [＋ Add photos ▾]
                                                                       ├ From Immich…
 124 photos · ▓▓▓▓ 120 ✓ on frame · 3 ◐ delivering · 1 ⚠ failed       ├ Upload files…
                                                                       └ Import from frame
┌ Folders ─────────────────────────────────────────────────────────────────────────┐
│ [ All 124 ]  [ Family 60 ⟳ ]  [ Italy 2024 40 ]  [ Scans 12 ]  [ On frame 12 ]  ＋ │
└───────────────────────────────────────────────────────────────────────────────────┘
  Family  · 60 photos · ⟲ Kept in sync from Immich "Family" · last run 4m ago  [Stop] [⋯]
┌───────┬───────┬───────┬───────┬───────┬───────┐
│ ✓     │ ✓     │ ◐     │ ✓     │ ⚠     │ ✓     │   ✓ on frame  ◐ delivering  ⚠ failed
│ [img] │ [img] │ [img] │ [img] │ [img] │ [img] │   hover: ‹ ›  reorder · ✕ remove
└───────┴───────┴───────┴───────┴───────┴───────┘   (⋯ folder menu: rename · change source · delete)
```

Notes on the wireframe:

- **One "Add photos" entry** with a source menu replaces the Library "+ Add photos / Import" buttons
  *and* the whole Albums "Add to folder" surface. The selected folder is the destination context
  (exactly the simplification `AddToFolder` already made — drop the redundant destination picker).
- **Folder chips** carry a count and a keep-in-sync glyph (⟳); "All" is the flat view (today's
  behavior). A folder bound to Immich shows its sync status + Stop inline — reuse `FolderSyncStatus`,
  re-pointed at per-folder bindings.
- **Per-photo state dots and reorder/remove** are exactly today's `LibraryTab` controls. Add a
  capability-gated "Delete from frame" (vs "Remove from library") for connected frames, replacing the
  Albums delete affordance.
- **Served vs connected:** identical layout. Served frames simply never show Prev/Next or the optional
  "also show as a device folder" toggle; their delivery dots read "queued → on frame" as the frame
  polls. Keep-in-sync and uploads now work for them (fixes F5).
- **Live device truth** (the old Albums live read) becomes an optional, collapsible "On the device
  right now" diagnostic under Overview for connected frames — clearly secondary, and allowed to be
  unavailable when asleep without blocking management.

**Curate (`/curate`)** stays as the multi-frame, Immich-first entry point but gains an optional target
*folder* per frame (defaulting to "All"). It already writes through Engine A, so no engine change is
needed there.

---

## 5. Migration / implementation notes

**Already supported (no new infra):**

- Curation/delivery engine end-to-end: `library_item` + `delivery`, `enqueue_desired` delta-only
  queueing (#46), background `drain`, served-via-cache + connected-via-push in
  `DeliveryService._deliver`, offline-tolerant retry. This is the engine everything should ride.
- `source` column on `library_item` (immich|upload|frame) and `ingest_upload` already producing
  first-class upload library items — the model already expects multi-source folders.
- Frame-import already bridges device contents into the library as delivered (`frame_import.py`).
- Served delivery, served onboarding, per-photo delivery read-back — all present.

**Missing (the actual work):**

1. **Folder dimension on the curation model.** Add a `folder` (group) column to `library_item`;
   teach `set_library` / `add_library_item` / `list_library` and `GET /library` to carry it; group in
   `LibraryTab`. Low-risk, additive (mirror the existing `source` migration in `store.py::_migrate`).
2. **Per-folder source binding** to replace `album_sync`. A `frame_id`-keyed binding table (folder →
   Immich album, mode keep-in-sync|once). Re-point `sync_subscription`'s reconcile logic to *set a
   folder's desired library rows* and call `enqueue_desired`, instead of pushing to a device album.
   The scheduler's `run_due_subscriptions` becomes "reconcile bound folders." This is the **core
   unify-onto-the-queue step** and the highest-value, highest-care change.
3. **Route web uploads through `ingest_upload`.** Make `POST /{id}/upload` (id-keyed) create
   `library_item` (source `upload`) + queue delivery, the same as served-device uploads — closing F4.
   Retire `SyncService.upload_files`.
4. **Per-photo "Delete from frame"** in Library for `capabilities.delete` frames (reuse
   `delete_photo`); "Remove from library" for the curation removal that already exists.
5. **Optional device-folder projection** for connected frames: drive `create_album` / `mirror_album`
   from the delivery side so a Slyde folder can appear on the Memento device. This is the only piece
   that must keep talking to the device live; keep it optional and non-blocking so management never
   dead-ends when asleep (fixes F8).
6. **UI:** fold `AddToFolder`/`ImmichPicker`/`DirectUpload`/`FolderSyncStatus` into Library as the
   folder + "Add photos" surface; delete `AlbumsTab` as a tab; drop the `albums` capability gate from
   the tab list. Reuse the components — most of the UI already exists, it just lives in the wrong tab.

**Phasing (Memento-green throughout, per ADR-009):**

- **Phase 1 (UI consolidation, no engine change):** Move keep-in-sync status + "Add photos" into
  Library as-is, still calling Engine B under the hood for connected frames; keep Albums reachable but
  de-emphasized. Immediately reduces the two-tab confusion. *Risk: low.*
- **Phase 2 (folders in Engine A):** Items 1 + 3 + 4. Web uploads and a flat folder grouping become
  first-class curation; per-photo delivery state now covers uploads. *Risk: medium — schema migration;
  reconcile dest_name strategy (F6) so imported/legacy photos dedup cleanly.*
- **Phase 3 (keep-in-sync on the queue):** Item 2. Subscriptions become per-folder bindings on Engine
  A; keep-in-sync and uploads light up for **served** frames (fixes F5). Migrate existing `album_sync`
  rows to bindings. *Risk: medium-high — this is the behavioral heart; needs a migration + parity tests
  against `sync_subscription`'s add/drop semantics.*
- **Phase 4 (retire Engine B):** Optional device-folder projection (item 5) lands on the delivery
  side; `SyncService` + `synced_photo` + `album_sync` are removed; Albums tab deleted. *Risk: medium —
  data migration of `synced_photo`/`album_sync`; connected-frame regression guard via the
  emulator/integration suite.*

**Risks / watch-items:**

- **dest_name reconciliation (F6)** is the sharpest correctness risk: unifying engines means one
  canonical naming scheme so the same asset isn't double-counted across legacy/import/curation. Decide
  this before Phase 2.
- **Device-folder semantics for Memento:** users who organized real folders on the device expect them
  preserved. Frame-import + device-folder projection must round-trip folder names, or be explicit that
  Slyde folders are the source of truth going forward.
- **Migration of live subscriptions:** existing `album_sync` rows must convert to per-folder bindings
  without a sync gap or a re-push of already-delivered photos (lean on `delivered_payloads`/#46
  delta-skip).
- **ADR-009 regression guard:** every phase must keep the Memento emulator/integration/`memento-lan`
  conformance tests green; connected push behavior is the contract that cannot regress.

**Bottom line:** the owner's model is the correct target and is mostly a *consolidation* onto
infrastructure Slyde already built for exactly this reason (`framework-design.md` §2.4/§2.6). Library
becomes the one per-frame home, folders + source bindings give it structure, the delivery queue gives
it honesty and offline/served support, and Albums — along with the entire legacy `SyncService` engine —
goes away.
