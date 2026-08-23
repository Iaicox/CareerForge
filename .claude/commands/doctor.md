---
description: Check that this workspace has everything it needs to build and track applications
---

# /doctor

```bash
python tools/doctor.py
```

Show the table as-is, then say in one or two sentences what the user should do
next. Order the advice by what actually blocks them:

1. Required failures first — the workspace cannot do its job until these are fixed.
2. Then warnings that limit something specific (no LibreOffice on a machine
   without Word means no PDFs; no pypdf and no pdfinfo means approximate page
   counts on the LibreOffice path).
3. Say nothing about the checks that passed beyond "the rest is fine".

If `profile` or `configuration` is the failure, the answer is `/setup`.
