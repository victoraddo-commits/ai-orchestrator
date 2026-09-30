"""core.site_recipes.schema - schema unit tests."""

import pytest
from pydantic import ValidationError

from core.providers.schema import AutomationPolicy
from core.site_recipes.schema import (
    FieldSpec,
    FlowType,
    RecipeSource,
    RecipeStatus,
    RecipeStep,
    SiteProfile,
    SiteRecipe,
    SiteRequirements,
    normalize_domain,
)


def test_normalize_domain_strips_scheme_path_and_www():
    assert normalize_domain("https://www.Example.com/signup?x=1") == "example.com"
    assert normalize_domain("Example.com:443") == "example.com"
    assert normalize_domain("user@example.com") == "example.com"


def test_site_recipe_defaults_are_safe():
    recipe = SiteRecipe(domain="example.com")
    assert recipe.flow_type is FlowType.unknown
    assert recipe.status is RecipeStatus.draft
    assert recipe.source is RecipeSource.learned
    assert recipe.version == 1
    assert recipe.requirements == SiteRequirements()
    assert recipe.fields == [] and recipe.steps == []


def test_site_recipe_forbids_extra_fields():
    with pytest.raises(ValidationError):
        SiteRecipe(domain="example.com", password="hunter2")


def test_site_recipe_rejects_bad_domain():
    with pytest.raises(ValidationError):
        SiteRecipe(domain="not a domain")


def test_field_and_step_specs_validate():
    field = FieldSpec(name="Email", selector="#email", kind="email",
                      value_source="identity.email")
    step = RecipeStep(index=1, action="fill", selector="#email",
                      value_source="identity.email")
    recipe = SiteRecipe(domain="example.com", fields=[field], steps=[step])
    assert recipe.fields[0].value_source == "identity.email"
    assert recipe.steps[0].action == "fill"


def test_site_profile_defaults_policy_unknown():
    profile = SiteProfile(domain="unknown.example")
    assert profile.automation_policy is AutomationPolicy.UNKNOWN
    assert profile.registrable is True
    assert profile.flow_type is FlowType.unknown
