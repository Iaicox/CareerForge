#!/usr/bin/env python3
"""SessionStart hook: tell a fresh clone that it needs /setup.

Prints nothing once the workspace is configured, so it costs nothing in daily
use. The same rule is stated at the top of CLAUDE.md; this hook only makes it
impossible to miss on the very first session.

Exits 0 no matter what -- a hook must never be the reason a session fails to
start.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def main() -> int:
    missing = []
    if not (REPO / "profile" / "candidate.md").exists():
        missing.append("your profile (profile/candidate.md)")
    if not (REPO / "config" / "config.toml").exists():
        missing.append("your configuration (config/config.toml)")
    if not (REPO / "tracker" / "careerforge.db").exists():
        missing.append("the tracker database (tracker/careerforge.db)")

    if not missing:
        return 0

    print(
        "This CareerForge workspace is not set up yet - missing "
        + ", ".join(missing)
        + ". Run /setup to build it, or /doctor to see what the toolchain is "
        "missing. Do not start drafting documents until the profile exists."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
