# Naukri AutoApply

A Selenium-based helper for searching recent Naukri.com job postings and, when explicitly enabled, applying through visible Apply buttons.

Use this at your own risk and respect Naukri.com's terms. The default config runs in dry-run mode, so it can inspect matching jobs without submitting applications.

## Requirements

- Python 3.8+
- Google Chrome
- A Naukri account

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Update `config.ini`:

```ini
[DEFAULT]
headless = false
dry_run = true
chrome_profile_dir = chrome_profile

[JOB_SEARCH]
keywords = Software Engineer
locations = Bangalore, Pune, Hyderabad
experience = 3+ years
salary = 8-16 Lakhs
```

## Validate Config

```bash
python Main.py --check-config
```

## Run

```bash
python Main.py
```

On the first run, Chrome opens the Naukri login page. Enter your credentials manually (email/password or Google). After you sign in, the session is saved under `chrome_profile/` and reused on later runs.

To force login again:

```bash
python Main.py --clear-session
```

Close any Chrome window already using that same profile before starting.

To allow real applications, set:

```ini
dry_run = false
```

## Useful Limits

`config.ini` includes a `[LIMITS]` section:

- `max_pages`: search result pages to inspect per keyword/location pair
- `max_jobs_per_page`: jobs to inspect on each page
- `max_applications`: maximum submitted applications per run
- `wait_seconds`: Selenium explicit wait timeout
- `login_timeout_seconds`: how long to wait for manual login
