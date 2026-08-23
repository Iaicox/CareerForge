# CV Templates and Tailoring Guide

## Template: Markdown -> DOCX -> PDF (pandoc + MS Word)

CVs are written in Markdown and built into styled DOCX + PDF. The visual design follows `templates/CV_template.docx` (Roboto fonts, gray secondary text, right-aligned dates) via pandoc reference-doc `templates/reference_cv.docx`.

**Master reference:** `templates/cv_master.md` - comprehensive CV with all competencies, experience, and achievements. **Copy it as the starting point for every targeted CV; never edit the master itself.**
**Output file:** `applications/<company-slug>/cv_{slug}.md`
**Build:** `powershell -NoProfile -ExecutionPolicy Bypass -File tools\build.ps1 -Path applications\<company-slug>` (produces `.docx` and `.pdf`, reports page count; same command with `processing\<company-slug>` once the folder has moved there)

## Markup Conventions (must be preserved exactly)

The master uses pandoc constructs mapped to Word styles in the reference doc:

| Markdown construct | Renders as |
|---|---|
| YAML `title:` / `subtitle:` | Centered name (Roboto 16pt) and headline (12pt) |
| `::: {custom-style="Contact"}` div | Centered contact line |
| `# Heading` | Section heading (Experience, Education, ...) |
| `::: {custom-style="CompanyLine"}` div | Company row with right tab stop |
| `::: {custom-style="RoleLine"}` div | Role row (Roboto regular 11pt) with right tab stop |
| `::: {custom-style="StackLine"}` div | Gray "Stack: ..." line on its own row, directly under the mission paragraph |
| `::: {custom-style="TabLine"}` div | Certifications/Education/Skills row with right tab stop |
| `` `<w:r><w:tab/></w:r>`{=openxml} `` | Jump to the right-aligned tab stop (put dates/locations after it) |
| `[text]{custom-style="Muted"}` span | Gray secondary text (company descriptions, dates, locations) |
| Plain paragraph | Body text (mission line under each role) |
| `- bullet` | Achievement bullet |

Breaking these (a stray bracket, a lost `:::` fence, a mangled openxml snippet) breaks the layout - the reviewer checklist includes verifying they are intact.

## Profile Statement Templates

The paragraph right after the contact line is the most important section to customize. Write 3-4 lines that function as an elevator pitch explaining why the candidate is uniquely qualified for *this specific role*. Focus on what the employer gains.

**For Senior Frontend Developer roles (Vue/Nuxt):**
> 8 years building high-impact web applications. Specialist in Vue 3 and Nuxt 3 ecosystems - led migrations from legacy stacks, built micro-frontend architectures, and created shared design systems adopted across entire product suites. Proven track record of measurable delivery: 90% build time reduction, 2x content loading speed, 70% revenue uplift from SSR migration.

**For Frontend Lead / Tech Lead roles:**
> Senior Frontend Developer with 8 years of experience and a track record of leading teams through complex migrations and architectural transformations. Combines hands-on expertise in Vue 3, Nuxt 3, and TypeScript with team leadership skills: mentoring junior developers, establishing code review processes, and running internal workshops. Delivers at both the feature level and the architectural level.

**For React/TypeScript roles:**
> Frontend Engineer with 8 years of experience across Vue and React ecosystems. Strong TypeScript foundation, deep experience with SSR (Nuxt 3, Next.js), and a record of building scalable component libraries and micro-frontend architectures. Equally at home owning a feature end-to-end or making architecture decisions for the team.

**For FinTech / Financial roles:**
> Senior Frontend Developer with 8 years of experience building production-grade web applications. Experienced integrating financial infrastructure (Stripe.js payment flows, Firebase auth) and delivering platforms where reliability and precision are non-negotiable. Specialist in Vue 3, Nuxt 3, and TypeScript with a strong record of measurable technical impact.

## Section-by-Section Tailoring

