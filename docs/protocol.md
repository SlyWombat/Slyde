# Memento Smart Frame — Local Network Protocol

Reverse-engineered from the official Windows app (`Assembly-CSharp.dll`, Unity/Sarbakan,
namespace `Cadre`), then **reconciled against the frame's own server-side code** in #75
(`system/app/Cadre.apk` → `assemblies/CadreAndroid.dll`, disassembled with
`reversing/tools/cildasm.py`). The three `Action` enums, `CommandBroadcast.Commands` and every
limit below matched value for value; the additions #75 produced are marked inline.
The frame is the **server**; the app/our tool is the **client**.
All of this runs purely on the LAN — no cloud needed (firmware 6.02+); the frame does however
have a cloud pull channel of its own, documented at the end.

## Ports (TCP/UDP)
| Port | Proto | Purpose | Source |
|------|-------|---------|--------|
| 2015 | UDP   | Discovery request (client → broadcast) | `SocketState.msi_BroadcastPort` |
| 2016 | UDP   | Discovery response (frame → client)    | `SocketState.msi_ResponseBroadcastPort` |
| 2017 | TCP   | Control channel (JSON commands)        | `SocketState.msi_TransferPort` |
| 2018 | TCP   | File transfer (raw byte stream)        | `SocketState.msi_FileTransferPort` |

Socket tuning on 2017: `TTL=42`, `TCP_NODELAY`, 1000 ms send/recv timeout, 256 KiB buffers.

## Versions
- `APP_VERSION = 6`, `ENCRYPT_VERSION = 5`. Frames running softver ≥ 5 expect command
  data sub-payloads to be **DES-encrypted** (see Crypto). Our frame is 6.x → encryption ON.

## Crypto (recovered from `Cadre.Utils`)

### AES — used for the discovery broadcast payload ("secure" frames)
- Algorithm: AES-CBC, PKCS7 padding, 256-bit key.
- Key/IV via PBKDF2 (`Rfc2898DeriveBytes`, **HMAC-SHA1**, default **1000** iterations):
  - password = `"otnemeM"`  ("Memento" reversed)
  - salt = bytes `[101,109,97,114,102,116,114,97,109,83]` = ASCII `"emarftramS"` ("SmartFrame" reversed)
  - Key = first 32 bytes of the stream; IV = next 16 bytes (same generator, sequential).
- Plaintext is **UTF-16LE** (".NET Unicode"); ciphertext is Base64.
- On decode, ` ` (space) is restored to `+` before Base64 decode.

### DES — used for command data sub-payloads (`FastEncrypt`/`FastDecrypt`)
- Algorithm: DES-CBC, PKCS7. Key = `"M3m3nt0 "` (bytes `4D 33 6D 33 6E 74 30 20`),
  IV = `"UHDFram3"` (bytes `55 48 44 46 72 61 6D 33`).
- Plaintext UTF-16LE, ciphertext Base64.
- A field is considered "encrypted" iff it does **not** start with `{`. Plain JSON (`{...}`)
  is passed through unencrypted (older frames). For softver ≥ 5 the app always DES-encrypts.

## Discovery handshake
1. Client sends a UDP datagram to `255.255.255.255:2015`, ASCII body:
   ```
   MEMENTO_SMARTFRAME_<broadcastID>|<APP_VERSION>|<EOF>
   ```
   e.g. `MEMENTO_SMARTFRAME_1|6|<EOF>`. Sent from each local IPv4 interface (ephemeral src port).
   Repeated ~once per second while the connect dialog is open.
2. Client listens on UDP `2016`. Frame replies with either plaintext or AES-encrypted body.
   After AES-decrypt (if needed) the body is pipe-split into 3 parts:
   ```
   MEMENTO_SMARTFRAME|<json>|<trailer>
   ```
3. `<json>` fields. The frame's own responder (`Cadre.ReceiveBroadcastThread.ListernerCallback`
   in `CadreAndroid.dll`, recovered in #75) emits them in this order:
   `name`, **`utf8_name`**, `softver`, `hardver`, `size`, `orientation`, `ip`, `mac`,
   `IsConnected` (bool), `TryAndBuyMode` (bool), `guid`, `ServerImageDownload` (bool),
   `hasInternet` (bool). `utf8_name` is `name` put through `Utils.EncodeJSONUTF8String`, and
   was missing from this list. `Utils.ProcessBroadcast` on the client accepts a reply only if
   name, softver, hardver, size, orientation and ip are all present/non-zero.
   **The reply is always AES-encrypted on 6.02** — the responder calls `Cadre.Utils.Encrypt`
   unconditionally. `ServerImageDownload` is not merely a flag: see "Cloud pull channel" below.

