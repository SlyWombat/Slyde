# Memento 35" — vendor firmware teardown

Complete static teardown of the recovered vendor OTA package, done for #75 to unblock #74.
Nothing here touched the device.

**Subject.** `Projects/memento-firmware/Memento_35.zip` — 122,937,593 bytes,
md5 `57a7151d932a11f603e44c44fcb49968` (verified, `reversing/firmware-teardown/teardown.log`),
1034 entries, 237 MiB uncompressed. Kept outside this repo (large, copyrighted).

**How to re-derive everything below.** No binwalk, abootimg, simg2img, unzip, 7z, java,
apktool, innoextract, lzop, mono or dtc exists on this box, so the tools were written for
the job and live in `reversing/tools/`:

| tool | what it does |
|------|--------------|
| `fwlib.py` | strings/hexdump, Android boot-image header, gzip+cpio ramdisk, FDT→DTS printer, `IKCFG_ST` extraction |
| `ucl.py` | UCL NRV2B/NRV2D/NRV2E decompressors + the uclpack container — this is what opens `bootloader.img` |
| `cildasm.py` | a CIL disassembler (ECMA-335 metadata + opcode stream + token/`ldstr` resolution) for the Xamarin app |
| `certs.py` | enough DER/PKCS#7 to identify a certificate without openssl |
| `teardown.py` | driver: takes the zip, regenerates every artefact below |

```
python3 -m venv venv && venv/bin/pip install dnfile lzallright
venv/bin/python reversing/tools/teardown.py ../memento-firmware/Memento_35.zip \
    --out reversing/firmware-teardown --work /tmp/fw
venv/bin/python reversing/tools/cildasm.py --enums \
    ../memento-firmware/extracted/CadreAndroid.dll
venv/bin/python reversing/tools/cildasm.py \
    ../memento-firmware/extracted/CadreAndroid.dll > /tmp/CadreAndroid.il
```

Use a **Linux** interpreter. `python3` on this WSL box's PATH is Windows Python and rewrites
text files as CRLF/cp1252. `uv` is not installed, so #75's `uv run --with dnfile` recipe does
not work here; `lzallright` is additionally needed (the kernel is LZO-compressed and
`python-lzo` has no wheel for this box).

Small derived **text** artefacts are committed under `reversing/firmware-teardown/`
(`uboot-default-env.txt`, `emmc-partitions.txt`, `boot.dts`, `kernel.config`,
`cadre-enums.txt`, `certificates.txt`, `boot-img.txt`). The blobs are not.

---

## Findings

Ordered by how much they change #74's decisions, not by the order of #75's scope list.

### F1. The panel is V-by-One HS, 8 lanes, 3840×2160 — not LVDS, not eDP

From the DTB in `boot.img`'s second slot (`reversing/firmware-teardown/boot.dts`, node `lcd`):

```
vbyone_0 {
    interface     = "vbyone";
    basic_setting = <0xf00 0x870 0x1130 0x8ca 0x50 0x5a>;
    lcd_timing    = <0xffffffff 0xffffffff 0xffffffff 0x2f 0x50 0x2f 0x50 0x2f 0x2f 0x3 0x9>;
    vbyone_att    = <0x8 0x4 0x1 0x4>;
    panel_power_pin = "GPIOH_10";
};
```

`basic_setting` decodes as h_active / v_active / h_period / v_period / h_sync / v_sync =
**3840 / 2160 / 4400 / 2250 / 80 / 90**. The cell meanings are confirmed by the `lvds_0`
node in the same DTB, `<0x780 0x438 0x898 0x465 0x94 0x29>` = 1920/1080/2200/1125/148/41,
which is textbook 1080p60 — so the reading is not a guess. 4400 × 2250 × 60 Hz = 594 MHz,
and U-Boot's default env agrees: `outputmode=4k2k60hz`, `panel_type=vbyone_0`.
`vbyone_att` = 8 lanes, 4 regions.

The other four `lcd` children (`lvds_0/2/3/4`) are 1080p and 1366×768 LVDS modes for sibling
products; only `vbyone_0` is selected.

`/system/etc/CadreHardware.json` gives the *app's* canvas as 3240×2160 on a 35" landscape
panel — so #74's "matted 16:9 4K panel" guess is right: a 3840-wide panel with the app
painting a 3240-wide 3:2 image.

**What this means for #74 A.** Option 3 (replace the board with a Pi) is much more expensive
than the issue assumes. V-by-One HS is a display-internal LVDS-successor link; it is neither
LVDS nor eDP, and there is no cheap bridge from HDMI/DSI to 8-lane V-by-One. Option 3 requires
either an off-the-shelf 4K V-by-One T-CON board or replacing the panel.

Also note `lcd_timing`'s first three cells are `0xffffffff` — computed at runtime by the LCD
driver, not tabulated. A port to mainline (option 2) cannot simply copy this node; it has to
reproduce the Amlogic `lcd_tv` driver's V-by-One PLL/lane setup. That is the real risk in
option 2, and it is bigger than "panel timings", because the timings are the easy half.

### F2. The OTA trust store holds the exact certificate that signed this package — proved

`certs.py` extracts and compares (`reversing/firmware-teardown/certificates.txt`):

| where | sha256 of the DER certificate |
|-------|-------------------------------|
| `META-INF/CERT.RSA` (the signature on this OTA) | `f40112be…6c0fe9c7` |
| `system/etc/security/otacerts.zip` → `testkey.x509.pem` | `f40112be…6c0fe9c7` |
| `META-INF/com/android/otacert` | `f40112be…6c0fe9c7` |

