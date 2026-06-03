# memento-emulator

A faithful emulator of the **Memento Smart Frame's server side** — UDP discovery (2015/2016)
and the TCP control (2017) + file (2018) channels, including the DES-encrypted command payloads.

Used as the test target so the client library and backend are exercised end-to-end (including
photo uploads) without touching a real frame.

```bash
memento-emulator --host 127.0.0.1 --name "Test Frame"
```

The default config mirrors a real firmware-6.02 frame (with placeholder Wi-Fi credentials).
