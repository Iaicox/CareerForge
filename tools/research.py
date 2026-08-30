#!/usr/bin/env python3
"""Company research, cached so the same company is not researched twice.

    python tools/research.py get "Acme" --url https://acme.example
    python tools/research.py get "Acme" --role "Senior Frontend Developer" --location "Lisbon"
    python tools/research.py get "Acme" --force
    python tools/research.py put "Acme" --file research.json
    python tools/research.py list

`/apply`'s reviewer and `/interview` both need to know what a company does and
what has happened there lately. Without a cache each of them pays for that
separately, every time.

The cache lives in the `companies` table rather than a parallel directory of
JSON files, so there is one source of truth and `tracker.py export` carries it.

**What is stored is leads, not evidence.** The verification checklist in
CLAUDE.md still applies before any of this reaches a document. That rule does
not relax because the research is cached -- a stale cache is exactly how an
unverified claim ages quietly into a confident one, which is why every entry
carries the date it was fetched and every claim carries its source.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gemini  # noqa: E402
import tracker  # noqa: E402
from tracker import TrackerError  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_NEEDS_CLAUDE = 0, 1, 3


def cache_days() -> int:
    return gemini.settings()["cache_days"]


def find_company(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM companies WHERE slug = ?", (tracker.slugify(name),)
    ).fetchone()


def age_days(researched_at: str | None) -> float | None:
    stamp = tracker.parse_iso_utc(researched_at)
    if stamp is None:
        return None
    return (datetime.now(timezone.utc) - stamp).total_seconds() / 86400


def read_cache(conn: sqlite3.Connection, name: str) -> tuple[dict | None, float | None]:
    row = find_company(conn, name)
    if not row or not row["research_json"]:
        return None, None
    try:
        return json.loads(row["research_json"]), age_days(row["researched_at"])
    except (TypeError, ValueError):
        return None, None


def write_cache(conn: sqlite3.Connection, name: str, data: dict,
                website: str | None = None) -> None:
    company_id = tracker.company_upsert(conn, name, website)
    conn.execute(
        "UPDATE companies SET research_json = ?, researched_at = ? WHERE id = ?",
        (json.dumps(data, ensure_ascii=False), tracker.now(), company_id),
    )


def salary_due(cached: dict) -> bool:
    """Whether asking what this company pays is worth another grounded call.

    The `salary` key is written only when the question was answered, so an
    entry from before it existed has none and is refreshed. An entry whose
    salary call was refused has none either -- but refreshing that one on
    every lookup re-runs the whole grounded research and spends more of the
    quota that refused in the first place, so the refusal carries the time it
    lifts and the entry stands until then.
    """
    if "salary" in cached:
        return False
    after = tracker.parse_iso_utc(cached.get("salary_retry_after"))
    return after is None or datetime.now(timezone.utc) >= after


def get(conn: sqlite3.Connection, name: str, url: str | None,
        force: bool, role: str | None = None, location: str | None = None) -> tuple[dict, str]:
    """Returns (research, where it came from).

    With a role given, research also asks what the company pays for it, and an
    entry that has not answered that question yet is refreshed -- see
    salary_due() for when a refused one is asked again.
    """
    if not force:
        cached, age = read_cache(conn, name)
        if cached is not None and age is not None and age <= cache_days():
            if not (role and salary_due(cached)):
                cached["cache_age_days"] = round(age, 1)
                return cached, "cache"

    if not gemini.task_enabled("research"):
        raise GeminiPass("research is not delegated to Gemini")
    data = gemini.research_company(name, url, role=role, location=location)
    with conn:
        write_cache(conn, name, data, url)
    return data, "gemini"


class GeminiPass(Exception):
    """Not an error: the caller should do the research itself, then `put` it."""


def main() -> int:
    ap = argparse.ArgumentParser(description="Cached company research")
    sub = ap.add_subparsers(dest="command", required=True)

    g = sub.add_parser("get", help="cached research, fetching it if stale")
    g.add_argument("company")
    g.add_argument("--url", help="company website, to disambiguate the name")
    g.add_argument("--role", help="the role in question; research then asks what the company pays for it")
    g.add_argument("--location", help="where the posting is; salary figures come back per location")
    g.add_argument("--force", action="store_true", help="ignore the cache")

    p = sub.add_parser("put", help="store research gathered elsewhere")
    p.add_argument("company")
    p.add_argument("--file", help="JSON file; reads stdin when omitted")
    p.add_argument("--url")

    ls = sub.add_parser("list", help="what is cached, and how old")
    ls.add_argument("--json", action="store_true")

    args = ap.parse_args()
    conn = tracker.connect()
    try:
        if args.command == "get":
            try:
                data, origin = get(conn, args.company, args.url, args.force,
                                   args.role, args.location)
            except GeminiPass as exc:
                print(f"no usable cache and {exc}.", file=sys.stderr)
                print("Research it yourself, then store it with:", file=sys.stderr)
                print(f'  python tools/research.py put "{args.company}" --file <json>',
                      file=sys.stderr)
                return EXIT_NEEDS_CLAUDE
            except (gemini.GeminiUnavailable, gemini.GeminiBadOutput) as exc:
                stale, age = read_cache(conn, args.company)
                if stale is not None:
                    # Better an old answer, clearly labelled, than none at all.
                    stale["cache_age_days"] = round(age, 1) if age else None
                    stale["stale"] = True
                    print(f"gemini unavailable ({exc}); returning cache "
                          f"{stale['cache_age_days']} days old", file=sys.stderr)
                    print(json.dumps(stale, ensure_ascii=False, indent=2))
                    return EXIT_OK
                print(f"gemini unavailable: {exc}", file=sys.stderr)
                print("Research it yourself, then store it with:", file=sys.stderr)
                print(f'  python tools/research.py put "{args.company}" --file <json>',
                      file=sys.stderr)
                return EXIT_NEEDS_CLAUDE
            print(f"source: {origin}", file=sys.stderr)
            print(json.dumps(data, ensure_ascii=False, indent=2))

        elif args.command == "put":
            raw = (sys.stdin.read() if args.file in (None, "-")
                   else Path(args.file).read_text(encoding="utf-8"))
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise TrackerError("expected a JSON object")
            data.setdefault("name", args.company)
            data.setdefault(
                "fetched_at",
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            with conn:
                write_cache(conn, args.company, data, args.url)
            print(f"cached research for {args.company}")

        elif args.command == "list":
            rows = []
            for r in conn.execute(
                "SELECT name, slug, researched_at FROM companies "
                "WHERE research_json IS NOT NULL ORDER BY researched_at DESC"
            ):
                age = age_days(r["researched_at"])
                rows.append({
                    "company": r["name"],
                    "researched_at": r["researched_at"],
                    "age_days": round(age, 1) if age is not None else None,
                    "fresh": age is not None and age <= cache_days(),
                })
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            elif not rows:
                print("(nothing cached yet)")
            else:
                print(f"{'Company':<34} {'Researched':<20} {'Age':>6}  State")
                for r in rows:
                    state = "fresh" if r["fresh"] else "stale"
                    age = f"{r['age_days']:.1f}d" if r["age_days"] is not None else "-"
                    print(f"{r['company'][:34]:<34} {str(r['researched_at'])[:19]:<20} "
                          f"{age:>6}  {state}")
    finally:
        conn.close()
    return EXIT_OK


if __name__ == "__main__":
    from console import use_utf8
    use_utf8()
    try:
        sys.exit(main())
    except TrackerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    except KeyboardInterrupt:
        sys.exit(130)
