# data/

Everything about you lives here. Everything outside this directory is the
framework, and daily work never writes there.

| Entry | What it is | Who writes it |
|---|---|---|
| `config/config.toml` | Locale, statuses, page limits, integrations | you (via `/setup`, or by hand) |
| `config/config.example.toml` | The template `config.toml` starts from | the framework — committed |
| `profile/` | Who you are: candidate data, masters, STAR stories, search queries. The source of truth for every claim in every document | you (via `/setup` and `/expand`) |
| `profile.example/` | The templates that tell `/setup` what to collect | the framework — committed |
| `documents/` | Raw material you drop in: old CVs, certificates, reviews. Read by `/setup` and `/expand`, never moved | you |
| `pipeline/applications/` `pipeline/processing/` `pipeline/rejected/` | One folder per application, moved between the three by its status | `/apply`, `tools/tracker.py` |
| `state/` | What the tools keep for themselves: `careerforge.db` (applications, and every posting ever seen), `notion.json`, Gemini logs and scratch, session digests. **Never edit by hand** — use `tools/tracker.py` and `tools/shortlist.py` | the tools |

Two rules make the layout work:

- **`config/` is what you write and the tools read; `state/` is what the tools
  write.** Edit the first freely. Leave the second to the tools.
- **A framework template sits beside the file it is a template for.**
  `config/config.example.toml` next to `config.toml`, `profile.example/` next to
  `profile/`. Those two are the only tracked content here besides the skeleton;
  change them when you want the framework to ask for something different, and
  never put real data in them — it would be committed.

`data/.gitignore` enforces this: everything is ignored except `.gitkeep`
files, the two READMEs and the templates. `git status` stays clean no matter
what you do in here.

Secrets (`.env`) stay at the repository root: they are per-machine, not part
of the data you would copy or sync.
