"""Loader for the private privacy denylist used by the pre-publish scans.

The denylist (source-corpus identifiers: author, employer, repo and coworker
names) is itself the secret, so it must never ship in this public repo in any
encoding — not base64, not split string fragments (CPython constant-folds
those into .pyc literals). It lives in an uncommitted file outside the repo;
scans skip when it is absent.
"""
import os

ENV_VAR = "PROOFGATE_FORBIDDEN_FILE"
DEFAULT_PATH = os.path.join(
    os.path.expanduser("~"), ".config", "proofgate-dev", "forbidden-tokens.txt"
)


def load_forbidden():
    """Lowercased denylist tokens; [] when no local denylist file exists."""
    path = os.environ.get(ENV_VAR) or DEFAULT_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return [
                line.strip().lower()
                for line in f
                if line.strip() and not line.lstrip().startswith("#")
            ]
    except OSError:
        return []
