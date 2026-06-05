# Capturing the eFrame app's cloud API (phone-side, no waiting)

The frame wakes every ~3 days, but its phone app talks to the **same backend**
right now. MITM the app and you learn the cloud API today. This is the *easy*
side because **you can install a trusted CA on your own phone** — something you
can't do on the closed frame.

## 1. Run mitmproxy

On a machine on the same LAN (laptop, or the OPNsense box, or a Pi):

```bash
pipx install mitmproxy            # or: pip install mitmproxy
mitmweb --listen-port 8080        # web UI at http://127.0.0.1:8081
# (mitmdump -w eframe-flows.mitm   # headless, saves flows to a file)
```

## 2. Point the phone at it + trust its CA

- Phone Wi-Fi → **proxy = <mitm-host-ip> : 8080** (manual proxy).
- On the phone browser open **http://mitm.it** → install the **mitmproxy CA**.
  - **Android:** Settings → Security → *Install a certificate* → CA certificate.
    User CAs are honored by apps **only if the app opts in**; many do not on
    Android 7+. If the app ignores your user CA, see §4.
  - **iOS:** install the profile, then Settings → General → About → *Certificate
    Trust Settings* → enable full trust for the mitmproxy root.

## 3. Drive the app, watch the flows

In the eFrame app, do each action and watch the requests appear:
- log in / create account  → **auth endpoint + token scheme**
- pair / bind a frame      → **how a frame-code maps to a device**
- send a photo to the frame → **the upload API + image format/path**
- "send now" / refresh      → **the trigger the frame later polls**

Capture, for each: the **hostname** (matches your AGH log), full **URL paths**,
**auth headers/tokens**, request/response **bodies**, and content types. That's
the spec for `fake_cloud.py`'s REPLACE stage.

## 4. If the app pins / ignores user CAs (escalation)

Cheap frame apps often *don't* pin — try §1–3 first. If HTTPS won't decrypt:

- **Static route (no MITM needed):** decompile the APK and read the endpoints
  straight out of the code — see `apk_extract.md`. Often enough on its own.
- **Frida / objection** (needs a rooted Android or jailbroken iOS, or an emulator):
  ```bash
  pip install frida-tools objection
  objection -g com.aluratek.eframe explore        # then: android sslpinning disable
  ```
- **Patched APK:** `apk-mitm app.apk` rewrites the network-security-config to
  trust user CAs and strips common pinning, then reinstall the patched build.

## 5. Hand-off to the fake-cloud

The captured hostname → an **AdGuard Home DNS Rewrite** pointing at the box
running `fake_cloud.py`. The captured paths → fill in `_maybe_serve_image()` and
any auth stubs. Then the frame talks to *you*, fed read-only from Immich.
