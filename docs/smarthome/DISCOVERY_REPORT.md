# Smart Home — Live Discovery Report (Phase E1)

**Date:** 2026-09-25
**Scope:** LAN `192.168.1.0/24`, swept from CT111 via the parallel
`scripts/smarthome_discover.py`.
**Rule honored:** nothing is fabricated — a device appears only if a real
smart-home port answered; unknown fields stay `UNKNOWN`.

## Method
- TCP fingerprint of smart-home ports: `8123` (Home Assistant), `6668`/`6667`
  (Tuya), `1883`/`8883` (MQTT), `554`/`2020` (cameras).
- Vendor derived from the MAC OUI map (`core/smarthome/oui.py`); unknown ⇒ blank.
- Result written to the canonical registry `memory/smarthome_registry.json`
  (idempotent upsert by provider key).

## Discovered devices

| IP | MAC | Vendor | Provider | Kind | State |
|---|---|---|---|---|---|
| 192.168.1.16 | (not captured) | Tuya Smart | tuya | switch (provisional) | UNKNOWN (no local key yet) |

## Notes / honest gaps
- Exactly **one** smart-home device answered on the LAN: the Tuya device at
  `192.168.1.16` (`50:8A:06:6A:3C:36`, TCP 6668). This matches the earlier
  independent tcpdump proof (UDP 6667 broadcasts from the same host).
- `provider_id` is `null` until the Tuya local key / cloud identity is wired
  (Phase E2). The device is registered by IP for now.
- MAC was not captured in this pass (active TCP scan + `ip neigh`); a passive
  ARP/mDNS capture is the next discovery enhancement.
- Home Assistant (LXC 115) will add its entity inventory once deployed
  (Task 8); MQTT/cameras appear if/when present.
- No Home Assistant, MQTT broker, or RTSP camera was found on the LAN at audit
  time — reported as **absent**, not guessed.

## Verification
- `tests/smarthome` → 19 passed.
- `scripts/smarthome_discover.py 192.168.1.16` → `created: 1`.
- Full LAN sweep → finds the same device, idempotent (`updated: 1`).
