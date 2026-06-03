# Memento Smart Frame — Local Network Protocol

Reverse-engineered from the official Windows app (`Assembly-CSharp.dll`, Unity/Sarbakan,
namespace `Cadre`). The frame is the **server**; the app/our tool is the **client**.
All of this runs purely on the LAN — no cloud needed (firmware 6.02+).

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
3. `<json>` fields (`Utils.ProcessBroadcast`): `name`, `softver`, `hardver`, `size`,
   `orientation`, `ip`, `mac`, `guid`, `IsConnected` (bool), `TryAndBuyMode` (bool),
   `ServerImageDownload` (bool), `hasInternet` (bool). A reply is accepted only if name,
   softver, hardver, size, orientation and ip are all present/non-zero.

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
Fields: `m_Action` (int enum), `m_DestinationFileName`, `m_SourceFileName`,
`m_FileInfoJSON`, `m_Data` (DES-encrypted JSON: `{srcfilename, dstfilename, filesize, info{}}`),
`m_FileSize`.
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
- `GetThumbnails` returns `<name>.thumb.png` images.

## Connect / session sequence (from `Client.cs`)
1. Discover frame (above) → get IP + ClientInfoData.
2. TCP connect 2017 (control), then TCP connect 2018 (file). Both must connect.
3. On both connected, run state sequence: `SendTime → GetConfig → GetFrameTime → CheckUpdate
   → GetThumbnailsList → GetAlbums → GetCurrentAlbum → GetThumbnails → … → Idle`.
4. Keep-alive: send `CommandControlFlow Beacon(0)` every ~5 s (`BEACON_TIMEOUT`); frame
   replies `BeaconDone(1)`. Command/transfer timeout 20 s / 60 s.
5. Disconnect: `CommandControlFlow Disconnect(20)`; frame replies `DisconnectDone(21)`.

## Config / setup data ranges (`SetupData.cs`, partial)
- Brightness: MIN −255 / MAX 160 (display-specific: 25" −240..180, 35" −250..160), min download 50.
- Image canvas: 3240 × 2160. CEST offset −32..32. MAX_AWAY_SCHEDULE 7. GUID length 36.
- Calibration presets: Darker/Dark/Standard/Bright/Brighter/Vivid.
- Orientation: Landscape / Portrait / Unknown.
- Away-schedule time units: SECOND 1, MINUTE 60, HOUR 3600, DAY 86400, WEEK 604800, NEVER 2419200.

## VERIFIED LIVE (2026-06-03, frame "Living Room" @ 192.168.10.113, fw 6.02)
- Control framing, command enums, and the **DES** command-payload crypto are all confirmed:
  GetFrameTime and GetConfig decrypt cleanly with key `M3m3nt0 ` / IV `UHDFram3`.
- Replies are wrapped by Newtonsoft TypeNameHandling:
  `{"$types":{"Cadre.CommandChangeSetup, CadreAndroid, ...":"1"},"$type":"1", <real fields>}`.
  The firmware assembly is **CadreAndroid** (the frame runs Android). Ignore `$types`/`$type`.
- We do NOT need to send `$type`; a plain `{field, m_Action, m_Socket:null}` object is accepted.
- `GetFrameTime` → `{"DateTime":"MM/dd/yyyy HH:mm:ss","ServerTime":"False"}`.
- `GetConfig` confirmed schema (the SetupData config object):
  `Name, DisplayOn, IsAway, NightModeOn, ShuffleOn, PortraitMode, DisplayTime,
  LightSensor[11], Brightness[11], BrightnessOffset{Standard,Dark,Darker,Bright,Brighter,Vivid}[11],
  OffThresholdOffset, CalibrationTableName, ContrastOffset, ExposureOffset, SaturationOffset,
  TemperatureOffset, AwayDay, AwayOffTime, AwayOnTime, AwayEnable, SoftwareVersion, HardwareVersion,
  ScreenSize, Width, Height, Orientation, WiFiSSID, WiFiPSWD (clear!), TimeZoneName, SideBars,
  SideBarsColor, GUID`. NOTE: the frame returns Wi-Fi SSID + password in cleartext on the LAN.

## Discovery over Tailscale
Broadcast (255.255.255.255:2015) does not traverse Tailscale. With a subnet router advertising
the frame's LAN (here 192.168.10.0/24), reach the frame by **unicast**: scan TCP 2017/2018 to
find it, then connect directly — discovery broadcast is not required once the IP is known.

## Open items to confirm against the live device (Phase 2)
- Exact `GetConfig`/`GetCurrentAlbum`/`GetAlbums` JSON schemas (read `SetupData.cs`,
  `Albums.cs` for the serializers; verify on the wire).
- Whether the discovery reply is sent AES-encrypted by firmware 6.x or plaintext.
- Album/photo data file format returned over 2018 for `GetAlbums`/`GetThumbnailsList`.
- `m_FileInfoJSON` (`info{}`) contents the frame expects on upload (orientation, etc.).
