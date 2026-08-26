#!/usr/bin/env python3
"""Check that a built PDF still reads as text to an applicant tracking system.

    python tools/atscheck.py applications/acme/cv_jane_doe.pdf
    python tools/atscheck.py <pdf> --source applications/acme/cv_jane_doe.md
    python tools/atscheck.py <pdf> --keywords Vue TypeScript "design systems"
    python tools/atscheck.py <pdf> --json

The failure this catches is silent and expensive: a PDF that looks perfect on
screen and extracts as mojibake, so the parser on the other end reads nothing
and a human never sees the application. Ligatures are the usual culprit --
they render correctly and destroy the text layer.

Findings are warnings, not errors. A design may be knowingly unfriendly to
parsers, and that is the candidate's call to make. Exit code 2 signals
findings so a caller can react; the build scripts report and carry on.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

EXIT_OK, EXIT_ERROR, EXIT_FINDINGS, EXIT_NO_EXTRACTOR = 0, 1, 2, 3

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+\d[\d\s().-]{7,}\d)")
# Typographic ligatures render fine and extract as a single glyph many parsers
# do not map back to letters.
LIGATURES = "ﬀﬁﬂﬃﬄﬅﬆĳǳǆ"


class Finding:
    def __init__(self, check: str, detail: str, severity: str = "warn"):
        self.check = check
        self.detail = detail
        self.severity = severity

    def as_dict(self) -> dict:
        return {"check": self.check, "detail": self.detail, "severity": self.severity}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def via_pypdf(path: Path) -> str | None:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return None
    try:
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    except Exception:
        return None


def via_pdftotext(path: Path) -> str | None:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            # -enc UTF-8 matters: without it the encoding varies by platform and
            # the check would flag or miss corruption depending on the machine.
            [exe, "-enc", "UTF-8", "-layout", str(path), "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def extract(path: Path) -> tuple[str, str]:
    """Returns (text, which extractor). Raises if neither is available."""
    for name, fn in (("pypdf", via_pypdf), ("pdftotext", via_pdftotext)):
        text = fn(path)
        if text is not None:
            return text, name
    raise LookupError(
        "no PDF text extractor available. Install one:\n"
        "  pip install pypdf\n"
        "  or poppler-utils (apt install poppler-utils / brew install poppler)"
    )


# ---------------------------------------------------------------------------
# Source expectations
# ---------------------------------------------------------------------------


def headings_from_markdown(path: Path) -> list[str]:
    """Section headings the PDF should still contain."""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        if not m:
            continue
        text = m.group(1)
        text = re.sub(r"\{[^}]*\}", "", text)      # pandoc attribute blocks
        text = re.sub(r"[*_`\[\]]", "", text)      # inline markup
        text = text.strip()
        if text and not text.startswith("<"):
            out.append(text)
    return out


def normalise(text: str) -> str:
    """Fold whitespace and case so a heading match is not defeated by layout."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().lower()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def run_checks(text: str, source: Path | None, keywords: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    flat = normalise(text)

    if not text.strip():
        findings.append(Finding(
            "text layer", "the PDF has no extractable text at all -- a parser "
            "sees an empty document", "fail",
        ))
        return findings

    bad = text.count("�")
    if bad:
        findings.append(Finding(
            "encoding", f"{bad} replacement character(s) in the extracted text: "
            "some glyphs do not map back to characters", "fail",
        ))

    found_ligatures = sorted({c for c in text if c in LIGATURES})
    if found_ligatures:
        findings.append(Finding(
            "ligatures",
            f"extracted text contains {' '.join(found_ligatures)} -- many parsers "
            "do not decompose these. Replace them in the source with plain letters",
        ))

    private_use = sorted({c for c in text if "\ue000" <= c <= "\uf8ff"})
    if private_use:
        findings.append(Finding(
            "glyphs", f"{len(private_use)} private-use character(s) extracted; "
            "these carry no meaning outside the font",
        ))

    if not EMAIL_RE.search(text):
        findings.append(Finding(
            "contact", "no email address survives extraction -- the one field a "
            "parser most needs", "fail",
        ))
    if not PHONE_RE.search(text):
        findings.append(Finding("contact", "no phone number survives extraction"))

    if source and source.exists():
        missing = [
            h for h in headings_from_markdown(source)
            if normalise(h) not in flat
        ]
        if missing:
            findings.append(Finding(
                "structure",
                "section heading(s) missing from the extracted text: "
                + ", ".join(missing[:6]) + ("…" if len(missing) > 6 else ""),
            ))

    if keywords:
        absent = [k for k in keywords if normalise(k) not in flat]
        if absent:
            findings.append(Finding(
                "keywords",
                "posting keyword(s) not present: " + ", ".join(absent[:10])
                + ("…" if len(absent) > 10 else ""),
            ))

    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Check a PDF's text layer for ATS readability")
    ap.add_argument("pdf")
    ap.add_argument("--source", help="the markdown the PDF was built from")
    ap.add_argument("--keywords", nargs="*", default=[],
                    help="posting keywords that should appear")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    path = Path(args.pdf)
    if not path.exists():
        print(f"error: no such file: {path}", file=sys.stderr)
        return EXIT_ERROR

    source = Path(args.source) if args.source else path.with_suffix(".md")

    try:
        text, extractor = extract(path)
    except LookupError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_NO_EXTRACTOR

    findings = run_checks(text, source if source.exists() else None, args.keywords)

    if args.json:
        print(json.dumps({
            "pdf": str(path),
            "extractor": extractor,
            "characters": len(text),
            "ok": not findings,
            "findings": [f.as_dict() for f in findings],
        }, ensure_ascii=False, indent=2))
        return EXIT_FINDINGS if findings else EXIT_OK

    label = path.name
    if not findings:
        print(f"ATS check {label}: OK ({len(text)} chars via {extractor})")
        return EXIT_OK

    print(f"ATS check {label}: {len(findings)} finding(s) ({extractor})")
    for f in findings:
        mark = "FAIL" if f.severity == "fail" else "warn"
        print(f"  [{mark}] {f.check}: {f.detail}")
    return EXIT_FINDINGS


if __name__ == "__main__":
    from console import use_utf8
    use_utf8()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
