"""RecipeLearner - propose a draft Site Recipe from a browser DOM/a11y tree.

The learner never persists anything itself and never sees a credential value:
it asks the reasoning backend for a proposal over a *sanitized* snapshot and
maps it into a :class:`SiteRecipe` with ``source=learned``/``status=draft``.
The caller (``GenericWebAdapter``) stores it and pauses for review.

A low-confidence proposal is still returned as a draft -- the learner never
promotes a recipe to ``published``; the operator decides.
"""

from __future__ import annotations

from typing import Optional

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
)


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

    def learn(self, profile: SiteProfile, *,
              session_id: Optional[str] = None,
              browser=None) -> SiteRecipe:
        """Build a draft recipe from the backend's proposal (never published)."""
        snapshot = self._inspect(session_id, browser)
        proposal = self._backend.propose_recipe(profile, snapshot) or {}

        steps = [RecipeStep(**step) for step in proposal.get("steps", [])]
        fields = [FieldSpec(**field) for field in proposal.get("fields", [])]
        requirements = proposal.get("requirements")
        flow_value = proposal.get("flow_type", profile.flow_type.value)

        return SiteRecipe(
            domain=profile.domain,
            signup_url=proposal.get("signup_url") or profile.signup_url,
            flow_type=FlowType(flow_value),
            fields=fields,
            steps=steps,
            requirements=(SiteRequirements(**requirements) if requirements
                          else profile.requirements),
            verification_flow=proposal.get("verification_flow"),
            confidence=float(proposal.get("confidence", 0.0)),
            source=RecipeSource.learned,
            status=RecipeStatus.draft,
        )


__all__ = ["RecipeLearner"]
