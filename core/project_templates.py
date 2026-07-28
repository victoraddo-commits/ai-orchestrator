"""Scaffolding prompts for the coding agent (core.coding_bridge), not a second
code-generation engine. Each template is just a well-specified starting
instruction handed to the same Claude Agent SDK session CloudCLI already
runs -- the agent decides the actual file contents, same as any other build.
"""

TEMPLATES = {
    "react": {
        "label": "React (Vite)",
        "base_instruction": (
            "Scaffold a new React application using Vite. Standard structure "
            "(src/, src/main.jsx, src/App.jsx), a package.json with dev/build/"
            "test scripts, and a minimal component test set up with Vitest."
        ),
    },
    "nextjs": {
        "label": "Next.js",
        "base_instruction": (
            "Scaffold a new Next.js application using the App Router. Standard "
            "structure (app/, app/page.tsx, app/layout.tsx), TypeScript, and a "
            "package.json with dev/build/start scripts."
        ),
    },
    "fastapi": {
        "label": "FastAPI",
        "base_instruction": (
            "Scaffold a new FastAPI application. Standard structure (app/main.py, "
            "a health check endpoint, requirements.txt or pyproject.toml), and a "
            "pytest test scaffold covering the health check."
        ),
    },
    "django": {
        "label": "Django",
        "base_instruction": (
            "Scaffold a new Django project via django-admin conventions. Standard "
            "structure with one starter app, requirements.txt, and Django's "
            "default test scaffold."
        ),
    },
    "node-api": {
        "label": "Node.js API (Express)",
        "base_instruction": (
            "Scaffold a new Node.js REST API using Express. Standard structure "
            "(src/index.js, a health check route), package.json with dev/start/"
            "test scripts, and a minimal test set up with the project's usual "
            "test runner."
        ),
    },
    "docker": {
        "label": "Dockerized service",
        "base_instruction": (
            "Add a production-ready multi-stage Dockerfile and a docker-compose.yml "
            "for this project, matching whatever stack is already present (or being "
            "built) in this repository. Include a .dockerignore."
        ),
    },
}


def get_template(name):
    return TEMPLATES.get(name)
