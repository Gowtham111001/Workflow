# Workflow

My personal automated workflow for the job application process.

## Goal

Build an assistant that:
1. Reads a base resume.
2. Finds matching job postings.
3. Tailors resume content for each posting.
4. Autofills application forms with stored profile details.
5. Submits applications only after approval.

## Workflow Blueprint

### 1) Inputs
- **Base resume** (`/data/resume/master_resume.md` or PDF-to-text output)
- **User profile** (`/data/profile/profile.json`)  
  Includes name, email, phone, location, links, work authorization, salary preference, and common application answers.
- **Preferences** (`/data/profile/preferences.json`)  
  Includes preferred roles, locations, minimum salary, remote/on-site preference, and excluded companies.

### 2) Job Discovery
- Pull jobs from selected sources (company pages, job boards, APIs, RSS).
- Normalize posting data to a common structure:
  - `title`, `company`, `location`, `description`, `requirements`, `apply_url`, `deadline`.
- Filter jobs using preferences and hard constraints.

### 3) Resume Tailoring
- Extract key requirements and skills from each posting.
- Match them against resume projects/experience.
- Generate a tailored resume draft by:
  - Reordering bullets for relevance.
  - Rewriting phrasing to mirror required skills (truthfully).
  - Keeping claims factual and evidence-based.
- Save versions per job:
  - `/output/<job_id>/resume_tailored.md`
  - `/output/<job_id>/resume_tailored.pdf`

### 4) Application Content Generation
- Generate role-specific answers (summary, “why this role”, etc.) using stored profile facts.
- Reuse approved templates for common questions.
- Save generated responses:
  - `/output/<job_id>/answers.json`

### 5) Form Autofill + Apply
- Open application URL with browser automation.
- Map form fields to profile keys.
- Autofill known fields and flag unknown questions for review.
- Require a final approval checkpoint before clicking submit.

### 6) Tracking
- Log each job and status in a tracker (`applied`, `needs_review`, `rejected`, `interview`).
- Store:
  - submission timestamp
  - tailored resume version used
  - generated answers used
  - follow-up reminder date

## Guardrails

- Never invent skills, tools, or years of experience.
- Do not auto-submit without explicit approval.
- Respect site terms, rate limits, and anti-bot policies.
- Keep personal data encrypted at rest and masked in logs.

## MVP Implementation Order

1. Profile/resume ingestion and local storage.
2. Job posting parser + filter.
3. Resume tailoring engine.
4. Answer generation.
5. Browser autofill with approval gate.
6. Application tracking dashboard/log.
