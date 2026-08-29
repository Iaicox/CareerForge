#!/usr/bin/env python3
"""Delegate token-heavy, judgement-light work to Gemini, over its REST API.

    python tools/gemini.py check
    python tools/gemini.py research-company --name "Acme" --url https://acme.example
    python tools/gemini.py research-company --name "Acme" --role "Senior Frontend Engineer" --location "Lisbon"
    python tools/gemini.py extract-posting --file data/pipeline/applications/acme/raw.html
    python tools/gemini.py rank --input to_rank.json --criteria data/profile/evaluation.md
    python tools/gemini.py summarize --file long.html --question "What is their tech stack?"

Gemini gathers and compresses. It never decides what is honest to claim about
the candidate -- that judgement stays in one place, with Claude, against
data/profile/ and the rules in CLAUDE.md.

This talks to generativelanguage.googleapis.com directly, with GEMINI_API_KEY
from .env. It used to shell out to the Gemini CLI; that stopped being
workable when the CLI began rewriting every model whose name ends in "flash"
to gemini-3.5-flash under a remote flag, ran its web-search tool on that
model whatever was asked for, and retried a quota error without end. Over
REST the model is exactly the one configured, web search is a tool on the
request, and a refusal is a refusal.

**Failure is never fatal.** Every subcommand exits 3 when Gemini is unusable,
so the caller can do the work itself instead. A job search must not stop
because a side tool is down.

Exit codes:
    0  success
    1  usage or unexpected error
    3  Gemini unavailable (no key, disabled, quota, unknown model, overloaded)
    4  timed out
    5  Gemini answered, but not with anything parseable
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402
from tracker import TrackerError, load_config, load_dotenv, rel  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_UNAVAILABLE, EXIT_TIMEOUT, EXIT_BAD_OUTPUT = 0, 1, 3, 4, 5

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

# One retry on a rate limit the API says will clear within this many seconds;
# a daily quota says "try again in hours" and is not worth waiting for.
RETRY_DELAY_CEILING = 30
# "High demand" (503) clears in seconds or not at all; two more tries, then stop.
OVERLOAD_BACKOFF = (5, 15)


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


def api_key() -> str:
    load_dotenv()
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise GeminiUnavailable(
            "GEMINI_API_KEY is not set -- put it in .env "
            "(https://aistudio.google.com/apikey)"
        )
    return key


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


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def http_post(url: str, body: dict, timeout: int) -> tuple[int, dict]:
    """One POST to the Gemini API. Returns (HTTP status, decoded JSON body).

    The timeout is the socket's: no byte for that long ends the call. That is
    the guarantee the CLI route could not give -- a request either answers or
    stops.
    """
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8", "replace")
            return res.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"error": {"code": exc.code, "message": raw[:500]}}
    except TimeoutError:
        raise
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError) or "timed out" in str(exc.reason).lower():
            raise TimeoutError(str(exc.reason))
        raise GeminiUnavailable(f"could not reach the Gemini API: {exc.reason}")


def retry_delay(error: dict) -> float | None:
    """How long the API asked us to wait, when it said."""
    for detail in error.get("details") or []:
        delay = detail.get("retryDelay")
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                continue
    m = re.search(r"retry in ([\d.]+)s", error.get("message", ""), re.I)
    return float(m.group(1)) if m else None


def call(prompt: str, *, kind: str = "call", model: str | None = None,
         timeout: int | None = None, payload: str | None = None,
         search: bool = False, json_mode: bool = False) -> str:
    """Run one prompt and return the text of the answer.

    `prompt` is the instruction; `payload` is bulk input -- a posting, a
    document, a transcript -- and goes first, so instructions read as though
    they follow the data. `search` attaches Google Search as a tool.
    `json_mode` asks the API for JSON; it cannot be combined with a tool, so
    a search call relies on the prompt and extract_json() instead.
    """
    s = settings()
    if not s["enabled"]:
        raise GeminiUnavailable("gemini is disabled in data/config/config.toml ([gemini] enabled)")
    model = (model or s["model"]).strip()
    limit = timeout or s["timeout_seconds"]

    text = f"{payload}\n\n{prompt}" if payload else prompt
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {},
    }
    if search:
        body["tools"] = [{"google_search": {}}]
    elif json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    url = f"{API_ROOT}/models/{model}:generateContent"

    started = time.monotonic()
    overload_tries = 0
    limit_retried = False
    while True:
        try:
            status, data = http_post(url, body, limit)
        except TimeoutError:
            log_call(kind, prompt, {"error": f"timed out after {limit}s"}, payload)
            raise GeminiTimeout(f"gemini timed out after {limit}s")
        error = data.get("error") if isinstance(data, dict) else None
        if status < 400 and not error:
            break
        error = error or {"code": status, "message": f"HTTP {status}"}
        message = str(error.get("message", "")).strip()
        if status == 429:
            delay = retry_delay(error)
            if delay is not None and delay <= RETRY_DELAY_CEILING and not limit_retried:
                limit_retried = True
                sleep(delay)
                continue
            log_call(kind, prompt, {"error": error}, payload)
            raise GeminiUnavailable(
                f"quota exhausted for {model}: {message[:200]} -- work falls back to Claude"
            )
        if status in (500, 502, 503, 504) and overload_tries < len(OVERLOAD_BACKOFF):
            sleep(OVERLOAD_BACKOFF[overload_tries])
            overload_tries += 1
            continue
        log_call(kind, prompt, {"error": error}, payload)
        if status in (401, 403):
            raise GeminiUnavailable(f"not authenticated: {message[:200]}")
        if status == 404:
            raise GeminiUnavailable(f"model {model!r} is not available to this key: {message[:200]}")
        if status in (500, 502, 503, 504):
            raise GeminiUnavailable(f"{model} is overloaded (HTTP {status}): {message[:200]}")
        raise GeminiUnavailable(f"gemini error (HTTP {status}): {message[:300]}")

    candidates = data.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") or [] if candidates else []
    answer = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    stats = {
        "model": data.get("modelVersion") or model,
        "usage": data.get("usageMetadata"),
        "finish": candidates[0].get("finishReason") if candidates else None,
        "latency_s": round(time.monotonic() - started, 1),
        "grounded": bool(candidates and candidates[0].get("groundingMetadata")),
    }
    log_call(kind, prompt, {"response": answer, "stats": stats}, payload)

    if not answer.strip():
        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        why = blocked or stats["finish"] or "no candidates"
        raise GeminiBadOutput(f"gemini returned nothing usable ({why})")
    return answer


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
              timeout: int | None = None, payload: str | None = None,
              search: bool = False) -> Any:
    raw = call(prompt, kind=kind, model=model, timeout=timeout, payload=payload,
               search=search, json_mode=not search)
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


SALARY_SCHEMA = """{
  "salary": [                      // figures for THIS company only; [] when none found
    {"role": string, "location": string, "currency": string,
     "amount_min": number|null, "amount_max": number|null,
     "period": "year"|"month", "basis": "gross"|"net"|null,
     "payments_per_year": number|null, "source_url": string, "date": "YYYY-MM"|null}
  ]
}"""


def salary_figures(name: str, role: str, location: str | None = None) -> list[dict]:
    """What the company pays for the role, from the web, each figure as stated.

    A question of its own rather than a clause in the research prompt: asked
    alongside news, culture and tech stack, the model came back with an empty
    list for companies whose Glassdoor pages it finds in seconds when asked
    only this. Judgement about which figure counts for which location stays
    with the caller.
    """
    where = f" in {location}" if location else ""
    prompt = f"""Find what the company "{name}" pays a {role} (or the closest title){where}. \
