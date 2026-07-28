import core.kai.identity as identity
import core.kai.mission as mission
import core.kai.goals as goals
import core.kai.policies as policies


def test_identity_statement_is_defined():
    assert "Kai" in identity.get_identity()
    assert "AI Orchestrator" in identity.get_identity()


def test_mission_statement_is_defined():
    assert "human control" in mission.get_mission().lower()


def test_capabilities_include_the_six_specified():
    caps = goals.get_capabilities()

    for expected in ["analyze", "plan", "delegate", "execute approved work", "learn", "improve"]:
        assert expected in caps


def test_restrictions_are_defined():
    restrictions = policies.get_restrictions()

    assert len(restrictions) == 4
    joined = " ".join(restrictions).lower()
    assert "approval" in joined
    assert "security" in joined
    assert "audit" in joined
    assert "production" in joined


def test_flag_protected_file_changes_detects_a_protected_file():
    flagged = policies.flag_protected_file_changes(["core/build_manager.py", "app/main.py"])

    assert "core/build_manager.py" in flagged
    assert "app/main.py" not in flagged


def test_flag_protected_file_changes_returns_empty_for_no_protected_files():
    assert policies.flag_protected_file_changes(["app/main.py", "README.md"]) == []


def test_flag_protected_file_changes_handles_empty_input():
    assert policies.flag_protected_file_changes([]) == []


def test_kai_policies_list_includes_the_expected_protected_files():
    # Sanity-check the actual protected set covers the mechanisms the
    # restrictions describe: approval gates, security config, auditing.
    protected = policies.PROTECTED_FILES

    assert "core/build_manager.py" in protected
    assert "core/kai/policies.py" in protected
    assert "core/execution_audit.py" in protected
    assert "core/security.py" in protected


def test_no_kai_module_ever_calls_approve_architecture_or_approve_deploy():
    # Same enforcement pattern as Phase 12L's roadmap_manager test: grep the
    # actual source of every module under core/kai/ so a future change that
    # adds an auto-approve path fails a test, not just a code review.
    import inspect
    import pkgutil
    import core.kai as kai_package

    for _, module_name, _ in pkgutil.iter_modules(kai_package.__path__):
        module = __import__(f"core.kai.{module_name}", fromlist=[module_name])
        source = inspect.getsource(module)
        assert "approve_architecture(" not in source, f"{module_name} calls approve_architecture("
        assert "approve_deploy(" not in source, f"{module_name} calls approve_deploy("
