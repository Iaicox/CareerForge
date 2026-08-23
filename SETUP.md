# Setup

Getting CareerForge running, in detail. If you just want to start, `/setup`
inside Claude Code walks you through all of it — this page is the reference for
when something does not work.

## 1. Prerequisites

Run `/doctor` (or `python tools/doctor.py`) at any point to see which of these
are missing and what to do about it.

### Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

You need an Anthropic API key or a Claude subscription. See the
[Claude Code docs](https://docs.claude.com/en/docs/claude-code).

### Python 3.11 or newer

```bash
python --version
```

3.11 is the floor because the tooling reads its configuration with `tomllib`,
which entered the standard library in that release. Nothing here needs `pip
install` — the tracker, the board and the Notion adapter are standard library
only.

### pandoc

| Platform | Command |
|---|---|
| Windows | `winget install JohnMacFarlane.Pandoc` |
| macOS | `brew install pandoc` |
| Debian/Ubuntu | `sudo apt install pandoc` |

Restart your terminal afterwards so `PATH` refreshes.

### A PDF engine: MS Word or LibreOffice

You need one of them. The build script prefers Word when it is available and
falls back to LibreOffice otherwise; force either with `-Engine word` /
`-Engine libreoffice`.

**MS Word** (Windows only) renders with perfect fidelity and reports exact page
counts through COM automation. Any Office 2016+ desktop install works.

**LibreOffice** works on every platform:

| Platform | Command |
|---|---|
| Windows | `winget install TheDocumentFoundation.LibreOffice` |
| macOS | `brew install --cask libreoffice` |
| Debian/Ubuntu | `sudo apt install libreoffice` |

On the LibreOffice path, page counts come from `tools/pagecount.py`. It uses
`pypdf` if importable, then poppler's `pdfinfo`, then a regex fallback that
warns you it is approximate. If you are enforcing a two-page CV, install one of
the first two:

```bash
pip install pypdf            # or: apt install poppler-utils / brew install poppler
```

### Fonts

The document design uses Roboto and Roboto Light, shipped in `templates/fonts/`.

- **Windows:** `tools\build.ps1` installs them per-user on its first run.
- **macOS:** open the files in Font Book.
- **Linux:** `cp templates/fonts/*.ttf ~/.local/share/fonts/ && fc-cache -f`

Without them, documents render with substitute fonts and the layout shifts.

## 2. Clone and set up

```bash
git clone <your-fork-url> careerforge
cd careerforge
claude
```

Then run `/setup`. It offers two paths:

- **Import from a CV** — share your CV with `@` or paste the text. It extracts
  what it can and asks about the rest.
- **Interview** — nine sections, conversational.

Both end in the same place: `profile/` populated, `config/config.toml` written,
`tracker/careerforge.db` created.

**Detail matters more than anything else here.** A profile that lists job titles
produces generic applications. A profile that describes what you actually built,
with numbers and with honest boundaries on what you did not do, produces
applications worth sending. Spend the time.

Re-run a single section later:

```
/setup --section search        # re-tune the job search as priorities change
/setup --section experience
/setup --section skills
```

### What gets written

| Path | Contents |
|---|---|
| `profile/candidate.md` | Identity, education, experience, projects, skills |
| `profile/behavioral.md` | How you work, strengths, ideal environment |
| `profile/evaluation.md` | Match areas, goals, location rules, salary floor, sector filter |
| `profile/interview-prep.md` | STAR examples from real experience |
| `profile/cv_master.md` | Your master CV |
| `profile/cover_letter_master.md` | The letter skeleton |
| `profile/search-queries.md` | What `/scrape` searches for |
| `config/config.toml` | Locale, statuses, page limits, engine |
| `tracker/careerforge.db` | The tracker |

All of it is gitignored. `git status` should be empty when `/setup` finishes —
if it is not, something wrote to a framework file, which is a bug.

## 3. Optional: salary benchmarking

If you have salary data — a union dataset, a survey, a Glassdoor export, your
own research:

- **By hand:** create `salary_data.json` in the repo root. Format:
  `tools/README_SALARY_TOOL.md`.
- **From Excel:**
  ```bash
  pip install openpyxl
  python tools/convert_salary_excel.py path/to/data.xlsx --source "My Survey 2026"
  ```

Company-name matching normalises legal forms and diacritics. The lists it uses
are in `config/config.toml` under `[salary]`; extend them for your market.

Without `salary_data.json`, `/apply` simply omits the benchmark.

## 4. Optional: mirror to Notion

Only if you want your pipeline readable on your phone.

1. Create an internal integration at
   [notion.so/my-integrations](https://www.notion.so/my-integrations) with
   **Read**, **Update** and **Insert content**.
2. Put the token in `NOTION_TOKEN`, or in `.notion_token` in the repo root
   (gitignored).
3. Pick or create a Notion page to hold the tracker, and connect the integration
   to it: `...` → Connections → your integration.
4. Create the databases from your own configured statuses:
   ```bash
   python tools/notion_sync.py provision --parent-page <page URL> --dry-run
   python tools/notion_sync.py provision --parent-page <page URL>
   ```
5. Set `notion.enabled = true` in `config/config.toml`.

Then `python tools/notion_sync.py push --files` mirrors your pipeline, PDFs
included. SQLite stays authoritative; if the two disagree, push again.

Moving an existing Notion tracker in? Fill in `config/notion.json` with your
database ids and run `python tools/notion_sync.py import --dry-run` first.

## 5. Try it

```
/apply https://example.com/careers/senior-frontend
```

Or paste the posting text directly — some portals block automated fetching.
Flags: `--no-cover` to skip the letter, `--cover` to draft it without asking.

The pipeline: evaluate fit → ask whether to proceed → draft in Markdown →
`application-reviewer` researches the company and critiques → revise → build
DOCX and PDF within your page limits → record in the tracker → present a
pass/fail verification checklist.

## 6. Editing and rebuilding

Every document exists three times: `.md` (source), `.docx` (editable), `.pdf`
(what you send).

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build.ps1 -Path applications\<slug>
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build.ps1 -Path applications\<slug> -Force
```

```bash
# macOS / Linux
tools/build.sh applications/<slug>
tools/build.sh applications/<slug> --force
```

Edited the `.docx` by hand in Word? Re-running detects that the DOCX is newer
than the Markdown and refreshes only the PDF, so your edits survive. `-Force` /
`--force` rebuilds from Markdown and discards them.

To change the visual design, open `templates/reference_cv.docx` or
`reference_cover.docx` in Word and edit the styles, or edit the constants in
`tools/make_reference.ps1` and regenerate.

## Troubleshooting

**"pandoc not found"** — install it and restart the terminal so `PATH` refreshes.

**"No PDF engine available"** — neither Word nor LibreOffice was found. Install
one. On Windows, Word must be able to open normally: a pending licence dialog or
a stuck modal blocks COM automation.

**Documents render with the wrong fonts** — install the Roboto files from
`templates/fonts/` (§1). On Windows, run `tools\build.ps1` once and restart Word
if it was open during the install.

**Page count looks wrong on the LibreOffice path** — install `pypdf` or
poppler's `pdfinfo`. The regex fallback warns you when it is guessing.

**"tracker database not found"** — `python tools/tracker.py init`.

**"no configuration found"** — copy `config/config.example.toml` to
`config/config.toml`, or run `/setup`.

**A folder move was refused** — a folder with that slug already exists in the
target stage directory. This is deliberate: nothing is overwritten. Merge or
rename the two folders by hand, then re-run the status change.

**The board shows "this application changed since you loaded it"** — the tracker
was updated elsewhere while the tab was open. The board reloads; retry the drag.