Byte-identical. Subject/issuer: `C=CA, ST=Quebec, L=Terrebonne, O=Memento Electronics inc.,
OU=Memento smart frame, CN=Memento Electronics, emailAddress=info@mementosmartframe.com`,
RSA-2048, self-signed, valid 2015-11-17 → **2043-04-04**.

So #72's inference is now evidence: the device's OTA trust store contains the public half of
Memento's own signing key, we do not hold the private half, and the file being *named*
`testkey.x509.pem` is a build-system artefact, not AOSP's public test key.
`ro.build.tags=test-keys` in `build.prop` is likewise a stale flag.

Two distinct Memento keys are in use — the OTA/platform key above signs `Home.apk`; a second
Memento certificate (`cb61901a…a800e4c0`) signs `Cadre.apk`, `Settings.apk` and
`OTAUpgrade.apk`.

The app-level USB `Update.txt` path does not escape this: `Cadre.apk`'s `classes.dex` calls
`android.os.RecoverySystem.verifyPackage` / `installPackage`, which is the standard AOSP path
and verifies against the same `otacerts.zip`.

### F3. There is a route onto the device that never touches the trust store: SD-card burn

Recovered U-Boot default environment (`reversing/firmware-teardown/uboot-default-env.txt`):

```
preboot=run upgrade_check;run storeargs;get_rebootmode; clear_rebootmode;
        echo reboot_mode=${reboot_mode}; run switch_bootmode
switch_bootmode=if test ${reboot_mode} = normal;        then run storeargs;
           else if test ${reboot_mode} = factory_reset;  then run prepare; run recovery;
           else if test ${reboot_mode} = update;         then run prepare; run storeargs; run update;
           else if test ${reboot_mode} = usb_burning;    then run usb_burning;
           else run storeargs; fi; fi; fi; fi;
update=echo update...; if mmcinfo; then
         if fatexist mmc 0 ${sdcburncfg}; then sdc_burn ${sdcburncfg};
         else if fatload mmc 0 ${loadaddr} aml_autoscript; then autoscr ${loadaddr}; fi;
              if fatload mmc 0 ${loadaddr} recovery.img; then bootm; fi;
         fi; fi;
       if imgread kernel recovery ${loadaddr}; then bootm; else echo no recovery in flash; fi
sdcburncfg=aml_sdc_burn.ini
upgrade_check=if itest ${upgrade_step} == 3; then run prepare; run storeargs; run update; fi;
              if itest ${upgrade_step} == 1; then defenv; setenv upgrade_step 2; saveenv; fi;
```

With `reboot_mode=update` (or `upgrade_step=3`, which is what the OTA's `updater-script` sets
before it starts writing), U-Boot will, from **FAT on `mmc 0` — the external SD**, in order:

1. run `sdc_burn aml_sdc_burn.ini` — Amlogic's `optimus` raw partition burner. It writes
   partitions directly. **It does not go through recovery, so no OTA signature is checked.**
2. failing that, `fatload` + `autoscr aml_autoscript` — execute an **unsigned U-Boot script**;
3. failing that, `fatload` + `bootm recovery.img` — boot an **unsigned kernel** from the SD.

That is three unsigned-code paths and, in (1), a full un-brick. The U-Boot binary confirms the
machinery is compiled in: `sdc_burn` / `sdc_update` commands, `v2_sdc_burn/optimus_sdc_burn.c`,
`"Burning a partition with image file in sdmmc card"`, `"to burn partition boot with boot.img
of mmc 0: \"sdc_update boot boot.img\""`.

`reboot_mode` is set by `get_rebootmode` and the accepted values are exactly
`normal`, `factory_reset`, `usb_burning`, `charging` (string table at U-Boot 0x953e4).
Android reaches it via `reboot <mode>`.

Three caveats, stated plainly:

- **`usb_burning` is not defined in the default environment.** `switch_bootmode` runs
  `run usb_burning`, but no `usb_burning=` assignment exists in the default env. On a device
  with a stock env, that branch is a no-op. USB burning therefore has to be entered another
  way — the U-Boot console `update` command (the binary carries `"Enter USB burn"`,
  `"Enter v2 usbburning mode"`, `v2_usb_burn/optimus_usb_burn.c`), or the SoC ROM's own
  USB boot before U-Boot runs.
- **Whether the enclosure exposes an SD slot is a hardware question.** The host exists —
  DTB `sdio`/`sdhc` nodes with `sd_all_pins`, and `vold.fstab` mounts
  `/storage/external_storage/sdcard1` from `aml_sdio.0/mmc_host/sd` — but the bytes cannot say
  whether a slot is fitted or reachable. See Q1.
- `bootdelay=1` and `console=ttyS0,115200n8`: there is a one-second window to interrupt
  autoboot on the serial console, if UART pads are reachable. See Q2.

### F4. The frame has no touchscreen, and this build could not use one

This kills #74's stated cheapest foothold ("`ro.adb.secure=1`, and the device **has a
touchscreen** (`ssd253x`), so an adb authorisation prompt can be accepted on-screen").

Four independent facts:

- `reversing/firmware-teardown/kernel.config`: **every** touchscreen option is unset —
  `# CONFIG_INPUT_TOUCHSCREEN is not set`, `# CONFIG_MESON_INPUT_TOUCHSCREEN is not set`,
  `# CONFIG_AML_TOUCH_ALGORITHM_SUPPORT is not set`, `# CONFIG_HID_MULTITOUCH is not set`,
  and no `CONFIG_TOUCHSCREEN_*=y|m` anywhere.
