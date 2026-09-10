# Pre-built security-scanner image for core/security_scanner.py.
#
# Root cause of the slow scans: every build ran
#   pip install --quiet semgrep && semgrep --config=auto ...
# inside a throwaway --read-only container with a tmpfs /root, so semgrep's
# ~150MB dependency tree (opentelemetry, httpx, jsonschema, semgrep-core…)
# was re-downloaded and re-installed from PyPI on *every single scan*. Nothing
# persisted, so the cost was paid N times for N builds.
#
# This image installs semgrep + bandit once, into the system site-packages
# (which live under the read-only root, NOT the ephemeral tmpfs /root), so
# the scanners are on PATH immediately and the scan command is just
#   semgrep --config=auto --json -q .
# with no per-build install. Rebuild periodically to pick up new scanner
# releases:  docker build -t kai-security-scanner:latest .
FROM python:3.12-slim

# --security=insecure maps to apparmor=unconfined for the build step — this
# LXC requires it (see /project/CLAUDE.md "Docker Usage"). Without it the RUN
# step fails with "apparmor failed to apply profile" during container init.
RUN --security=insecure pip install --no-cache-dir --quiet semgrep bandit \
    && rm -rf /root/.cache/pip

WORKDIR /workspace
