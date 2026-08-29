# Cover Letter Templates and Tailoring Guide

## Template: Markdown -> DOCX -> PDF (pandoc + MS Word)

Cover letters are written in Markdown and built into styled DOCX + PDF, matching the CV's visual design (Roboto fonts) via pandoc reference-doc `templates/reference_cover.docx`.

**Master reference:** `templates/cover_letter_master.md` - skeleton with `[PLACEHOLDERS]`. **Copy it as the starting point; never edit the master itself.**
**Output file:** `data/pipeline/applications/<company-slug>/cover_letter_{slug}.md`
**Build:** `powershell -NoProfile -ExecutionPolicy Bypass -File tools\build.ps1 -Path data\pipeline\applications\<company-slug>` (same command with `data\pipeline\processing\<company-slug>` once the folder has moved there)

## Structure (from the master)

1. YAML `title`/`subtitle` - name + headline, centered
2. `Contact` div - centered contact line
3. Date
4. Company name + city (use `\` line breaks to keep them in one block)
5. Salutation
6. Opening paragraph (2-3 sentences: the role, where posted, strongest-match hook)
7. Body paragraph 1 (3-4 sentences: most relevant achievement story with metrics)
8. Body paragraph 2 (3-4 sentences: second angle - architecture/migration/leadership matched to the posting)
9. Closing paragraph (2 sentences: why this company specifically + call to action)
10. `Sincerely,` + name

A short 3-5 item bullet list may replace part of a body paragraph when the posting is requirement-list heavy - keep the total word budget.

## Tailoring Guidelines

### Salutation
- If you know the hiring manager's name: "Dear [First Last],"
- If you know the team: "Dear [Company] hiring team,"
- Generic: "Dear Hiring Manager," (avoid "To whom it may concern")

### Length - Hard 1-Page Limit
- The built PDF must fit **1 page** (the build script reports the count)
- **Word budget: 250-300 words** of body text. This is the safe maximum; 350 words will overflow.
- When adding company-specific content, trim other content to compensate rather than adding net length

### Extra Context from the User
When the user provides extra context at the drafting gate (or any other time), work it into the letter:

- **Named referral** ("меня зареферил X"): first paragraph, right after stating the role - "I was referred to this role by [Name]" (confirm with the user whether the person may be named).
- **Contacts on the team / past collaboration with the company**: motivation paragraph - a concrete sentence about the relationship, not name-dropping ("Having worked with [Company]'s API team on [X], I saw first-hand...").
- **Deadlines, availability, visa/relocation notes**: closing paragraph, one factual sentence.

Rules: the user's own statements about their relationships need no external verification, but write only what the user actually said - do not embellish. Extra context does not extend the word budget: trim elsewhere to compensate.

### Non-English Cover Letters
- Same structure, content written in the posting's language
- Adjust date format and closing to local convention (e.g. "Com os melhores cumprimentos," for Portuguese)
- The CV stays in English regardless

### AI Tooling References
Any mention of agentic coding or AI tooling must reference **Claude Code** by name.

## Checklist Before Finalizing
- [ ] No `[PLACEHOLDER]` left anywhere
- [ ] No em-dashes (use commas or periods instead)
- [ ] No cliches or empty filler
- [ ] Every claim backed by a specific example
- [ ] Forward-looking framing: focuses on tasks you'll solve, not just past duties
- [ ] Motivation references this specific company's mission/values (verified facts only)
- [ ] Company name and role are correct throughout
- [ ] Date is current
- [ ] Fits on one page (build script confirms)
- [ ] Language matches the job posting language
- [ ] Salutation is appropriate (named person if possible)

## Submission Guidelines (Best Practice)
- Submit only the documents the employer requests
- Send the PDF (the DOCX is for your own edits)
- Follow all employer instructions regarding anonymity or specific materials
