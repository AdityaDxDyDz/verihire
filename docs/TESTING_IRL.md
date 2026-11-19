# Testing VeriHire yourself (Windows, PowerShell)

Three levels: **A** automated (everything, one command), **B** you click through the app, **C** real resumes from Kaggle.
Run every command from the repo root (`verihire\`) with the venv active.

## 0. One-time setup

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r backend\requirements-dev.txt -r ml\requirements-ingest.txt -r ml\requirements-rank.txt
pip install -e ml
pip install playwright; python -m playwright install chromium      # browser tests (or use --channel chrome later)
copy .env.example .env
docker compose -f db\docker-compose.yml up -d                       # Postgres 16 + pgvector on localhost:5432
python -c "import sys; sys.path.insert(0,'ml'); from docint.embed import get_embedder; get_embedder()"   # caches BGE-small (internet, once)
```
Node.js 18+ is optional (only for the frontend unit tests).

## 0b. Check what it reads from YOUR resume (no server needed)

```bat
python scripts\inspect_resume.py C:\path\to\my_resume.pdf --show-pii
python scripts\inspect_resume.py C:\path\to\my_resume.pdf --jd my_jd.txt --show-text
python scripts\inspect_resume.py data\resumes --limit 200        # a folder: most common problems + missed skills
```
Shows sections, personal details found (removed before indexing), years, education, titles, certifications, every
skill, and checks marked `[!!]` (e.g. "date ranges present but 0 years", "listed skill not in the dictionary",
"heading not recognised"). Folder mode lists the skills / headings most often missed: add them to
`ml/docint/ingest/skills_dict.json` / the chunker's section list - that is how parsing gets more robust.

## A. Automated — everything

```powershell
$env:TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/verihire_test"
python scripts\test_all.py --real-model
```
Expect 13 PASS lines and `ALL CHECKS PASSED`. The last step drives Chrome through the real app; its screenshots are in
`frontend\tests\screens_real\` — open them to see what it checked. If a browser step says SKIP, install Playwright's
Chromium or add `--channel chrome`.

## B. By hand — the app as a recruiter

In `.env` set `AUTO_MIGRATE=true` (and optionally `ANTHROPIC_API_KEY=...` to use Claude instead of the built-in rules). Then:

```powershell
uvicorn app.main:app --app-dir backend --loop app.eventloop:selector_loop --port 8000
# second terminal (venv active):
python scripts\seed_demo.py          # ~117 fictional resumes + 3 JDs into the demo@example.com workspace
```
Open http://localhost:8000 and sign in as `demo@example.com` (dev mode: e-mail only; any other e-mail = a new, empty workspace).

| Do this | You should see |
|---|---|
| Library | ~120 files; one **Failed** (corrupt) and one **Needs OCR** (scanned) with a plain-English reason; years / education / skills columns filled |
| Drop 2–3 of your own PDFs + a `.txt` | the text file is refused at once; PDFs show progress and turn **Ready** within seconds |
| Job & search → paste a JD → *Extract requirements* | required / preferred skills, min years, education — editable; *Save changes* |
| *Search resumes* | ranked table, names hidden (Candidate ABC123), match score 0–100, required-skill chips; "Hidden text found" badge on the 5 planted prompt-injection resumes, which must NOT be at the top |
| Click a candidate → *Analyse fit* | per-skill verdict with **yellow-highlighted quotes**; each quote is verbatim from the PDF |
| *Reveal identity* → confirm | name / e-mail / phone appear; the reveal is in `GET /api/v1/audit` |
| *Open page in PDF* | the original PDF opens at that page |
| Tick 2–3 rows → *Compare* | side-by-side comparison |
| `GET /api/v1/health` | `ml_components` all `real` (embedder `stub` = the BGE model is not cached → step 0) |

Kadam's 5-minute pre-demo checklist (keyboard, dark mode, mobile width, error banners): `frontend\tests\CHECKLIST.md`.

## C. Real resumes + job descriptions from Kaggle (cmd)

Datasets: **resumes** https://www.kaggle.com/datasets/snehaanbhawal/resume-dataset (~2,400 LiveCareer resumes, PDFs in
one folder per category: ACCOUNTANT, HR, INFORMATION-TECHNOLOGY, ...) and **job descriptions**
https://www.kaggle.com/datasets/adityarajsrv/job-descriptions-2025-tech-and-non-tech-roles (1,100 synthetic JDs).

1. Sign in on kaggle.com, **Download** both, unzip **outside the repo**:
   `C:\verihire-data\resumes` and `C:\verihire-data\jds`. Then find the folders / file:
   ```bat
   dir C:\verihire-data\resumes\data\data
   dir /s /b C:\verihire-data\jds\*.csv
   ```
   (the first should list category folders; if your unzip has one `data` level less, drop one `\data`)
2. Start the server (part B, terminal 1). In a second terminal (venv active):
   ```bat
   python scripts\import_kaggle.py --pdf-dir C:\verihire-data\resumes\data\data --limit 240 --jd-csv <the .csv from step 1> --jd-limit 15 --token dev:kaggle@example.com
   ```
   `--limit 240` takes 10 resumes from each of the 24 categories. JDs are picked spread over titles / categories;
   focus them with e.g. `--jd-match "accountant|teacher|developer|hr|sales|nurse"`. Run again with
   `--skip-upload` to try other JDs on the same resumes (add `--jd-file my_jd.txt` for your own).
   The script detects the CSV columns and prints which it used; override with `--jd-col` / `--jd-title-col`.
3. Read the output:
   - **LIBRARY**: ready / failed / needs_ocr with reasons, and how many resumes got years / education / skills.
   - **per JD**: extracted requirements, top 10 with score, required skills met, years and the resume's category.
   - **category check**: for JDs whose title points to a category (Accountant → ACCOUNTANT, Software Developer →
     INFORMATION-TECHNOLOGY / ENGINEERING, ...), the share of the top 10 from that category, and the average.
     This is the real-data quality signal of this pair of datasets. It is rough: categories overlap
     (a "Business Analyst" resume may sit in FINANCE or CONSULTANT).
   - a **note** when a JD has no recognised required skills: then the app ranks by text relevance and the UI says so.
   Everything is also in `kaggle_report.json` (contains file names - don't commit it).
4. Check in the UI: sign in as `kaggle@example.com`, open a JD, search, *Analyse fit* on a few candidates; check
   the highlighted quotes are really in the PDF and the skill chips make sense.
5. Optional: `python scripts\e2e_real.py --pdf-dir C:\verihire-data\resumes\data\data --headed`.

What to note for the report: category-check per role (tech vs non-tech), skills the dictionary still misses (add them
to `ml/docint/ingest/skills_dict.json` - canonical lowercase name + aliases, then rerun the import with a new token),
files that fail and why, and ingestion time for 240 files.

**Privacy:** these are real people's resumes. Keep them out of git (`.gitignore` covers `kaggle/` and the report), don't
upload them to a deployed instance others can access, and follow each dataset's licence.
