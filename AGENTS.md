# Bay Area events project instructions

This public, deliberately unlinked site builds a daily event dashboard for a family
based in Menlo Park. Read `README.md` for source-specific behavior and architecture.

## Privacy and publication

- The repository may contain only public event information.
- Never add attendance, RSVP, favorite, or "we are going" markers; those would publish
  the family's schedule. A personalized attendance feature requires moving the site
  behind Cloudflare Access first.
- Preserve `noindex` and the lack of links from michaelbailey.org unless Mike explicitly
  changes the publication decision.

## Data flow and commands

```text
code/sources/*.py -> code/fetch.py -> data/events.db -> code/build.py
                  -> site/events.json -> site/index.html
```

- `python code/fetch.py --dry-run` checks external sources without writing.
- `python code/fetch.py` updates the ignored SQLite database.
- `python code/build.py` regenerates `site/events.json`.
- `python code/test_env_keys.py` checks key resolution.
- Run `node code/validate_palette.js` before changing category colors.
- Source failures must remain visible in `source_runs` and the site health footer; do
  not silently drop or mask a failing scraper.

## Invariants

- Python 3.9+ and the standard library are the default; do not add a dependency without
  a concrete need and corresponding setup documentation.
- Event type uses the validated three-color-plus-neutral system. Crosscutting traits
  such as family, free, outdoor, or festival remain text badges.
- Keep all file writes UTF-8. On Windows, account for console encoding explicitly.
- Refresh the transcribed MIT and MPCSD holiday calendars annually rather than
  replacing them with a generic US-holiday package.
- `data/` and `outputs/` are ignored and mirrored; never commit the event database or
  secrets. `site/events.json` is the generated site payload.