- The decompressed kernel contains no `ssd25*` string of any kind, and there is no
  out-of-tree touch `.ko` in `system/` (the only module shipped is `system/lib/8188eu.ko`).
- `boot.dts` has no touch node; the two I²C buses (`i2c-A` @0xc1108500, `i2c-B` @0xc11087c0)
  carry no children at all.
- `build.prop`: `ro.platform.has.touch=false`.

The `ssd253x` mention traces to a single file, `/system/usr/idc/ssd253x-ts.idc` — an Android
input-device *configuration* file, not a driver. Its own header says it is the stock AOSP
sample "for the Elantech touch screen", copied from AOSP with only the filename changed. It
is dead weight from a sibling board.

Two claims worth keeping separate: **this build cannot use a touchscreen** (settled by the
bytes above) is not the same as **the panel assembly has no digitizer** (a hardware question
the bytes cannot answer). Either way, an on-screen adb prompt is not available on the
firmware as shipped.

### F5. `Backup.txt` writes the whole photo library to a USB stick

Direct answer to #75's question, and immediately useful to #72's 131 outstanding photos.

`CadreAndroid.SplashActivity.CreateMementoBackup` (IL at `MethodDef[544]`, rva 0x17620):

```
usb   = "/storage/external_storage/sda1/"          (Cadre.Utils..cctor: ms_UsbPath)
dest  = usb + "Memento_" + Config.s_Name           (the frame's display name)
Utils.DeleteFolder(dest, recursive: true)          # wipes any previous backup
Utils.CreateFolder(dest); Utils.CreateFolder(dest + "/Photos")
copy /mnt/sdcard/AlbumData.json     -> dest + "/AlbumData.json"
copy /mnt/sdcard/SetupData.json     -> dest + "/SetupData.json"
copy /mnt/sdcard/CurrentAlbum.json  -> dest + "/CurrentAlbum.json"
foreach f in Directory.GetFiles("/mnt/sdcard/Photos/"):     # non-recursive, ALL files
    copy f -> dest + "/Photos/" + Path.GetFileName(f)
    show "Backup to USB key in progress (N%)"
Utils.DeleteFile(usb + "Backup.txt")               # consumes its own trigger
show "Backup completed."
```

So: FAT32-format a stick, put an empty `Backup.txt` in its root, plug it in, reboot the
frame. The frame copies the entire `/mnt/sdcard/Photos/` directory plus the three JSON state
files, then deletes the trigger. It is bounded only by USB speed, needs no network, and is
not subject to the ~21 s control-channel tick documented in `docs/protocol.md`. It is a
strictly better bulk-extraction route than `ReadFile` over TCP 2018.

Two cautions: it **deletes** an existing `Memento_<name>` folder on the stick first, and the
paths are decoded from IL, not observed on the device — the run should be watched the first
time.

`Restore.txt` drives the mirror operation (`RestoreMementoBackup`, `MethodDef[545]`).

### F6. The complete USB trigger surface — larger than #74/#75 listed

All read by `CadreAndroid.SplashActivity.USB_DriveCheck` at app start, from
`/storage/external_storage/sda1/` (first partition of the USB stick), and acted on by
`ExecuteUsbCommands`. These are plain files read by the *app*: unsigned, no key needed.

| file | contents the app requires | effect |
|------|---------------------------|--------|
| `Backup.txt` | (presence) | F5 — copy library + state to `Memento_<name>/` on the stick |
| `Restore.txt` | (presence) | restore from that folder |
| `CopyUsbImages.txt` | presence **plus** a `Photos/` folder on the stick | bulk-import photos from the stick |
| `Update.txt` | presence **plus** `Memento_35.zip` (35") or `Memento_25.zip` (25"), chosen by `Config.i_ScreenSize` | OTA install via `RecoverySystem` — **signature-checked**, see F2 |
| `WifiSetup.txt` | ≥2 lines: line 1 SSID, line 2 passphrase, optional line 3 frame name (default `Memento`) | join a network |
| `WifiHost.txt` | ≥2 lines, each `<label> <value>` split on one space into exactly 2 tokens; token 2 of line 1 = AP SSID, token 2 of line 2 = PSK (≥8 chars) | turn the frame into a SoftAP (`com.adrenaline.wifi.WifiApManager.SetWifiApEnabled`) |
| `WifiReset.txt` | (presence) | clear stored SSID/PSK in `SetupData.json` and re-save |
| `NoWifi.txt` | (presence) | disable Wi-Fi |
| `FactoryReset.txt` | (presence) | factory reset |
| `ToggleOrientation.txt` | (presence) | flip portrait/landscape |
| `ForceReboot.txt` | (presence) | reboot |
| `Memento_Setup.txt` | a `SetupInfo` record | copied over the app's setup file, then applied |
| `Maintenance.txt` | exactly 10 lines, line[4] == `MeMento`, line[5] == `Maintenance` | maintenance mode |
| `TryAndBuy.txt` | exactly 10 lines, line[4] == `MeMento`, line[5] == `TryAndBuy` | demo mode |
| `RedBull_Illume.txt` | (presence) | a customer-specific mode |
| `GUID.txt` | frame GUID | identity, also used as the cloud `frameId` |

`Maintenance.txt`/`TryAndBuy.txt` are the only ones with any secret, and it is a hard-coded
string in the app.

### F7. Wi-Fi: RTL8188EU on USB — 2.4 GHz only, single stream, out-of-tree driver

