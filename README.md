# Naukri AutoApply

Selenium bot for Naukri.com: search jobs, apply (optional), and answer screening questions with **Groq** or **Ollama**.

Everything is driven by `.env` + a profile markdown file — no code changes for a new user or role.

Use at your own risk. Respect Naukri.com's terms. Keep `DRY_RUN=true` until you are ready for real applies.

## Requirements

- Python 3.9+
- Google Chrome
- Naukri account
- Optional: [Groq API key](https://console.groq.com/) or local [Ollama](https://ollama.com/)

## Quick start

```bash
pip install -r requirements.txt
python setup_bot.py
```

`setup_bot.py` will:

1. Install deps (optional prompt)
2. Ask for **required** `NAUKRI_EMAIL` and `NAUKRI_PASSWORD`
3. Collect name, summary, city, salary, skills, keywords, locations
4. Copy your resume into `resumes/` (`RESUME_PATH`)
5. Create a profile memory file under `profiles/`
6. Write `.env` and run `--check-config`

Then:

```bash
# Fill CTC / notice / experience in your profile markdown
python Main.py --ask "What is your notice period?"
python Main.py --now              # run once now (ignore schedule)
# or
python schedule_bot.py            # wait for IST windows and run
```

Set `DRY_RUN=false` in `.env` only when you want real applications.

## How to run

| Command | What it does |
| --- | --- |
| `python setup_bot.py` | Guided first-time setup |
| `python Main.py --now` | **Run once now** (ignores IST schedule) |
| `python Main.py` | Same as `--now` (immediate one-shot run) |
| `python schedule_bot.py` | Loop: run only inside IST windows |
| `python schedule_bot.py --now` | Ignore windows; run once immediately |
| `python schedule_bot.py --once` | Wait for next/current window, run once, exit |
| `python schedule_bot.py --status` | Show timezone, windows, next slot |
| `python Main.py --schedule` | Same as `schedule_bot.py` |
| `python Main.py --schedule --now` | Same as `schedule_bot.py --now` |
| `python Main.py --check-config` | Validate `.env` / profile (no browser) |
| `python Main.py --ask "…"` | Test one screening answer |
| `python Main.py --clear-session` | Clear cached Chrome login |
| `python Main.py --smoke-test` | Headless Chrome smoke check |
| `python Main.py --init` | Copy bare `.env` + profile templates |

`--now` / `--ignore-schedule` = do **not** wait for schedule times.

## IST scheduler

Default windows (`Asia/Kolkata`):

| Window | Time (IST) |
| --- | --- |
| Morning | 09:30 – 11:00 |
| Afternoon | 13:00 – 14:00 |
| Evening | 17:30 – 18:00 |

```env
SCHEDULE_TIMEZONE=Asia/Kolkata
SCHEDULE_WINDOWS=09:30-11:00,13:00-14:00,17:30-18:00
MAX_APPLICATIONS=15
DAILY_APPLY_LIMIT=50
```

- Scheduled mode: one bot launch per window.
- `--now`: skip waiting and apply immediately (still respects daily limit).

## Daily apply limit

Naukri allows about **50 applications per account per day**.

| Setting | Meaning |
| --- | --- |
| `MAX_APPLICATIONS` | Cap for **one** run / window (default 15) |
| `DAILY_APPLY_LIMIT` | Cap from today's Applied log (default 50) |

Bot stops when the Applied log hits the daily cap, or Naukri shows a limit message (logged in `*Limit.txt`).

Suggested: 15 × 3 windows ≈ 45/day (under 50).

## Important environment variables

### Required

| Variable | Purpose |
| --- | --- |
| `NAUKRI_EMAIL` | Naukri login (auto-login) |
| `NAUKRI_PASSWORD` | Naukri password (auto-login) |
| `KEYWORDS` | Comma-separated search keywords |
| `LOCATIONS` | Comma-separated cities |

### Strongly recommended

| Variable | Purpose |
| --- | --- |
| `CANDIDATE_NAME` / `PROFILE_SUMMARY` | Identity + LLM pitch |
| `PROFILE_MEMORY_PATH` | Path to detailed profile markdown |
| `YEARS_OF_EXPERIENCE` / `MIN_SALARY_LPA` / `EXPECTED_CTC_LPA` | Screening answers |
| `CURRENT_CITY` / `PINCODE` / `NOTICE_PERIOD` | Fast location / notice answers |
| `SKILL_HINTS` | Fast Yes / years-in-skill answers |
| `TITLE_INCLUDE_KEYWORDS` / `TITLE_EXCLUDE_KEYWORDS` | Title filters |
| `COMPANY_EXCLUDE_KEYWORDS` | Skip large / unwanted companies |
| `DRY_RUN` | `true` = log only, no real Apply |

### Optional

| Variable | Purpose |
| --- | --- |
| `RESUME_PATH` | Local resume under `resumes/` |
| `LLM_PROVIDER` | `groq` or `ollama` |
| `GROQ_API_KEY` | Required if provider is groq |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | Local LLM |
| `HEADLESS` / `CHROME_PROFILE_DIR` | Browser behavior |

Never commit `.env` — it is gitignored. Template: `.env.example`.

## Application logs

Each day writes four files under `data/` (gitignored):

| File | Contents |
| --- | --- |
| `15july2026Applied.txt` | Successful Naukri applies |
| `15july2026Skipped.txt` | Title/company filters, no button, dry-run |
| `15july2026External.txt` | Apply on company / external website |
| `15july2026Limit.txt` | Daily quota exhausted |

Example line:

```text
[14:32:05] APPLIED | AI Engineer | Acme Labs | https://www.naukri.com/job-listings-... | reason: naukri apply
```

## LLM usage

- Search and browsing never call the LLM.
- After Apply, LLM is used only when a screening / chatbot UI appears.
- Common questions (years, city, pincode, CTC, relocate) use `.env` first via fast rules.

## Dynamic config (no code edits)

| Source | Holds |
| --- | --- |
| `.env` | Login, search filters, city/CTC/notice, skills, LLM, schedule |
| `PROFILE_MEMORY_PATH` | Full experience, skills, education, Q&A table |
| `RESUME_PATH` | Local resume copy (also upload on Naukri if needed) |

Change role keywords, title filters, or company excludes only in `.env`.

## Manual setup (without setup_bot)

```bash
cp .env.example .env
cp profile_memory.example.md profiles/your-name.md
# edit .env — set email, password, PROFILE_MEMORY_PATH, KEYWORDS, …
python Main.py --check-config
python Main.py --now
```

## Project layout

```text
.env.example                 # tracked template (copy to .env)
setup_bot.py                 # guided onboarding
schedule_bot.py              # IST scheduler (+ --now)
Main.py                      # bot entrypoint
llm_client.py                # Groq / Ollama + screening answers
job_log.py                   # data/*Applied|Skipped|External|Limit
profile_memory.example.md    # blank profile template
profiles/example-candidate.md
profiles/README.md
resumes/                     # gitignored resume copies
data/                        # gitignored daily logs
requirements.txt
```

## Notes

- If `chrome_profile/` has no session and email/password are set, the bot auto-logs in.
- Captcha / OTP may need a one-time manual step in Chrome.
- Close other Chrome windows using the same `chrome_profile` before starting.

.venv\Scripts\python.exe Main.py --now