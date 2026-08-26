#!/usr/bin/env python3
"""The scraper's shortlist: what was found, what it scored, what has expired.

    python tools/shortlist.py show --min-score 60
    python tools/shortlist.py show --unscored --json
    python tools/shortlist.py merge-scores --file ranking.json
    python tools/shortlist.py sweep
    python tools/shortlist.py add --file postings.json

`/scrape` fills `job_scraper/seen_jobs.json`; `/rank` scores it and merges the
scores back here. Keeping that mechanical work in a tool means the agent never
hand-edits the JSON, which is both error-prone and expensive.

Scores persist. A triage finding that only ever reached the console is a
finding you pay for again next week.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tracker import REPO, TrackerError, parse_deadline  # noqa: E402

SEEN_PATH = REPO / "job_scraper" / "seen_jobs.json"
VERDICT_ORDER = {"strong": 0, "good": 1, "moderate": 2, "weak": 3, "poor": 4}

# Entries scraped before /rank existed carry a coarse "fit" instead of a score.
# Reading it keeps that history visible rather than showing a column of dashes,
# but it is never written back: only /rank produces a score.
LEGACY_FIT = {"high": "good", "medium": "moderate", "low": "weak"}


def verdict_of(entry: dict) -> str | None:
    if entry.get("verdict"):
        return entry["verdict"]
    return LEGACY_FIT.get(str(entry.get("fit") or "").lower())


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def load() -> dict:
    if not SEEN_PATH.exists():
        return {"seen": {}}
    try:
        data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise TrackerError(f"{SEEN_PATH.name} is not valid JSON: {exc}")
    data.setdefault("seen", {})
    return data


def save(data: dict) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def entries(data: dict) -> list[dict]:
    return [{**v, "id": k} for k, v in data["seen"].items()]


def sort_key(e: dict):
    # Highest score first; unscored last, since they still need a decision.
    score = e.get("score")
    return (
        0 if score is not None else 1,
        -(score or 0),
        VERDICT_ORDER.get(verdict_of(e), 9),
        e.get("company") or "",
    )


def is_expired(e: dict) -> bool:
    deadline = e.get("deadline")
    return bool(deadline) and deadline < today()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_show(args) -> int:
    data = load()
    rows = entries(data)

    if not args.include_expired:
        rows = [e for e in rows if not is_expired(e)]
    if args.unscored:
        rows = [e for e in rows if e.get("score") is None]
    if args.min_score is not None:
        rows = [e for e in rows if (e.get("score") or 0) >= args.min_score]
    if args.status:
        rows = [e for e in rows if e.get("status") == args.status]
    rows.sort(key=sort_key)
    if args.limit:
        rows = rows[: args.limit]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("(nothing matches)")
        return 0

    print(f"{'#':>3}  {'Fit':>4}  {'Verdict':<9} {'Company':<24} {'Role':<40} Deadline")
    for i, e in enumerate(rows, 1):
        score = "-" if e.get("score") is None else str(e["score"])
        verdict = verdict_of(e) or "-"
        if not e.get("verdict") and verdict != "-":
            verdict += "*"        # inherited from the old coarse fit rating
        print(
            f"{i:>3}  {score:>4}  {verdict:<9} "
            f"{(e.get('company') or '-')[:24]:<24} {(e.get('title') or '-')[:40]:<40} "
            f"{e.get('deadline') or '-'}"
        )
    print()
    for i, e in enumerate(rows, 1):
        if e.get("gaps") or e.get("strengths"):
            print(f"{i}. {e.get('company')} - {e.get('title')}")
            for s in e.get("strengths", [])[:3]:
                print(f"     + {s}")
            for g in e.get("gaps", [])[:3]:
                print(f"     - {g}")
    return 0


def cmd_merge_scores(args) -> int:
    raw = json.loads(
        sys.stdin.read() if args.file in (None, "-")
        else Path(args.file).read_text(encoding="utf-8")
    )
    results = raw.get("results", raw) if isinstance(raw, dict) else raw
    if not isinstance(results, list):
        raise TrackerError("expected a JSON list of scored postings")

    data = load()
    merged = unknown = 0
    for r in results:
        key = r.get("id")
        if key not in data["seen"]:
            unknown += 1
            continue
        entry = data["seen"][key]
        for field in ("score", "verdict", "strengths", "gaps",
                      "location_verdict", "language_verdict", "reason"):
            if r.get(field) is not None:
                entry[field] = r[field]
        if r.get("deadline"):
            entry["deadline"] = parse_deadline(r["deadline"])
        entry["scored_at"] = today()
        merged += 1
    save(data)

    print(f"scored {merged} posting(s)")
    if unknown:
        print(f"  {unknown} result(s) referenced ids not in the shortlist, ignored")
    return 0


def cmd_sweep(args) -> int:
    data = load()
    expired = []
    for key, entry in data["seen"].items():
        if is_expired(entry) and entry.get("status") != "expired":
            entry["status"] = "expired"
            expired.append(f"{entry.get('company')} - {entry.get('title')}")
    save(data)
    if not expired:
        print("nothing has expired")
        return 0
    print(f"marked {len(expired)} posting(s) expired:")
    for line in expired:
        print(f"  {line}")
    return 0


def cmd_add(args) -> int:
    raw = json.loads(
        sys.stdin.read() if args.file in (None, "-")
        else Path(args.file).read_text(encoding="utf-8")
    )
    postings = raw.get("postings", raw) if isinstance(raw, dict) else raw
    if not isinstance(postings, list):
        raise TrackerError("expected a JSON list of postings")

    data = load()
    added = seen_before = 0
    for p in postings:
        key = (p.get("url") or "").strip() or f"{p.get('company')}::{p.get('title')}"
        if key in data["seen"]:
            seen_before += 1
            continue
        data["seen"][key] = {
            "title": p.get("title"),
            "company": p.get("company"),
            "url": p.get("url"),
            "location": p.get("location"),
            "source": p.get("source"),
            "deadline": parse_deadline(p.get("deadline")),
            "summary": p.get("summary"),
            "first_seen": today(),
            "status": "new",
        }
        added += 1
    save(data)
    print(f"added {added} new posting(s); {seen_before} already known")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="The scraper shortlist")
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("show", help="the shortlist, best fit first")
    s.add_argument("--min-score", type=int)
    s.add_argument("--unscored", action="store_true", help="only what /rank has not scored")
    s.add_argument("--status")
    s.add_argument("--limit", type=int)
    s.add_argument("--include-expired", action="store_true")
    s.add_argument("--json", action="store_true")

    m = sub.add_parser("merge-scores", help="write ranking results back into the shortlist")
    m.add_argument("--file", help="JSON from tools/gemini.py rank; stdin when omitted")

    sub.add_parser("sweep", help="mark postings whose deadline has passed")

    a = sub.add_parser("add", help="record newly scraped postings")
    a.add_argument("--file", help="JSON list of postings; stdin when omitted")

    args = ap.parse_args()
    return {
        "show": cmd_show,
        "merge-scores": cmd_merge_scores,
        "sweep": cmd_sweep,
        "add": cmd_add,
    }[args.command](args)


if __name__ == "__main__":
    from console import use_utf8
    use_utf8()
    try:
        sys.exit(main())
    except TrackerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