- `CONFIG_RTL8188EU=m`; the module is shipped as `/system/lib/8188eu.ko` (1,058,713 bytes,
  `vermagic=3.10.33 SMP preempt mod_unload ARMv7`, `description=Realtek Wireless Lan Driver`,
  `srcversion=263471B8F8F071EEF9D581A`). The USB code paths are live in it (`rtw_enusbss`,
  `rtw_usb_rxagg_mode`, `Switch USB Mode`), so this is the USB part, not the SDIO sibling.
- DTB has a `wifi_power` node only (`power_gpio = "GPIOY_5"`, `power_gpio2 = "GPIOAO_6"`) —
  power control, no bus node, consistent with a USB-attached radio.
- `build.prop`: `wifi.interface=wlan0`, `ro.wifi.channels=11`, `net.ethwifi.coexist=true`,
  `net.ethwifi.prior=eth0` (Ethernet is `status = "disabled"` in the DTB, so this is moot).
- `system/etc/wifi/wpa_supplicant.conf` is stock and `update_config=1`, so credentials are
  written at runtime to `/data/misc/wifi/wpa_supplicant.conf`, not baked into the image.

**Consequences for #74 C.** The existing radio is 802.11n 1×1 on 2.4 GHz. There is no 5 GHz
option without new hardware, whatever OS runs. `rtl8188eu` is the driver with the worst
reputation in this class for exactly the symptom #73 describes; a mainline port (option 2)
would get the in-tree `r8188eu` staging driver, which is different code and might behave
better, but that is a hypothesis, not a fix.

Credentials are also duplicated in the app's own config: `ConfigData` carries `s_WiFiSSID`
and `s_WiFiPSWD` in plaintext inside `SetupData.json`, and `GetConfig` returns them over the
LAN protocol (DES-encrypted in transit, with a hard-coded key — see `docs/protocol.md`).

### F8. The app's Wi-Fi watchdog exists but is never instantiated — it is *not* causing #73

`CadreAndroid.WifiWatchdog` is a complete radio-cycling watchdog: on
`SupplicantState.Disconnected` it calls `DisableWiFi()` (`WifiManager.SetWifiEnabled(false)`),
schedules `EnableWiFi()` 5000 ms later (`mso_WiFiStateChanged_Timer`), then retries
`TryToConnect()` up to `mi_RetryCount` (default 3) times every `mf_RetryTime` (default 10) s,
calling `EnableNetwork` + `SaveConfiguration`. If that ran, it would produce a fresh
association — and so a fresh DHCP DISCOVER — on every supplicant blip, which is a very good
fit for #73's four leases in a day.

It does not run. `MainActivity.mo_WatchDog` is read in `Update`, `OnPause`, `OnDestroy`,
`InstallUsbUpdate` and `DownloadWebClientThread`, and assigned **only `null`**. There is no
`newobj CadreAndroid.WifiWatchdog::.ctor` anywhere in the assembly (contrast
`ServerImageDownload`, which *is* constructed, at `MainActivity` IL_026b). The delegate
interface `IWatchdogDelegate` is implemented by `MainActivity` but never called.

So the app-level explanation for #73 is ruled out for firmware 6.02, and the remaining
candidates are the `rtl8188eu` driver/firmware, the radio itself, and the AP. That narrowing
matters for #74's justification: it makes "our own firmware fixes it" contingent on the
driver being at fault, which is still unproven. See Q4.

### F9. An undocumented cloud pull channel — the frame fetches and displays photos on its own

`CadreAndroid.ServerImageDownload` is constructed and `Start()`ed by `MainActivity`, and polls
on `CadreServerCore.TimerInterval`:

| endpoint | method / body | purpose |
|----------|---------------|---------|
| `https://pictureshare.mementosmartframe.com/image.downloadurl` | POST `application/json` `{"frameId":"<guid>"}` → JSON `{"url": …}` | ask for the next queued image |
| *(that `url`)* | GET via `System.Net.WebClient.DownloadData` | fetch the image; reads response headers `X-Amz-Meta-Filename` and `X-Amz-Meta-Orientation` (an S3 object) |
| `https://pictureshare.mementosmartframe.com/image.downloaded` | POST `{"frameId":…,"imageName":…}` | acknowledge |
| `https://pictureshare.mementosmartframe.com/image.delete` | POST `{"frameId":…,"imageName":…}` | delete from the bucket |
| `https://pictureshare.mementosmartframe.com/frame.reset` | POST `{"frameId":…}` | revoke remote access |
| `https://builds.mementosmartframe.com/api/time.php?iso` | GET | clock sync |
| `http://clients3.google.com/generate_204` | GET | Android connectivity check |

The download lands as `DownloadImage.tmp` in `/mnt/sdcard/Photos/`, is renamed, added to the
reserved album **`Remote_$%^&(*@#!`**, given `{"orientation":N}` via
`CadreServerCore.SetNewImageInfo`, and **displayed immediately**.

`frameId` is the frame's GUID, read from `GUID.txt`. The feature's on/off state is
`CadreServerCore.mb_ServerImageDownload`, which is exactly the `ServerImageDownload` boolean
already visible in the discovery reply — `docs/protocol.md` listed the flag without knowing
what it switched.

That last `clients3.google.com` line is worth flagging: `docs/protocol.md` records that when
the frame is in its slow ~21 s tick mode, its connectivity check to `clients3.google.com` is
stuck half-open. This is the code that makes that request.

