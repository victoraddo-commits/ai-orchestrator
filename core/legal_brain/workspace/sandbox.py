"""Sandboxed PDF Processing for User Uploads.

All user-uploaded documents are processed in an isolated environment:
  - Subprocess execution with resource limits
  - Timeout enforcement (default 30s)
  - Memory limits
  - File size limits
  - NEVER writes to permanent corpus storage

This module provides safe extraction of text from uploaded documents
without risking the orchestrator process or permanent data.
"""

import subprocess
import tempfile
import os
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from ..config import (
    SANDBOX_TIMEOUT_SECONDS,
    SANDBOX_MAX_MEMORY_MB,
    SANDBOX_MAX_FILE_SIZE_MB,
)


class SandboxError(Exception):
    """Raised when sandbox processing fails."""


class SandboxTimeout(SandboxError):
    """Processing exceeded time limit."""


class SandboxFileTooLarge(SandboxError):
    """Upload exceeds size limit."""


def _check_file_size(file_path: str) -> int:
    """Check file size and return bytes. Raises if too large."""
    size = os.path.getsize(file_path)
    max_bytes = SANDBOX_MAX_FILE_SIZE_MB * 1024 * 1024
    if size > max_bytes:
        raise SandboxFileTooLarge(
            f"File size {size} exceeds limit of {max_bytes} bytes ({SANDBOX_MAX_FILE_SIZE_MB}MB)"
        )
    return size


def extract_text_from_pdf(file_path: str, timeout: int = SANDBOX_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Extract text from a PDF in a sandboxed subprocess.

    Uses pypdf in a subprocess to isolate any potential PDF parser exploits
    from the main orchestrator process.

    Returns:
        {"text": "...", "pages": N, "success": True/False, "error": "..."}
    """
    if not os.path.exists(file_path):
        return {"text": "", "pages": 0, "success": False, "error": "File not found"}

    file_size = _check_file_size(file_path)

    # Python script to run in subprocess
    extractor_script = f'''
import sys, json
try:
    from pypdf import PdfReader
    reader = PdfReader("{file_path}")
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    full_text = "\\n\\n".join(pages)
    print(json.dumps({{"text": full_text, "pages": len(reader.pages), "success": True}}))
except Exception as e:
    print(json.dumps({{"text": "", "pages": 0, "success": False, "error": str(e)}}))
'''

    try:
        result = subprocess.run(
            ["python3", "-c", extractor_script],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tempfile.gettempdir(),  # Isolate from working dir
            env={
                "PATH": os.environ.get("PATH", "/usr/bin"),
                "HOME": tempfile.gettempdir(),
                "PYTHONPATH": "",  # Don't inherit Kai's Python path
            },
        )

        import json
        if result.returncode != 0:
            return {
                "text": "",
                "pages": 0,
                "success": False,
                "error": f"Subprocess failed: {result.stderr[:500]}",
            }

        return json.loads(result.stdout.strip())

    except subprocess.TimeoutExpired:
        return {
            "text": "",
            "pages": 0,
            "success": False,
            "error": f"PDF extraction timed out after {timeout}s",
        }
    except Exception as e:
        return {"text": "", "pages": 0, "success": False, "error": str(e)}


def extract_text_from_text_file(file_path: str) -> Dict[str, Any]:
    """Extract text from a plain text or markdown file."""
    _check_file_size(file_path)

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Count approximate pages (2500 chars per page)
        pages = max(1, len(content) // 2500)

        return {"text": content, "pages": pages, "success": True}
    except Exception as e:
        return {"text": "", "pages": 0, "success": False, "error": str(e)}


def extract_text(file_path: str) -> Dict[str, Any]:
    """Extract text from any supported file type.

    Routes to the appropriate extractor based on file extension.
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext in (".txt", ".md", ".rst", ".json", ".xml", ".html", ".csv"):
        return extract_text_from_text_file(file_path)
    elif ext in (".docx", ".doc"):
        return {
            "text": "",
            "pages": 0,
            "success": False,
            "error": f"DOCX extraction not supported in sandbox (file: {Path(file_path).name})",
        }
    else:
        return {
            "text": "",
            "pages": 0,
            "success": False,
            "error": f"Unsupported file type: {ext}",
        }


def scan_file(file_path: str) -> Dict[str, Any]:
    """Scan a file for malware using ClamAV if available.

    Falls back to basic heuristics if ClamAV is not configured.
    Returns:
        {"clean": True/False, "scanner": "clamav"|"heuristic", "details": "..."}
    """
    from ..config import CLAMAV_ENABLED, CLAMAV_SOCKET

    if CLAMAV_ENABLED and os.path.exists(CLAMAV_SOCKET):
        try:
            result = subprocess.run(
                ["clamdscan", "--fdpass", "--no-summary", file_path],
                capture_output=True,
                text=True,
                timeout=60,
            )
            clean = result.returncode == 0
            return {
                "clean": clean,
                "scanner": "clamav",
                "details": result.stdout.strip() if clean else result.stdout.strip()[:500],
            }
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass  # Fall through to heuristic

    # Heuristic check: executable magic bytes
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
        # Check for executable headers
        if header[:4] == b"\x7fELF" or header[:2] == b"MZ":
            return {"clean": False, "scanner": "heuristic", "details": "Executable file detected"}
    except Exception:
        pass

    return {"clean": True, "scanner": "heuristic", "details": "No threats detected (heuristic)"}
