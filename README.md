# Rusty for Home Assistant

A [HACS](https://hacs.xyz/) custom integration that connects [Home Assistant](https://www.home-assistant.io/)
to [Rusty](https://github.com/SerafiniJose/rusty), the Android Spotify/DLNA receiver appliance. It talks to the
Rusty app directly over your local network — no cloud, no account linking.

Rusty is discovered automatically via zeroconf, or can be added manually with the device's IP address.

## Requirements

- Home Assistant >= 2026.10
- A device running the Rusty Android app with **Remote Control** enabled in its settings
  (Rusty app → Settings → Remote Control)
- The Home Assistant host must be able to reach the Rusty device on your local network (port 8765)

## Entities

Entity IDs are derived from the device's name (as set in the Rusty app) plus the entity. For a
device named "Rusty", they look like:

- `light.rusty_screen` — the device screen, as a light entity (on/off and brightness)
- `number.rusty_volume` — device volume, as a 0-100 slider
- `binary_sensor.rusty_spotify_playing` — on when Spotify is playing on the device
- `binary_sensor.rusty_dlna_playing` — on when DLNA playback is active on the device
- `select.rusty_active_panel` — navigates between Rusty panels (Spotify, Home Assistant, DLNA…)
- `select.rusty_ha_dashboard` — selects the active HA dashboard on the device's chip bar
  (only appears when the device has HA dashboards configured)

If you rename the device, its entity IDs change accordingly.

The device page also includes a "Visit" link to the device's local web UI. That link only works
when you're on the same local network as the device (LAN-only, not reachable remotely).

## Services

### `rusty.announce`

Speaks text on a Rusty device using its on-device text-to-speech engine. Select the target
device using the device picker in the service call UI.

| Field | Description |
|-------|-------------|
| `device_id` | The Rusty device (select from the device picker) |
| `text` | The text to speak (1–4000 characters) |

## Authentication

The Rusty API supports an optional Bearer password. Set it in the Rusty app under
Settings → Remote Control → API Password.

- If no password is set, the integration connects without authentication.
- If a password is set, enter it when adding the integration. Home Assistant keeps it
  in the config entry in `.storage/core.config_entries` on the HA host; the integration
  does not encrypt it, so protect access to the HA config directory and its backups.
- If the password changes, Home Assistant will detect the `401` response and prompt
  you to re-authenticate via a notification.

**Note:** The API password travels as a Bearer token in plain HTTP on the LAN. Keep the
device on a network you trust (a separate IoT VLAN if you run one).

### Zeroconf security

When a known Rusty device is detected at a new IP address, the integration asks for
explicit confirmation before updating the stored address. If the device has a password,
you must re-enter the credential to confirm the move — this prevents a rogue mDNS
advertisement from redirecting traffic to an impersonator.

## TLS / HTTPS

If your HA instance uses HTTPS with a self-signed certificate, the Rusty Android app's
WebView will reject it unless the CA is trusted by Android:

1. Export your CA certificate as a `.crt` or `.pem` file.
2. On the Echo Show / Android device: **Settings → Security → Install certificate → CA certificate**.
3. Select the exported file.

After importing the CA, the WebView will trust certificates signed by that CA.
(Cleartext HTTP still works too if you prefer to keep HA on http.)

## Installation

1. In Home Assistant, go to **HACS** → the **⋮** menu → **Custom repositories**.
2. Add this repository's URL (`https://github.com/SerafiniJose/rusty-ha-integration`) with category **Integration**.
3. Find **Rusty** in HACS and click **Install**.
4. **Restart Home Assistant.**
5. Go to **Settings → Devices & Services**. If Rusty was discovered on your network, it will show up
   as a discovered integration — click **Configure** to add it. Otherwise, click **Add Integration**,
   search for **Rusty**, and enter the device's IP address manually.

## Related

- [Rusty Android app](https://github.com/SerafiniJose/rusty) — the app this integration talks to.
