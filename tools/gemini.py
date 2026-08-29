#!/usr/bin/env python3
"""Delegate token-heavy, judgement-light work to the Gemini CLI.

    python tools/gemini.py check
    python tools/gemini.py research-company --name "Acme" --url https://acme.example
    python tools/gemini.py extract-posting --file data/pipeline/applications/acme/raw.html
    python tools/gemini.py rank --input to_rank.json --criteria data/profile/evaluation.md
    python tools/gemini.py summarize --file long.html --question "What is their tech stack?"

Gemini gathers and compresses. It never decides what is honest to claim about
the candidate -- that judgement stays in one place, with Claude, against
data/profile/ and the rules in CLAUDE.md.

**Failure is never fatal.** Every subcommand exits 3 when Gemini is unusable,
so the caller can do the work itself instead. A job search must not stop
because a side tool is down.

Exit codes:
    0  success
    1  usage or unexpected error
    3  Gemini unavailable (not installed, not authenticated, or disabled)
    4  timed out
    5  Gemini answered, but not with anything parseable
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402
from tracker import TrackerError, load_config, load_dotenv, rel  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_UNAVAILABLE, EXIT_TIMEOUT, EXIT_BAD_OUTPUT = 0, 1, 3, 4, 5


class GeminiUnavailable(Exception):
    """Gemini cannot be used right now. The caller should do the work itself."""

    exit_code = EXIT_UNAVAILABLE


class GeminiTimeout(GeminiUnavailable):
    exit_code = EXIT_TIMEOUT


class GeminiBadOutput(Exception):
    exit_code = EXIT_BAD_OUTPUT


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def settings() -> dict:
    try:
        cfg = load_config().data.get("gemini", {})
    except TrackerError:
        cfg = {}
    return {
        "enabled": cfg.get("enabled", False),
        "model": cfg.get("model", "gemini-2.5-flash"),
        "timeout_seconds": int(cfg.get("timeout_seconds", 120)),
        "cache_days": int(cfg.get("cache_days", 30)),
        "tasks": list(cfg.get("tasks", ["research", "extract", "rank", "summarize"])),
        "log": bool(cfg.get("log", True)),
    }


def task_enabled(task: str) -> bool:
    s = settings()
    return bool(s["enabled"]) and task in s["tasks"]


def binary() -> str:
    exe = shutil.which("gemini")
    if not exe:
        raise GeminiUnavailable(
            "the gemini CLI is not on PATH (npm install -g @google/gemini-cli)"
        )
    return exe


# ---------------------------------------------------------------------------
# Calling
# ---------------------------------------------------------------------------


def log_call(kind: str, prompt: str, result: dict, payload: str | None = None) -> None:
    """Record what was sent to Google, so that stays inspectable."""
    if not settings()["log"]:
        return
    try:
        paths.GEMINI_LOG.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc)
        entry = {
            "at": stamp.isoformat(timespec="seconds"),
            "kind": kind,
            "prompt_chars": len(prompt),
            "prompt": prompt,
            # The payload is what actually left the machine in bulk; record its
            # size and opening so the log says what was sent, not just why.
            "payload_chars": len(payload or ""),
            "payload_head": (payload or "")[:2000],
            "response_chars": len(str(result.get("response", ""))),
            "stats": result.get("stats"),
            "error": result.get("error"),
        }
        path = paths.GEMINI_LOG / f"{stamp:%Y-%m-%d}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass  # logging must never be the reason a call fails


def call(prompt: str, *, kind: str = "call", model: str | None = None,
         timeout: int | None = None, payload: str | None = None) -> str:
    """Run one headless Gemini prompt and return its text response.

    `prompt` is the instruction; `payload` is bulk input -- a posting, a
    document, a transcript. The payload goes over stdin rather than in argv,
    because a command line has a hard size limit (about 32 KB on Windows) and
    a job posting can exceed it on its own. Gemini appends the -p prompt after
    whatever arrives on stdin, so instructions must read as though they follow
    the data.
    """
    s = settings()
    if not s["enabled"]:
        raise GeminiUnavailable("gemini is disabled in data/config/config.toml ([gemini] enabled)")

    # GEMINI_API_KEY may live in .env; the subprocess inherits os.environ.
    load_dotenv()

    cmd = [
        binary(),
        "-p", prompt,
        "-o", "json",
        "-m", model or s["model"],
        # Read-only: this is a summariser, not an agent let loose in the repo.
        "--approval-mode", "plan",
        # Without this the approval mode is silently downgraded in an untrusted
        # folder, which would quietly hand it write tools.
        "--skip-trust",
    ]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    # Gemini is itself an agent: run in the repo it discovers CLAUDE.md, follows
    # ITS instructions instead of the prompt, and pokes at the workspace with
    # its own tools (which cannot even see data/profile/, since it is gitignored).
    # An empty working directory gives it nothing to be distracted by, and as a
    # side effect nothing leaves this machine except what the payload carries.
    workdir = paths.GEMINI_CWD
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            cmd, input=payload, capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=str(workdir),
            timeout=timeout or s["timeout_seconds"], env=env,
        )
    except subprocess.TimeoutExpired:
        raise GeminiTimeout(f"gemini timed out after {timeout or s['timeout_seconds']}s")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 206 or "too long" in str(exc).lower():
            raise GeminiUnavailable(
                "the prompt is too long for a command line -- pass bulk input as "
                "`payload` so it goes over stdin instead"
            )
        raise GeminiUnavailable(f"could not run gemini: {exc}")

    # The JSON envelope lands on stdout on the happy path, but an auth failure
    # writes it to stderr instead, so both have to be considered.
    envelope = extract_json(proc.stdout)
    if not isinstance(envelope, dict) or (
        "response" not in envelope and "error" not in envelope
    ):
        from_stderr = extract_json(proc.stderr)
        if isinstance(from_stderr, dict):
            envelope = from_stderr
    if not isinstance(envelope, dict):
        envelope = {}
    log_call(kind, prompt, envelope, payload)

    # Exit codes are not reliable here either: the same failure has been seen
    # exiting 0 and exiting 41, reporting itself only inside the JSON. The
    # error key is what counts.
    if envelope.get("error"):
        err = envelope["error"]
        message = err.get("message", "unknown error") if isinstance(err, dict) else str(err)
        if re.search(r"quota|rate.?limit|resource.?exhausted|429", message, re.I):
            raise GeminiUnavailable(
                "quota exhausted for this model today -- work falls back to Claude"
            )
        if re.search(r"auth|api[_ ]key|credential|sign ?in|login|GEMINI_API_KEY", message, re.I):
            raise GeminiUnavailable(f"not authenticated: {message}")
        raise GeminiUnavailable(f"gemini error: {message}")

    if "response" not in envelope:
        # Not every failure arrives in the JSON envelope -- some are emitted as
        # plain text on stderr -- so classify from the raw output too, or the
        # user gets "nothing usable" when the real answer is "log in" or
        # "come back tomorrow".
        combined = f"{proc.stderr or ''}\n{proc.stdout or ''}".strip()
        if re.search(r"quota|rate.?limit|resource.?exhausted|429", combined, re.I):
            raise GeminiUnavailable(
                "quota exhausted for this model today. Wait for the daily reset, "
                "switch [gemini] model to a lighter one, or sign in with a Google "
                "account instead of an API key (run `gemini` interactively) -- "
                "meanwhile the work falls back to Claude."
            )
        if re.search(r"auth|api[_ ]key|credential|sign ?in|GEMINI_API_KEY", combined, re.I):
            raise GeminiUnavailable(f"not authenticated: {combined[:300]}")
        raise GeminiUnavailable(f"gemini returned nothing usable: {combined[:300]}")

    return str(envelope["response"])


def extract_json(text: str) -> Any:
    """Parse JSON that may arrive bare, fenced, or wrapped in prose."""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except ValueError:
            pass
    # Fall back to the outermost brace or bracket span.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                continue
    return None


def call_json(prompt: str, *, kind: str, model: str | None = None,
              timeout: int | None = None, payload: str | None = None) -> Any:
    raw = call(prompt, kind=kind, model=model, timeout=timeout, payload=payload)
    data = extract_json(raw)
    if data is None:
        raise GeminiBadOutput(
            f"expected JSON from gemini, got: {raw.strip()[:300]}"
        )
    return data


JSON_ONLY = (
    "Return ONLY a JSON object. No prose before or after it, no markdown fence. "
    "Use null for anything you do not know -- never guess, and never invent a "
    "fact to fill a field."
)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def research_company(name: str, url: str | None = None, role: str | None = None,
                     location: str | None = None) -> dict:
    target = ""
    if role or location:
        target = (
            f"\n\nThe candidate is looking at the role \"{role or 'unknown'}\" "
            f"at this company, located: {location or 'unknown'}. Also look for what "
            "this company pays for that role: salary bands from the posting, "
            "Glassdoor, levels.fyi, Indeed, Landing.jobs and similar. Report every "
            "figure you find for THIS company with its own location, currency, "
            "period and basis exactly as the source states them -- a figure for "
            "another location is still worth reporting, labelled with that "
            "location. Never convert, never estimate: a figure that is not stated "
            "is not in the list."
        )
    prompt = f"""Research the company "{name}"{f' (website: {url})' if url else ''} \
