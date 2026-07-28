"""Kai's enforced restrictions -- not just documentation.

The "cannot approve its own production changes" restriction is enforced
structurally: no module under core/kai/ ever calls approve_architecture or
approve_deploy anywhere in its source (see tests/test_kai_identity.py,
which greps every module in this package for both names). That test fails
loudly if a future change adds an auto-approve path.

The other three restrictions don't have an equivalent single choke point
-- "don't modify security boundaries" or "don't disable auditing" describe
outcomes of arbitrary generated code, not a specific function call to grep
for. PROTECTED_FILES + flag_protected_file_changes() is the practical
enforcement available at this layer: every self-modifying build already
requires human architecture and deploy approval (build_manager.py), so this
doesn't add a new gate -- it makes sure that when a build touches one of
the files a safety guarantee actually depends on, the human reviewing it
sees that flagged explicitly rather than having to notice it themselves in
a diff.
"""

RESTRICTIONS = [
    "Kai cannot remove approval requirements",
    "Kai cannot modify its own security boundaries",
    "Kai cannot disable auditing",
    "Kai cannot approve its own production changes",
]

# Files where a change plausibly weakens one of the four restrictions above.
PROTECTED_FILES = {
    "core/build_manager.py",       # approval-gate state machine
    "core/roadmap_manager.py",     # never calls approve_* -- enforced by test
    "core/api.py",                 # bearer-token auth, approval endpoints
    "core/kai/policies.py",        # this file
    "core/lifecycle.py",           # history/audit trail primitive
    "core/execution_audit.py",     # audit log
    "core/security.py",            # dangerous-command detection
    "core/action_policy.py",       # risk tiers / approval requirements
}


def get_restrictions():
    return RESTRICTIONS


def flag_protected_file_changes(files_changed):
    return [f for f in files_changed if f in PROTECTED_FILES]
