"""KAI 2.0 Cognitive Model Router — KAI MODEL TEAM

Maps high-level cognitive functions to specific AI models based on task
requirements. Enables swapping model implementations without changing
application code.

Architecture:
    Application requests COGNITIVE ROLE → Router selects PHYSICAL MODEL

KAI MODEL TEAM (one Kai team, five roles):
    - reasoning            → kai.brain        (GLM-4.7-Flash, kai-brain:latest)
    - coding               → kai.coder.fast   (Qwen2.5-Coder-7B, kai-coder:7b)
    - specialist_coding    → kai.coder.fast   (Qwen2.5-Coder-7B, kai-coder:7b)
    - coder                → kai.coder        (Qwen3-Coder-30B — pending benchmark;
                                                served by kai-coder:7b meanwhile)
    - deep                 → kai.deep         (Qwen3.6-27B, kai-brain:27b)
    - security             → kai.security     (qwen2.5:7b — security advisory)
    - fast                 → kai.brain        (GLM-4.7-Flash, 47 tok/s)
    - fallback             → llama3.2:3b      (emergency only)

MOST IMPORTANT RULE:
    Kai Brain is both the manager AND a productive worker. It reviews and
    verifies ALL significant coder output, but also writes code itself whenever
    no higher-priority orchestration/review task requires it. Never keep
    Kai Brain idle while useful coding work is available.
"""

from typing import Dict, List, Optional
import re
from core.logger import info
import logging
logger = logging.getLogger(__name__)


# COGNITIVE MODEL REGISTRY
# Maps cognitive role names to physical Ollama model names.
# Follows the KAI MODEL TEAM operating rules (one Kai team, five roles).
COGNITIVE_MODELS = {
    # 🧠 kai.brain — GLM-4.7-Flash. Manager AND worker.
    "reasoning": {
        "model_name": "kai-brain:latest",  # GLM-4.7-Flash (aliased to kai-brain:latest)
        "description": "Kai Brain (kai.brain) — primary brain + orchestrator. Plans, reasons, decides, researches, coordinates, and reviews/verifies ALL significant coder output. Also writes code itself whenever no higher-priority orchestration/review task requires it.",
        "capabilities": ["text_task", "tools", "coding"],
        "use_cases": [
            "conversation",
            "reasoning",
            "planning",
            "decisions",
            "research",
            "coordination",
            "review & verification of coder output",
            "architecture",
            "multi-step analysis",
            "roadmap management"
        ]
    },

    # ⚡ kai.coder.fast — Qwen2.5-Coder-7B. Fast everyday coding worker.
    "coding": {
        "model_name": "kai-coder:7b",  # Qwen2.5-Coder-7B
        "description": "kai.coder.fast — fast everyday coding worker. Simple fixes, scripts, commands, configs, debugging, and routine coding. First choice when the task is straightforward.",
        "capabilities": ["text_task", "coding"],
        "use_cases": [
            "simple fixes",
            "scripts",
            "commands",
            "configs",
            "debugging",
            "routine coding",
            "small patches",
            "devops automation"
        ]
    },

    # ⚡ kai.coder.fast — Qwen2.5-Coder-7B. Small specialist edits.
    "specialist_coding": {
        "model_name": "kai-coder:7b",  # Qwen2.5-Coder-7B
        "description": "kai.coder.fast (specialist) — small patches, configuration edits, and simple scripts.",
        "capabilities": ["text_task", "coding"],
        "use_cases": [
            "small patches",
            "configuration edits",
            "simple scripts",
            "quick debugging",
            "code transformations"
        ]
    },

    # 💻 kai.coder — Qwen3-Coder-30B. High-end coding specialist (pending benchmark).
    "coder": {
        "model_name": "kai-coder:7b",  # promote to Qwen3-Coder-30B once benchmark proves it worthwhile
        "description": "kai.coder — high-end coding specialist for complex coding, multi-file changes, major refactors, difficult debugging, and large implementations. Served by kai-coder:7b until Qwen3-Coder-30B benchmark proves it worthwhile.",
        "capabilities": ["text_task", "coding"],
        "use_cases": [
            "complex coding",
            "multi-file changes",
            "major refactors",
            "difficult debugging",
            "large implementations"
        ]
    },

    # 🧠 kai.deep — Qwen3.6-27B. Deep-reasoning escalation.
    "deep": {
        "model_name": "kai-brain:27b",  # Qwen3.6-27B
        "description": "kai.deep — deep-reasoning escalation model. Used for unusually difficult architecture, reasoning, investigations, or failures that Kai Brain cannot confidently solve.",
        "capabilities": ["text_task", "tools"],
        "use_cases": [
            "difficult architecture",
            "deep reasoning",
            "investigations",
            "hard failures",
            "escalation from Kai Brain"
        ]
    },

    # 🛡️ kai.security — security advisory/checking. Deterministic controls remain authoritative.
    "security": {
        "model_name": "qwen2.5:7b",
        "description": "kai.security — security advisory/checking model. Reviews suspicious code, tools, MCP servers, prompts, and actions. Deterministic security controls remain authoritative.",
        "capabilities": ["text_task"],
        "use_cases": [
            "security review",
            "suspicious code review",
            "prompt/action review",
            "tool/MCP audit"
        ]
    },

    # Fast responses — served by Kai Brain (GLM-4.7-Flash, ~47 tok/s).
    "fast": {
        "model_name": "kai-brain:latest",  # GLM-4.7-Flash
        "description": "Fast response — quick classification, status, and summaries via Kai Brain (GLM-4.7-Flash).",
        "capabilities": ["text_task"],
        "use_cases": [
            "quick responses",
            "classification",
            "routing decisions",
            "summarization",
            "status queries",
            "notifications",
            "simple conversations"
        ]
    },

    # Emergency fallback only.
    "fallback": {
        "model_name": "llama3.2:3b",
        "description": "Emergency fallback - basic responses, health checks.",
        "capabilities": ["text_task"],
        "use_cases": [
            "health checks",
            "emergency responses",
            "basic queries",
            "service continuity"
        ]
    }
}


