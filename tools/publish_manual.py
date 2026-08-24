#!/usr/bin/env python3
"""Render docs/manual.md into the published-manual HTML page.

    python tools/publish_manual.py
    # -> tracker/manual-artifact.html

The manual is published as a Claude artifact so it is readable without cloning:

    ARTIFACT_URL below

The page is generated from docs/manual.md, so it cannot drift from the manual
in the repository. After editing the manual, regenerate and republish:

    1. python tools/publish_manual.py
    2. In a Claude Code session: publish tracker/manual-artifact.html with the
       Artifact tool, passing url=ARTIFACT_URL so the existing page updates
       instead of a second one appearing.

The visual shell lives in tools/manual_template.html, with the rendered body
substituted for the <!--BODY--> marker.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ARTIFACT_URL = "https://claude.ai/code/artifact/2b68ae96-7ba7-4e71-8942-451c949f6ea5"

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "docs" / "manual.md"
TEMPLATE = Path(__file__).resolve().parent / "manual_template.html"
OUTPUT = REPO / "tracker" / "manual-artifact.html"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", text.lower()).replace(" ", "-")


def inline(text: str) -> str:
    """Escape, then re-apply the inline markup the manual actually uses."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", out)
    out = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        out,
    )
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", out)
    return out


# Paragraphs that state a limit or a guarantee are the spine of the manual and
# get their own visual treatment.
NOTE_OPENERS = (
    "<strong>It does not", "<strong>Read-only", "<strong>Everything is a proposal",
    "<strong>It will not", "<strong>All of it is gitignored", "<strong>Change nothing",
    "<strong>A weak verdict", "<strong>Framework files",
)


def render(md: str) -> tuple[str, list[tuple[str, str, str]]]:
    lines = md.splitlines()
    out: list[str] = []
    toc: list[tuple[str, str, str]] = []  # (number, title, anchor)
    i = 0
    in_section = False

    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            lang = line[3:].strip()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            code = html.escape("\n".join(body), quote=False)
            cls = f' class="lang-{lang}"' if lang else ""
            out.append(f'<div class="code"><pre{cls}><code>{code}</code></pre></div>')
            continue

        if line.startswith("|") and i + 1 < len(lines) and re.match(
            r"^\|[\s:|-]+\|$", lines[i + 1]
        ):
            header = [c.strip() for c in line.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            head = "".join(f"<th>{inline(c)}</th>" for c in header)
            body = "".join(
                "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
                for r in rows
            )
            thead = "" if all(not c for c in header) else f"<thead><tr>{head}</tr></thead>"
            out.append(
                f'<div class="scroll"><table>{thead}<tbody>{body}</tbody></table></div>'
            )
            continue

        m = re.match(r"^(#{1,3}) (.+)$", line)
        if m:
            level, title = len(m.group(1)), m.group(2)
            if level == 1:
                i += 1
                continue  # the page title lives in the masthead
            if level == 2:
                if in_section:
                    out.append("</section>")
                num = ""
                nm = re.match(r"^(\d+)\.\s+(.*)$", title)
                if nm:
                    num, title = nm.group(1), nm.group(2)
                anchor = slug(f"{num}. {title}" if num else title)
                toc.append((num, title, anchor))
                out.append(f'<section id="{anchor}">')
                marker = f'<span class="num">{num}</span>' if num else ""
                out.append(f"<h2>{marker}{inline(title)}</h2>")
                in_section = True
            else:
                out.append(f'<h3 id="{slug(title)}">{inline(title)}</h3>')
            i += 1
            continue

        if line.strip() == "---":
            i += 1
            continue

        if re.match(r"^\s*[-*] ", line) or re.match(r"^\s*\d+\. ", line):
            ordered = bool(re.match(r"^\s*\d+\. ", line))
            items: list[str] = []
            while i < len(lines) and (
                re.match(r"^\s*[-*] ", lines[i])
                or re.match(r"^\s*\d+\. ", lines[i])
                or (items and lines[i].startswith("  ") and lines[i].strip())
            ):
                stripped = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", lines[i])
                if re.match(r"^\s*(?:[-*]|\d+\.)\s", lines[i]):
                    items.append(stripped)
                elif items:
                    items[-1] += " " + lines[i].strip()
                i += 1
            tag = "ol" if ordered else "ul"
            body = "".join(f"<li>{inline(it)}</li>" for it in items)
            out.append(f"<{tag}>{body}</{tag}>")
            continue

        if not line.strip():
            i += 1
            continue

        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^(#{1,3} |```|\||\s*[-*] |\s*\d+\. |---$)", lines[i]
        ):
            para.append(lines[i])
            i += 1
        rendered = inline(" ".join(p.strip() for p in para))
        cls = ' class="note"' if rendered.startswith(NOTE_OPENERS) else ""
        out.append(f"<p{cls}>{rendered}</p>")

    if in_section:
        out.append("</section>")
    return "\n".join(out), toc


def check_balance(text: str) -> list[str]:
    problems = []
    for tag in ("section", "table", "ul", "ol", "pre", "p", "h2", "h3", "div"):
        opened = len(re.findall(rf"<{tag}[ >]", text))
        closed = len(re.findall(rf"</{tag}>", text))
        if opened != closed:
            problems.append(f"<{tag}>: {opened} opened, {closed} closed")
    return problems


def main() -> int:
    md = SOURCE.read_text(encoding="utf-8")
    # HTML comments (the republish note at the top) are for the file's readers,
    # not the page's -- rendered naively they would appear as escaped text.
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    # The source's own contents list is replaced by the page's live rail.
    md = re.sub(r"\*\*Contents\*\*.*?(?=\n---)", "", md, flags=re.S)

    body, toc = render(md)
    nav = "\n".join(
        f'<li><a href="#{anchor}"><span class="n">{num or "·"}</span>'
        f"{html.escape(title)}</a></li>"
        for num, title, anchor in toc
    )
    assembled = (
        f'<nav id="toc"><p class="rail-label">Contents</p><ol>{nav}</ol></nav>\n'
        f'<article id="doc">{body}</article>'
    )

    template = TEMPLATE.read_text(encoding="utf-8")
    if "<!--BODY-->" not in template:
        print("error: tools/manual_template.html has no <!--BODY--> marker",
              file=sys.stderr)
        return 1
    page = template.replace("<!--BODY-->", assembled)

    problems = check_balance(page)
    if problems:
        print("error: generated markup is unbalanced:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(page, encoding="utf-8")
    print(f"sections : {len(toc)}")
    print(f"page     : {len(page)} chars -> {OUTPUT.relative_to(REPO)}")
    print(f"artifact : {ARTIFACT_URL}")
    print("republish by publishing that file with the Artifact tool, "
          "passing this url so the existing page updates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
