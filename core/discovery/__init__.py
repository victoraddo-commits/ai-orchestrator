"""core.discovery - domain classification and reasoning for universal web registration."""

from core.discovery.reasoning import (  # noqa: F401
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    FixtureReasoningBackend,
    LocalModelReasoningBackend,
    ReasoningBackend,
)

__all__ = [
    "ReasoningBackend",
    "FixtureReasoningBackend",
    "LocalModelReasoningBackend",
    "DEFAULT_MODEL",
    "DEFAULT_ENDPOINT",
]
