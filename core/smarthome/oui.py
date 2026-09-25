"""Minimal MAC OUI -> vendor map for smart-home discovery.

Not a full IEEE registry: a curated set of prefixes for devices seen in this
estate plus common smart-home silicon. Unknown prefixes return ``None`` — we
never guess a vendor.
"""
from __future__ import annotations

_VENDORS: dict[str, str] = {
    "50:8A:06": "Tuya Smart",
    "68:57:2D": "Tuya Smart",
    "10:52:1C": "Tuya Smart",
    "B4:E6:2D": "Espressif",
    "24:0A:C4": "Espressif",
    "84:F3:EB": "Espressif",
    "EC:FA:BC": "Espressif",
    "68:C6:3A": "Espressif",
    "F4:CF:A2": "Espressif",
    "18:B4:30": "Nest",
    "44:61:32": "ecobee",
    "0C:47:C9": "Amazon",
    "68:54:FD": "Amazon",
    "FC:65:DE": "Amazon",
    "44:65:0D": "Amazon",
    "34:D2:70": "Amazon",
    "D8:F1:5B": "eero",
    "F8:BB:BF": "eero",
    "00:17:88": "Philips Hue",
    "EC:B5:FA": "Philips Hue",
    "B0:CE:18": "Sonoff/eWeLink",
    "84:0D:8E": "Sonoff/eWeLink",
}


def prefix(mac: str) -> str:
    """Return the normalised ``AA:BB:CC`` OUI prefix (uppercase, colon-separated)."""
    clean = "".join(c for c in (mac or "") if c.isalnum()).upper()
    clean = clean[:6]
    if len(clean) < 6:
        return ""
    return ":".join(clean[i:i + 2] for i in range(0, 6, 2))


def vendor_for(mac: str) -> str | None:
    """Return the vendor for a MAC, or ``None`` when the prefix is not known."""
    p = prefix(mac)
    return _VENDORS.get(p) if p else None