for a candidate preparing a job application.{target}

Use web search. Every factual claim must carry the source URL you found it at, \
and a date where the source gives one. Recency matters: a "recent" launch from \
three years ago is not recent.

{JSON_ONLY}

Schema:
{{
  "name": string,
  "website": string|null,
  "what_they_do": string,          // one or two sentences: product and market
  "size": string|null,             // headcount or band, if stated anywhere
  "locations": [string],
  "recent_news": [
    {{"headline": string, "date": "YYYY-MM-DD"|null, "source_url": string}}
  ],
  "tech_stack": [string],          // only if publicly stated
  "culture_signals": [
    {{"signal": string, "source_url": string}}
  ],
  "red_flags": [
    {{"concern": string, "source_url": string}}   // layoffs, lawsuits, churn
  ],
  "unverified": [string],          // things you believe but could not source
  "salary": [                      // figures for THIS company only; [] when none found
    {{"role": string, "location": string, "currency": string,
     "amount_min": number|null, "amount_max": number|null,
     "period": "year"|"month", "basis": "gross"|"net"|null,
     "payments_per_year": number|null, "source_url": string, "date": "YYYY-MM"|null}}
  ]
}}"""
    data = call_json(prompt, kind="research-company")
    if not isinstance(data, dict):
        raise GeminiBadOutput("research did not come back as an object")
    data.setdefault("name", name)
    if not isinstance(data.get("salary"), list):
        data["salary"] = []
    data["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return data


def extract_posting(text: str) -> dict:
    prompt = f"""Extract structured data from the job posting above.

