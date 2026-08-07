"""Phase 18A-b: Input Validation Hardening.

Provides reusable validation utilities for API endpoints:
- SQL injection pattern detection
- XSS pattern detection
- Type-safe parameter validation
- String length / range validation
- Email format validation

Does NOT handle authorization (that's authz.py's job).
Validation failures return structured error dicts, not
exceptions — callers decide how to surface them.
"""

import re
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Validation patterns
# ---------------------------------------------------------------------------

# SQL injection patterns (heuristic — not a substitute for parameterized queries)
_SQL_INJECTION_PATTERNS = [
    r"(?i)(\bUNION\b.*\bSELECT\b)",       # UNION SELECT
    r"(?i)(\bDROP\b\s+\bTABLE\b)",         # DROP TABLE
    r"(?i)(\bALTER\b\s+\bTABLE\b)",        # ALTER TABLE
    r"(?i)(\bINSERT\b\s+\bINTO\b)",        # INSERT INTO
    r"(?i)(\bDELETE\b\s+\bFROM\b)",        # DELETE FROM
    r"(?i)(--\s*\n)",                       # SQL comment injection
    r"(?i)(\bEXEC\b\s*\()",                # EXEC(
    r"(?i)(\bEXECUTE\b\s*\()",             # EXECUTE(
    r"(?i)(';\s*)",                         # Statement terminator
    r"(?i)(\bOR\b\s+1\s*=\s*1)",           # OR 1=1
    r"(?i)(\bOR\b\s+'1'\s*=\s*'1)",        # OR '1'='1'
]

# XSS patterns
_XSS_PATTERNS = [
    r"(?i)<script[^>]*>",                  # <script> tag
    r"(?i)javascript\s*:",                  # javascript: protocol
    r"(?i)on\w+\s*=",                       # onerror=, onclick=, etc.
    r"(?i)<iframe[^>]*>",                  # <iframe>
    r"(?i)<embed[^>]*>",                    # <embed>
    r"(?i)<object[^>]*>",                   # <object>
    r"(?i)data:text/html",                  # data: URI
    r"(?i)<link[^>]*rel\s*=\s*['\"]?stylesheet",  # stylesheet injection
]

# Safe email regex (RFC 5322 simplified)
_EMAIL_PATTERN = re.compile(
    r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
)

# Safe URL pattern
_URL_PATTERN = re.compile(
    r'^https?://[^\s/$.?#].[^\s]*$'
)


# ---------------------------------------------------------------------------
# Validation functions
# ---------------------------------------------------------------------------

def contains_sql_injection(value: str) -> bool:
    """Check if a string contains SQL injection patterns.

    This is a heuristic — always use parameterized queries as the primary defense.
    """
    if not value or not isinstance(value, str):
        return False
    for pattern in _SQL_INJECTION_PATTERNS:
        if re.search(pattern, value):
            return True
    return False


def contains_xss(value: str) -> bool:
    """Check if a string contains XSS patterns."""
    if not value or not isinstance(value, str):
        return False
    for pattern in _XSS_PATTERNS:
        if re.search(pattern, value):
            return True
    return False


def is_valid_email(email: str) -> bool:
    """Validate email format."""
    if not email or not isinstance(email, str):
        return False
    return bool(_EMAIL_PATTERN.match(email.strip()))


def is_valid_url(url: str, allowed_schemes: Tuple[str, ...] = ("http", "https")) -> bool:
    """Validate URL format and scheme."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in allowed_schemes and bool(parsed.netloc)
    except Exception:
        return False


def validate_string(
    value: Optional[str],
    field_name: str = "value",
    min_length: int = 0,
    max_length: int = 1024,
    allow_empty: bool = False,
    check_sql: bool = False,
    check_xss: bool = True,
) -> Dict[str, Any]:
    """Validate a string parameter.

    Returns:
        {"valid": True, "value": sanitized_value} or
        {"valid": False, "error": "description", "field": field_name}
    """
    if value is None:
        if allow_empty:
            return {"valid": True, "value": ""}
        return {"valid": False, "error": f"{field_name} is required", "field": field_name}

    if not isinstance(value, str):
        return {"valid": False, "error": f"{field_name} must be a string", "field": field_name}

    value = value.strip()

    if not value and not allow_empty:
        return {"valid": False, "error": f"{field_name} cannot be empty", "field": field_name}

    if not value and allow_empty:
        return {"valid": True, "value": ""}

    if len(value) < min_length:
        return {
            "valid": False,
            "error": f"{field_name} must be at least {min_length} characters",
            "field": field_name,
        }

    if len(value) > max_length:
        return {
            "valid": False,
            "error": f"{field_name} must be at most {max_length} characters",
            "field": field_name,
        }

    if check_sql and contains_sql_injection(value):
        return {"valid": False, "error": f"{field_name} contains invalid patterns", "field": field_name}

    if check_xss and contains_xss(value):
        return {"valid": False, "error": f"{field_name} contains invalid patterns", "field": field_name}

    return {"valid": True, "value": value}


def validate_dict_fields(
    data: Dict[str, Any],
    required: Optional[List[str]] = None,
    optional: Optional[List[str]] = None,
    check_xss: bool = True,
) -> Dict[str, Any]:
    """Validate a dict's fields.

    Checks that required fields are present, only allowed fields exist,
    and optional fields don't contain XSS/SQL injection.
    """
    result = {"valid": True, "errors": [], "fields": {}}

    if required is None:
        required = []
    if optional is None:
        optional = []

    allowed = set(required) | set(optional)

    # Check for unknown fields
    for key in data:
        if allowed and key not in allowed:
            result["errors"].append({"field": key, "error": f"Unknown field: {key}"})
            result["valid"] = False

    # Check required fields
    for field in required:
        if field not in data:
            result["errors"].append({"field": field, "error": f"Required field missing: {field}"})
            result["valid"] = False
        else:
            val_result = validate_string(data[field], field, check_xss=check_xss)
            if val_result["valid"]:
                result["fields"][field] = val_result["value"]
            else:
                result["errors"].append(val_result)
                result["valid"] = False

    # Validate optional fields if present
    for field in optional:
        if field in data and data[field] is not None:
            val_result = validate_string(data[field], field, check_xss=check_xss)
            if val_result["valid"]:
                result["fields"][field] = val_result["value"]
            else:
                result["errors"].append(val_result)
                result["valid"] = False

    return result


def sanitize_html(value: str) -> str:
    """Basic HTML entity encoding for display.

    Escape <, >, &, ", ' to prevent XSS in rendered output.
    """
    if not isinstance(value, str):
        return str(value)
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
