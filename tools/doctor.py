#!/usr/bin/env python3
"""Check that this CareerForge workspace can actually do its job.

    python tools/doctor.py            # human-readable table
    python tools/doctor.py --json     # for /setup and /doctor

Reports one line per check: ok / missing / optional, plus how to fix it.
Exit code is 1 if any required check failed, 0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402

IS_WINDOWS = platform.system() == "Windows"

OK, MISSING, WARN = "ok", "missing", "warning"


class Check:
    def __init__(self, name: str, status: str, detail: str = "", fix: str = "",
                 required: bool = True):
        self.name = name
        self.status = status
        self.detail = detail
        self.fix = fix
        self.required = required

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "fix": self.fix,
            "required": self.required,
        }

    @property
    def failed(self) -> bool:
        return self.required and self.status == MISSING


def which_version(exe: str, args: list[str]) -> str | None:
    path = shutil.which(exe)
    if not path:
        return None
    try:
        out = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=20
        )
        first = (out.stdout or out.stderr).strip().splitlines()
        return first[0] if first else path
    except (OSError, subprocess.SubprocessError):
        return path


def check_python() -> Check:
    v = sys.version_info
    if v >= (3, 11):
        return Check("Python", OK, f"{v.major}.{v.minor}.{v.micro}")
    return Check(
        "Python", MISSING, f"{v.major}.{v.minor}.{v.micro}",
        "CareerForge needs Python 3.11+ (it reads config with the stdlib tomllib module)",
    )


def check_pandoc() -> Check:
    v = which_version("pandoc", ["--version"])
    if v:
        return Check("pandoc", OK, v)
    fix = (
        "winget install JohnMacFarlane.Pandoc" if IS_WINDOWS
        else "brew install pandoc  /  apt install pandoc"
    )
    return Check("pandoc", MISSING, "not on PATH", fix)


def check_word() -> Check:
    if not IS_WINDOWS:
        return Check("MS Word", WARN, "not available on this platform",
                     "LibreOffice is used instead", required=False)
    try:
        import ctypes  # noqa: F401
        probe = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "try { $w = New-Object -ComObject Word.Application; "
             "$v = $w.Version; $w.Quit(); $v } catch { '' }"],
            capture_output=True, text=True, timeout=60,
        )
        version = probe.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        version = ""
    if version:
        return Check("MS Word", OK, f"version {version} (preferred PDF engine)",
                     required=False)
    return Check("MS Word", WARN, "not installed or not scriptable",
                 "optional; LibreOffice will be used instead", required=False)


def check_libreoffice() -> Check:
    for exe in ("soffice", "libreoffice"):
        if shutil.which(exe):
            return Check("LibreOffice", OK, shutil.which(exe), required=False)
    if IS_WINDOWS:
        for candidate in (
            Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
            Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
        ):
            if candidate.exists():
                return Check("LibreOffice", OK, str(candidate), required=False)
    fix = (
        "winget install TheDocumentFoundation.LibreOffice" if IS_WINDOWS
        else "brew install --cask libreoffice  /  apt install libreoffice"
    )
    return Check("LibreOffice", WARN, "not found", fix, required=False)


def check_pdf_engine(word: Check, libre: Check) -> Check:
    if word.status == OK or libre.status == OK:
        engine = "MS Word" if word.status == OK else "LibreOffice"
        return Check("PDF engine", OK, f"{engine} will be used")
    return Check(
        "PDF engine", MISSING, "neither MS Word nor LibreOffice is available",
        "install one of them; documents cannot be turned into PDFs without it",
    )


def check_pagecount() -> Check:
    try:
        import pypdf  # noqa: F401
        return Check("page counting", OK, "pypdf", required=False)
    except ImportError:
        pass
    if shutil.which("pdfinfo"):
        return Check("page counting", OK, "poppler pdfinfo", required=False)
    if IS_WINDOWS:
        return Check("page counting", WARN, "MS Word or an approximate fallback",
                     "pip install pypdf for exact counts on the LibreOffice path",
                     required=False)
    return Check("page counting", WARN, "approximate fallback only",
                 "pip install pypdf", required=False)


def check_fonts() -> Check:
    fonts = list((REPO / "templates" / "fonts").glob("*.ttf"))
    if fonts:
        return Check("document fonts", OK,
                     f"{len(fonts)} files in templates/fonts (installed on first build)",
                     required=False)
    return Check("document fonts", WARN, "templates/fonts is empty",
                 "documents will render with substitute fonts", required=False)


def check_templates() -> Check:
    needed = ["reference_cv.docx", "reference_cover.docx"]
    missing = [n for n in needed if not (REPO / "templates" / n).exists()]
    if missing:
        return Check("docx reference docs", MISSING, f"missing {', '.join(missing)}",
                     "regenerate with tools/make_reference.ps1")
    return Check("docx reference docs", OK, "reference_cv.docx, reference_cover.docx")


def check_config() -> Check:
    cfg = paths.CONFIG
    if not cfg.exists():
        return Check("configuration", MISSING, "data/config/config.toml not found",
                     "run /setup, or copy data/config/config.example.toml")
    try:
        import tomllib
        with cfg.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:
        return Check("configuration", MISSING, f"data/config/config.toml is invalid: {exc}",
                     "fix the syntax, or restore from data/config/config.example.toml")
    n = len(data.get("statuses", []))
    if not n:
        return Check("configuration", MISSING, "no statuses configured",
                     "copy the [[statuses]] blocks from data/config/config.example.toml")
    return Check("configuration", OK, f"locale {data.get('locale', 'en')}, {n} statuses")


def check_profile() -> Check:
    required = ["candidate.md", "cv_master.md"]
    present = [f for f in required if (paths.PROFILE / f).exists()]
    if len(present) == len(required):
        extras = [
            f for f in ("behavioral.md", "evaluation.md", "interview-prep.md",
                        "search-queries.md", "cover_letter_master.md")
            if (paths.PROFILE / f).exists()
        ]
        return Check("profile", OK, f"{len(present) + len(extras)} files in data/profile/")
    return Check("profile", MISSING,
                 f"missing {', '.join(f for f in required if f not in present)}",
                 "run /setup to build your profile")


def check_tracker() -> Check:
    db = paths.DB
    if not db.exists():
        return Check("tracker database", MISSING, "data/state/careerforge.db not found",
                     "run: python tools/tracker.py init")
    try:
        import sqlite3
        conn = sqlite3.connect(db)
        n = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        conn.close()
    except Exception as exc:
        return Check("tracker database", MISSING, f"unreadable: {exc}",
                     "run: python tools/tracker.py init")
    return Check("tracker database", OK, f"{n} application(s)")


def check_notion() -> Check:
    cfg = paths.CONFIG
    enabled = False
    if cfg.exists():
        try:
            import tomllib
            with cfg.open("rb") as fh:
                enabled = bool(tomllib.load(fh).get("notion", {}).get("enabled"))
        except Exception:
            enabled = False
    if not enabled:
        return Check("Notion mirror", OK, "disabled (optional)", required=False)
    import os
    _load_dotenv()
    token = (
        os.environ.get("NOTION_KEY")
        or os.environ.get("NOTION_TOKEN")
        or (REPO / ".notion_token").exists()
    )
    ids = paths.NOTION_IDS.exists()
    if token and ids:
        return Check("Notion mirror", OK, "token and database ids present", required=False)
    what = []
    if not token:
        what.append("no token (NOTION_KEY in .env, or .notion_token)")
    if not ids:
        what.append("no data/state/notion.json")
    return Check("Notion mirror", WARN, "; ".join(what),
                 "python tools/notion_sync.py provision", required=False)


def _load_dotenv() -> None:
    """Doctor must see the same secrets the tools do."""
    try:
        sys.path.insert(0, str(REPO / "tools"))
        from tracker import load_dotenv
        load_dotenv()
    except Exception:
        pass


def check_dotenv() -> Check:
    path = paths.ENV
    if not path.exists():
        return Check(".env", WARN, "not present",
                     "optional; holds GEMINI_API_KEY, NOTION_KEY, MAIL_PASSWORD",
                     required=False)
    try:
        keys = [
            line.split("=", 1)[0].strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#") and "=" in line
        ]
    except OSError as exc:
        return Check(".env", WARN, f"unreadable: {exc}", required=False)
    return Check(".env", OK, f"keys: {', '.join(keys) or '(none)'}", required=False)


def check_gemini() -> Check:
    """Optional: bulk gathering delegated off Claude's context."""
    try:
        import gemini
        settings = gemini.settings()
        enabled, timeout = bool(settings["enabled"]), settings["timeout_seconds"]
    except Exception:
        enabled, timeout = False, 120
    if not enabled:
        return Check("Gemini delegation", OK, "disabled (optional)", required=False)

    try:
        probe = subprocess.run(
            [sys.executable, str(REPO / "tools" / "gemini.py"), "check"],
            capture_output=True, text=True,
            # `check` runs the plain pool and then the search pool, and each of
            # them is allowed the configured budget. Anything less than both and
            # doctor reports a timeout of its own making.
            timeout=2 * timeout + 30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("Gemini delegation", WARN, f"probe failed: {exc}",
                     "python tools/gemini.py check", required=False)
    if probe.returncode == 0:
        return Check("Gemini delegation", OK, "key answers", required=False)
    reason = ""
    for line in (probe.stdout or "").splitlines():
        if "NOT USABLE" in line:
            reason = line.split("NOT USABLE:", 1)[-1].strip()[:80]
    return Check(
        "Gemini delegation", WARN, reason or "enabled but not usable",
        "set GEMINI_API_KEY in .env (https://aistudio.google.com/apikey); "
        "work falls back to Claude meanwhile",
        required=False,
    )


def run_checks() -> list[Check]:
    word = check_word()
    libre = check_libreoffice()
    return [
        check_python(),
        check_config(),
        check_profile(),
        check_tracker(),
        check_pandoc(),
        word,
        libre,
        check_pdf_engine(word, libre),
        check_pagecount(),
        check_templates(),
        check_fonts(),
        check_dotenv(),
        check_gemini(),
        check_notion(),
    ]


SYMBOL = {OK: "ok  ", MISSING: "MISS", WARN: "warn"}


def main() -> int:
    ap = argparse.ArgumentParser(description="CareerForge environment check")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    checks = run_checks()
    failed = [c for c in checks if c.failed]

    if args.json:
        print(json.dumps(
            {"ok": not failed, "checks": [c.as_dict() for c in checks]},
            ensure_ascii=False, indent=2,
        ))
        return 1 if failed else 0

    width = max(len(c.name) for c in checks)
    print(f"CareerForge doctor  ({platform.system()}, {REPO})\n")
    for c in checks:
        line = f"  [{SYMBOL[c.status]}] {c.name.ljust(width)}  {c.detail}"
        print(line.rstrip())
        if c.status != OK and c.fix:
            print(f"{' ' * (9 + width + 2)}-> {c.fix}")
    print()
    if failed:
        print(f"{len(failed)} required check(s) failed. Fix those before applying to jobs.")
        return 1
    print("Everything required is in place.")
    return 0


if __name__ == "__main__":
    from console import use_utf8
    use_utf8()
    sys.exit(main())
