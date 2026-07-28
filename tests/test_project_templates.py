import core.project_templates as project_templates


def test_get_template_returns_none_for_unknown_name():
    assert project_templates.get_template("cobol-mainframe") is None


def test_all_six_named_templates_are_available():
    expected = {"react", "nextjs", "fastapi", "django", "node-api", "docker"}
    assert expected.issubset(set(project_templates.TEMPLATES))


def test_get_template_returns_label_and_base_instruction():
    template = project_templates.get_template("fastapi")

    assert template is not None
    assert "label" in template
    assert "base_instruction" in template
    assert "FastAPI" in template["base_instruction"]


def test_every_template_has_a_non_empty_base_instruction():
    for name, template in project_templates.TEMPLATES.items():
        assert template["base_instruction"].strip(), f"{name} has an empty base_instruction"
        assert template["label"].strip(), f"{name} has an empty label"
