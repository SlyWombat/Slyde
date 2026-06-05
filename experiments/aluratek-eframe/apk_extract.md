# Getting & analyzing the eFrame APK (static recon)

**App:** Aluratek eFrame · **package:** `com.xiaowooya.aluratek_eframe`
(developer "xiaowooya" = a Chinese ODM — the frame is white-labeled, so the cloud
is likely a generic vendor backend shared by other rebranded frames.)

Static analysis often reveals the **base URL, endpoints, and cert-pinning config
without any live capture** — do this in parallel with the phone MITM.

## A. Export the copy already on your phone (cleanest — it's *your* app)

Needs USB debugging (Settings → About → tap *Build number* ×7 → Developer
options → USB debugging) and `adb` on your computer.

```bash
# 1. find every APK for the package (modern apps ship as split APKs)
adb shell pm path com.xiaowooya.aluratek_eframe
#   package:/data/app/~~xxxx==/com.xiaowooya.aluratek_eframe-yyyy==/base.apk
#   package:.../split_config.arm64_v8a.apk
#   package:.../split_config.xxhdpi.apk

# 2. pull them all
adb pull /data/app/~~xxxx==/com.xiaowooya.aluratek_eframe-yyyy==/base.apk .
#   (repeat adb pull for each split_*.apk line)
```

`base.apk` holds the code + manifest + network config — it's the one that matters.

## B. Or download it by package name (if no Android / no cable)

From **APKMirror** or **APKPure** search `com.xiaowooya.aluratek_eframe`. Match the
version to what's on your phone. These are third-party mirrors — moderate trust;
verify the signing cert/hash if you can. (iOS `.ipa` export is much harder —
needs a jailbroken device or a Mac with Apple Configurator + decryption; if you
have an Android phone, use path A.)

## C. Decompile & grep for the goods

```bash
# Java/Kotlin source (best for reading logic):
pipx install jadx            # or download jadx-gui
jadx -d out_jadx base.apk
grep -rEi "https?://[a-z0-9.-]+" out_jadx | sort -u      # base URLs & endpoints
grep -rEi "pin|sha256/|certificate|trustmanager|okhttp" out_jadx   # pinning?

# Resources + manifest + network-security-config (cert trust + cleartext domains):
pipx install apktool
apktool d base.apk -o out_apktool
cat out_apktool/res/xml/network_security_config.xml 2>/dev/null   # CA trust + domains
# AndroidManifest: look for usesCleartextTraffic, the config ref, MQTT/services
```

## What to extract
- **Cloud hostname(s) / base URL** → cross-check with the AdGuard Home query log.
- **`network_security_config.xml`** → does it trust **user CAs** (MITM-able) or pin
  a cert? `<certificates src="user"/>` or no pinning ⇒ the fake-cloud MITM works.
- **API shape**: auth, the frame-code→device binding, the image upload/fetch path,
  and any MQTT/push channel the frame uses to know "new photo available".
- **Image format** the frame expects (often a pre-dithered buffer for the e-paper
  panel, not a plain JPEG — important for `fake_cloud.py`'s REPLACE stage).
