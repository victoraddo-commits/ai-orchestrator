"""Source parser.

Extract function and class signatures from Python source files
to provide structured context for the test-generation prompt.
"""

import ast


def parse_file(filepath):
    """Parse a Python source file and return structured signature info.

    Returns {
        "path": str,
        "functions": [{"name": str, "args": [str], "has_docstring": bool, "async": bool, "line": int}],
        "classes": [{"name": str, "methods": [...same shape as functions...], "line": int}],
        "imports": [str],
        "module_docstring": str or None,
    }
    """
    with open(filepath, encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    result = {
        "path": str(filepath),
        "functions": [],
        "classes": [],
        "imports": [],
        "module_docstring": ast.get_docstring(tree),
    }

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            result["imports"].append(ast.unparse(node))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_dunder(node.name) and not node.name == "__init__":
                continue
            result["functions"].append(_extract_func_info(node))

        elif isinstance(node, ast.ClassDef):
            class_info = {"name": node.name, "methods": [], "line": node.lineno}
            for body_item in node.body:
                if isinstance(body_item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    class_info["methods"].append(_extract_func_info(body_item))
            result["classes"].append(class_info)

    return result


def _extract_func_info(node):
    decorators = [
        ast.unparse(d) for d in node.decorator_list
    ] if node.decorator_list else []
    return {
        "name": node.name,
        "args": [arg.arg for arg in node.args.args],
        "has_docstring": ast.get_docstring(node) is not None,
        "async": isinstance(node, ast.AsyncFunctionDef),
        "line": node.lineno,
        "decorators": decorators,
    }


def _is_dunder(name):
    return name.startswith("__") and name.endswith("__")


def build_context(source_infos):
    """Build a human-readable context string from parsed source info.

    Used as the context block in the generation prompt.
    """
    parts = []

    for info in source_infos:
        path = info["path"]
        parts.append(f"### {path}")

        if info["module_docstring"]:
            parts.append(f'"""')
            parts.append(info["module_docstring"].strip())
            parts.append(f'"""')
            parts.append("")

        if info["imports"]:
            parts.append("Imports:")
            for imp in info["imports"]:
                parts.append(f"  {imp}")
            parts.append("")

        for func in info["functions"]:
            async_prefix = "async " if func["async"] else ""
            args_str = ", ".join(func["args"])
            parts.append(f"{async_prefix}def {func['name']}({args_str}):")
            if func["has_docstring"]:
                parts.append(f"    \"\"\"...\"\"\"")
            else:
                parts.append(f"    ...")
            parts.append("")

        for cls in info["classes"]:
            parts.append(f"class {cls['name']}:")
            for method in cls["methods"]:
                async_prefix = "async " if method["async"] else ""
                args_str = ", ".join(method["args"])
                parts.append(f"    {async_prefix}def {method['name']}({args_str}):")
                if method["has_docstring"]:
                    parts.append(f"        \"\"\"...\"\"\"")
                else:
                    parts.append(f"        ...")
            parts.append("")

    return "\n".join(parts)
