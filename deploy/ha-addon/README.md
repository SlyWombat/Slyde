# Home Assistant Add-on Guide — Slyde + SwitchBot AI Art Frame (13.3")

Run **Slyde** directly inside Home Assistant OS / Supervised as a native Add-on with **Sidebar Ingress** and zero-config **`SUPERVISOR_TOKEN`** authentication to [`ha-switchbot-art-frame`](https://github.com/se0siris/ha-switchbot-art-frame).

## Architecture

1. **Home Assistant + ESPHome Bluetooth Proxy + `ha-switchbot-art-frame`** manage the encrypted BLE link to the frame (deleting the oldest local slot when all 10 slots are full, waking Wi-Fi, and triggering the S3 download) and clean up SwitchBot's 10-slot cloud album.
2. **Slyde (this Add-on)** connects read-only to **Immich**, smart-fits each photo to your panel's resolution (`1200×1600` portrait or `1600×1200` landscape on the 13.3" frame), compresses the JPEG below `650 KB`, and calls Home Assistant's `switchbot_art_frame.show_image` action (`make_room: true`) over the WebSocket API, one upload at a time, waiting for each to finish.

## Slideshow: who runs it?

Pick one with the `switchbot_slideshow` option:

| | `slyde` (recommended) | `frame` |
|---|---|---|
| What happens | Every `switchbot_slideshow_interval_minutes` Slyde sends **one** new photo. | Every interval Slyde replaces **all 10** photos on the frame; the frame's own slideshow cycles them. |
| Order | Random, every album photo shown once before any repeats (Shuffle-Deck). | Same, in batches of 10. |
| SwitchBot app | **Set the frame to a static image.** Otherwise it also cycles through the photos it still stores. | Set up the slideshow (interval) in the SwitchBot app. |
| Caveat | Each change wakes the frame (BLE + Wi-Fi), so very short intervals cost battery. Set `switchbot_quiet_hours` (e.g. `22:00-07:00`) to skip changes overnight. | Photos are stored without being shown (`show: false`), which needs a version of ha-switchbot-art-frame whose `show_image` has the `show` option; older versions reject the upload. |

`off` transfers the whole album without any slideshow logic.

## Installation on Home Assistant OS

1. Install **[SwitchBot Art Frame Tools (`ha-switchbot-art-frame`)](https://github.com/se0siris/ha-switchbot-art-frame)** via HACS in Home Assistant and configure it with your Art Frame and SwitchBot account. Note the sensor entity ID (e.g. `sensor.art_frame_eeff_local_images`).
2. Add this repository as an add-on repository (**Settings → Add-ons → Add-on Store → ⋮ → Repositories**), or copy the whole Slyde checkout to `/addons/slyde`. The add-on manifest (`config.yaml` + `build.yaml`) lives at the repository root, so the Supervisor builds the regular `Dockerfile` with the full source tree.
3. In Home Assistant, go to **Settings → Add-ons → Add-on Store → ⋮ → Check for updates**, then install **Slyde Frame Manager**.
4. Open the **Configuration** tab of the Add-on and set:
   - `immich_base_url`: URL of your Immich server (e.g. `http://192.168.1.50:2283`)
   - `immich_api_key`: Read-only Immich API key
   - `ha_art_frame_entity_id`: Your `sensor.art_frame_*_local_images` entity ID
   - `switchbot_frame_size`: `"13.3"`
   - `switchbot_orientation`: `"portrait"`
   - `switchbot_slideshow`: `slyde` (then set the frame to a static image in the SwitchBot app)
   - `switchbot_slideshow_interval_minutes`: e.g. `60` (a new photo every hour)
   - `switchbot_quiet_hours`: e.g. `22:00-07:00` (no changes overnight)
5. Start the Add-on, open **Slyde** in the Home Assistant sidebar, go to your frame's **Library** tab and use **Keep a new folder in sync with an Immich album**.
