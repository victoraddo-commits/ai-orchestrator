"""Generator: call the coding provider to generate tests."""

import logging

logger = logging.getLogger(__name__)


def _find_project_root(source_files, test_dir):
    """Find the common ancestor of all source files and the test directory."""
    import os
    from pathlib import Path

    test_dir = Path(test_dir).resolve()
    candidates = [Path(f).resolve() for f in source_files] + [test_dir]
    common = candidates[0]
    for c in candidates[1:]:
        while not str(c).startswith(str(common) + os.sep) and c != common and str(c) != str(common):
            if len(common.parts) > len(c.parts):
                common = common.parent
            else:
                c = c.parent
                if not str(common).startswith(str(c)):
                    common = common.parent
                    c = candidates[0]
    return str(common)


def generate_tests(source_files, test_dir, provider_name="gpuai_minimax", timeout=None, project_path=None):
    """Generate tests for the given source files using the specified coding provider.

    Args:
        source_files: List of Path objects pointing to Python source files.
        test_dir: Path where generated test files should be written.
        provider_name: Name of the registered AI provider to use (default: gpuai_minimax).
        timeout: Wall-clock timeout in seconds for the generation call.
        project_path: Optional project root to run the coding agent in. Auto-detected if not given.

    Returns:
        dict with keys: success, response_text, files_changed, tool_errors, cost, provider.
    """
    import os
    import core.ai_provider as ai_provider
    from core.test_gen.prompt import build_generation_prompt
    from core.test_gen.parser import parse_file
    from core.test_gen.config import DEFAULT_TIMEOUT

    if timeout is None:
        timeout = DEFAULT_TIMEOUT

    provider = ai_provider.get_provider(provider_name)
    if provider is None:
        return {
            "success": False,
            "response_text": "",
            "files_changed": [],
            "tool_errors": [{"tool": None, "content": f"Provider '{provider_name}' not registered"}],
            "cost": None,
            "provider": provider_name,
        }

    run_coding = provider.get("run_coding_task")
    if run_coding is None:
        return {
            "success": False,
            "response_text": "",
            "files_changed": [],
            "tool_errors": [{"tool": None, "content": f"Provider '{provider_name}' does not support coding_agent capability"}],
            "cost": None,
            "provider": provider_name,
        }

    if not provider.get("available_fn", lambda: False)():
        return {
            "success": False,
            "response_text": "",
            "files_changed": [],
            "tool_errors": [{"tool": None, "content": f"Provider '{provider_name}' is not available"}],
            "cost": None,
            "provider": provider_name,
        }

    source_infos = [parse_file(str(f)) for f in source_files]
    instruction = build_generation_prompt(source_infos, str(test_dir))

    if project_path is None:
        project_path = _find_project_root(source_files, test_dir)

    result = run_coding(project_path, instruction, timeout=timeout)
    result["provider"] = provider_name
    return result
