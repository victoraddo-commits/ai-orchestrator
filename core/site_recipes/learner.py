"""RecipeLearner - propose a draft Site Recipe from a browser DOM/a11y tree.

The learner never persists anything itself and never sees a credential value:
it asks the reasoning backend for a proposal over a *sanitized* snapshot and
maps it into a :class:`SiteRecipe` with ``source=learned``/``status=draft``.
The caller (``GenericWebAdapter``) stores it and pauses for review.

Backend proposals are **untrusted**: unknown keys are filtered out and a
proposal that cannot be mapped (a missing required field, a wrong type, an
invalid flow type) is reduced to an **empty draft** so the operator reviews it,
instead of letting a ``ValidationError`` escape ``registration()``. The one
deliberate exception is a literal (non-symbolic) ``value_source``: that is a
secret-boundary violation and is raised.

A low-confidence proposal is still returned as a draft -- the learner never
promotes a recipe to ``published``; the operator decides.
"""

from __future__ import annotations

from typing import Optional

from pydantic import ValidationError

from core.discovery.reasoning import ReasoningBackend
from core.site_recipes.schema import (
    FieldSpec,
    FlowType,
    RecipeSource,
    RecipeStatus,
    RecipeStep,
    SiteProfile,
    SiteRecipe,
    SiteRequirements,
    is_symbolic_value_source,
)

#: Only these keys are copied out of an untrusted proposal item.
_STEP_KEYS = frozenset(RecipeStep.model_fields)
_FIELD_KEYS = frozenset(FieldSpec.model_fields)
_REQUIREMENT_KEYS = frozenset(SiteRequirements.model_fields)


def _filtered(item: object, allowed: frozenset) -> Optional[dict]:
    """Return *item* restricted to *allowed* keys, or None if it is not a dict."""
    if not isinstance(item, dict):
        return None
    return {key: value for key, value in item.items() if key in allowed}


def _has_literal_secret(proposal: dict) -> bool:
    """True when the proposal names a non-symbolic (literal) value_source."""
    for key in ("steps", "fields"):
        for raw in proposal.get(key) or []:
            if not isinstance(raw, dict):
                continue
            source = raw.get("value_source")
            if isinstance(source, str) and not is_symbolic_value_source(source):
                return True
    return False


class RecipeLearner:
    """Turn a DOM/a11y snapshot into a draft ``SiteRecipe`` via a backend."""

    def __init__(self, backend: ReasoningBackend, browser=None):
        self._backend = backend
        self._browser = browser

    def _inspect(self, session_id, browser=None) -> dict:
        cli = browser if browser is not None else self._browser
        if cli is None or not session_id:
            return {}
        result = cli.perform(session_id, "inspect", {})
        if isinstance(result, dict) and "snapshot" in result:
            return result["snapshot"] or {}
        return result or {}

    @staticmethod
    def _empty_draft(profile: SiteProfile) -> SiteRecipe:
        """A safe, reviewable draft mirroring the classifier profile."""
        return SiteRecipe(
            domain=profile.domain,
            signup_url=profile.signup_url,
            flow_type=profile.flow_type,
            requirements=profile.requirements,
            confidence=0.0,
            source=RecipeSource.learned,
            status=RecipeStatus.draft,
        )

    @staticmethod
    def _requirements(raw, profile: SiteProfile) -> SiteRequirements:
        cleaned = _filtered(raw, _REQUIREMENT_KEYS)
        if not cleaned:
            return profile.requirements
        return SiteRequirements(**cleaned)

    def learn(self, profile: SiteProfile, *,
              session_id: Optional[str] = None,
              browser=None) -> SiteRecipe:
        """Build a draft recipe from the backend's proposal (never published)."""
        snapshot = self._inspect(session_id, browser)
        proposal = self._backend.propose_recipe(profile, snapshot) or {}
        if not isinstance(proposal, dict):
            return self._empty_draft(profile)

        try:
            steps = []
            for raw in proposal.get("steps") or []:
                cleaned = _filtered(raw, _STEP_KEYS)
                if cleaned is None:
                    continue
                steps.append(RecipeStep(**cleaned))

            fields = []
            for raw in proposal.get("fields") or []:
                cleaned = _filtered(raw, _FIELD_KEYS)
                if cleaned is None:
                    continue
                fields.append(FieldSpec(**cleaned))

            try:
                flow_type = FlowType(proposal.get("flow_type",
                                                  profile.flow_type.value))
            except (ValueError, TypeError):
                flow_type = profile.flow_type

            return SiteRecipe(
                domain=profile.domain,
                signup_url=proposal.get("signup_url") or profile.signup_url,
                flow_type=flow_type,
                fields=fields,
                steps=steps,
                requirements=self._requirements(proposal.get("requirements"),
                                                profile),
                verification_flow=proposal.get("verification_flow"),
                confidence=float(proposal.get("confidence", 0.0)),
                source=RecipeSource.learned,
                status=RecipeStatus.draft,
            )
        except ValidationError:
            # A literal secret is a hard boundary; everything else a backend
            # gets wrong becomes an empty draft for the operator to review.
            if _has_literal_secret(proposal):
                raise
            return self._empty_draft(profile)
        except (ValueError, TypeError):
            return self._empty_draft(profile)


__all__ = ["RecipeLearner"]
