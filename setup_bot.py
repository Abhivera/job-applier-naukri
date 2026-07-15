#!/usr/bin/env python3
"""Interactive setup for AutoApplyNukari.

Creates .env, profile memory, and copies your resume.
Run once for a new machine / new user:

    python setup_bot.py
"""

from __future__ import annotations

import getpass
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
ENV_EXAMPLE = BASE_DIR / ".env.example"
PROFILE_EXAMPLE = BASE_DIR / "profile_memory.example.md"
RESUMES_DIR = BASE_DIR / "resumes"
PROFILES_DIR = BASE_DIR / "profiles"


def prompt(label: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    if secret:
        value = getpass.getpass(f"{label}{suffix}: ").strip()
    else:
        value = input(f"{label}{suffix}: ").strip()
    return value or default


def prompt_yes(label: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"{label} ({hint}): ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "1", "true"}


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "candidate"


def set_env_value(content: str, key: str, value: str) -> str:
    """Set KEY=value in dotenv text (add line if missing)."""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(content):
        return pattern.sub(line, content)
    return content.rstrip() + f"\n{line}\n"


def ensure_deps() -> None:
    req = BASE_DIR / "requirements.txt"
    if not req.exists():
        return
    if prompt_yes("Install / update Python dependencies from requirements.txt?", True):
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req)])


def copy_resume(candidate_slug: str) -> str:
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    (RESUMES_DIR / ".gitkeep").touch(exist_ok=True)

    if not prompt_yes("Do you want to upload / copy a resume file now?", True):
        return ""

    raw = prompt("Path to your resume (PDF / DOC / DOCX)")
    if not raw:
        print("Skipped resume copy.")
        return ""

    src = Path(raw).expanduser()
    if not src.is_file():
        print(f"File not found: {src}")
        return ""

    dest = RESUMES_DIR / f"{candidate_slug}{src.suffix.lower()}"
    shutil.copy2(src, dest)
    rel = dest.relative_to(BASE_DIR).as_posix()
    print(f"Resume copied to: {rel}")
    return rel


def ensure_profile(candidate_name: str, candidate_slug: str) -> str:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    default_path = PROFILES_DIR / f"{candidate_slug}.md"
    choice = prompt(
        "Profile memory path (markdown with your Q&A / experience)",
        str(default_path.relative_to(BASE_DIR).as_posix()),
    )
    path = Path(choice)
    if not path.is_absolute():
        path = BASE_DIR / path

    if path.exists():
        print(f"Using existing profile: {path.relative_to(BASE_DIR)}")
        return path.relative_to(BASE_DIR).as_posix()

    if not PROFILE_EXAMPLE.exists():
        raise FileNotFoundError(f"Missing template: {PROFILE_EXAMPLE}")

    text = PROFILE_EXAMPLE.read_text(encoding="utf-8")
    if candidate_name:
        text = text.replace("- Full name:", f"- Full name: {candidate_name}", 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"Created profile template: {path.relative_to(BASE_DIR)}")
    print("Fill experience / skills / CTC / notice period in that file before real applies.")
    return path.relative_to(BASE_DIR).as_posix()


def write_env(values: dict[str, str]) -> None:
    if ENV_PATH.exists():
        content = ENV_PATH.read_text(encoding="utf-8")
        print("Updating existing .env …")
    elif ENV_EXAMPLE.exists():
        content = ENV_EXAMPLE.read_text(encoding="utf-8")
        print("Creating .env from .env.example …")
    else:
        content = ""

    for key, value in values.items():
        content = set_env_value(content, key, value)

    ENV_PATH.write_text(content, encoding="utf-8")
    print(f"Wrote {ENV_PATH.name}")


