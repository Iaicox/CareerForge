#!/usr/bin/env python3
"""Count the pages in a PDF, using whatever is available.

    python tools/pagecount.py path/to/file.pdf

Prints the page count on stdout. On the regex fallback it also prints a warning
to stderr, because that path can be wrong on PDFs with compressed object
streams -- the count is a hint there, not a guarantee.

Exit code 0 with a number on stdout means the count is trustworthy;
exit code 0 with a warning on stderr means it is a guess.
Install pypdf (pip install pypdf) or poppler's pdfinfo to remove the guessing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path


def via_pypdf(path: Path) -> int | None:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return None
    try:
        return len(PdfReader(str(path)).pages)
    except Exception:
        return None


def via_pdfinfo(path: Path) -> int | None:
    exe = shutil.which("pdfinfo")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, str(path)], capture_output=True, text=True, timeout=30
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"^Pages:\s+(\d+)", out, re.MULTILINE)
    return int(m.group(1)) if m else None


def via_regex(path: Path) -> int | None:
    """Last resort: count page objects in the raw bytes."""
    data = path.read_bytes()
    counts = [
        len(re.findall(rb"/Type\s*/Page[^s]", data)),
        max((int(m.group(1)) for m in re.finditer(rb"/Count\s+(\d+)", data)), default=0),
    ]
    best = max(counts)
    return best or None


def page_count(path: Path) -> tuple[int, bool]:
    """Returns (pages, exact)."""
    for fn in (via_pypdf, via_pdfinfo):
        n = fn(path)
        if n:
            return n, True
    n = via_regex(path)
    if n:
        return n, False
    raise SystemExit(f"could not determine the page count of {path}")


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] in ("-h", "--help"):
        # Asking for help is not a usage error: stdout, exit 0, and the whole
        # docstring rather than the one usage line a mistake gets.
        print(__doc__.strip())
        return 0
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.exists():
        print(f"error: no such file: {path}", file=sys.stderr)
        return 1
    pages, exact = page_count(path)
    print(pages)
    if not exact:
        print(
            f"warning: page count for {path.name} is approximate "
            "(install pypdf or poppler-utils for an exact count)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    from console import use_utf8
    use_utf8()
    sys.exit(main(sys.argv))
