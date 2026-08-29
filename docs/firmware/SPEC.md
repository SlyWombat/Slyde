# Our own frame firmware — v1 specification

**Status:** agreed design, not yet started. Deliverable of #74. Grounded in
[`docs/firmware-teardown.md`](../firmware-teardown.md) (#75); findings are cited as **F1–F13**
and its open questions as **Q1–Q8**.

**Target:** the Memento 35" frame — Amlogic **T868/G9TV** (Meson8 family, quad Cortex-A9),
Android 4.4.2, board `n301`.

---

## 1. What v1 is, and what it is not

v1 does three things:

1. **Boots on the frame and drives the panel.**
2. **Joins `ktown` and holds the association** — measurably better than the vendor build.
3. **Accepts a signed over-the-air update from Slyde**, and survives a bad or interrupted one.

v1 deliberately does **not** include photo rendering, slideshow behaviour, a cloud, multi-frame
support, or any external release. Everything is internal: no compatibility promises, no support
burden. The existing vendor app keeps running the display until a later phase replaces it.

The unit is irreplaceable, so every decision below is biased toward reversibility over elegance.

---

## 2. Decisions

### D1 — Strategy: modified stock (option 1)

Keep the vendor's kernel, DTB and bootloader; replace only what we must inside `system/`.

The teardown moved the balance decisively. The panel is **8-lane V-by-One HS, 3840×2160 in
4400×2250 @60 Hz** (F1) — neither LVDS nor eDP, so *option 3* (replace the board) needs a 4K
V-by-One T-CON, not a cheap bridge, and the panel model stops being the only unknown.
*Option 2* (new OS) is not blocked by timings, which are in hand, but by the V-by-One PLL and
lane setup living in Amlogic's out-of-tree `lcd_tv` driver and partly computed at runtime — with
thin Meson8/T868 mainline support and a move to the staging `r8188eu` driver on top.

Option 1 inherits a working display, a working radio and a known partition table (F10), and
concentrates all the risk in one place: getting our bits onto the device.

**Reconsider option 3 only if** the frame is opened for Q1/Q2 anyway *and* a 4K V-by-One driver
board turns out to be cheaply available.

### D2 — Write path: SD-card burn, and never the vendor OTA

The vendor's own update route is closed to us: `Update.txt` + a package goes through
`RecoverySystem.verifyPackage` against `otacerts.zip`, and that store holds the exact certificate
that signed the vendor package — proven byte-identical, sha256 `f40112be…6c0fe9c7` (F2). The
private key died with the company.

We use **`reboot update` (or `upgrade_step=3`) plus a FAT SD card**, which makes U-Boot run
`sdc_burn aml_sdc_burn.ini` — a raw partition burner that never involves recovery and checks no
signature — with unsigned `aml_autoscript` and unsigned `recovery.img` as fallbacks on the same
path (F3).

### D3 — Never rewrite the bootloader

The vendor's OTA rewrites the `bootloader` partition (F10). **Ours must not.** That bootloader is
the *only* thing giving us the SD escape hatch of D2; overwriting it risks trading our un-brick
route for a cosmetic change. The bootloader is touched only if a future phase has a specific
reason, with Q1/Q2 both answered and a serial console attached.

### D4 — Trust anchor: replace `otacerts.zip` with our certificate

Once we can write `system/`, the cheapest real OTA is the vendor's own machinery with the trust
anchor swapped: put **our** certificate in `system/etc/security/otacerts.zip`, and from then on
recovery accepts packages signed by **our** key and nothing else — including, deliberately, no
longer accepting Memento's.

This is a one-way door. It is taken only after D5's key custody is in place.

### D5 — Signing key custody, decided before the first signed image

This project exists because a signing key held by one party died with them and locked a fleet out
of its own update channel (F2, cert valid to 2043). We do not repeat it.

- **Algorithm:** RSA-2048 (matching what the stock recovery verifies) for the OTA key.
- **The private key never lives on a build machine or in this repo.** It is generated offline and
  stored in the password manager alongside the other irreplaceable project keys, with **at least
  two copies in different physical locations**.
- **Recovery plan, written down:** if the key is lost, the device is recovered by D2's SD path,
  not by a new OTA. That is precisely why D3 forbids touching the bootloader — losing the key must
  remain an inconvenience, not a brick.
- The public certificate is committed to this repo; the private key's *location* is recorded in
  the project's memory notes, never its contents.

### D6 — Rollback: watchdog-confirmed commit, not A/B

SlyLED's FOTA spec assumes A/B slots on an MCU. This hardware has a fixed Amlogic partition table
with no second `system`, and repartitioning would put the escape hatch itself at risk (D3). The
equivalent safety, in descending order of who catches the failure:

1. **Boot-count commit.** An init service marks a new build "pending". The new system must reach a
   healthy state — network up, our agent running — and set "committed" within **10 minutes**. If
   three consecutive boots fail to commit, the device restores the previous `system` image.
2. **Previous image retained.** The last known-good `system` image is kept on `/data` (or an SD
   card, if Q1 gives us one) so restoration needs no network.
3. **SD burn** (D2) as the physical backstop when software rollback cannot run.

The vendor's approach — a single-shot `format` + write of `/system` with `upgrade_step` as the
only interlock (F10) — is exactly the fragility this replaces. One interrupted write left no way
back except D2.

### D7 — Wi-Fi: keep runtime provisioning, and do not claim #73 is fixed

The radio is a **Realtek RTL8188EU on USB — 802.11n 1×1, 2.4 GHz only** (F7). There is no 5 GHz
under any option without new hardware; `ktown` must therefore offer a 2.4 GHz BSS for the frame.

Credentials are provisioned **at runtime** — `wpa_supplicant.conf` with `update_config=1` writing
to `/data`, plus a USB seed file — which is already how the vendor does it (F7, F6). #74 worried
about SlyLED's compiled-in-credentials friction; it does not apply here and must not be
reintroduced.

**The claim "our firmware fixes #73" is conditional and currently unproven.** The app-level
watchdog that would have explained the flapping is dead code — never instantiated (F8) — so the
remaining candidates are the `rtl8188eu` driver, the radio, and the AP. Only the first is ours to
fix. Q4 separates them, costs nothing, and runs before any firmware work (see Phase 0).

### D8 — Identity: reuse the frame's existing GUID

The frame already has a stable identity: a 36-character GUID in `/mnt/sdcard/GUID.txt`, which
doubles as the cloud `frameId` and appears in the discovery reply (F9, E). Slyde already keys
frames by that GUID and follows them across DHCP changes. We keep it — a new identity scheme would
buy nothing and break existing curation.

### D9 — Server/device split: the device polls

The vendor put the queue on the server and the poll on the device, with explicit acknowledgement
(F9). Slyde's served-backend pattern already implements that shape for the eFrame. Our update
agent follows it: the device asks Slyde "is there a build for me?", downloads, verifies, applies,
and acknowledges. No inbound connection to the frame is required, which also sidesteps the ~21 s
service tick (#71) that makes the LAN control channel awkward.

### D10 — Do not repeat the credential leak

The vendor's `GetConfig` returns the frame's stored Wi-Fi SSID **and passphrase**, protected only
by a hard-coded DES key (F7, protocol corrections). Anything we build must never serve
credentials over the LAN, encrypted or not.

---

## 3. OTA design

Modelled on SlyLED's `docs/OTA-Firmware update.md`, adapted where this hardware differs.

| SlyLED FOTA | Here |
|---|---|
| Dual-bank A/B slots | **Not available** — fixed partition table. Replaced by D6: boot-count commit, retained previous image, SD backstop. |
| Signed images, public key on device | **Yes** — D4/D5, our cert in `otacerts.zip`. |
| Anti-rollback by version id | **Yes** — the vendor's own `updater-script` already asserts a build-date floor (F10); we keep that and add an explicit version. |
| Watchdog confirmation, auto-revert | **Yes** — D6. |
| Chunked, resumable download | **Yes** — the download is ours (D9); resume by HTTP Range. |
| Whole-image hash before commit | **Yes** — sha256 checked before the package is handed to recovery. |
| Firmware in GitHub releases | **No** — hosted by **Slyde**, on the LAN. This is internal-only, the frames cannot reach the internet reliably (#73), and Slyde already has the delivery machinery. |

**Update flow:** agent polls Slyde → Slyde answers with version, URL, sha256, signature → agent
downloads (resumable) → verifies hash, then signature → writes to a staging area → marks pending
and reboots into recovery → recovery verifies the package against our trust store and applies it →
new system boots, must commit within 10 minutes → agent acknowledges to Slyde.

**Failure at any step leaves the running system untouched**, because nothing is written to
`system` until recovery runs, and recovery only runs on a package that already passed hash and
signature checks.

---

## 4. Phased plan

Each phase has a gate. **A phase does not start until its gate is met.**

### Phase 0 — Answer the questions that decide whether to proceed *(no writes to the device)*

- **Q4 — is #73 ours to fix?** DHCP reservation on the frame's MAC to separate "loses association"
  from "gets a new lease"; 48 h of AP association/deauth logs classified by reason code; compare
  against a second RTL8188EU on the same AP. **This also sets the reliability bar** in D7.
- **Q1 — is there an SD slot wired to U-Boot's `mmc 0`?** Inspect the enclosure. If a slot exists,
  prove the path **read-only first**: a FAT card carrying *only* an `aml_autoscript` that prints a
  banner and returns, then `reboot update`.
- **Q2 — are UART pads reachable?** `console=ttyS0,115200n8`, `bootdelay=1` — a serial console
  gives the U-Boot prompt and is the most controllable route to everything in D2.
- **Q3 — secure boot fused?** `efuse info` on that console. Not answerable from images.
- **Q8 — does `Backup.txt` work?** An empty `Backup.txt` on a FAT32 stick copies the whole photo
  library to it (F5). Independently useful: it would close out #72's remaining 131 photos.
- **Full backup of the running device** — every partition read out and stored off-device, verified
  by hash, before anything is written. Non-negotiable.

**Gate to Phase 1:** a *verified* recovery route exists — Q1 proven read-only, **or** Q2 plus Q3
giving a console and a known-unfused part. #74's own bar. If neither is met, **stop here**: the
frame keeps working under Slyde and nothing is risked.

**Stopping test:** if Q4 shows the fault is the radio or the AP, v1's main justification is gone.
Re-decide before proceeding — the correct outcome may be to fix `ktown` and close #74.

### Phase 1 — Get our own code onto the device, reversibly

- Build a modified `system` image from the genuine tree: our update agent, adb enabled, our
  certificate in `otacerts.zip` (D4).
- Apply it by SD burn (D2). Bootloader untouched (D3).
- Prove restoration of the Phase 0 backup by the same route *before* relying on it.

**Gate to Phase 2:** the device boots our modified system, joins `ktown`, is reachable over adb,
and has been restored from backup at least once.

### Phase 2 — OTA

- Slyde endpoint serving version, URL, hash and signature (D9).
- Update agent: poll, resumable download, verify, stage, reboot, commit (§3).
- Rollback per D6, including the deliberate failure tests below.

**Gate to done:** the acceptance tests pass.

---

## 5. Acceptance tests for v1

| # | Test | Pass |
|---|---|---|
| A1 | Cold boot | Boots to a working display within the vendor build's time, no regression |
| A2 | Joins `ktown` unattended from cold | Associated and DHCP-leased within 60 s |
| A3 | **Association held for 7 days** | Disconnects ≤ the bar set by Q4; **no DHCP address change** with a reservation in place |
| A4 | AP restart | Reassociates within 120 s, same lease |
| A5 | Normal OTA | New build downloads, verifies, applies, commits, acknowledges — unattended |
| A6 | **Interrupted OTA** — power cut mid-download | Running system unaffected; retry resumes or restarts cleanly |
| A7 | **Interrupted OTA** — power cut mid-apply | Device recovers to a bootable system without human help |
| A8 | **Bad image** — a build that boots but never commits | Auto-reverts to the previous image within 3 boots |
| A9 | **Wrong signature** — package signed with the wrong key | Rejected, not applied, and reported |
| A10 | Anti-rollback | A lower version id is refused |
| A11 | Credential hygiene (D10) | No LAN command returns the Wi-Fi passphrase |
| A12 | Restore from backup | Phase 0 backup restores a working vendor system by SD burn |

A6, A7 and A8 are the tests that matter. An OTA system that has never been interrupted on purpose
is an OTA system that has not been tested.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Brick during a write | D2 SD burn + Phase 0 full backup + A12 proving restoration *before* it is needed |
| Secure boot fused, ROM rejects our images | Q3 before any write. Evidence suggests unfused — the OTA rewrites the bootloader with an unsigned image (F10) — but it is inference |
| No SD slot fitted | Q1. Falls back to Q2's serial console; if neither, stop |
| #73 is the radio or the AP | Q4 in Phase 0, before any firmware effort |
| Signing key lost | D5 custody; D3 keeps the SD route alive so it stays an inconvenience |
| Effort exceeds value | v1 is scoped to three things; the display app is explicitly out of scope |

---

## 7. Open items carried from the teardown

Q5 (is a digitizer fitted?), Q6 (reconstruct `recovery.img`), Q7 (are the `pictureshare`
endpoints alive?) do not gate v1. Q5 is answered for free if the frame is opened for Q1/Q2.

---

## 8. What this spec does not decide

- The replacement display application — out of scope for v1 (§1), specified separately once the
  device is ours to update.
- Whether to ever move to option 2 or 3 (D1). v1 is explicitly a first move, not a final one.
