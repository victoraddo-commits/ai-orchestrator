"""core.sms adapters — token-authenticated webhook ingest + fixture source."""

import json
from pathlib import Path

import pytest

from core.sms.adapter import (
    FixtureAdapter,
    GatewayAdapter,
    RawSms,
    WebhookAdapter,
    WebhookAuthError,
    WebhookPayloadError,
    parse_payload,
)

FIX = Path(__file__).parent / "fixtures" / "sms"


def test_parse_payload_standard_keys():
    raw = parse_payload({"from": "12345", "to": "+233248077604",
                         "body": "Your code is 482913", "timestamp": "2026-09-30T10:00:00Z"})
    assert isinstance(raw, RawSms)
    assert raw.from_number == "12345"
    assert raw.to_number == "+233248077604"
    assert raw.body == "Your code is 482913"


def test_parse_payload_missing_sender_raises():
    with pytest.raises(WebhookPayloadError):
        parse_payload({"body": "hello"})


def test_parse_payload_missing_body_raises():
    with pytest.raises(WebhookPayloadError):
        parse_payload({"from": "12345"})


def test_webhook_adapter_rejects_bad_token():
    adapter = WebhookAdapter(token="correct-token")
    with pytest.raises(WebhookAuthError):
        adapter.ingest_webhook({"from": "12345", "body": "hi"}, token="wrong-token")


def test_webhook_adapter_accepts_good_token():
    adapter = WebhookAdapter(token="correct-token")
    raw = adapter.ingest_webhook({"from": "12345", "body": "hi"}, token="correct-token")
    assert raw.from_number == "12345"


def test_webhook_adapter_receive_is_empty():
    assert WebhookAdapter(token="t").receive() == []


def test_fixture_adapter_reads_directory():
    adapter = FixtureAdapter(FIX)
    messages = adapter.receive()
    assert len(messages) >= 1
    assert all(isinstance(m, RawSms) for m in messages)


def test_fixture_adapter_is_gateway_adapter():
    assert isinstance(FixtureAdapter(FIX), GatewayAdapter)