**For #74 F.** A server-push path already exists in the device: poll an endpoint, get a URL,
download, display, acknowledge. If a future Slyde build wants the frame to pull rather than be
pushed to, this is the shape the vendor already chose, and it maps onto Slyde's existing
served-backend pattern. It is also, in principle, hijackable by DNS on the LAN without
touching the device at all — the endpoints are plain HTTPS to a domain we do not control, so
that would need a cert the frame accepts; not free, but noted.

### F10. Boot chain and package layout

**`updater-script`** (Edify), in full order:

```
assert build date >= 1501179828 and ro.product.device == "n301"
set_bootloader_env("upgrade_step", "3")        # arm the U-Boot update path
format ext4 /dev/block/system ; mount /system
package_extract_dir("recovery", "/system")     # -> /system/recovery-from-boot.p
package_extract_dir("system",   "/system")
symlink busybox -> ~350 applets in /system/xbin ; toolbox -> /system/bin ; mksh -> sh
write_raw_image(logo.img, "logo")
set_metadata_recursive(...)                    # incl. SELinux labels
unmount("/system")
write_raw_image(boot.img,       "boot")
write_raw_image(bootloader.img, "bootloader")  # the bootloader IS rewritten by an OTA
set_bootloader_env("upgrade_step", "1")        # next boot: defenv, step 2, saveenv
```

`upgrade_step` is the interlock: `3` = "an update is pending, run it"; `1` = "the update
landed, reset the environment to defaults and mark it done" (`upgrade_check`, F3).

**`bootloader.img`** — 377,216 bytes. Layout:

| range | contents |
|-------|----------|
| 0x00000–0x07dff | Amlogic M8 BL1/TPL, plain ARM, uncompressed. DDR training, `ucl decompress...`, `load_uboot`, `Aml log : M8-TPL-SEC-DEC-1/2`, build stamp `Jan 23 2018 12:35:45`, `M3HHREV0` at 0x1b0, `M8BL32KBRS10` at 0x7ff0 |
| 0x07e00–0x07fff | Amlogic descriptor: total size 0x5c180, timestamps `2018/01/23 12:35:53`, `AMLC` marker |
| 0x08000–end | uclpack container: magic `\x00\xe9UCL\xff\x01\x1a`, version 301 (UCL 1.03), method 7, block size 0x40000; 256 KiB blocks, each `BE32 uncompressed_len, BE32 compressed_len, data`, **NRV2D** with an 8-bit bit buffer |

Decompressed U-Boot: **759,524 bytes**, which matches `_end_ofs` (0xb96e4) in its own header
at offset 0x4c — an internal consistency check that the decompression is exact. Identity:
`U-boot(g9tv_n301_v1@)(Jan 23 2018-12:35:44)`, built by `moment`, chip `g9tv`, board
`g9tv_board`. Vendor source tree, from paths left in both stages:
`/home/moment/Unity_Production/01599_Adrenaline_Cadre_Android/amlandroid/uboot/...`.

**eMMC partition table**, read out of U-Boot (`reversing/firmware-teardown/emmc-partitions.txt`):

```
bootloader   4 MiB      logo      32 MiB     system  1024 MiB
reserved    64 MiB      recovery  32 MiB     cache    256 MiB
cache        0          misc      32 MiB     param    128 MiB
env          8 MiB      boot      32 MiB     cri_data  16 MiB
                                             data      AUTO
```

`fstab` in the ramdisk mounts `/system`, `/data`, `/cache`, `/param` by name from
`/dev/block/`; `/system` is ext4, read-only.

**`boot.img`** — 6,662,808 bytes, sha1 `3da983bef1cba79baf67e05670e66d139a0bfbfd`. That sha1
is independently confirmed by `recovery/etc/install-recovery.sh` inside the package, which
names the same digest and size for the boot partition. Page size 2048; kernel 6,044,766 B
@ 0x10008000; ramdisk 591,311 B @ 0x11000000; second stage 22,631 B @ 0x10f00000 — the
**second stage is the DTB**, not a second bootloader. The header carries **no kernel cmdline**;
the cmdline comes entirely from U-Boot's `bootargs`.

**Kernel** — a U-Boot uImage (`Linux-3.10.33`, load/entry 0x00208000, comp = 4 = LZO) wrapping
an lzop stream; 11,085,888 bytes decompressed.
`Linux version 3.10.33 (moment@Ubuntu-Moment-SL) (gcc 4.4.7 Ubuntu/Linaro) #52 SMP PREEMPT
Fri Feb 9 13:31:10 EST 2018`. `CONFIG_IKCONFIG_PROC` is on, so the whole `.config` is
recoverable and is committed as `reversing/firmware-teardown/kernel.config`.

**SoC** — DTB root: `compatible = "AMLOGIC,T868_G9TV"`, `model = "AMLOGIC G9TV T868"`,
four Cortex-A9 cores. This refines #74's "Amlogic Meson8": the part is the **T868 / G9TV**,
the TV-oriented member of the Meson8 family (`ro.board.platform=meson8`,
`ro.product.device=n301`).

**Dates are not uniform**, which is worth knowing before anyone reasons about "the 2017
build": `system/` is 2017-07-27, the bootloader is 2018-01-23, and the kernel is 2018-02-09.
This OTA ships a later boot chain on top of an older Android tree.

**Board detail** worth having on record: `adc_keypad` on SARADC channels 0/1/3 provides seven
keys (`power`, `menu`, `source`, `volume+/-`, `channel+`); backlight is PWM_B on `GPIOY_7`
with enable on `GPIOY_6`, 160 Hz, levels 0–160; panel power on `GPIOH_10`; there is an IR
receiver (`meson-remote`); HDMI TX and Ethernet are both `status = "disable(d)"`; the DTB has
`efuse`, `securitykey` and `amlogic-watchdog` nodes.

