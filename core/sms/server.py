"""CT111 HTTP surface for the SMS worker (FastAPI).

A thin transport over :mod:`core.sms.manager`. An external gateway — or the
Android SMS-forwarder app on the phone with the SIM — POSTs
``{from,to,body,timestamp}`` to ``/webhook/sms`` with a shared bearer token.
Only the token holder can deliver; an unauthenticated body is never parsed.

The response is metadata only — never the message body and never a code.

One log line is emitted per received ``POST`` under ``/webhook/sms`` with the
remote address, method, path, HTTP status and a coarse failure category. The
token value and the message body are never logged.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from core.sms.adapter import WebhookAdapter, WebhookAuthError, WebhookPayloadError
from core.sms.schema import SmsIngestResult

logger = logging.getLogger("kai.sms")

WEBHOOK_PATH = "/webhook/sms"

# Coarse status -> reason category. The log never carries the failure detail,
# only the category, so no payload content can leak through it.
_STATUS_REASON = {
    200: "ok",
    401: "auth_failed",
    422: "bad_payload",
    404: "not_found",
}


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    prefix = "bearer "
    if authorization.lower().startswith(prefix):
        return authorization[len(prefix):].strip()
    return authorization.strip()


def _remote_addr(request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = getattr(request, "client", None)
    return client.host if client else "-"


def _log_webhook(request, status_code: int, reason: str) -> None:
    logger.log(
        logging.WARNING if status_code >= 400 else logging.INFO,
        "sms.webhook remote=%s method=%s path=%s status=%s reason=%s",
        _remote_addr(request), request.method, request.url.path, status_code, reason,
    )


def create_app(manager_module=None, token: Optional[str] = None,
               adapter: Optional[WebhookAdapter] = None) -> FastAPI:
    from core.sms import manager as _manager

    manager = manager_module or _manager
    if token is None:
        from core.sms import vault

        token = vault.load_webhook_token() or os.environ.get("KAI_SMS_WEBHOOK_TOKEN")
    webhook = adapter or WebhookAdapter(token)

    app = FastAPI(title="KAI SMS Worker", version="1.0.0")

    @app.middleware("http")
    async def _webhook_logger(request, call_next):  # noqa: ANN001
        matches = request.method == "POST" and request.url.path.startswith(WEBHOOK_PATH)
        try:
            response = await call_next(request)
        except Exception:
            if matches:
                _log_webhook(request, 500, "error")
            raise
        if matches:
            _log_webhook(
                request,
                response.status_code,
                _STATUS_REASON.get(response.status_code, f"http_{response.status_code}"),
            )
        return response

    @app.exception_handler(WebhookAuthError)
    def _401(_req, _exc):  # noqa: ANN001
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})

    @app.exception_handler(WebhookPayloadError)
    def _422(_req, exc):  # noqa: ANN001
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "worker": manager.health()}

    @app.post("/webhook/sms")
    def webhook_sms(body: dict, authorization: Optional[str] = Header(default=None),
                    x_sms_token: Optional[str] = Header(default=None)) -> dict:
        presented = _bearer(authorization) or x_sms_token
        sms = manager.ingest_webhook(body, token=presented, adapter=webhook)
        return SmsIngestResult(
            ok=True, message_id=sms.message_id, classification=sms.classification.value,
            otp_present=sms.otp_present, suspicious=sms.suspicious,
            account_id=sms.account_id, mission_id=sms.mission_id,
        ).model_dump(mode="json")

    return app


__all__ = ["create_app"]