# TASK CLASSIFICATION PATTERNS
# Regex patterns to auto-classify tasks into cognitive roles
COGNITIVE_PATTERNS = {
    "reasoning": [
        r"plan|strategy|architect|design|analyze|evaluate|decide",
        r"roadmap|vision|direction|approach|methodology",
        r"orchestrat|coordinat|prioritiz|organiz",
        r"complex|difficult|challeng|sophistic"
    ],

    "coding": [
        r"implement|develop|build|create.*code|write.*code",
        r"refactor|debug|fix.*bug|troubleshoot",
        r"repository|codebase|git|commit|pull.*request",
        r"devops|docker|kubernetes|deploy|ci/cd",
        r"test|unittest|integration.*test"
    ],

    "fast": [
        r"status|health|check|verify|confirm",
        r"quick|fast|brief|short|summar",
        r"classif|categor|tag|label",
        r"is.*running|what.*status|show.*state"
    ],

    "specialist_coding": [
        r"patch|fix.*line|edit.*config|update.*setting",
        r"small.*script|quick.*script|simple.*code",
        r"one.*liner|sed|awk|grep.*replace"
    ],

    "coder": [
        r"major.*refactor|multi.*file|large.*implement",
        r"complex.*(code|bug|implement)|difficult.*(debug|refactor)",
        r"rewrite|restructur|redesign.*system|significant.*change"
    ],

    "deep": [
        r"deep.*(reason|investigat|analyz)|unsolv|escalat",
        r"difficult.*(architect|reason|investigat)|hard.*problem",
        r"root.*cause.*analyz|why.*(fail|broken)|investigate.*deep"
    ],

    "security": [
        r"secur|vulnerab|exploit|injection|auth.*(bypass|check)",
        r"suspicious.*(code|tool|prompt)|malicious|threat|audit.*secur",
        r"review.*secur|penetrat|attack.*surface|credential.*(leak|expos)"
    ]
}


def classify_task(prompt: str, explicit_role: Optional[str] = None) -> str:
    """Classify a task into a cognitive role.

    Args:
        prompt: The task description/prompt
        explicit_role: If provided, override auto-classification

    Returns:
        Cognitive role name (reasoning, coding, fast, etc.)
    """
    if explicit_role and explicit_role in COGNITIVE_MODELS:
        return explicit_role

    # Auto-classify based on patterns
    prompt_lower = prompt.lower()

    # Check each role's patterns
    for role, patterns in COGNITIVE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, prompt_lower):
                info(f"Cognitive router: classified as '{role}' (pattern: {pattern[:30]}...)")
                return role

    # Default to reasoning for complex/unclear tasks
    info("Cognitive router: no pattern match, defaulting to 'reasoning'")
    return "reasoning"


def get_model_for_role(role: str) -> str:
    """Get the physical model name for a cognitive role.

    Args:
        role: Cognitive role name

    Returns:
        Ollama model name (e.g., "qwen2.5:7b", "kai-brain:27b")
    """
    if role not in COGNITIVE_MODELS:
        logger.warning(f"Unknown cognitive role '{role}', using 'reasoning'")
        role = "reasoning"

    model_name = COGNITIVE_MODELS[role]["model_name"]
    info(f"Cognitive router: {role} → {model_name}")
    return model_name


def get_role_info(role: str) -> Dict:
    """Get full information about a cognitive role.

    Returns dict with model_name, description, capabilities, use_cases.
    """
    return COGNITIVE_MODELS.get(role, COGNITIVE_MODELS["reasoning"])


def route_task(prompt: str, task_type: Optional[str] = None,
               explicit_role: Optional[str] = None) -> Dict:
    """Route a task to the appropriate cognitive model.

    Args:
        prompt: Task description
        task_type: Legacy task type (coding, planning, etc.) - optional
        explicit_role: Override auto-classification with specific role

    Returns:
        Dict with:
            - role: Cognitive role selected
            - model: Physical model name
            - info: Role information dict
    """
    # Classify into cognitive role
    role = classify_task(prompt, explicit_role)

    # Get model assignment
    model = get_model_for_role(role)
    role_info = get_role_info(role)

    return {
        "role": role,
        "model": model,
        "description": role_info["description"],
        "capabilities": role_info["capabilities"]
    }


def get_available_roles() -> List[str]:
    """Get list of all cognitive roles."""
    return list(COGNITIVE_MODELS.keys())


def update_model_assignment(role: str, model_name: str) -> bool:
    """Update which physical model handles a cognitive role.

    This allows swapping models without changing application code.

    Args:
        role: Cognitive role (reasoning, coding, etc.)
        model_name: New Ollama model name

    Returns:
        True if update successful
    """
    if role not in COGNITIVE_MODELS:
        logger.warning(f"Cannot update unknown role '{role}'")
        return False

    old_model = COGNITIVE_MODELS[role]["model_name"]
    COGNITIVE_MODELS[role]["model_name"] = model_name
    info(f"Cognitive model updated: {role} {old_model} → {model_name}")
    return True