Use web search: Glassdoor, levels.fyi, Indeed, Landing.jobs, the company's own \
postings. Report every figure you find for THIS company, each with its own \
location, currency, period and basis exactly as the source states them; a \
figure for another location is still worth reporting, labelled with that \
location. Never convert, never estimate: a figure that is not stated is not \
in the list.

{JSON_ONLY}

Schema:
{SALARY_SCHEMA}"""
    data = call_json(prompt, kind="salary-figures", search=True)
    figures = data.get("salary") if isinstance(data, dict) else None
    return [f for f in figures if isinstance(f, dict)] if isinstance(figures, list) else []


def research_company(name: str, url: str | None = None, role: str | None = None,
                     location: str | None = None) -> dict:
    prompt = f"""Research the company "{name}"{f' (website: {url})' if url else ''} \
for a candidate preparing a job application.

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
  "unverified": [string]           // things you believe but could not source
}}"""
    data = call_json(prompt, kind="research-company", search=True)
    if not isinstance(data, dict):
        raise GeminiBadOutput("research did not come back as an object")
    data.setdefault("name", name)
    data["salary"] = []
    if role:
        # Its own call, so a refusal here costs the salary block, not the
        # research; the reason is kept where the reader of the cache sees it.
        try:
            data["salary"] = salary_figures(name, role, location)
        except (GeminiUnavailable, GeminiBadOutput) as exc:
            data["salary_error"] = str(exc)
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
    try:
        api_key()
        print("  key       GEMINI_API_KEY present")
    except GeminiUnavailable as exc:
        print(f"  key       {exc}")
        return EXIT_UNAVAILABLE
    if not s["enabled"]:
        print("\n  set [gemini] enabled = true in data/config/config.toml to use it")
        return EXIT_UNAVAILABLE
    started = time.monotonic()
    try:
        reply = call("Reply with exactly: OK", kind="check", timeout=60)
    except (GeminiUnavailable, GeminiBadOutput) as exc:
        print(f"\n  NOT USABLE: {exc}")
        return getattr(exc, "exit_code", EXIT_UNAVAILABLE)
    print(f"  live      yes ({reply.strip()[:40]}, {time.monotonic() - started:.1f}s)")
    print(f"  log       {rel(paths.GEMINI_LOG)}")
    return EXIT_OK


def main() -> int:
    ap = argparse.ArgumentParser(description="Delegate bulk work to Gemini over its API")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="is Gemini configured, and does the key answer?")

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
