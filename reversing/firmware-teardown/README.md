# Derived firmware artefacts (#75)

Text extracted from the vendor OTA package `Memento_35.zip`
(md5 `57a7151d932a11f603e44c44fcb49968`, kept outside this repo in
`Projects/memento-firmware/`). Everything here is regenerable:

```
python3 -m venv venv && venv/bin/pip install dnfile lzallright
venv/bin/python reversing/tools/teardown.py ../memento-firmware/Memento_35.zip \
    --out reversing/firmware-teardown --work /tmp/fw
venv/bin/python reversing/tools/cildasm.py --enums \
    ../memento-firmware/extracted/CadreAndroid.dll > reversing/firmware-teardown/cadre-enums.txt
```

Use a **Linux** interpreter — `python3` on the WSL PATH is Windows Python and rewrites
these as CRLF/cp1252.

| file | source |
|------|--------|
| `uboot-default-env.txt` | U-Boot's compiled-in default environment, from the UCL-packed second stage of `bootloader.img` |
| `emmc-partitions.txt` | the two Amlogic `struct partitions` tables inside U-Boot |
| `boot.dts` | the DTB carried in `boot.img`'s second-stage slot |
| `kernel.config` | `CONFIG_IKCONFIG` blob inside the LZO-compressed kernel |
| `boot-img.txt` | `boot.img` header fields, size and sha1 |
| `certificates.txt` | OTA signing cert vs. the device trust store vs. the APK signing certs |
| `cadre-enums.txt` | every literal-constant type in `CadreAndroid.dll` — the frame's command surface and limits |

Read the analysis in [`docs/firmware-teardown.md`](../../docs/firmware-teardown.md).