def main() -> int:
    print("=" * 60)
    print(" AutoApplyNukari — setup")
    print("=" * 60)
    print("This walks you through required secrets and local files.")
    print("Your .env is gitignored — never commit email/password.\n")

    ensure_deps()

    print("\n--- REQUIRED: Naukri login ---")
    print("Used for auto-login when chrome_profile has no session.")
    email = prompt("NAUKRI_EMAIL (your Naukri login email)")
    password = prompt("NAUKRI_PASSWORD (hidden)", secret=True)
    if not email or not password:
        print("\nWarning: email/password empty — bot cannot auto-login until you set them.")

    print("\n--- Candidate identity ---")
    name = prompt("CANDIDATE_NAME", "Your Name")
    slug = slugify(name)
    summary = prompt(
        "PROFILE_SUMMARY (1–3 lines about you / target roles)",
        "Software engineer seeking relevant roles. Open to remote, hybrid, or onsite.",
    )
    years = prompt("YEARS_OF_EXPERIENCE", "3+")
    min_salary = prompt("MIN_SALARY_LPA", "8")
    expected = prompt("EXPECTED_CTC_LPA", min_salary)
    city = prompt("CURRENT_CITY", "")
    pincode = prompt("PINCODE", "")
    notice = prompt("NOTICE_PERIOD (e.g. 30 days / Immediate / Will confirm)", "Will confirm")
    relocate = prompt("WILLING_TO_RELOCATE", "Yes")

    print("\n--- Job search (comma-separated) ---")
    keywords = prompt("KEYWORDS", "Software Engineer, Backend Developer")
    locations = prompt("LOCATIONS", "Bangalore, Hyderabad, Pune, Remote")
    skill_hints = prompt(
        "SKILL_HINTS (skills for fast Yes / years answers)",
        "python, javascript, react, node, sql, aws, docker",
    )

    print("\n--- LLM (optional for screening questions) ---")
    provider = prompt("LLM_PROVIDER (groq | ollama)", "groq").lower()
    groq_key = ""
    ollama_model = "llama3.2"
    if provider == "groq":
        groq_key = prompt("GROQ_API_KEY (https://console.groq.com/)", secret=True)
    else:
        ollama_model = prompt("OLLAMA_MODEL", "llama3.2")

    dry_run = "true" if prompt_yes("Keep DRY_RUN=true (no real Apply clicks)?", True) else "false"

    print("\n--- Resume & profile memory ---")
    resume_rel = copy_resume(slug)
    profile_rel = ensure_profile(name, slug)

    values = {
        "NAUKRI_EMAIL": email,
        "NAUKRI_PASSWORD": password,
        "CANDIDATE_NAME": name,
        "PROFILE_SUMMARY": summary,
        "YEARS_OF_EXPERIENCE": years,
        "MIN_SALARY_LPA": min_salary,
        "EXPECTED_CTC_LPA": expected,
        "CURRENT_CITY": city,
        "PINCODE": pincode,
        "NOTICE_PERIOD": notice,
        "WILLING_TO_RELOCATE": relocate,
        "KEYWORDS": keywords,
        "LOCATIONS": locations,
        "SKILL_HINTS": skill_hints,
        "PROFILE_MEMORY_PATH": profile_rel,
        "RESUME_PATH": resume_rel,
        "LLM_PROVIDER": provider,
        "DRY_RUN": dry_run,
    }
    if groq_key:
        values["GROQ_API_KEY"] = groq_key
    if provider == "ollama":
        values["OLLAMA_MODEL"] = ollama_model

    write_env(values)

    print("\n--- Validate ---")
    try:
        subprocess.check_call([sys.executable, str(BASE_DIR / "Main.py"), "--check-config"])
    except subprocess.CalledProcessError:
        print("check-config reported issues — fix .env / profile and retry.")
        return 1

    print("\nSetup complete.")
    print("Next steps:")
    print(f"  1. Edit profile facts: {profile_rel}")
    if resume_rel:
        print(f"  2. Resume stored at: {resume_rel} (upload to Naukri profile manually if needed)")
    print("  3. Test answers:  python Main.py --ask \"What is your notice period?\"")
    print("  4. Run bot:       python Main.py")
    print("  Keep DRY_RUN=false in .env only when you want real applications.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