### Experience
- Keep the formula per company, each element on its own line: company line -> role line -> one mission sentence (strongest result) -> `StackLine` ("Stack: ...") -> 2-3 achievement bullets
- **Every bullet follows the AR formula (Action + Result, from STAR):** what was done and what it led to. Both shapes are fine:
  - Result-first: "Reduced build time by 90% by optimising the bundling pipeline"
  - Action-first: "Built a reusable CRM component library, cutting page development time from 8h to under 1h"
  - A bullet with an action but no outcome is incomplete; if no metric exists, state the concrete qualitative result ("adopted team-wide") - never invent numbers
- Rewrite bullets to emphasize aspects most relevant to the target role; reorder so the most relevant lead
- **Emphasize measurable results always:** 90% build time reduction, 2x loading speed, 70% revenue uplift, 1.8x orders, 100%->10% managerial workload, 8h->1h page dev time, 1.5h->5-10min proposal time

### Skills & Languages
Reorder the groups and their contents so the JD-matching stack leads. Keep the grouped structure (Frontend / Architecture & Tooling / Testing & Integrations / Languages); rename or re-split groups when the role calls for it (e.g. lead with "Markup & Styling" for a markup-heavy role).

### Projects Section (when to include)
He now has two independent projects worth showing (see `01-candidate-profile.md`): **Reconcil** (public, Apache-2.0, on-chain accounting ledger, TypeScript/React 19/Next 15, CI that enforces architecture) and **Revoice** (in development, React browser extension for video dubbing, Azure + browser TTS, media APIs).

Include a `# Projects` section - placed after `# Experience` - when the role involves any of: React/Next, Browser APIs or media handling, fintech/crypto, code quality and testing infrastructure, or asks for a portfolio. Reuse the existing `CompanyLine` / `RoleLine` / `StackLine` styles; do not invent new pandoc styles (they don't exist in `reference_cv.docx`). Two entries at ~2 lines each fits within the page budget; if it pushes past 2 pages, trim the oldest role's bullets (Gelster) first.

Employment history is still all in private employer repos, so keep quantified achievements prominent regardless - but "his GitHub is empty" is no longer true and should not be conceded. Never claim Revoice is finished, published, or has users, and never present Reconcil's React surface (a landing page) as a large React application.

### Education
For senior roles, keep education brief (degree + institution + year only). The 2019 bootcamp can be omitted if space is tight.

### Languages
Always include: Russian (native), English (B2+), Portuguese (A2).

## Page Budget - Hard 2-Page Limit

The built PDF **must** fit within 2 pages (the build script reports the count). Content limits:

| Section | Max budget |
|---------|-----------|
| Profile statement | 3-4 lines |
| ChatPlace (most recent) | mission + 3-4 bullets |
| Infomediji | mission + 3-4 bullets |
| MKS-System | mission + 2-3 bullets |
| Gelster | mission + 2 bullets |
| Certifications | 1-3 lines (trim to the most role-relevant) |
| Education | 2 entries, brief |
| Skills & Languages | 4 lines |

**If in doubt, cut rather than squeeze.** Do not shrink fonts or margins to force-fit content.

## Manual Edits by the User

The user may hand-edit the generated `.docx` in Word. The build script detects a `.docx` newer than its `.md` and then only refreshes the PDF. Never overwrite a hand-edited `.docx` without asking (that requires `-Force`).

## Checklist Before Finalizing

Mirrors the checklist in `06-cover-letter-templates.md`. Cover letters stay clean because that checklist exists; CVs did not have one, and em-dashes leaked into 16 of 48 of them. Run this against the tailored copy, not the master.

- [ ] **No em-dashes** (use a comma, colon, or period instead). Check the tailored lines specifically - the two places they appear are the profile statement and the `# Projects` subtitles, and the Projects subtitle does not exist in the master, so it is only catchable here. Note that en-dashes in ranges ("Jul 2020 – Nov 2022", "5–10 minutes") are correct and must stay
- [ ] Pandoc markup intact: `:::` fences opened and closed, `{custom-style="..."}` spans unbroken, `` `<w:r><w:tab/></w:r>`{=openxml} `` snippets whole
- [ ] Every bullet states a result, not only an action
- [ ] Built PDF is <= 2 pages, per the build script output
- [ ] Every claim is backed by `01-candidate-profile.md`; nothing invented or upgraded during tailoring
