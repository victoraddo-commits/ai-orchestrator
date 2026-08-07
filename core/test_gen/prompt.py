"""Prompt builder for test generation.

Build the instruction prompt sent to the coding provider.
"""


def build_generation_prompt(source_infos, test_dir):
    """Build the instruction string for generating tests.

    The prompt includes parsed source context and instructs the coding
    agent to produce pytest-compatible test files.
    """
    from core.test_gen.parser import build_context

    context = build_context(source_infos)

    filenames = "\n".join(
        f"- {info['path']}"
        for info in source_infos
    )

    return f"""Generate pytest-compatible tests for the following Python source file(s).

Write the tests to `{test_dir}/`. Each source file gets a corresponding
`test_<basename>.py` file. Follow these rules:

1. Import the module(s) under test correctly using their package path.
2. Cover every public function and method with at least one test.
3. Cover edge cases: empty inputs, boundary values, expected exceptions.
4. Use pytest fixtures and parametrize where appropriate.
5. Do NOT use any external mocking libraries not already imported by the source.
6. Tests must be runnable with `pytest {test_dir}/` from the project root.

Source file(s):
{filenames}

--- Source context ---
{context}

--- End ---

Write the test files now. Do not modify the original source files."""
