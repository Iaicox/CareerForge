#!/usr/bin/env python3
"""The shortlist: every posting seen, what /rank scored, what was decided.

    python tools/shortlist.py show --min-score 60
    python tools/shortlist.py show --unscored --json
    python tools/shortlist.py check <url> [<url> ...]
    python tools/shortlist.py add --file postings.json
    python tools/shortlist.py merge-scores --file ranking.json
    python tools/shortlist.py mark --url <url> --status skipped --note "React only"
    python tools/shortlist.py sweep
    python tools/shortlist.py import-json data/job_scraper/seen_jobs.json

The rows live in the tracker database (`postings`), keyed on a normalised URL,
so the scraper, /rank and /apply all read and write one record: a posting
declined in /apply never resurfaces in /scrape, and a scrape never re-fetches
what the tracker already holds.

Scores and verdicts persist. A finding that only ever reached the console is a
finding you pay for again next week.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tracker  # noqa: E402
from tracker import TrackerError  # noqa: E402

# Entries scraped before /rank existed carry a coarse "fit" instead of a score.
# import-json turns it into a verdict once so that history stays visible; it
# is never written as a score, because only /rank produces one.
LEGACY_FIT = {"high": "good", "medium": "moderate", "low": "weak"}


def verdict_of(entry: dict) -> str | None:
    if entry.get("verdict"):
        return entry["verdict"]
    return LEGACY_FIT.get(str(entry.get("fit") or "").lower())


def read_json(file: str | None):
    text = sys.stdin.read() if file in (None, "-") else Path(file).read_text(encoding="utf-8")
    return json.loads(text)


def describe(row) -> str:
    note = f"  -- {row['note']}" if row["note"] else ""
    return f"#{row['id']} {row['company'] or '-'} - {row['title'] or '-'}{note}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_show(args, conn, cfg) -> int:
    rows = tracker.list_postings(
        conn, cfg,
        status=args.status,
        all=bool(args.all or args.include_expired),
        unscored=args.unscored,
        min_score=args.min_score,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return 0
    if not rows:
        print("(nothing matches)")
        return 0

    print(f"{'#':>3}  {'Fit':>4}  {'Verdict':<9} {'Status':<8} {'Company':<24} {'Role':<40} Deadline")
    for i, e in enumerate(rows, 1):
        score = "-" if e.get("score") is None else str(e["score"])
        print(
            f"{i:>3}  {score:>4}  {(e.get('verdict') or '-'):<9} {e['status']:<8} "
            f"{(e.get('company') or '-')[:24]:<24} {(e.get('title') or '-')[:40]:<40} "
            f"{e.get('deadline') or '-'}"
        )
    print()
    for i, e in enumerate(rows, 1):
        if e.get("gaps") or e.get("strengths") or e.get("note"):
            print(f"{i}. {e.get('company')} - {e.get('title')}  (#{e['id']})")
            for s in (e.get("strengths") or [])[:3]:
                print(f"     + {s}")
            for g in (e.get("gaps") or [])[:3]:
                print(f"     - {g}")
            if e.get("note"):
                print(f"     note: {e['note']}")
    return 0


def cmd_check(args, conn, cfg) -> int:
    """Is this URL already known? One line per URL, the status when it is."""
    out = []
    for url in args.urls:
        key = tracker.normalize_url(url)
        row = tracker.posting_by_key(conn, key) if key else None
        if row is None:
            out.append({"url": url, "url_key": key, "known": False})
            continue
        app = None
        if row["application_id"]:
            a = conn.execute(
                "SELECT slug FROM applications WHERE id = ?", (row["application_id"],)
            ).fetchone()
            app = a["slug"] if a else None
        out.append({
            "url": url, "url_key": key, "known": True, "id": row["id"],
            "status": row["status"], "company": row["company"], "title": row["title"],
            "note": row["note"], "score": row["score"], "verdict": row["verdict"],
            "application_slug": app,
        })
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    for o in out:
        if not o["known"]:
            print(f"{'new':<10} {o['url']}")
        else:
            app = f"  (application {o['application_slug']})" if o["application_slug"] else ""
            note = f"  -- {o['note']}" if o["note"] else ""
            print(f"{o['status']:<10} #{o['id']} {o['company'] or '-'} - {o['title'] or '-'}{app}{note}")
    return 0


def cmd_add(args, conn, cfg) -> int:
    raw = read_json(args.file)
    postings = raw.get("postings", raw) if isinstance(raw, dict) else raw
    if not isinstance(postings, list):
        raise TrackerError("expected a JSON list of postings")
    with conn:
        added, known = tracker.upsert_postings(conn, cfg, postings)
    print(f"added {added} new posting(s); {known} already known")
    return 0


def cmd_merge_scores(args, conn, cfg) -> int:
    raw = read_json(args.file)
    results = raw.get("results", raw) if isinstance(raw, dict) else raw
    if not isinstance(results, list):
        raise TrackerError("expected a JSON list of scored postings")
    with conn:
        merged, unknown = tracker.merge_scores(conn, cfg, results)
    print(f"scored {merged} posting(s)")
    if unknown:
        print(f"  {unknown} result(s) referenced ids not in the shortlist, ignored")
    return 0


def cmd_sweep(args, conn, cfg) -> int:
    with conn:
        expired = tracker.sweep_postings(conn, cfg)
    if not expired:
        print("nothing has expired")
        return 0
    print(f"marked {len(expired)} posting(s) expired:")
    for line in expired:
        print(f"  {line}")
    return 0


def cmd_mark(args, conn, cfg) -> int:
    ident = args.id if args.id is not None else args.url
    stub = None
    if args.company or args.title:
        stub = {
            "title": args.title, "company": args.company,
            "location": args.location, "source": args.source or "apply",
            "salary_text": args.salary,
        }
    with conn:
        row = tracker.mark_posting(conn, cfg, ident, args.status, note=args.note,
                                   stub=stub, salary_text=args.salary)
    print(f"{row['status']:<10} {describe(row)}")
    return 0


def cmd_import_json(args, conn, cfg) -> int:
    """One-time: bring the old seen_jobs.json into the table.

    Legacy keys were hand-made slugs; rows are re-keyed on the URL. A row that
    already exists keeps a terminal status it has (an `applied` row stays
    applied) and adopts the file's verdict, status and note where it had none.
    """
    data = json.loads(Path(args.path).read_text(encoding="utf-8"))
    seen = data.get("seen", data) if isinstance(data, dict) else {}
    if not isinstance(seen, dict):
        raise TrackerError("expected {\"seen\": {...}}")
    added = merged = unusable = 0
    unmapped: set[str] = set()
    known_ids = {s["id"] for s in cfg.posting_statuses}
    with conn:
        for key, entry in seen.items():
            url = (entry.get("url") or "").strip()
            if not url and ("://" in key or key.startswith("www.")):
                url = key
            company, title = entry.get("company"), entry.get("title")
            try:
                new_key = tracker.posting_key(url, company, title)
            except TrackerError:
                unusable += 1
                continue
            status = entry.get("status") or "new"
            note = entry.get("note")
            if status not in known_ids:
                unmapped.add(status)
                note = f"[{status}] {note}" if note else f"[{status}]"
                status = "new"
            verdict = verdict_of(entry)
            row = tracker.posting_by_key(conn, new_key)
            if row is None:
                tracker.insert_posting(conn, cfg, {
                    "url": url or None, "company": company, "title": title,
                    "location": entry.get("location"), "source": entry.get("source"),
                    "summary": entry.get("summary"), "deadline": entry.get("deadline"),
                    "first_seen": entry.get("first_seen"), "status": status, "note": note,
                    "score": entry.get("score"), "verdict": verdict,
                    "strengths": entry.get("strengths"), "gaps": entry.get("gaps"),
                    "location_verdict": entry.get("location_verdict"),
                    "language_verdict": entry.get("language_verdict"),
                    "reason": entry.get("reason"), "scored_at": entry.get("scored_at"),
                })
                added += 1
                continue
            # Already there: fill in what the row lacks, never downgrade.
            sets, params = [], []
            if not cfg.posting_is_terminal(row["status"]) and status != "new":
                sets.append("status = ?"); params.append(status)
            if not row["note"] and note:
                sets.append("note = ?"); params.append(note)
            if row["verdict"] is None and verdict:
                sets.append("verdict = ?"); params.append(verdict)
            if row["score"] is None and entry.get("score") is not None:
                sets.append("score = ?"); params.append(entry["score"])
            if sets:
                sets.append("updated_at = ?"); params.append(tracker.now())
                params.append(row["id"])
                conn.execute(f"UPDATE postings SET {', '.join(sets)} WHERE id = ?", params)
            merged += 1
    print(f"imported {added} posting(s); {merged} already present (filled in where empty)")
    if unusable:
        print(f"  {unusable} entr(ies) had neither a URL nor a company and title, skipped")
    if unmapped:
        print("  statuses with no match in [[posting_statuses]] (stored as new, kept in the note): "
              + ", ".join(sorted(unmapped)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="The shortlist: every posting seen, in the tracker database")
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("show", help="the shortlist, best fit first")
    s.add_argument("--min-score", type=int)
    s.add_argument("--unscored", action="store_true", help="only what /rank has not scored")
    s.add_argument("--status", help="one status only")
    s.add_argument("--all", action="store_true", help="include applied, skipped and expired")
    s.add_argument("--include-expired", action="store_true", help=argparse.SUPPRESS)  # alias of --all
    s.add_argument("--limit", type=int)
    s.add_argument("--json", action="store_true")

    c = sub.add_parser("check", help="is this posting already known? one line per URL")
    c.add_argument("urls", nargs="+")
    c.add_argument("--json", action="store_true")

    a = sub.add_parser("add", help="record newly scraped postings")
    a.add_argument("--file", help="JSON list of postings; stdin when omitted")

    m = sub.add_parser("merge-scores", help="write ranking results back into the shortlist")
    m.add_argument("--file", help="JSON from tools/gemini.py rank; stdin when omitted")

    sub.add_parser("sweep", help="expire open postings whose deadline has passed")

    k = sub.add_parser("mark", help="record a verdict: skipped with a reason, maybe, applied")
    who = k.add_mutually_exclusive_group(required=True)
    who.add_argument("--url")
    who.add_argument("--id", type=int)
    k.add_argument("--status", required=True)
    k.add_argument("--note", help="why -- the one line you will want next time it comes up")
    k.add_argument("--company", help="with --title: record a URL nobody has seen yet")
    k.add_argument("--title")
    k.add_argument("--location")
    k.add_argument("--source")
    k.add_argument("--salary", help="the pay as the posting states it, verbatim")

    i = sub.add_parser("import-json", help="one-time import of the old seen_jobs.json")
    i.add_argument("path")

    args = ap.parse_args()
    cfg = tracker.load_config()
    conn = tracker.connect()
    try:
        return {
            "show": cmd_show,
            "check": cmd_check,
            "add": cmd_add,
            "merge-scores": cmd_merge_scores,
            "sweep": cmd_sweep,
            "mark": cmd_mark,
            "import-json": cmd_import_json,
        }[args.command](args, conn, cfg)
    finally:
        conn.close()


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
