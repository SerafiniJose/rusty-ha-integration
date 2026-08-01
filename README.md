# Rusty for Home Assistant

A [HACS](https://hacs.xyz/) custom integration that connects [Home Assistant](https://www.home-assistant.io/)
to [Rusty](https://github.com/SerafiniJose/rusty), the Android Spotify/DLNA receiver appliance. It talks to the
Rusty app directly over your local network — no cloud, no account linking.

Rusty is discovered automatically via zeroconf, or can be added manually with the device's IP address.

## Requirements

- Home Assistant >= 2025.6
- A device running the Rusty Android app with **Remote Control** enabled in its settings
  (Rusty app → Settings → Remote Control)
- The Home Assistant host must be able to reach the Rusty device on your local network (port 8765)

## Entities

- `light.rusty_screen` — the device screen, as a light entity (on/off and brightness)
- `number.rusty_volume` — device volume, as a 0-100 slider
- `binary_sensor.rusty_spotify_playing` — on when Spotify is playing on the device
- `binary_sensor.rusty_dlna_playing` — on when DLNA playback is active on the device

The device page also includes a "Visit" link to the device's local web UI. That link only works
when you're on the same local network as the device (LAN-only, not reachable remotely).

## Installation

1. In Home Assistant, go to **HACS** → the **⋮** menu → **Custom repositories**.
2. Add this repository's URL (`https://github.com/SerafiniJose/rusty-homeassistant`) with category **Integration**.
3. Find **Rusty** in HACS and click **Install**.
4. **Restart Home Assistant.**
5. Go to **Settings → Devices & Services**. If Rusty was discovered on your network, it will show up
   as a discovered integration — click **Configure** to add it. Otherwise, click **Add Integration**,
   search for **Rusty**, and enter the device's IP address manually.

## Related

- [Rusty Android app](https://github.com/SerafiniJose/rusty) — the app this integration talks to.