Copy what the posting says. Do not infer, normalise or improve it: if the \
salary is not stated, it is null, not an estimate.

{JSON_ONLY}

Schema:
{{
  "role": string,
  "company": string,
  "location": string|null,         // as written in the posting
  "work_mode": "remote"|"hybrid"|"onsite"|null,
  "employment_type": string|null,
  "salary": string|null,           // verbatim, including currency and period
  "deadline": "YYYY-MM-DD"|null,   // null for "ASAP", "rolling", "until filled"
  "languages_required": [string],
  "requirements": [string],
  "nice_to_have": [string],
  "tech_stack": [string],
  "contact_name": string|null,
  "contact_email": string|null,
  "apply_url": string|null
}}"""
    data = call_json(prompt, kind="extract-posting", payload=text[:200000])
    if not isinstance(data, dict):
        raise GeminiBadOutput("extraction did not come back as an object")
    return data


def rank_postings(postings: list[dict], criteria: str) -> list[dict]:
    slim = [
        {
            "id": p.get("id") or p.get("url"),
            "title": p.get("title"),
            "company": p.get("company"),
            "location": p.get("location"),
            "summary": (p.get("summary") or p.get("requirements") or "")[:1500],
        }
        for p in postings
    ]
    prompt = f"""Above are a candidate's criteria followed by a JSON list of job \
postings. Score each posting against those criteria.

Judge honestly. A posting that fails a hard constraint -- location, or a \
required language the candidate does not have -- scores as a fail regardless of \
how well the rest matches. Do not inflate scores to be encouraging.

{JSON_ONLY}

Return {{"results": [
  {{
    "id": string,
    "score": 0-100,
    "verdict": "strong"|"good"|"moderate"|"weak"|"poor",
    "strengths": [string],
    "gaps": [string],
    "location_verdict": "pass"|"fail"|"flag",
    "language_verdict": "pass"|"fail"|"flag",
    "reason": string          // one sentence
  }}
]}}"""
    payload = (
        f"CRITERIA:\n{criteria[:40000]}\n\n"
        f"POSTINGS:\n{json.dumps(slim, ensure_ascii=False)}"
    )
    data = call_json(prompt, kind="rank", payload=payload)
    if isinstance(data, dict):
        data = data.get("results", [])
    if not isinstance(data, list):
        raise GeminiBadOutput("ranking did not come back as a list")
    return data


def summarize(text: str, question: str) -> str:
    prompt = f"""{question}