## Control channel (TCP 2017) — message framing
Every message, both directions, is:
```
<.NET type FullName>|<JSON object>|<commandID>|<EOF>
```
- Receiver splits on `|`: `[0]`=type name, `[1]`=JSON, `[2]`=int command id.
- Messages are concatenated on the stream and delimited by the literal `<EOF>`.
- `JsonConvert.DeserializeObject(json, Type.GetType(typeName))` then `ClientExecute(id)`.
- Special control line `COMMUNICATION_ENDED` (no JSON) terminates the session.
- The `m_Socket` field is stripped before serialize (always send `null`/omit it).

Three command classes (all in namespace `Cadre`, so type name = `Cadre.<Class>`):

### `Cadre.CommandControlFlow`
Fields: `m_Action` (int enum), `m_SourceFileName`, `m_UpdateUrl`, `m_UpdateMd5`,
`m_Data` (DES-encrypted JSON: `{srcfilename, filenames[], url, md5}`), `m_Filenames[]`.
Action enum: `0 Beacon, 1 BeaconDone, 2 NextFrame, 3 NextFrameDone, 4 PreviousFrame,
5 PreviousFrameDone, 6 DisplayImage, 7 DisplayImageDone, 8 DeleteImage, 9 DeleteImageDone,
10 GetCurrentImageName, 11 …Done, 12 SendCurrentImageName, 13 …Done, 14 ForgetNetwork,
15 …Done, 16 FactoryReset, 17 …Done, 18 TriggerUpdate, 19 …Done, 20 Disconnect, 21 DisconnectDone`.
Client sends the even (request) value; frame replies with the +1 (…Done) value.

### `Cadre.CommandChangeSetup`
Fields: `m_Action` (int enum), `sData` (DES-encrypted JSON config payload).
Action enum: `0 GetConfig, 2 SendConfig, 4 GetCurrentAlbum, 6 SendCurrentAlbum, 8 SendTime,
10 ChangeBrightness, 12 ChangeCalibration, 14 ChangeEvening, 16 ChangePower, 18 ChangeShuffle,
20 ChangePictureDuration, 22 ChangeThreshold, 24 ChangeContrast, 26 ChangeExposure,
28 ChangeSaturation, 30 ChangeTimeZone, 32 ChangeOrientation, 34 ChangeTemperature,
36 GetFrameTime` (+1 = the corresponding …Done reply carrying data in `sData`).

