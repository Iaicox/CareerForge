#!/usr/bin/env python3
"""Mine past Claude Code transcripts for the decisions worth keeping.

    python tools/session_digest.py extract --from C--Projects-job-search
    python tools/session_digest.py digest
    python tools/session_digest.py merge
    python tools/session_digest.py run --from C--Projects-job-search

Months of sessions hold standing rules, positioning decisions and honesty
boundaries that exist nowhere else. The transcripts themselves are not worth
carrying into a new project -- they are full of absolute paths and filenames
that no longer exist -- but the reasoning is.

Three steps, so a failure in the middle does not cost the work before it:

  extract  transcripts -> one text file per session, human turns and assistant
           prose only. Tool calls and their results are the bulk of the bytes
           and none of the reasoning, so dropping them takes a corpus from
           tens of megabytes to a couple.
  digest   each session -> a small JSON of decisions, rules and rejected ideas
  merge    all digests -> data/profile/history.md, organised by theme

Work lands in tracker/session-digest/, which is gitignored like everything
else about you.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gemini  # noqa: E402
import paths  # noqa: E402
from tracker import TrackerError  # noqa: E402

PROJECTS = Path.home() / ".claude" / "projects"
def work_dir() -> Path:
    """Read at call time, never captured: paths.configure() has to be able
    to move the whole layout, which a module-level constant outlives."""
    return paths.SESSION_DIGEST


def extracted_dir() -> Path:
    return work_dir() / "sessions"


def digests_dir() -> Path:
    return work_dir() / "digests"


def output_file() -> Path:
    return paths.PROFILE / "history.md"

EXIT_OK, EXIT_ERROR, EXIT_NEEDS_CLAUDE = 0, 1, 3

# A tool result arrives as a user entry; these prefixes mark ones that are
# machine output rather than something the person typed.
MACHINE_PREFIXES = (
    "<tool_use_error", "[Request interrupted", "<system-reminder",
    "<local-command-stdout", "Caveat: The messages below",
)


def message_text(entry: dict) -> str:
    msg = entry.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def extract_session(path: Path) -> tuple[str, int]:
    """Returns (readable transcript, number of human turns)."""
    lines: list[str] = []
    human = 0
    for raw in path.open(encoding="utf-8", errors="replace"):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        kind = entry.get("type")
        if kind not in ("user", "assistant"):
            continue
        body = message_text(entry).strip()
        if not body:
            continue
        if kind == "user":
            if body.startswith(MACHINE_PREFIXES):
                continue
            human += 1
            lines.append(f"\n### USER\n{body}")
        else:
            lines.append(f"\n### ASSISTANT\n{body}")
    return "\n".join(lines).strip(), human


DIGEST_PROMPT = """Above is a transcript of a working session between a job \
seeker and an AI assistant helping with their job search.

Pull out only what is still true and still useful. Skip the mechanics of what \
was done; keep the reasoning behind it.

{json_only}

Schema:
{{
  "standing_rules": [string],      // rules the user set that apply from then on
  "positioning": [string],         // decisions about how to present their experience
  "honesty_boundaries": [string],  // things explicitly NOT to claim, and why
  "rejected": [string],            // approaches tried and abandoned, with the reason
  "facts": [string],               // durable facts about the user, their market or a company
  "open_threads": [string]         // anything left unresolved
}}

Every item must be traceable to something actually said in this transcript. \
An empty array is the correct answer when the session contains nothing of that \
kind -- do not pad."""


MERGE_PROMPT = """Above are digests of many working sessions from one job \
search, in no particular order.

Merge them into one document organised by theme, not by date. Collapse \
duplicates. Where two sessions contradict each other, keep both and say they \
conflict -- a later session is usually but not always the correction.

Write markdown with these sections, omitting any that would be empty:

# Standing rules
# Positioning decisions
# Honesty boundaries
# Approaches tried and rejected
# Durable facts
# Open threads

