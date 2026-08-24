# documents/

Drop your career materials here. `/setup` reads whatever it finds; `/expand`
uses them to fill gaps later. Nothing in this folder is committed except this
file.

## What is worth putting here

| | |
|---|---|
| **CVs** | Every version you have, including old ones. Earlier CVs often describe work the current one had to cut for space — that detail is exactly what makes a tailored application possible |
| **Cover letters** | Ones you were happy with. They carry your voice better than any description of it |
| **Certificates** | Course completions, professional certifications, verification links |
| **Reference letters** | And any written feedback: performance reviews, recommendations, testimonials |
| **Portfolio exports** | Case studies, project write-ups, a personal-site export |
| **Assessments** | Behavioural profiles — Predictive Index, DISC, StrengthsFinder — if you have taken one |
| **Job descriptions of roles you held** | The internal ones, which are usually far more specific than anything on your CV |

Any format that can be read as text: `.md`, `.txt`, `.pdf`, `.docx`, `.html`.

## Why more is better

The single biggest factor in the quality of a tailored CV is how much raw
material the profile was built from. A profile assembled from one two-page CV
can only ever restate that CV. A profile assembled from four CVs, two
performance reviews and a project write-up can pick, for each posting, the
evidence that actually fits it.

Old and rejected material still counts. The point is not to present all of it —
it is to have it available to choose from.

## What happens to it

`/setup` extracts what it can and asks about the gaps. `/expand` goes back over
it when you have added something new.

Everything extracted lands in `profile/`, which is also gitignored. Nothing here
is sent anywhere unless you have enabled the Gemini delegation in
`config/config.toml` — see `docs/manual.md`.

Files stay where you put them; nothing here is moved, renamed or deleted.