### `Cadre.CommandControlTransferFile`
Fields (confirmed against the frame's own field table, #75): `m_Action` (int enum),
`m_DestinationFileName`, `m_SourceFileName`, `m_FileInfoJSON`,
`m_Data` (DES-encrypted JSON: `{srcfilename, dstfilename, filesize, info{}}`),
`m_FileSize`, `mb_TransferingFileSuccess`.

> **`info{}` resolved (#75).** The frame's `ParseJson` reads four keys — `srcfilename`,
> `dstfilename`, `info` (defaulting to `{}` when absent) and `filesize` — while its
> `EncryptJson` only ever *emits* `srcfilename`, `dstfilename` and `filesize`. The single key
> the frame consumes from `info` is **`orientation`**, an `ImageMode` int:
> `0 Normal, 1 Portrait, 2 Panoramique` (`CadreAndroid.ServerImageDownload.AddImage` builds
> exactly `{"orientation":N}` and hands it to `CadreServerCore.SetNewImageInfo`). Omitting
> `info` is safe.
Action enum (groups of 5: base, Started, Ended, Succeeded, Failed):
`0 ReadFile…, 5 WriteFile…, 10 GetThumbnailsList…, 15 GetThumbnails…, 20 GetAlbums…, 25 SendAlbums…`.

## File transfer (TCP 2018) — raw stream, length pre-announced
There is **no framing** on 2018. The exact byte count is announced in the 2017 control
message's `filesize`, then exactly that many bytes flow on 2018.

### Upload an image (client → frame)
1. `2017 →` `Cadre.CommandControlTransferFile` `m_Action=WriteFile(5)`,
   `m_Data = DES({"srcfilename","dstfilename","filesize":"<n>","info":{}})`.
2. `2017 ←` frame replies `WriteFileStarted(6)`.
3. `2018 →` client streams the raw file bytes (≤256 KiB chunks) until `filesize` sent.
4. `2017 →` client sends `WriteFileEnded(7)` with `{srcfilename,dstfilename,filesize}`.
5. `2017 ←` frame replies `WriteFileSucceeded(8)` or `WriteFileFailed(9)`.
   On success the frame stores the photo; a `<name>.thumb.png` becomes available.

### Download (frame → client): ReadFile / GetThumbnails / GetThumbnailsList / GetAlbums
Symmetric: client sends the base action; frame replies `…Started` with `filesize`; client
reads exactly that many bytes on 2018; client sends `…Ended`; frame replies `…Succeeded`.
- `GetAlbums` / `GetThumbnailsList` return JSON/data files describing the frame's library.
- `GetThumbnails` returns `<name>.thumb.png` images — **256×170**, ~50-80 KB.

> **`ReadFile` needs two things the other download paths hide (verified 2026-08-29, #72).** It
> works on firmware 6.02 — an earlier note here said it didn't, which was wrong: both failures were
> caller-side. Recovered by decompiling the frame's own `system/app/Cadre.apk` →
> `assemblies/CadreAndroid.dll`:
>
> 1. **An absolute path.** `ServerExecute` (action 0) hands `m_DestinationFileName` straight to
>    `File.Open`. `GetThumbnails` (action 15) instead resolves a bare name via
>    `CadreServerCore.GetThumbnail` → `Utils.AppendImageDir`; `ReadFile` does no resolution at all,
>    so a bare filename fails to open and the transfer dies with no error on the wire. Photos live
>    under **`/mnt/sdcard/Photos/`** (`Utils..cctor`: `ms_ImageDir = <external storage> + "/Photos/"`).
> 2. **A non-zero `filesize` in the request.** `ReadFile` never stats the file — it streams from the
>    size the *client* announces. `FileServer.SendFile` begins with
>    `if (fileSize == 0) { log "Error: File size is 0 byte "; return; }`, so a size-less request is
>    abandoned before the file is even opened. That is what produced `ReadFileStarted` with
>    `m_FileSize = 0` and no bytes.
>
> No protocol call reports a photo's true length (`ThumbnailsList.txt` carries md5s, not sizes), so
> a client must **over-announce and read until the frame goes quiet**, trimming at the JPEG
> end-of-image marker. Over-announcing is safe precisely because the frame never compares the
> announced size to the file. Implemented in `memento_core.client.download_image`.
>
> Verified live: `/mnt/sdcard/Photos/0000868046_og.jpg` → 423,308 bytes → **3240x2160** JPEG.
>
> Request keys, from the frame's `ParseJson`: `srcfilename`, `dstfilename`, `info`, `filesize`
> (a **string**, read with `Int32.TryParse`).

## The service tick — a NEW connection is served only every ~21s (2026-08-26, #71)

Measured on "Living Room" @ 192.168.10.141, fw 6.02, and it dominates every timeout decision:

- **The frame services a newly opened control connection only once per ~20.7s tick, phase-locked.**
  Eight connects at staggered offsets had their first reply land at the same point in the cycle
  (13.0-13.4s in; phase concentration R=0.999). TCP `connect()` completes at once — 0.02-0.31s —
  and then nothing happens until the tick.
- **So a cold connection's "slow first command" is not work.** It is `T - (connect_time mod T)`:
  uniformly 0-21s, with ~40s whenever a tick is missed. It is not attached to any one command —
  `GetConfig` measured 21-42s as a session's first command and 0.6s as its second.
- **Everything after the first reply is sub-second**, on the same session.
- **A session lives one tick (~21-23s).** The frame tears it down whether or not you are using it;
  polling every 2s does not keep it alive. It does *not* send a FIN — the peer socket sits in
  `FIN_WAIT_2` while the frame still considers the session open.
- **Reconnecting the instant it hangs up keeps you in phase.** Four back-to-back sessions, each
  reopened immediately on close, had first replies in 0.2/0.0/0.0/0.1s. Any pause costs a full tick.
- **A second concurrent client starves rather than being refused**: of two simultaneous clients one
  was served in 40.2s, the other timed out at 60s. So the vendor phone app being connected is
  indistinguishable from the frame being slow.

Consequence for any client: **do not open a session per operation.** A per-op connect cannot beat
its own timeout — 3.5s or 10s against a guaranteed-up-to-21s first reply times out every time, and
the frame reads as permanently wedged while in fact answering everything. Hold one warm session,
reconnect immediately on EOF, and run every op on it (`slyde_backend/warm.py`). A path that must
connect cold needs a budget of **>=42s** (two ticks).

Whether this tick is the frame's normal state is unknown: reads of 0.39-0.52s were recorded
earlier in the same frame's history, which a 20.7s tick makes impossible. It survives a power
cycle. When it is in this mode, the frame's clock is unset (`GetFrameTime` -> `01/01/0001`,
`ServerTime: False`) and its Android connectivity check to `clients3.google.com` is stuck
half-open, so it believes it has no internet — suggestive, unproven.

## Discovery over Tailscale
Broadcast (255.255.255.255:2015) does not traverse Tailscale. With a subnet router advertising
the frame's LAN (here 192.168.10.0/24), reach the frame by **unicast**: scan TCP 2017/2018 to
find it, then connect directly — discovery broadcast is not required once the IP is known.

## Albums & thumbnails (recovered + validated live, 2026-06-03)

### `m_Action` is serialized as the enum NAME
The device serializes enum fields with Newtonsoft `StringEnumConverter`, so replies carry
`"m_Action":"GetAlbumsStarted"` (a string), while it still accepts integers on input. Decoders
must accept both. Transfer size is announced in the top-level `m_FileSize` field.

### `AlbumData.json` (GetAlbums / SendAlbums) — **AES-encrypted**
Unlike command payloads (DES), the album file uses the **AES** cipher (`Cadre.Utils.Decrypt`,
same key as discovery). Plaintext iff it starts with `{`. Decrypted shape (flat, indexed keys):
```json
{ "AlbumName_0": "Photos_$%^&(*@#!", "ImageName_0": ["a.jpg","b.jpg"],
  "AlbumName_1": "Holidays",         "ImageName_1": ["a.jpg"] }
```
- Images are referenced by **filename**; the same file can be in several albums.
- Three **reserved** albums always exist: `Photos_$%^&(*@#!`, `Evening_$%^&(*@#!`,
  `Remote_$%^&(*@#!` (suffix `_$%^&(*@#!`). "Photos" holds the entire library.
- Limits: MAX_ALBUMS 67, MAX_IMAGES 3000, filename ≤ 64 chars; thumbnails 256×170.
- Create/modify albums by editing this structure and `SendAlbums` it back (AES-encrypted).
  GetThumbnails request: `{"dstfilename":"<name>.thumb.png"}`.

### `ThumbnailsList.txt` (GetThumbnailsList) — **plaintext**
A header line `Memento Version <ver>` followed by one line per image:
```
<imagename>.thumb.png|<md5-of-thumbnail>
```
Individual thumbnails are fetched with `GetThumbnails` as `<imagename>.thumb.png` (PNG bytes).

> Confirmed against frame "Living Room": 33 albums (3 reserved + 30 user albums), 1164 images.

## `GetConfig` / `SendConfig` payload — the `ConfigData` schema (#75)

`sData` carries a DES-encrypted JSON serialisation of the frame's `ConfigData`. Field names
read straight out of `CadreAndroid.dll`'s metadata, in declaration order:

```
s_Name  b_DisplayOn  b_IsAway  b_ShuffleOn  b_NightModeOn  b_PortaitMode  f_DisplayTime
i_LightSensor  i_Brightness
i_BrightnessOffsetStandard  i_BrightnessOffsetDark  i_BrightnessOffsetDarker
i_BrightnessOffsetBright    i_BrightnessOffsetBrighter  i_BrightnessOffsetVivid
s_CalibrationTableName  i_OffThresholdOffset
i_ContrastOffset  i_ExposureOffset  i_SaturationOffset  i_TemperatureOffset
i_AwayDay  i_AwayOffTime  i_AwayOnTime  b_AwayEnable
f_SoftwareVersion  f_HardwareVersion  i_ScreenSize  i_Width  i_Height  s_Orientation
s_WiFiSSID  s_WiFiPSWD  s_TimeZoneName  i_SideBars  i_SideBarsColor  s_GUID
```

Note `b_PortaitMode` — the vendor's typo, not ours. `i_AwayDay` indexes `AwayDays`
(`0 Sunday … 6 Saturday, 7 Weekend, 8 Weekdays, 9 AllDay`); `i_SideBars` indexes `SideBars`
(`0 Blurred, 1 Colored`).

> **`GetConfig` returns the frame's Wi-Fi passphrase.** `s_WiFiSSID` / `s_WiFiPSWD` are stored
> in plaintext in `/mnt/sdcard/SetupData.json` and travel in this payload, protected only by
> the hard-coded DES key above. Treat a captured `GetConfig` reply as credential material.

The device-side display parameters (`DisplayConfig`, applied by the app rather than sent) are
`b_DisplayOn`, `i_BrightnessTarget`, `i_BrightnessThreshold`, `f_LowLuminanceThreshold`,
`i_LowLuminanceTransparency`, `f_HighLuminanceThreshold`, `i_HighLuminanceTransparency`,
`i_OffThreshold`, `i_ContrastOffset`, `i_ExposureOffset`, `i_SaturationOffset`,
`i_TemperatureOffset`.

## Cloud pull channel — `ServerImageDownload` (#75)

The `ServerImageDownload` boolean in the discovery reply switches a channel that has nothing
to do with the LAN protocol: `CadreAndroid.ServerImageDownload` polls Memento's cloud on
`CadreServerCore.TimerInterval` and displays whatever it is handed.

| endpoint | request | response |
|----------|---------|----------|
| `POST https://pictureshare.mementosmartframe.com/image.downloadurl` | `application/json` `{"frameId":"<guid>"}` | `{"url": …}` |
| `GET <url>` | — | image bytes; headers `X-Amz-Meta-Filename`, `X-Amz-Meta-Orientation` (S3) |
| `POST …/image.downloaded` | `{"frameId":…,"imageName":…}` | acknowledgement |
| `POST …/image.delete` | `{"frameId":…,"imageName":…}` | remove from the bucket |
| `POST …/frame.reset` | `{"frameId":…}` | revoke remote access |

Also used by the frame: `GET https://builds.mementosmartframe.com/api/time.php?iso` for the
clock, and `GET http://clients3.google.com/generate_204` for Android's connectivity check —
the latter is the request noted above as stuck half-open when the frame is in its slow-tick
mode. `frameId` is the GUID in `/mnt/sdcard/GUID.txt`.

A downloaded image is written as `DownloadImage.tmp` in `/mnt/sdcard/Photos/`, renamed, added
to the reserved album `Remote_$%^&(*@#!`, and displayed immediately. Whether the endpoints
still answer is untested — see #75 Q7.

## Bulk extraction without the protocol — USB `Backup.txt` (#75)

Faster and simpler than `ReadFile` for whole-library work, and unaffected by the ~21 s tick:
put an empty `Backup.txt` in the root of a FAT32 USB stick, plug it into the frame and reboot.
`SplashActivity.CreateMementoBackup` copies `AlbumData.json`, `SetupData.json`,
`CurrentAlbum.json` and **every file in `/mnt/sdcard/Photos/`** to `Memento_<frame name>/` on
the stick, then deletes the trigger file. It wipes any existing `Memento_<name>` folder on the
stick first. See `docs/firmware-teardown.md` F5/F6 for the full trigger list.

## Open items to confirm against the live device (Phase 2)
- `GetCurrentAlbum` / `GetAlbums` wire JSON (the `AlbumData` type is just
  `s_AlbumName` + `l_ImagesName`; verify the on-the-wire container).
- Album/photo data file format returned over 2018 for `GetAlbums`/`GetThumbnailsList`.

*Closed by #75: the `GetConfig` schema, the discovery reply's encryption, and `info{}`.*
