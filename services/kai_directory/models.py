from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")


@dataclass
class Record:
    id: str
    name: str
    display_name: str = ""
    category: str = "general"
    host: str = ""
    container: str = ""
    ip: str = ""
    port: int = 0
    bind: str = ""
    tailnet_name: str = ""
    internal_url: str = ""
    proxy_node: str = ""
    health_url: str = ""
    health_status: str = "unknown"
    owner: str = ""
    tags: str = ""
    source: str = "manual"
    target_url: str = ""
    updated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ"))

    def __post_init__(self):
        self.port = int(self.port or 0)
        if not self.target_url and self.ip and self.port:
            self.target_url = f"http://{self.ip}:{self.port}"
        if not self.health_url and self.target_url:
            self.health_url = self.target_url

    def to_dict(self) -> dict:
        return asdict(self)
