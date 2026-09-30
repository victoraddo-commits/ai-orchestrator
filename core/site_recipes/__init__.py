"""core.site_recipes - universal website registration recipes.

A *Site Recipe* is the reviewable, secret-free, per-domain plan for how to
register an account on a website: flow type, fields, requirements, ordered
browser steps, verification flow and confidence. It is produced either by a
human (``source=seeded``) or by ``RecipeLearner`` (``source=learned``), and is
promoted ``draft -> published`` by an operator before it drives a live run.
"""

from core.site_recipes.schema import (  # noqa: F401
    FieldSpec,
    FlowType,
    RecipeSource,
    RecipeStatus,
    RecipeStep,
    SiteProfile,
    SiteRecipe,
    SiteRequirements,
    normalize_domain,
    now_iso,
)

__all__ = [
    "FieldSpec",
    "FlowType",
    "RecipeSource",
    "RecipeStatus",
    "RecipeStep",
    "SiteProfile",
    "SiteRecipe",
    "SiteRequirements",
    "normalize_domain",
    "now_iso",
]
