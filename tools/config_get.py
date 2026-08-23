#!/usr/bin/env python3
"""Read a value out of config/config.toml for the shell scripts.

    python tools/config_get.py documents          # JSON object
    python tools/config_get.py documents.engine   # bare scalar

Exists so build.ps1 and build.sh do not need a TOML parser of their own, and
so the page limits live in exactly one place.
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from tracker import TrackerError, load_config  # noqa: E402

DEFAULTS = {
    "documents": {
        "cv_filename": "cv_{slug}.md",
        "cover_filename": "cover_letter_{slug}.md",
        "name_slug": "candidate",
        "cv_max_pages": 2,
        "cover_max_pages": 1,
        "engine": "auto",
    }
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        data = load_config().data
    except TrackerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    node: object = data
    default: object = DEFAULTS
    for part in argv[1].split("."):
        if isinstance(default, dict):
            default = default.get(part)
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            node = None
            break
    if node is None:
        node = default
    if node is None:
        print(f"error: no such key: {argv[1]}", file=sys.stderr)
        return 1

    if isinstance(node, (dict, list)):
        # Fill in any key the user's config omitted, so callers can rely on it.
        if isinstance(node, dict) and isinstance(default, dict):
            node = {**default, **node}
        print(json.dumps(node, ensure_ascii=False))
    else:
        print(node)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