Answer from the text above only. If it does not answer the question, say so \
plainly rather than filling the gap from general knowledge."""
    return call(prompt, kind="summarize", payload=text[:1000000])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def read_input(path: str | None, inline: str | None) -> str:
    if inline:
        return inline
    if path == "-":
        return sys.stdin.read()
    if path:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    raise TrackerError("provide --file or --text")


def cmd_check() -> int:
    s = settings()
    print(f"  enabled   {s['enabled']}")
    print(f"  model     {s['model']}")
    print(f"  tasks     {', '.join(s['tasks']) or '(none)'}")
    exe = shutil.which("gemini")
    print(f"  binary    {exe or 'NOT FOUND'}")
    if not exe:
        print("\n  install: npm install -g @google/gemini-cli")
        return EXIT_UNAVAILABLE
    if not s["enabled"]:
        print("\n  set [gemini] enabled = true in data/config/config.toml to use it")
        return EXIT_UNAVAILABLE
    try:
        reply = call("Reply with exactly: OK", kind="check", timeout=60)
    except (GeminiUnavailable, GeminiBadOutput) as exc:
        print(f"\n  NOT USABLE: {exc}")
        if "not authenticated" in str(exc):
            print("\n  Authenticate once, either way:")
            print("    - run `gemini` interactively and sign in with a Google account, or")
            print("    - set GEMINI_API_KEY from https://aistudio.google.com/apikey")
        return getattr(exc, "exit_code", EXIT_UNAVAILABLE)
    print(f"  live      yes ({reply.strip()[:40]})")
    print(f"  log       {rel(paths.GEMINI_LOG)}")
    return EXIT_OK


def main() -> int:
    ap = argparse.ArgumentParser(description="Delegate bulk work to the Gemini CLI")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="is Gemini installed, configured and authenticated?")

    rc = sub.add_parser("research-company", help="research a company, with sources")
    rc.add_argument("--name", required=True)
    rc.add_argument("--url")
    rc.add_argument("--role", help="the role in question; asks for salary figures too")
    rc.add_argument("--location", help="where the posting is; figures are reported per location")

    ep = sub.add_parser("extract-posting", help="job posting text or HTML -> JSON")
    ep.add_argument("--file")
    ep.add_argument("--text")

    rk = sub.add_parser("rank", help="score a batch of postings against criteria")
    rk.add_argument("--input", required=True, help="JSON list of postings, e.g. from shortlist.py show --json")
    rk.add_argument("--criteria", required=True, help="file describing what the candidate wants")

    sm = sub.add_parser("summarize", help="answer a question from a long document")
    sm.add_argument("--file")
    sm.add_argument("--text")
    sm.add_argument("--question", required=True)

    args = ap.parse_args()

    if args.command == "check":
        return cmd_check()

    try:
        if args.command == "research-company":
            print(json.dumps(research_company(args.name, args.url, args.role, args.location),
                             ensure_ascii=False, indent=2))

        elif args.command == "extract-posting":
            print(json.dumps(extract_posting(read_input(args.file, args.text)),
                             ensure_ascii=False, indent=2))

        elif args.command == "rank":
            raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "seen" in raw:
                postings = [{**v, "id": k} for k, v in raw["seen"].items()]
            elif isinstance(raw, dict):
                postings = list(raw.values())
            else:
                postings = raw
            criteria = Path(args.criteria).read_text(encoding="utf-8", errors="replace")
            print(json.dumps(rank_postings(postings, criteria),
                             ensure_ascii=False, indent=2))

        elif args.command == "summarize":
            print(summarize(read_input(args.file, args.text), args.question))

    except (GeminiUnavailable, GeminiBadOutput) as exc:
        print(f"gemini unavailable: {exc}", file=sys.stderr)
        print("fall back to doing this in Claude.", file=sys.stderr)
        return getattr(exc, "exit_code", EXIT_UNAVAILABLE)
    except TrackerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    from console import use_utf8
    use_utf8()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
