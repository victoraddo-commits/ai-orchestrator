"""Kai Betting — standalone ASGI entrypoint.

api.py only defines a router (meant to be mounted on a host app). This
wraps it in its own FastAPI app so it can run standalone via uvicorn,
independent of the ai-orchestrator monolith.
"""

from fastapi import FastAPI

from core.kai_betting.api import router

app = FastAPI(title="Kai Betting API")
app.include_router(router)
