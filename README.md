# Workflow: jobpilot

My personal automated workflow for the job application process.

`jobpilot` finds postings, scores how well you fit each one, tailors your resume and
cover letter for the good matches (with a mechanical fact check so nothing gets
invented), fills the application form for you, and tracks every application in a
local database.

```
discover ─► filter ─► score ─► tailor ─► review ─► apply ─► track
 (ATS APIs)  (rules)  (Claude)  (Claude +   (you)   (browser   (SQLite)
                                fact check)          autofill)
```

The full design, including why each stage works the way it does, is in
**[docs/WORKFLOW.md](docs/WORKFLOW.md)**.

## Quick start

```bash
pip install -e ".[dev]"
playwright install chromium            # browser for PDF rendering + form filling
export ANTHROPIC_API_KEY=sk-ant-...    # or `ant auth login`

jobpilot init                          # creates data/settings.yaml + data/profile.yaml
# edit data/settings.yaml  -> target titles, locations, company boards to watch
# edit data/profile.yaml   -> autofill details + your answer bank

jobpilot ingest ~/Documents/resume.pdf # -> data/master_resume.yaml (review and extend it!)

jobpilot discover                      # pull postings from the boards you listed
jobpilot add <posting url>             # ...or track any single posting
jobpilot score                         # fit-score everything new
jobpilot tailor --top 5                # tailored resume + cover letter PDFs for the best matches
jobpilot review                        # approve / skip each one
jobpilot apply                         # fills forms in a browser; you click Submit
jobpilot status                        # where everything stands
jobpilot mark <job id> interviewing    # record outcomes
```

Or run the whole pipeline: `jobpilot run`.

Everything personal lives in `data/` (git-ignored): your resume, profile, generated
PDFs and the tracker database.

## How much is automated?

You choose with `apply.mode` in `data/settings.yaml`:

| mode | what happens |
|------|--------------|
| `review` | Stops after tailoring. You apply yourself using the generated PDFs. |
| `assisted` *(default)* | Fills the form in a visible browser. You check it and click Submit. |
| `auto` | Submits by itself, but only if every field came from your profile or answer bank, nothing was AI-drafted, and there's no CAPTCHA. Otherwise it falls back to `assisted`. |

`apply.require_review: false` also skips the `review` step for jobs that pass the fact check.
Daily and per-company caps apply in every mode.

## Tests

```bash
pytest          # 38 tests; browser tests drive headless Chromium against a local form
```

Set `JOBPILOT_CHROMIUM=/path/to/chrome` to use an existing Chrome/Chromium instead
of Playwright's bundled one.