### F11. System image: no `su`, and SELinux is defeatable from the boot args

- **No `su` anywhere.** `/system/xbin` ships only `busybox` and `dexdump` (the ~350 applet
  names in `updater-script` are symlinks to busybox). No SuperSU, no `/system/bin/su`.
- `default.prop`: `ro.secure=1`, `ro.debuggable=0`, `persist.sys.usb.config=mtp`.
  `build.prop`: `ro.adb.secure=1`, and `#service.adb.tcp.port=5555` is commented out.
- `adbd` is in the ramdisk at `/sbin/adbd` but `disabled` in `init.rc`; it is started by the
  `sys.usb.config` property triggers in `init.usb.rc`. Default USB config is MTP, so adbd is
  not running at boot.
- **SELinux is compiled in and enforcing by default, but the boot argument is honoured.**
  `CONFIG_SECURITY_SELINUX=y`, `CONFIG_DEFAULT_SECURITY="selinux"`,
  `CONFIG_SECURITY_SELINUX_DEVELOP=y`, `CONFIG_SECURITY_SELINUX_DISABLE` and
  `..._BOOTPARAM` not set. The ramdisk `init` binary contains `ro.boot.selinux`,
  `permissive`, `enforcing` and `SELinux: Unknown value of ro.boot.selinux. Got: "%s".
  Assuming enforcing.` — i.e. this `user` build was compiled **with** `ALLOW_DISABLE_SELINUX`.
  U-Boot's `bootargs` carries no `androidboot.selinux=`, so the shipped state is enforcing;
  adding `androidboot.selinux=permissive` to `bootargs` would make it permissive, and
  `bootargs` is writable from Android via the `ubootenv.var.*` property bridge
  (`ro.mtd.ubootenv=ubootenv`, `ro.ubootenv.varible.prefix=ubootenv.var`) or `fw_setenv`
  (`/system/etc/fw_env.config` points at `/dev/mtd1` and `/dev/mtd2`, 0x4000 each).
- `OTAUpgrade.apk` is **Amlogic stock** — `com.amlogic.update.*` and
  `com.amlapp.update.otaupgrade.*`, with `--update_package=`, `Sorry! Your cpu is not
  Amlogic,u can't run`. Nothing custom rides on it, confirming #75's suspicion.
