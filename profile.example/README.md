# profile.example

Templates for everything in `profile/`. `/setup` reads them to know what to
collect and what shape to write.

`profile/` is gitignored; this directory is not. So:

- **Change a template here** when you want the framework to ask for something
  different, or to structure it differently.
- **Never put real data here.** It would be committed.

If you would rather fill things in by hand than run `/setup`, copy this
directory to `profile/` and edit. Every file is optional except
`candidate.md` and `cv_master.md`.
