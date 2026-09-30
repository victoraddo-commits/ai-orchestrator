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
    is_symbolic_value_source,
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


SYMBOLIC_SOURCES = [
    "identity.full_name",
    "identity.display_name",
    "identity.email",
    "identity.phone",
    "generated_password",
    "generated_username",
    "totp",
    "literal:secrets/accounts/acme/ref-1",
]

NON_SYMBOLIC_SOURCES = [
    "hunter2-literal",
    "constant:hunter2",
    "password123",
    "sk-live-abcdef",
    "identity.password",
    "literal:hunter2",
]


def test_field_spec_rejects_literal_secret_value_source():
    with pytest.raises(ValidationError):
        FieldSpec(name="Email", selector="#email", value_source="hunter2-literal")


def test_field_spec_accepts_generated_password_value_source():
    field = FieldSpec(name="Password", selector="#password",
                      value_source="generated_password")
    assert field.value_source == "generated_password"


@pytest.mark.parametrize("source", SYMBOLIC_SOURCES)
def test_symbolic_value_sources_are_accepted(source):
    assert is_symbolic_value_source(source) is True
    assert FieldSpec(name="X", selector="#x", value_source=source).value_source == source
    assert RecipeStep(index=1, action="fill", selector="#x",
                      value_source=source).value_source == source


@pytest.mark.parametrize("source", NON_SYMBOLIC_SOURCES)
def test_literal_or_secret_value_sources_are_rejected(source):
    assert is_symbolic_value_source(source) is False
    with pytest.raises(ValidationError):
        FieldSpec(name="X", selector="#x", value_source=source)
    with pytest.raises(ValidationError):
        RecipeStep(index=1, action="fill", selector="#x", value_source=source)


def test_recipe_confidence_is_bounded():
    assert SiteRecipe(domain="example.com", confidence=1.0).confidence == 1.0
    with pytest.raises(ValidationError):
        SiteRecipe(domain="example.com", confidence=1.5)
    with pytest.raises(ValidationError):
        SiteRecipe(domain="example.com", confidence=-0.1)


def test_recipe_version_must_be_positive():
    assert SiteRecipe(domain="example.com", version=1).version == 1
    with pytest.raises(ValidationError):
        SiteRecipe(domain="example.com", version=0)
