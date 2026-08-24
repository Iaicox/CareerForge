---
description: Find employer replies in your mailbox and update the tracker
argument-hint: "[--since YYYY-MM-DD]"
---

# /mailsync — reconcile the mailbox with the pipeline

An application whose reply sat unread for a week is worse than one never sent.
This scans the mailbox you apply from and matches what it finds to open
applications.

---

## Step 1: scan

```bash
python tools/mailsync.py scan --json $ARGUMENTS
```

Exit code 3 means the mailbox is not configured — show the message, which
already contains the `[mail]` block to add and where the app password goes, and
stop. Do not try to work around it.

The scan is read-only: the mailbox is opened `readonly=True` and messages are
fetched with `BODY.PEEK`, so nothing is marked read, moved or flagged. Running
it must never change what the user sees in their mail client. Say so, because
people are reasonably nervous about a tool touching their email.

## Step 2: judge each match yourself

The tool proposes; it does not decide. Its classification comes from patterns
in the subject and body, and patterns are wrong in ways that matter:

- A "we regret" can be a rejection for one role inside a message that offers
  another.
- An automated acknowledgement reads like a real reply.
- A recruiter forwarding an unrelated posting matches on the company name.

Read the `subject`, `snippet` and `match_reason` on each finding. Where the
proposed classification is wrong, say so and use your own. Where the message is
genuinely ambiguous, present it as ambiguous rather than picking.

## Step 3: propose, in one table

| Company | Role | Now | Proposed | Why | Evidence |

`possibly_forgotten` in the output deserves its own section: applications still
marked `draft` where the employer has already replied. That almost always means
it was sent and never recorded, and it is the single most valuable thing this
command finds.

**Change nothing until the user confirms**, row by row. They may exclude any.

## Step 4: apply what was approved

```bash
python tools/tracker.py set-status <slug> <status>
python tools/tracker.py event add <slug> --type <type> --date <ISO> --outcome <outcome>
python tools/tracker.py note <slug> --append --text "<what the message said>"
```

A status change moves the application folder. Say which folder went where.

For a rejection, record the event before the status: once the status is
terminal the funnel history is all that explains how far it got.

## Step 5: report

How many messages were scanned, how many matched, how many statuses changed,
and what was left ambiguous for the user to look at themselves.

If nothing matched, say that plainly — it is a normal result, not a failure,
and inventing a match to have something to report is worse than silence.