Keep each item to one or two lines. Do not invent connective tissue between \
items, and do not add anything not present in the digests."""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_extract(args) -> int:
    source = Path(args.source)
    if not source.is_absolute():
        source = PROJECTS / source
    if not source.exists():
        raise TrackerError(
            f"no such transcript directory: {source}\n"
            f"Available: {', '.join(sorted(p.name for p in PROJECTS.iterdir() if p.is_dir()))}"
        )

    transcripts = sorted(source.glob("*.jsonl"))
    if not transcripts:
        raise TrackerError(f"no .jsonl transcripts in {source}")

    extracted_dir().mkdir(parents=True, exist_ok=True)
    raw_bytes = kept_bytes = 0
    written = skipped = 0

    for path in transcripts:
        raw_bytes += path.stat().st_size
        text, human = extract_session(path)
        # A session with one or two human turns is a false start, not a record
        # of anything decided.
        if human < args.min_turns:
            skipped += 1
            continue
        target = extracted_dir() / f"{path.stem}.txt"
        target.write_text(text, encoding="utf-8")
        kept_bytes += len(text.encode("utf-8"))
        written += 1

    print(f"transcripts     : {len(transcripts)}")
    print(f"extracted       : {written}  (skipped {skipped} with under "
          f"{args.min_turns} human turns)")
    print(f"raw             : {raw_bytes / 1024 / 1024:.1f} MB")
    print(f"after filtering : {kept_bytes / 1024 / 1024:.1f} MB "
          f"({kept_bytes / raw_bytes * 100:.1f}% of raw)")
    print(f"written to      : {extracted_dir()}")
    return EXIT_OK


def cmd_digest(args) -> int:
    sessions = sorted(extracted_dir().glob("*.txt"))
    if not sessions:
        raise TrackerError(f"nothing extracted yet -- run: session_digest.py extract")

    digests_dir().mkdir(parents=True, exist_ok=True)
    done = failed = reused = 0

    for path in sessions:
        target = digests_dir() / f"{path.stem}.json"
        if target.exists() and not args.force:
            reused += 1
            continue
        transcript = path.read_text(encoding="utf-8")
        prompt = DIGEST_PROMPT.format(json_only=gemini.JSON_ONLY)
        try:
            data = gemini.call_json(
                prompt, kind="session-digest",
                payload=transcript[: args.max_chars],
            )
        except (gemini.GeminiUnavailable, gemini.GeminiBadOutput) as exc:
            failed += 1
            if failed == 1:
                print(f"gemini unavailable: {exc}", file=sys.stderr)
            continue
        target.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        done += 1
        print(f"  digested {path.stem[:8]}  ({len(transcript) // 1000}k chars)")

    print(f"\ndigested {done}, reused {reused}, failed {failed}")
    if failed:
        print(
            f"\nThe {failed} that failed can be done in Claude instead: read the "
            f"files in {extracted_dir()} and write one JSON per session into {digests_dir()} "
            "using the schema in this file's DIGEST_PROMPT.",
            file=sys.stderr,
        )
        return EXIT_NEEDS_CLAUDE if done == 0 else EXIT_OK
    return EXIT_OK


def cmd_merge(args) -> int:
    digests = sorted(digests_dir().glob("*.json"))
    if not digests:
        raise TrackerError(f"no digests in {digests_dir()} -- run: session_digest.py digest")

    combined = []
    for path in digests:
        try:
            combined.append(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            print(f"  skipping unreadable digest {path.name}", file=sys.stderr)

    payload = json.dumps(combined, ensure_ascii=False)
    try:
        text = gemini.call(
            MERGE_PROMPT,
            kind="session-merge",
            model=args.model,
            payload=payload[: args.max_chars],
        )
    except (gemini.GeminiUnavailable, gemini.GeminiBadOutput) as exc:
        raw = work_dir() / "digests-combined.json"
        raw.write_text(json.dumps(combined, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"gemini unavailable: {exc}", file=sys.stderr)
        print(f"All {len(combined)} digests are combined in {raw}. "
              "Merge them in Claude and write the result to "
              f"{output_file()}.", file=sys.stderr)
        return EXIT_NEEDS_CLAUDE

    output_file().parent.mkdir(parents=True, exist_ok=True)
    header = (
        "<!-- Written by tools/session_digest.py from past Claude Code sessions.\n"
        "     Every item should be traceable to a transcript; treat anything that\n"
        "     is not as suspect and check it before acting on it. -->\n\n"
    )
    output_file().write_text(header + text.strip() + "\n", encoding="utf-8")
    print(f"merged {len(combined)} digest(s) into {output_file()}")
    return EXIT_OK


def cmd_run(args) -> int:
    code = cmd_extract(args)
    if code != EXIT_OK:
        return code
    print()
    code = cmd_digest(args)
    if code != EXIT_OK:
        return code
    print()
    return cmd_merge(args)


def main() -> int:
    ap = argparse.ArgumentParser(description="Digest past sessions into data/profile/history.md")
    sub = ap.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--from", dest="source", default="C--Projects-job-search",
                        help="transcript directory, absolute or under ~/.claude/projects")
        sp.add_argument("--min-turns", type=int, default=3,
                        help="skip sessions with fewer human turns")
        sp.add_argument("--max-chars", type=int, default=400_000)
        sp.add_argument("--model", help="override the model for this step")
        sp.add_argument("--force", action="store_true", help="redo work already done")
        return sp

    common(sub.add_parser("extract", help="transcripts -> readable session files"))
    common(sub.add_parser("digest", help="session files -> per-session JSON"))
    common(sub.add_parser("merge", help="digests -> data/profile/history.md"))
    common(sub.add_parser("run", help="all three in order"))

    args = ap.parse_args()
    return {
        "extract": cmd_extract, "digest": cmd_digest,
        "merge": cmd_merge, "run": cmd_run,
    }[args.command](args)


if __name__ == "__main__":
    from console import use_utf8
    use_utf8()
    try:
        sys.exit(main())
    except TrackerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    except KeyboardInterrupt:
        sys.exit(130)
