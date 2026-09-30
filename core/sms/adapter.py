"""core.sms gateway adapters — the receive-path abstraction.

Two concrete sources feed the worker:

* :class:`WebhookAdapter` — an external SMS gateway (or the Android
  SMS-forwarder app) POSTs ``{from,to,body,timestamp}`` to the worker; the
  adapter authenticates the shared bearer token with a constant-time compare.
* :class:`FixtureAdapter` — reads JSON message files from a directory so the
  whole pipeline is testable offline (no phone, no gateway, no cost).

No adapter logs or persists message bodies; authentication happens before any
parsing so an unauthenticated body is never even read into the pipeline.
"""

from __future__ import annotations

import hmac
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class WebhookAuthError(RuntimeError):
    """Raised when a webhook delivery fails token authentication."""


class WebhookPayloadError(ValueError):
    """Raised when a webhook delivery is missing required fields."""


@dataclass
class RawSms:
    from_number: str
    to_number: Optional[str]
    body: str
    timestamp: Optional[str] = None
    gateway: str = "unknown"


def _first(payload: dict, *keys: str):
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_payload(payload: dict) -> RawSms:
    """Validate a ``{from,to,body,timestamp}`` delivery into a :class:`RawSms`."""
    if not isinstance(payload, dict):
        raise WebhookPayloadError("payload must be a JSON object")
    sender = _first(payload, "from", "from_number", "sender", "source")
    body = _first(payload, "body", "text", "message", "content")
    if sender is None:
        raise WebhookPayloadError("missing sender ('from')")
    if body is None:
        raise WebhookPayloadError("missing message body ('body')")
    to_number = _first(payload, "to", "to_number", "line", "recipient")
    timestamp = _first(payload, "timestamp", "received_at", "ts", "date")
    return RawSms(
        from_number=str(sender),
        to_number=str(to_number) if to_number is not None else None,
        body=str(body),
        timestamp=str(timestamp) if timestamp is not None else None,
        gateway=str(_first(payload, "gateway", "device") or "webhook"),
    )


class GatewayAdapter(ABC):
    """Uniform receive surface. Pull sources implement :meth:`receive`; push
    sources implement :meth:`ingest_webhook` (the other raises)."""

    @abstractmethod
    def receive(self, limit: int = 50) -> list[RawSms]:
        raise NotImplementedError

    @abstractmethod
    def ingest_webhook(self, payload: dict, *, token: Optional[str] = None) -> RawSms:
        raise NotImplementedError


class WebhookAdapter(GatewayAdapter):
    """Push source: authenticates a bearer token, then parses the payload."""

    def __init__(self, token: Optional[str]):
        self.token = token or ""

    def authenticate(self, presented: Optional[str]) -> None:
        expected = self.token or ""
        if not expected or not presented or not hmac.compare_digest(str(presented), expected):
            raise WebhookAuthError("invalid webhook token")

    def ingest_webhook(self, payload: dict, *, token: Optional[str] = None) -> RawSms:
        self.authenticate(token)
        return parse_payload(payload)

    def receive(self, limit: int = 50) -> list[RawSms]:
        return []


class FixtureAdapter(GatewayAdapter):
    """Pull source: a directory of JSON files (each a message or a list)."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def _iter_messages(self):
        if not self.directory.exists():
            return
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    yield path, item

    def receive(self, limit: int = 50) -> list[RawSms]:
        out: list[RawSms] = []
        for _path, item in self._iter_messages():
            try:
                out.append(parse_payload(item))
            except WebhookPayloadError:
                continue
            if len(out) >= limit:
                break
        return out

    def ingest_webhook(self, payload: dict, *, token: Optional[str] = None) -> RawSms:
        return parse_payload(payload)


__all__ = [
    "RawSms",
    "GatewayAdapter",
    "WebhookAdapter",
    "FixtureAdapter",
    "WebhookAuthError",
    "WebhookPayloadError",
    "parse_payload",
]