- The frame's own native helper classes are `com.adrenaline.imagedisplay.{ImagePlayer,
  FullscreenImageDisplay,OTAUpdateHelper}` and `com.adrenaline.wifi.WifiApManager` in
  `Cadre.apk`'s `classes.dex`; the Xamarin layer calls into them.
- Storage: `/mnt/sdcard` is the app's data path (`SetupData.json`, `AlbumData.json`,
  `CurrentAlbum.json`, `GUID.txt`, `ShuffleList.txt`, `ImageTimer.txt`), photos in
  `/mnt/sdcard/Photos/`, hardware descriptors in `/system/etc/` (`CadreHardware.json`:
  35", 3240×2160, landscape; `CadreSoftware.json`: 6.02).

### F12. The Windows client is the same Unity app, not a second implementation

`smartframepc.exe` is an Inno Setup 5.5.7 installer (`ProductName = Memento SmartFrame`,
`ProductVersion = 6.0`, Authenticode-signed by Memento Electronics via Comodo). Its LZMA1
payload (props `5d 00 00 80 00`, 8 MiB dictionary, immediately after the `zlb\x1a` magic at
0xde00) decompresses with stdlib `lzma` in `FORMAT_ALONE` and contains **UnityPlayer,
mono.dll, `/UnityShaderCache/`, `Assembly-CSharp`** and `com.Memento.SmartFrame`.

So it is the same Unity/Sarbakan codebase as the phone app, built for Windows — not an
independent implementation of the protocol. #75's hope that it would provide a cross-check or
a different cloud/OTA URL scheme does not hold, and `reversing/decompiled/` (already extracted
from `Memento_6.0.apk`) is the same source. No further work is warranted here; `innoextract`
would only produce files we already have.

### F13. The frame's complete command surface, from the server side

Recovered from `CadreAndroid.dll`'s metadata; full listing in
`reversing/firmware-teardown/cadre-enums.txt`. This **matches `docs/protocol.md`'s three
`Action` enums exactly**, which is worth stating: the protocol doc was written from the phone
app, and the frame's own code agrees with it value for value. Corrections and additions that
did come out of this pass are listed under "Corrections to `docs/protocol.md`" below.

Server-side limits (`Cadre.Albums`, `Cadre.SetupData`, `Cadre.SocketState`):
`MAX_ALBUMS` 67, `MAX_IMAGES` 3000, `MAX_FILENAME_LENGTH` 64, thumbnails 256×170,
`MAX_IMAGE_WIDTH/HEIGHT` 15000×10000, `GUID_LENGTH` 36, transfer buffers 256 KiB send /
1 MiB receive on the file socket, `APP_VERSION` 6.0, `ENCRYPT_VERSION` 5.0, image canvas
3240×2160, `REBOOT_WAIT_HOURS` 23 / `REBOOT_WAIT_MINUTES` 30 (the frame reboots itself daily).

---

## Direct answers to #74

#74 asks A–G (#75 says A–F). Answered where the firmware settles it.

**A — strategy.** The firmware moves the balance against option 3 and clarifies the risk in
option 2.

- *Option 1, modified stock.* Now clearly the cheapest first move. We hold genuine
  `bootloader.img`, `boot.img`, `logo.img` and the complete `system/` tree, the exact
  partition table (F10), and — critically — a write path that does not need the vendor key
  (F3). "Patch `system/`, keep the vendor kernel and DTB" avoids every display-bring-up risk.
- *Option 2, new OS on the same board.* The blocker is not panel timings, which are in hand
  (F1); it is the V-by-One HS link setup, which lives in Amlogic's out-of-tree `lcd_tv` driver
  and is partly computed at runtime (`lcd_timing` starting `0xffffffff`). Meson8/T868 mainline
  support is thin, and the Wi-Fi part would move to the staging `r8188eu` driver. High effort,
  real display risk.
- *Option 3, replace the board.* Materially worse than #74 assumed. The panel is 8-lane
  V-by-One HS at 3840×2160 (F1) — neither LVDS nor eDP, no cheap Pi bridge. It needs a 4K
  V-by-One T-CON/scaler board, so "the panel model" is no longer the only unknown.

**Recommendation for the spec: option 1 for v1**, with option 3 reconsidered only if the frame
is ever opened and a V-by-One driver board turns out to be available cheaply.

**B — flashing and un-bricking.** Partly settled, and the news is good.

- A route exists that bypasses the OTA trust store entirely: **`reboot update` (or
  `upgrade_step=3`) + a FAT SD card carrying `aml_sdc_burn.ini`**, which runs U-Boot's
  `sdc_burn` raw partition burner. Fallbacks on the same path execute an unsigned
  `aml_autoscript` or boot an unsigned `recovery.img` (F3).
- The vendor's own documented recovery — USB stick with `Update.txt` + `Memento_35.zip` — is
  **not** usable for our own images: it goes through `RecoverySystem.verifyPackage` against
  `otacerts.zip` (F2, F6).
- `run usb_burning` is a dead branch in the stock env (F3), so Amlogic USB burn has to be
  entered by the ROM or the U-Boot console.
- **Secure boot fuse state is not answerable from these bytes** — it is OTP silicon state. See
  Q3 for what the images *do* suggest and the experiment that would settle it.
- **A verified recovery route does not yet exist**, because F3 depends on an SD slot being
  present and reachable (Q1). Until Q1 and Q3 are answered on the actual unit, #74's own
  gate ("a verified recovery route must exist before the first write") is not met.

**C — Wi-Fi and `ktown`.** Chipset settled, cause of #73 narrowed but not settled.

- Chipset: **Realtek RTL8188EU on USB**, 802.11n 1×1, **2.4 GHz only** (F7). No 5 GHz without
  new hardware under any of the three options.
- Provisioning at runtime is already how the vendor does it: `wpa_supplicant.conf` with
  `update_config=1` writing to `/data`, plus the app's own `ConfigData.s_WiFiSSID` /
  `s_WiFiPSWD` and the USB `WifiSetup.txt` trigger (F6). Credentials are not compiled in, so
  #74's SlyLED-ESP32 friction does not apply here; it should not be re-introduced.
- The app-level watchdog that would have explained the flapping is **dead code** (F8). This
  removes the most convenient hypothesis and leaves the driver, the radio, and the AP.
- The spec should therefore state plainly that "our own firmware fixes #73" is **conditional**:
  it holds if the fault is `rtl8188eu`, and not if it is the radio or the AP. Q4 names the
  experiment that separates them, and it can be run before any firmware work starts.

**D — OTA design.** The firmware does not constrain the design, but it supplies three lessons
worth writing into the spec:

- *This is the exact failure we are designing against.* The trust store holds a certificate
  valid until **2043** whose private key died with the vendor (F2). Signing-key custody is not
  a footnote.
- *Dual-bank is not what the vendor did.* Its update is a single-shot `format` + write of
  `/system` with a `upgrade_step` flag as the only interlock (F10). One interrupted write
  leaves no way back except F3.
- *The device already knows how to pull.* F9 is a working poll/fetch/ack loop; a Slyde-hosted
  version of that shape needs no new concept on the device side.

**E — identity and provisioning.** The vendor's frame identity is a **GUID** in
`/mnt/sdcard/GUID.txt` (36 chars, `SetupData.GUID_LENGTH`), which doubles as the cloud
`frameId` (F9) and appears in the discovery reply. First-boot setup runs from
`SetupActivity`/`SetupInfo` (`ms_AccessPointName`, `ms_FrameName`, `ms_TimeZone`,
`ms_WiFiName`, `ms_WiFiPass`, `ms_Security`, `ms_PortraitMode`), seedable from USB via
`Memento_Setup.txt`, and the frame can raise its own SoftAP for provisioning
(`WifiHost.txt` → `WifiApManager`). Factory reset is `FactoryReset.txt`, the
`CommandControlFlow.FactoryReset` LAN command, or `reboot factory_reset` into recovery.

**F — what Slyde provides.** F9 settles the precedent: the vendor put the queue on the server
and the poll on the device, with an explicit acknowledgement and delete. That is the same
split Slyde's served-backend pattern already implements.

**G — risk and go/no-go.** The firmware supports a narrower, cheaper first phase than #74
proposes, and it makes the case for stopping earlier if two things do not check out.
Concretely: **do not write anything to the device until Q1 (SD slot) or Q3 (a proven ROM/USB
recovery path) is answered.** Both are cheap, and both are answered by looking at the hardware
rather than by more analysis.

---

## Open questions

What the bytes could not answer, with the experiment that would.

**Q1 — Is there an SD-card slot on the unit, wired to `mmc 0`?**
This gates the whole of F3 and therefore #74 B. *Experiment:* look at the frame's back and
edges for a microSD/SD slot. If one exists, prove the path read-only first: put a FAT card
with **only** `aml_autoscript` on it (a script that prints a banner and returns — no writes),
issue `reboot update`, and watch the screen or serial console. Do not put `aml_sdc_burn.ini`
on a card until a full backup exists.

**Q2 — Are UART pads reachable inside the enclosure?**
`console=ttyS0,115200n8` with `bootdelay=1` means a serial console gives the U-Boot prompt,
`printenv`/`setenv`, and the `update` USB-burn command — the most controllable route to
everything in F3. *Experiment:* open the back and look for a 3- or 4-pin header near the SoC;
identify with a scope or a USB-TTL adapter at 115200 8N1.

**Q3 — Is secure boot fused on this SoC?**
**Not answerable from firmware images** — fuse state is OTP silicon state, not image content.
What the images *do* say, as inference rather than fact: the shipped `bootloader.img` carries
no Amlogic secure-boot wrapper (no `@AML` magic, no TOC/signature block; plain ARM vectors at
0x0 and a bare UCL container at 0x8000), and this OTA rewrites the `bootloader` partition with
that unsigned image (F10) — which would brick every fused unit in the field. That is real
evidence the vendor's production parts were left unfused, but it is inference. U-Boot does
carry `efuse secure_boot_set`, so the capability exists.
*Experiment:* on a serial console, `efuse info` / `efuse dump` reports the chip's efuse state
directly. Failing that, attempting an Amlogic USB-burn with an unsigned image and observing
whether the ROM accepts it settles it — but that is a destructive test and must wait for Q1/Q2.

**Q4 — Is #73's flapping the `rtl8188eu` driver, the radio, or the AP?**
F8 rules out the app. *Experiment, no firmware work needed:* (a) put a DHCP reservation on the
frame's MAC and see whether the address churn stops while the disconnects continue — that
separates "loses association" from "gets a new lease"; (b) log the AP's association/deauth
events for the frame's MAC for 48 h and classify the deauth reason codes; (c) compare against
a second RTL8188EU dongle on the same AP. Define #74's reliability bar from (b)'s numbers.

**Q5 — Does the panel assembly contain a digitizer that this build simply cannot drive?**
F4 settles that the *firmware* has no touch support; it cannot settle the hardware.
*Experiment:* open the frame and look for a touch controller and its I²C/interrupt lines. Only
worth doing if it is opened for Q1/Q2 anyway.

**Q6 — Reconstruct `recovery.img` and confirm how recovery verifies packages.**
Left undone deliberately: F2 plus `RecoverySystem.verifyPackage` already settle the question
recovery would have answered, and the patch format is real work. It is fully specified if
anyone wants it: `recovery/recovery-from-boot.p` is `IMGDIFF2` (738,556 bytes), and
`recovery/etc/install-recovery.sh` names the exact operation and the checkable result —
apply it to `boot.img` (6,662,808 B, sha1 `3da983be…0bfbfd`) with
`/system/etc/recovery-resource.dat` as bonus data, expecting **7,818,558 bytes, sha1
`1b89eeb3ded92a64026ecf69179a6dc146c8abeb`**. Implementing an IMGDIFF2 applier (bsdiff plus
chunked deflate re-compression) is the cost.

**Q7 — Are the `pictureshare.mementosmartframe.com` endpoints actually dead?**
F9 gives the exact URLs and request bodies; #72 reports the cloud API as dead but the
`pictureshare` host specifically has not been probed. *Experiment:* resolve the host and POST
`{"frameId":"<our frame's GUID>"}` to `/image.downloadurl` from a workstation.

**Q8 — Does `Backup.txt` behave as the IL says on the real frame?**
F5 is decoded, not observed. *Experiment:* FAT32 stick, empty `Backup.txt` in the root, plug
in, reboot, watch the on-screen progress text. Use an empty stick — the routine deletes any
existing `Memento_<name>` folder first. If it works it is the fastest way to close out #72's
remaining 131 photos.

---

## Corrections to `docs/protocol.md`

Landed in that file in the same change as this document.

1. **`utf8_name` was missing** from the discovery reply's JSON fields. The frame's own
   responder (`Cadre.ReceiveBroadcastThread.ListernerCallback`) emits, in order:
   `name`, `utf8_name`, `softver`, `hardver`, `size`, `orientation`, `ip`, `mac`,
   `IsConnected`, `TryAndBuyMode`, `guid`, `ServerImageDownload`, `hasInternet`.
2. **The discovery reply is AES-encrypted on 6.02** — `protocol.md` listed this as an open
   item. The responder calls `Cadre.Utils.Encrypt` (the AES path) unconditionally.
3. **`GetConfig`/`SendConfig` schema is now known** — the `ConfigData` field list, 36 fields,
   including the frame's stored Wi-Fi SSID and passphrase.
4. **`m_FileInfoJSON` / `info{}` is now known** — `CommandControlTransferFile.ParseJson` reads
   `srcfilename`, `dstfilename`, `info` (default `{}`) and `filesize`, while `EncryptJson`
   only ever *emits* the first, second and fourth. The only key the frame consumes from
   `info` is `orientation`, an `ImageMode` int (0 Normal, 1 Portrait, 2 Panoramique).
5. **`ServerImageDownload` explained** — F9; it is a cloud pull channel, not just a flag.
6. The three `Action` enums, `CommandBroadcast.Commands` and every server-side limit
   **confirmed unchanged** against the frame's own code.
