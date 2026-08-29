"""Where CareerForge keeps the user's data.

Everything that is yours lives under data/; everything else in the repository
is the framework. This module is the one place that knows the shape of that
tree. Tools read `paths.X` at call time -- never `from paths import X` -- so
that configure() can point them all at another root at once, which is how the
tests run against a throwaway repo.

    data/
      config/          what you write, the tools read: config.toml (+ example)
      profile/         the source of truth for every claim   (profile.example/ beside it)
      documents/       raw material you drop in for /setup and /expand
      pipeline/        per-application folders, moved between stages by status
      state/           what the tools write: the database, notion ids, logs

The secrets file (.env) stays at the repo root, and the published manual is a
build product of framework docs, so neither is under data/.
"""

from __future__ import annotations

from pathlib import Path

STAGES = ("applications", "processing", "rejected")

# Filled in by configure(); listed here so the names exist for readers and linters.
REPO: Path
DATA: Path
CONFIG_DIR: Path
CONFIG: Path
CONFIG_EXAMPLE: Path
PROFILE: Path
PROFILE_EXAMPLE: Path
DOCUMENTS: Path
PIPELINE: Path
STATE: Path
DB: Path
NOTION_IDS: Path
GEMINI_LOG: Path
SESSION_DIGEST: Path
MANUAL_ARTIFACT: Path
ENV: Path


def _layout(repo: Path) -> dict[str, Path]:
    data = repo / "data"
    config_dir = data / "config"
    state = data / "state"
    return {
        "REPO": repo,
        "DATA": data,
        "CONFIG_DIR": config_dir,
        "CONFIG": config_dir / "config.toml",
        "CONFIG_EXAMPLE": config_dir / "config.example.toml",
        "PROFILE": data / "profile",
        "PROFILE_EXAMPLE": data / "profile.example",
        "DOCUMENTS": data / "documents",
        "PIPELINE": data / "pipeline",
            "STATE": state,
        "DB": state / "careerforge.db",
        "NOTION_IDS": state / "notion.json",
        "GEMINI_LOG": state / "gemini-log",
        "SESSION_DIGEST": state / "session-digest",
        "MANUAL_ARTIFACT": repo / "docs" / "manual-artifact.html",
        "ENV": repo / ".env",
    }


def configure(repo: Path | str) -> None:
    """Point every path at another repository root. Tests use this."""
    globals().update(_layout(Path(repo).resolve()))


def stage_dir(stage: str) -> Path:
    """The directory an application folder sits in while in this stage."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {', '.join(STAGES)}")
    return PIPELINE / stage


configure(Path(__file__).resolve().parent.parent)


def main(argv: list[str]) -> int:
    """`python tools/paths.py` prints where every piece of data is expected."""
    if argv[1:] in (["-h"], ["--help"]):
        print(__doc__.strip())
        return 0
    for name, value in sorted(globals().items()):
        if name.isupper() and isinstance(value, Path):
            mark = "" if value.exists() else "   (missing)"
            print(f"{name:<16} {value}{mark}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv))
