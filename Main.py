from __future__ import annotations

import argparse
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, quote_plus

from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from job_log import (
    BASE_DIR,
    count_todays_applies,
    normalize_job_url,
    record_application,
    remaining_daily_applies,
)
from llm_client import DynamicProfileSummary, LLMSettings, ProfileAnswerer

ENV_PATH = BASE_DIR / ".env"
ENV_EXAMPLE_PATH = BASE_DIR / ".env.example"
PROFILE_EXAMPLE_PATH = BASE_DIR / "profile_memory.example.md"
DEFAULT_CHROME_PROFILE = BASE_DIR / "chrome_profile"
DEFAULT_PROFILE_MEMORY = BASE_DIR / "profile_memory.md"


@dataclass(frozen=True)
class AppConfig:
    email: str
    password: str
    candidate_name: str
    profile_summary: str
    years_of_experience: str
    min_salary_lpa: str
    expected_ctc_lpa: str
    current_city: str
    pincode: str
    notice_period: str
    willing_to_relocate: str
    skill_hints: list[str]
    resume_path: str
    keywords: list[str]
    locations: list[str]
    experience: str
    salary: str
    job_age: int
    title_include_keywords: list[str]
    title_exclude_keywords: list[str]
    company_exclude_keywords: list[str]
    work_modes: list[str]
    preferred_company_types: list[str]
    chrome_driver_path: str
    chrome_profile_dir: str
    headless: bool
    dry_run: bool
    max_pages: int
    max_jobs_per_page: int
    max_applications: int
    daily_apply_limit: int
    wait_seconds: int
    login_timeout_seconds: int
    chrome_debugging_port: int | None
    llm: LLMSettings


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def keyword_in_text(keyword: str, text: str) -> bool:
    """Match keyword in text; short tokens use word boundaries (AI != paid, ML != html)."""
    needle = keyword.lower().strip()
    haystack = text.lower()
    if not needle:
        return False
    if " " in needle or "/" in needle or len(needle) > 3:
        return needle in haystack
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return int(raw.strip())


def load_env_files() -> None:
    if load_dotenv is None:
        raise ImportError(
            "python-dotenv is required. Install with: pip install -r requirements.txt"
        )
    if ENV_EXAMPLE_PATH.exists():
        load_dotenv(ENV_EXAMPLE_PATH, override=False)
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=True)
    elif not ENV_EXAMPLE_PATH.exists():
        raise FileNotFoundError(
            f"Missing {ENV_PATH.name}. Copy .env.example to .env and fill in your values."
        )


def load_config() -> AppConfig:
    load_env_files()

    keywords = split_csv(env_str("KEYWORDS"))
    locations = split_csv(env_str("LOCATIONS"))
    title_include_keywords = split_csv(env_str("TITLE_INCLUDE_KEYWORDS"))
    title_exclude_keywords = split_csv(env_str("TITLE_EXCLUDE_KEYWORDS"))
    company_exclude_keywords = split_csv(env_str("COMPANY_EXCLUDE_KEYWORDS"))
    work_modes = split_csv(env_str("WORK_MODES", "remote, hybrid, onsite"))
    preferred_company_types = split_csv(env_str("PREFERRED_COMPANY_TYPES", "startup, mid-size"))
    skill_hints = [s.lower() for s in split_csv(env_str("SKILL_HINTS"))]
    candidate_name = env_str("CANDIDATE_NAME")
    profile_summary = env_str("PROFILE_SUMMARY")
    years_of_experience = env_str("YEARS_OF_EXPERIENCE")
    min_salary_lpa = env_str("MIN_SALARY_LPA")
    expected_ctc_lpa = env_str("EXPECTED_CTC_LPA") or min_salary_lpa
    current_city = env_str("CURRENT_CITY")
    pincode = env_str("PINCODE")
    notice_period = env_str("NOTICE_PERIOD", "Will confirm")
    willing_to_relocate = env_str("WILLING_TO_RELOCATE", "Yes") or "Yes"

    resume_raw = env_str("RESUME_PATH")
    resume_path = ""
    if resume_raw:
        resume_file = Path(resume_raw)
        if not resume_file.is_absolute():
            resume_file = BASE_DIR / resume_file
        resume_path = str(resume_file)

    missing = []
    if not keywords:
        missing.append("KEYWORDS")
    if not locations:
        missing.append("LOCATIONS")
    if missing:
        raise ValueError(f"Missing required .env values: {', '.join(missing)}")

    profile_dir = env_str("CHROME_PROFILE_DIR") or str(DEFAULT_CHROME_PROFILE)
    debugging_port_val = env_str("CHROME_DEBUGGING_PORT")
    chrome_debugging_port = int(debugging_port_val) if debugging_port_val.isdigit() else None

    memory_path = Path(env_str("PROFILE_MEMORY_PATH", "profile_memory.md"))
    if not memory_path.is_absolute():
        memory_path = BASE_DIR / memory_path

    dynamic_summary = DynamicProfileSummary(
        candidate_name=candidate_name,
        profile_summary=profile_summary,
        years_of_experience=years_of_experience,
        min_salary_lpa=min_salary_lpa,
        expected_ctc_lpa=expected_ctc_lpa,
        current_city=current_city,
        pincode=pincode,
        notice_period=notice_period,
        willing_to_relocate=willing_to_relocate,
        work_modes=tuple(work_modes),
        preferred_company_types=tuple(preferred_company_types),
        preferred_locations=tuple(locations),
        target_keywords=tuple(keywords),
        skill_hints=tuple(skill_hints),
        resume_path=resume_path,
    )

    llm = LLMSettings(
        enabled=env_bool("LLM_ENABLED", True),
        provider=env_str("LLM_PROVIDER", "groq") or "groq",
        profile_memory_path=memory_path,
        max_tokens=env_int("LLM_MAX_TOKENS", 256),
        temperature=float(env_str("LLM_TEMPERATURE", "0.2") or "0.2"),
        groq_api_key=env_str("GROQ_API_KEY"),
        groq_model=env_str("GROQ_MODEL", "llama-3.3-70b-versatile") or "llama-3.3-70b-versatile",
        groq_base_url=env_str("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        or "https://api.groq.com/openai/v1",
        ollama_base_url=env_str("OLLAMA_BASE_URL", "http://localhost:11434")
        or "http://localhost:11434",
        ollama_model=env_str("OLLAMA_MODEL", "llama3.2") or "llama3.2",
        dynamic_summary=dynamic_summary,
    )

    return AppConfig(
        email=env_str("NAUKRI_EMAIL"),
        password=env_str("NAUKRI_PASSWORD"),
        candidate_name=candidate_name,
        profile_summary=profile_summary,
        years_of_experience=years_of_experience,
        min_salary_lpa=min_salary_lpa,
        expected_ctc_lpa=expected_ctc_lpa,
        current_city=current_city,
        pincode=pincode,
        notice_period=notice_period,
        willing_to_relocate=willing_to_relocate,
        skill_hints=skill_hints,
        resume_path=resume_path,
        keywords=keywords,
        locations=locations,
        experience=env_str("EXPERIENCE"),
        salary=env_str("SALARY"),
        job_age=env_int("JOB_AGE", 1),
        title_include_keywords=title_include_keywords,
        title_exclude_keywords=title_exclude_keywords,
        company_exclude_keywords=company_exclude_keywords,
        work_modes=work_modes,
        preferred_company_types=preferred_company_types,
        chrome_driver_path=env_str("CHROME_DRIVER_PATH"),
        chrome_profile_dir=profile_dir,
        headless=env_bool("HEADLESS", False),
        dry_run=env_bool("DRY_RUN", True),
        max_pages=env_int("MAX_PAGES", 3),
        max_jobs_per_page=env_int("MAX_JOBS_PER_PAGE", 10),
        max_applications=env_int("MAX_APPLICATIONS", 5),
        daily_apply_limit=env_int("DAILY_APPLY_LIMIT", 50),
        wait_seconds=env_int("WAIT_SECONDS", 20),
        login_timeout_seconds=env_int("LOGIN_TIMEOUT_SECONDS", 600),
        chrome_debugging_port=chrome_debugging_port,
        llm=llm,
    )


def init_user_files() -> None:
    """Create .env and profile_memory.md from examples if they are missing."""
    created = []
    resumes_dir = BASE_DIR / "resumes"
    resumes_dir.mkdir(exist_ok=True)
    if not ENV_PATH.exists():
        if not ENV_EXAMPLE_PATH.exists():
            raise FileNotFoundError(f"Missing template: {ENV_EXAMPLE_PATH}")
        ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        created.append(ENV_PATH.name)
    if not DEFAULT_PROFILE_MEMORY.exists():
        if not PROFILE_EXAMPLE_PATH.exists():
            raise FileNotFoundError(f"Missing template: {PROFILE_EXAMPLE_PATH}")
        DEFAULT_PROFILE_MEMORY.write_text(
            PROFILE_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        created.append(DEFAULT_PROFILE_MEMORY.name)

    if created:
        print("Created: " + ", ".join(created))
        print("Next: run  python setup_bot.py  (recommended) to set email/password/resume.")
        print("Or edit .env manually, then: python Main.py --check-config")
    else:
        print("Nothing to create — .env and profile_memory.md already exist.")
        print("For guided setup (email, password, resume): python setup_bot.py")
        print(f"Blank profile template: {PROFILE_EXAMPLE_PATH.name}")



class NaukriAutoApply:
    def __init__(self, app_config: AppConfig):
        self.config = app_config
        self.driver = None
        self.wait = None
        self.answerer = ProfileAnswerer(app_config.llm) if app_config.llm.enabled else None
        self.setup_driver()

    def setup_driver(self):
        self.driver = create_chrome_driver(
            headless=self.config.headless,
            chrome_driver_path=self.config.chrome_driver_path,
            chrome_profile_dir=self.config.chrome_profile_dir,
            chrome_debugging_port=self.config.chrome_debugging_port,
        )
        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self.wait = WebDriverWait(self.driver, self.config.wait_seconds)

    def is_session_active(self):
        try:
            self.driver.current_url
            return True
        except (InvalidSessionIdException, WebDriverException):
            return False

    def ensure_session_active(self):
        if self.is_session_active():
            return True

        print("Browser session lost, restarting...")
        try:
            self.driver.quit()
        except WebDriverException:
            pass

        self.setup_driver()
        return False

    def find_element_by_multiple_selectors(self, selectors, timeout=10):
        for selector_type, selector_value in selectors:
            try:
                return WebDriverWait(self.driver, timeout).until(
                    EC.element_to_be_clickable((selector_type, selector_value))
                )
            except TimeoutException:
                try:
                    return WebDriverWait(self.driver, 2).until(
                        EC.presence_of_element_located((selector_type, selector_value))
                    )
                except TimeoutException:
                    continue
        return None

    def safe_click(self, element):
        try:
            self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
            time.sleep(1)
            element.click()
            return True
        except WebDriverException:
            try:
                self.driver.execute_script("arguments[0].click();", element)
                return True
            except WebDriverException:
                return False

    def safe_send_keys(self, element, text):
        try:
            self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
            time.sleep(1)
            element.clear()
            time.sleep(0.5)
            element.send_keys(text)
            return True
        except WebDriverException:
            try:
                self.driver.execute_script("arguments[0].value = '';", element)
                self.driver.execute_script("arguments[0].value = arguments[1];", element, text)
                return True
            except WebDriverException:
                return False

    def is_logged_in(self) -> bool:
        try:
            current_url = self.driver.current_url.lower()
            base_url = current_url.split("?")[0]
            if "nlogin" in base_url or base_url.rstrip("/").endswith("/login"):
                pass

            login_ctas = self.driver.find_elements(
                By.XPATH,
                "//a[@id='login_Layer' or contains(@class,'nI-gNb-lg-rg__login')]",
            )
            if any(el.is_displayed() for el in login_ctas):
                return False

            username_fields = self.driver.find_elements(By.ID, "usernameField")
            if any(el.is_displayed() for el in username_fields):
                return False

            logged_in_markers = [
                (By.CSS_SELECTOR, ".nI-gNb-drawer__icon"),
                (By.CSS_SELECTOR, "img.nI-gNb-icon-img"),
                (By.CSS_SELECTOR, ".nI-gNb-drawer"),
                (By.XPATH, "//a[contains(@href,'/mnjuser/')]"),
                (By.XPATH, "//a[contains(@href,'logout') or contains(text(),'Logout')]"),
                (By.XPATH, "//*[contains(@class,'user-name') or contains(@class,'userName')]"),
            ]
            for selector_type, selector_value in logged_in_markers:
                elements = self.driver.find_elements(selector_type, selector_value)
                if any(el.is_displayed() for el in elements):
                    return True

            cookies = {cookie.get("name", "") for cookie in self.driver.get_cookies()}
            session_cookies = {
                "nauk_at",
                "nauk_rt",
                "SESNcookie",
                "NIMAIN",
                "_t_ds",
                "UN",
                "SERVERID",
            }
            if cookies & session_cookies:
                if "nlogin" not in base_url and not base_url.rstrip("/").endswith("/login"):
                    return True
        except WebDriverException:
            return False
        return False

    def wait_for_manual_login(self, timeout_seconds: int | None = None) -> bool:
        import threading

        timeout_seconds = timeout_seconds or self.config.login_timeout_seconds
        print("Waiting for you to finish login in the browser...")
        print("Use email/password or Google - whatever you prefer.")
        print("When done, press Enter in this terminal (or wait for auto-detect).")
        print(f"Session will be cached in: {self.config.chrome_profile_dir}")

        enter_pressed = threading.Event()

        def watch_enter():
            try:
                input()
            except EOFError:
                return
            enter_pressed.set()

        threading.Thread(target=watch_enter, daemon=True).start()
        start_time = time.time()
        main_window = self.driver.current_window_handle

        while time.time() - start_time < timeout_seconds:
            if not self.ensure_session_active():
                print("Session lost during login. Restart the script.")
                return False

            try:
                handles = self.driver.window_handles
                for handle in handles:
                    self.driver.switch_to.window(handle)
                    if "naukri.com" in self.driver.current_url.lower():
                        main_window = handle
                        break
                else:
                    if main_window in handles:
                        self.driver.switch_to.window(main_window)

                if self.is_logged_in():
                    print("Login successful. Session cached for next runs.")
                    time.sleep(2)
                    return True

                if enter_pressed.is_set():
                    print("Checking login status after confirmation...")
                    self.driver.get("https://www.naukri.com/")
                    time.sleep(3)
                    if self.is_logged_in():
                        print("Login successful. Session cached for next runs.")
                        return True
                    print("Still not logged in. Finish login in Chrome, then press Enter again.")
                    enter_pressed.clear()
                    threading.Thread(target=watch_enter, daemon=True).start()
            except WebDriverException:
                pass

            time.sleep(2)

        print("Login timeout. Log in manually and run again if needed.")
        return False

    def auto_login_with_credentials(self) -> bool:
        """Fill Naukri email/password from .env and submit. Returns True if logged in."""
        email = (self.config.email or "").strip()
        password = (self.config.password or "").strip()
        if not email or not password:
            print("NAUKRI_EMAIL / NAUKRI_PASSWORD not set in .env — cannot auto-login.")
            return False

        print(f"Attempting auto-login as {email}...")
        self.driver.get("https://www.naukri.com/nlogin/login")
        time.sleep(3)

        # Dismiss cookie / overlay if present
        for selector in (
            "//button[contains(text(),'Accept')]",
            "//button[contains(text(),'Got it')]",
            ".btn-1",
        ):
            try:
                for el in (
                    self.driver.find_elements(By.XPATH, selector)
                    if selector.startswith("//")
                    else self.driver.find_elements(By.CSS_SELECTOR, selector)
                ):
                    if el.is_displayed():
                        self.safe_click(el)
                        time.sleep(0.5)
            except WebDriverException:
                pass

        username = self.find_element_by_multiple_selectors(
            [
                (By.ID, "usernameField"),
                (By.NAME, "email"),
                (By.CSS_SELECTOR, "input[type='text'][placeholder*='Email']"),
                (By.CSS_SELECTOR, "input[placeholder*='Email ID']"),
                (By.XPATH, "//input[@type='text' or @type='email']"),
            ],
            timeout=8,
        )
        password_field = self.find_element_by_multiple_selectors(
            [
                (By.ID, "passwordField"),
                (By.NAME, "password"),
                (By.CSS_SELECTOR, "input[type='password']"),
            ],
            timeout=5,
        )
        if not username or not password_field:
            print("Login form fields not found.")
            return False

        if not self.safe_send_keys(username, email):
            print("Could not enter email.")
            return False
        if not self.safe_send_keys(password_field, password):
            print("Could not enter password.")
            return False

        login_button = self.find_element_by_multiple_selectors(
            [
                (By.XPATH, "//button[@type='submit']"),
                (By.XPATH, "//button[contains(text(),'Login') or contains(text(),'Sign in')]"),
                (By.CSS_SELECTOR, "button[type='submit']"),
                (By.CSS_SELECTOR, ".blue-btn"),
                (By.XPATH, "//button[contains(@class,'login')]"),
            ],
            timeout=5,
        )
        if not login_button or not self.safe_click(login_button):
            try:
                password_field.send_keys(Keys.RETURN)
            except WebDriverException:
                print("Could not submit login form.")
                return False

        # Wait for redirect / logged-in state (captcha may block)
        deadline = time.time() + 45
        while time.time() < deadline:
            time.sleep(2)
            if self.is_logged_in():
                print("Auto-login successful. Session cached for next runs.")
                return True
            # Captcha / OTP / extra verify page
            page = (self.driver.page_source or "").lower()
            if any(token in page for token in ("captcha", "otp", "verify mobile", "enter otp")):
                print("Naukri needs extra verification (captcha/OTP). Complete it in Chrome...")
                return self.wait_for_manual_login(timeout_seconds=300)

        print("Auto-login did not complete in time.")
        return False

    def login(self):
        print("Opening Naukri homepage to check cached session...")
        self.driver.get("https://www.naukri.com/")
        time.sleep(3)

        if self.is_logged_in():
            print("Already logged in via cached Chrome profile.")
            return True

        if self.config.email and self.config.password:
            if self.auto_login_with_credentials():
                return True
            print("Auto-login failed — falling back to manual login.")
        else:
            print("No NAUKRI_PASSWORD in .env — opening login page for manual sign-in.")

        self.driver.get("https://www.naukri.com/nlogin/login")
        time.sleep(2)
        print("Enter your Naukri credentials in Chrome if needed.")
        print("After a successful login, this profile is reused automatically.")
        return self.wait_for_manual_login()

    def search_jobs(self):
        if not self.ensure_session_active():
            print("Browser session not active. Restart the script.")
            return

        print("Starting job search...")
        for keyword in self.config.keywords:
            for location in self.config.locations:
                try:
                    if not self.ensure_session_active():
                        print("Session lost during job search.")
                        return

                    print(f"Searching for: {keyword} in {location}")
                    if self.search_by_url(keyword, location) or self.manual_search(keyword, location):
                        self.apply_filters()
                        self.process_job_listings()
                    else:
                        print(f"Could not search for {keyword} in {location}")
                except WebDriverException as error:
                    print(f"Error searching for {keyword} in {location}: {error}")

    def search_by_url(self, keyword, location):
        keyword_encoded = quote(keyword)
        location_encoded = quote(location)
        experience_encoded = quote_plus(self.config.experience)
        search_urls = [
            f"https://www.naukri.com/{keyword_encoded}-jobs-in-{location_encoded}?experience={experience_encoded}&jobAge={self.config.job_age}",
            f"https://www.naukri.com/jobs?k={keyword_encoded}&l={location_encoded}&experience={experience_encoded}&jobAge={self.config.job_age}",
            f"https://www.naukri.com/{keyword_encoded}-jobs?l={location_encoded}&experience={experience_encoded}&jobAge={self.config.job_age}",
        ]

        for search_url in search_urls:
            try:
                print(f"Trying URL: {search_url}")
                self.driver.get(search_url)
                time.sleep(5)

                current_url = self.driver.current_url
                page_source = self.driver.page_source.lower()
                has_results = "job" in current_url and any(
                    marker in page_source for marker in ("results", "apply", "position")
                )
                if has_results:
                    print("Search successful via direct URL.")
                    return True
            except WebDriverException as error:
                print(f"Error with URL {search_url}: {error}")

        return False

    def manual_search(self, keyword, location):
        try:
            self.driver.get("https://www.naukri.com/")
            time.sleep(3)

            search_selectors = [
                (By.ID, "qsb-keyword-sugg"),
                (By.XPATH, "//input[@placeholder='Enter keyword / designation / companies']"),
                (By.CSS_SELECTOR, "input[data-cy='keyword-input']"),
                (By.CSS_SELECTOR, ".suggestor-input"),
                (By.XPATH, "//input[contains(@id, 'keyword')]"),
                (By.XPATH, "//input[@name='qp']"),
                (By.XPATH, "//input[contains(@placeholder, 'keyword')]"),
            ]
            search_field = self.find_element_by_multiple_selectors(search_selectors, timeout=10)
            if not search_field or not self.safe_send_keys(search_field, keyword):
                print("Could not enter keyword.")
                return False

            location_selectors = [
                (By.ID, "qsb-location-sugg"),
                (By.XPATH, "//input[@placeholder='Enter location']"),
                (By.CSS_SELECTOR, "input[data-cy='location-input']"),
                (By.XPATH, "//input[contains(@id, 'location')]"),
                (By.XPATH, "//input[@name='ql']"),
                (By.XPATH, "//input[contains(@placeholder, 'location')]"),
            ]
            location_field = self.find_element_by_multiple_selectors(location_selectors, timeout=5)
            if location_field:
                self.safe_send_keys(location_field, location)

            search_button_selectors = [
                (By.XPATH, "//button[contains(text(),'Search')]"),
                (By.CSS_SELECTOR, "button[data-cy='search-button']"),
                (By.XPATH, "//button[@type='submit']"),
                (By.XPATH, "//input[@type='submit']"),
                (By.CSS_SELECTOR, ".qsb-search-button"),
                (By.CSS_SELECTOR, ".search-btn"),
            ]
            search_button = self.find_element_by_multiple_selectors(search_button_selectors, timeout=5)
            if search_button and self.safe_click(search_button):
                time.sleep(5)
                return True

            search_field.send_keys(Keys.RETURN)
            time.sleep(5)
            return True
        except WebDriverException as error:
            print(f"Error in manual search: {error}")
            return False

    def apply_filters(self):
        if not self.ensure_session_active():
            return

        print("Attempting to apply filters...")
        time.sleep(3)
        self.apply_date_filter()
        self.apply_named_filter("Experience", self.config.experience)
        self.apply_named_filter("Salary", self.config.salary)

    def apply_date_filter(self):
        date_selectors = [
            (By.XPATH, "//span[contains(text(),'Freshness')]"),
            (By.XPATH, "//div[contains(text(),'Freshness')]"),
            (By.XPATH, "//h3[contains(text(),'Freshness')]"),
            (By.XPATH, "//span[contains(text(),'Date Posted')]"),
            (By.XPATH, "//div[contains(text(),'Date Posted')]"),
            (By.CSS_SELECTOR, "[data-cy='date-filter']"),
            (By.XPATH, "//button[contains(@class, 'filter') and contains(text(), 'Date')]"),
            (By.XPATH, "//h3[contains(text(), 'Date')]"),
            (By.XPATH, "//span[text()='Date']"),
        ]
        date_dropdown = self.find_element_by_multiple_selectors(date_selectors, timeout=3)
        if not date_dropdown or not self.safe_click(date_dropdown):
            print("Freshness/Date filter not found; skipping.")
            return

        time.sleep(2)
        age = self.config.job_age
        if age == 1:
            option_texts = ["24 hours", "Last 24 hours", "Today", "1 day"]
        elif age <= 3:
            option_texts = ["3 days", "Last 3 days"]
        elif age <= 7:
            option_texts = ["7 days", "Last 7 days"]
        elif age <= 15:
            option_texts = ["15 days", "Last 15 days"]
        else:
            option_texts = [f"{age} days", f"Last {age} days"]

        or_clause = " or ".join([f"contains(text(),'{t}')" for t in option_texts])
        option_selector = (
            f"//li[{or_clause}]"
            f"|//div[{or_clause}]"
            f"|//label[{or_clause}]"
            f"|//span[{or_clause}]"
        )
        for option in self.driver.find_elements(By.XPATH, option_selector):
            if self.safe_click(option):
                print(f"Applied {age}-day(s) freshness/date filter.")
                time.sleep(2)
                return

        print(f"Could not find {age}-day(s) filter option.")

    def apply_named_filter(self, label, value):
        if not value:
            return

        selectors = [
            (By.XPATH, f"//span[contains(text(),'{label}')]"),
            (By.XPATH, f"//div[contains(text(),'{label}')]"),
            (By.CSS_SELECTOR, f"[data-cy='{label.lower()}-filter']"),
            (By.XPATH, f"//button[contains(@class, 'filter') and contains(text(), '{label}')]"),
        ]
        dropdown = self.find_element_by_multiple_selectors(selectors, timeout=3)
        if not dropdown or not self.safe_click(dropdown):
            print(f"{label} filter not found; skipping.")
            return

        time.sleep(2)
        try:
            option = self.driver.find_element(By.XPATH, f"//li[contains(text(),'{value}')]")
            if self.safe_click(option):
                print(f"Applied {label.lower()} filter.")
                time.sleep(2)
        except WebDriverException:
            print(f"{label} filter option not found.")

    def effective_apply_cap(self) -> int:
        """Per-run cap, also clamped by Naukri daily limit remaining (from today's log)."""
        remaining = remaining_daily_applies(self.config.daily_apply_limit)
        per_run = self.config.max_applications
        cap = min(per_run, remaining) if self.config.daily_apply_limit > 0 else per_run
        used = count_todays_applies()
        print(
            f"Apply budget: per-run={per_run}, daily_limit={self.config.daily_apply_limit}, "
            f"already_today={used}, remaining_today={remaining}, this_run_cap={cap}"
        )
        return cap

    def page_shows_daily_limit(self) -> bool:
        """Detect Naukri UI messages when the ~50/day quota is exhausted."""
        try:
            text = (self.driver.page_source or "").lower()
        except WebDriverException:
            return False
        markers = (
            "daily limit",
            "daily quota",
            "reached the limit",
            "reached your limit",
            "application limit",
            "quota of applying",
            "exhausted your daily",
            "you have exceeded",
            "limit of 50",
            "apply limit",
        )
        return any(marker in text for marker in markers)

    def process_job_listings(self):
        if not self.ensure_session_active():
            return

        apply_cap = self.effective_apply_cap()
        if apply_cap <= 0:
            used = count_todays_applies()
            print(
                f"Daily Naukri apply limit reached "
                f"({used}/{self.config.daily_apply_limit} today). Stopping."
            )
            log_path = record_application(
                "N/A",
                "Naukri",
                job_url="",
                status="limit",
                reason=(
                    f"daily apply limit exhausted "
                    f"({used}/{self.config.daily_apply_limit} in Applied log)"
                ),
            )
            print(f"Recorded in: {log_path.relative_to(BASE_DIR)}")
            return

        page = 1
        applied_count = 0
        while page <= self.config.max_pages:
            if not self.ensure_session_active():
                print("Session lost during job processing.")
                return

            print(f"Processing page {page}...")
            job_listings = self.find_job_listings()
            if not job_listings:
                print("No job listings found on this page.")
                break

            print(f"Found {len(job_listings)} job listings.")
            for index, job in enumerate(job_listings[: self.config.max_jobs_per_page], start=1):
                applied_count += self.process_single_job(job, index)
                if self.page_shows_daily_limit():
                    print("Naukri daily apply limit message detected on page. Stopping.")
                    log_path = record_application(
                        "N/A",
                        "Naukri",
                        job_url="",
                        status="limit",
                        reason="naukri daily apply limit reached (~50/day)",
                    )
                    print(f"Recorded in: {log_path.relative_to(BASE_DIR)}")
                    print(f"Total applications submitted this run: {applied_count}")
                    return
                if applied_count >= apply_cap:
                    print(f"Reached application limit for this run: {applied_count}/{apply_cap}.")
                    return

            if not self.go_to_next_page():
                break
            page += 1

        print(f"Total applications submitted: {applied_count}")

    def find_job_listings(self):
        job_selectors = [
            (By.XPATH, "//article[contains(@class,'jobTuple')]"),
            (By.CSS_SELECTOR, ".jobTuple"),
            (By.CSS_SELECTOR, ".srp-jobtuple-wrapper"),
            (By.CSS_SELECTOR, "[data-job-id]"),
            (By.XPATH, "//div[contains(@class, 'job-tile')]"),
            (By.XPATH, "//div[@class='row'][.//a[contains(@class,'title')]]"),
        ]
        for selector_type, selector_value in job_selectors:
            try:
                job_listings = self.driver.find_elements(selector_type, selector_value)
                if job_listings:
                    print(f"Found jobs using selector: {selector_value}")
                    return job_listings
            except WebDriverException:
                continue
        return []

    def title_skip_reason(self, job_title: str) -> str | None:
        """Return skip reason if title fails filters; None if OK to open."""
        if not job_title:
            return "empty title"

        for exclude_keyword in self.config.title_exclude_keywords:
            if keyword_in_text(exclude_keyword, job_title):
                return f"title exclude keyword: {exclude_keyword}"

        if self.config.title_include_keywords:
            for include_keyword in self.config.title_include_keywords:
                if keyword_in_text(include_keyword, job_title):
                    return None
            return "title does not match include filters"

        return None

    def is_title_matching(self, job_title: str) -> bool:
        return self.title_skip_reason(job_title) is None

    def company_skip_reason(self, company: str) -> str | None:
        """Return skip reason if company is excluded; None if allowed."""
        if not company or company == "Unknown":
            return None
        for exclude_keyword in self.config.company_exclude_keywords:
            if keyword_in_text(exclude_keyword, company):
                return f"excluded company / large enterprise: {exclude_keyword}"
        return None

    def is_company_allowed(self, company: str) -> bool:
        return self.company_skip_reason(company) is None

    def _job_href(self, job_link) -> str:
        if job_link is None:
            return ""
        try:
            for attr in ("href", "data-href", "data-url", "data-job-url"):
                value = (job_link.get_attribute(attr) or "").strip()
                normalized = normalize_job_url(value)
                if normalized:
                    return normalized
        except WebDriverException:
            pass
        return ""

    def extract_job_url(self, job, job_link=None) -> str:
        """Best-effort job URL from the title link or the listing card."""
        url = self._job_href(job_link)
        if url:
            return url
        try:
            candidates = job.find_elements(By.CSS_SELECTOR, "a[href*='job-listings'], a[href*='/job'], a.title, a[href]")
            for anchor in candidates:
                href = normalize_job_url((anchor.get_attribute("href") or "").strip())
                if href and "naukri.com" in href and ("job" in href or "job-listings" in href):
                    return href
            for attr in ("data-job-id", "data-jobid"):
                job_id = (job.get_attribute(attr) or "").strip()
                if job_id.isdigit():
                    return f"https://www.naukri.com/job-listings-{job_id}"
        except WebDriverException:
            pass
        return ""

    def process_single_job(self, job, index):
        try:
            self.driver.execute_script("arguments[0].scrollIntoView(true);", job)
            time.sleep(1)

            job_link, job_title = self.extract_job_link(job)
            company = self.extract_company(job)
            job_url = self.extract_job_url(job, job_link)

            if not job_link:
                reason = "no clickable job link"
                print(f"No clickable link found for job {index}: {job_title} at {company}")
                record_application(job_title, company, job_url=job_url, status="skipped", reason=reason)
                return 0

            title_reason = self.title_skip_reason(job_title)
            if title_reason:
                print(f"Skipping job {index}: '{job_title}' at {company} ({title_reason})")
                print(f"  link: {job_url or '(no link)'}")
                record_application(job_title, company, job_url=job_url, status="skipped", reason=title_reason)
                return 0

            company_reason = self.company_skip_reason(company)
            if company_reason:
                print(f"Skipping job {index}: '{job_title}' at {company} ({company_reason})")
                print(f"  link: {job_url or '(no link)'}")
                record_application(job_title, company, job_url=job_url, status="skipped", reason=company_reason)
                return 0

            print(f"Opening job details for: {job_title} at {company}")
            main_window = self.driver.current_window_handle
            opened = self.open_job_in_new_tab(job_link)
            if not opened:
                reason = "could not open job details"
                print(f"Could not open job details for: {job_title}")
                record_application(job_title, company, job_url=job_url, status="skipped", reason=reason)
                return 0

            # Prefer the opened tab URL (canonical) when available
            try:
                opened_url = normalize_job_url(self.driver.current_url or "")
                if opened_url:
                    job_url = opened_url
            except WebDriverException:
                pass

            applied = self.try_apply(job_title, company, job_url=job_url)
            self.close_extra_tabs(main_window)
            return applied
        except WebDriverException as error:
            print(f"Error processing job {index}: {error}")
            try:
                record_application(
                    "Unknown",
                    "Unknown",
                    status="skipped",
                    reason=f"error: {error}",
                )
            except OSError:
                pass
            self.close_extra_tabs(self.driver.window_handles[0])
            return 0

    def extract_job_link(self, job):
        title_link_selectors = [
            "a.title",
            ".title a",
            "[data-cy='job-title']",
            "a[title]",
            ".jobTupleHeader a",
            ".row1 a",
            "h3 a",
            ".jobtitle a",
        ]
        for selector in title_link_selectors:
            try:
                job_links = job.find_elements(By.CSS_SELECTOR, selector)
                if job_links:
                    job_link = job_links[0]
                    job_title = job_link.text or job_link.get_attribute("title") or "Unknown"
                    return job_link, job_title
            except WebDriverException:
                continue
        return None, "Unknown"

    def extract_company(self, job):
        company_selectors = [
            ".compName",
            ".company-name",
            "[data-cy='company-name']",
            ".subTitle a",
            ".row2 .ellipsis",
            ".org a",
            ".comp-name",
            "a.comp-name",
        ]
        for selector in company_selectors:
            try:
                company = job.find_element(By.CSS_SELECTOR, selector).text
                if company:
                    return company
            except WebDriverException:
                continue
        return "Unknown"

    def open_job_in_new_tab(self, job_link):
        try:
            self.driver.execute_script("arguments[0].setAttribute('target', '_blank');", job_link)
            if not self.safe_click(job_link):
                job_url = job_link.get_attribute("href")
                if not job_url:
                    return False
                self.driver.execute_script("window.open(arguments[0], '_blank');", job_url)

            time.sleep(2)
            self.wait.until(lambda driver: len(driver.window_handles) > 1)
            self.driver.switch_to.window(self.driver.window_handles[-1])
            time.sleep(3)
            return True
        except WebDriverException:
            return False

    EXTERNAL_APPLY_REASON = (
        "external website - apply on company site (cannot apply via Naukri)"
    )

    def _button_looks_external(self, element) -> bool:
        try:
            parts = [
                element.text or "",
                element.get_attribute("aria-label") or "",
                element.get_attribute("title") or "",
                element.get_attribute("class") or "",
                element.get_attribute("href") or "",
                element.get_attribute("id") or "",
            ]
            blob = " ".join(parts).lower()
        except WebDriverException:
            return False
        markers = (
            "company site",
            "company website",
            "external apply",
            "external-apply",
            "apply externally",
            "apply on company",
            "companysite",
            "company-site",
            "redirect-apply",
            "apply outside",
        )
        return any(marker in blob for marker in markers)

    def detect_external_apply_reason(self) -> str | None:
        """If this job only allows company-website apply, return a clear skip reason."""
        external_selectors = [
            (By.XPATH, "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'company site')]"),
            (By.XPATH, "//a[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'company site')]"),
            (By.XPATH, "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'company website')]"),
            (By.XPATH, "//a[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'company website')]"),
            (By.XPATH, "//*[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply on company')]"),
            (By.XPATH, "//*[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'external apply')]"),
            (By.CSS_SELECTOR, "a[href*='companySite'], a[href*='company-site'], a[href*='externalApply']"),
            (By.CSS_SELECTOR, "button[class*='external'], a[class*='external'], [class*='company-site'], [class*='companySite']"),
            (By.CSS_SELECTOR, "#company-site-button, [data-cy*='external'], [data-test*='external']"),
        ]
        try:
            for by, selector in external_selectors:
                for element in self.driver.find_elements(by, selector):
                    try:
                        if element.is_displayed():
                            return self.EXTERNAL_APPLY_REASON
                    except WebDriverException:
                        continue

            # Visible apply controls whose text/class points outside Naukri
            apply_like = self.driver.find_elements(
                By.XPATH,
                "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply')]"
                "|//a[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply')]"
                "|//span[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'apply')]",
            )
            for element in apply_like:
                try:
                    if element.is_displayed() and self._button_looks_external(element):
                        return self.EXTERNAL_APPLY_REASON
                except WebDriverException:
                    continue

            page_text = (self.driver.page_source or "").lower()
            if any(
                phrase in page_text
                for phrase in (
                    "apply on company site",
                    "apply on company website",
                    "apply on the company website",
                    "this job is from a company website",
                    "you will be redirected to the company",
                )
            ):
                # Only treat as external if there is no clear Naukri Easy Apply control
                naukri_apply = self.driver.find_elements(
                    By.XPATH,
                    "//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'easy apply')]"
                    "|//*[@id='apply-button']"
                    "|//button[@data-cy='apply-button']",
                )
                if not any(self._is_visible_enabled(el) for el in naukri_apply):
                    return self.EXTERNAL_APPLY_REASON
        except WebDriverException:
            pass
        return None

    def _is_visible_enabled(self, element) -> bool:
        try:
            return element.is_displayed() and element.is_enabled()
        except WebDriverException:
            return False

    def try_apply(self, job_title, company, job_url: str = ""):
        apply_selector = (
            "//button[contains(text(),'Apply') or contains(text(),'Easy Apply')]"
            "|//a[contains(text(),'Apply')]"
            "|//span[contains(text(),'Apply')]"
            "|//div[contains(@class,'apply')]//button"
            "|//button[contains(@class,'apply')]"
            "|//*[@id='apply-button']"
            "|//button[@data-cy='apply-button']"
            "|//a[contains(@class,'apply')]"
        )
        try:
            current = normalize_job_url(self.driver.current_url or "")
            if current:
                job_url = current
        except WebDriverException:
            pass
        job_url = normalize_job_url(job_url)

        # Detect company-website / external apply first and log a clear reason
        external_reason = self.detect_external_apply_reason()
        if external_reason:
            print(f"Skipping job (external website): '{job_title}' at {company}")
            print(f"  reason: {external_reason}")
            print(f"  link: {job_url or '(no link)'}")
            record_application(
                job_title,
                company,
                job_url=job_url,
                status="external",
                reason=external_reason,
            )
            return 0

        for apply_button in self.driver.find_elements(By.XPATH, apply_selector):
            if not self._is_visible_enabled(apply_button):
                continue

            if self._button_looks_external(apply_button):
                print(f"Skipping job (external website): '{job_title}' at {company}")
                print(f"  reason: {self.EXTERNAL_APPLY_REASON}")
                print(f"  link: {job_url or '(no link)'}")
                record_application(
                    job_title,
                    company,
                    job_url=job_url,
                    status="external",
                    reason=self.EXTERNAL_APPLY_REASON,
                )
                return 0

            if self.config.dry_run:
                log_path = record_application(
                    job_title, company, job_url=job_url, status="dry_run", reason="dry_run mode"
                )
                print(f"Dry run: would apply to {job_title} at {company}")
                print(f"Recorded in: {log_path.relative_to(BASE_DIR)}")
                return 0

            if self.safe_click(apply_button):
                self.handle_apply_confirmation(job_title, company)
                log_path = record_application(
                    job_title, company, job_url=job_url, status="applied", reason="naukri apply"
                )
                print(f"Successfully applied to: {job_title} at {company}")
                print(f"Recorded in: {log_path.relative_to(BASE_DIR)}")
                time.sleep(3)
                return 1

        reason = "no apply button found"
        print(f"No apply button found for: {job_title} at {company} ({reason})")
        print(f"  link: {job_url or '(no link)'}")
        record_application(job_title, company, job_url=job_url, status="skipped", reason=reason)
        return 0

    def _question_label_for_input(self, element) -> str:
        try:
            label_text = self.driver.execute_script(
                """
                const el = arguments[0];
                if (el.id) {
                  const byFor = document.querySelector(`label[for="${el.id}"]`);
                  if (byFor && byFor.innerText) return byFor.innerText.trim();
                }
                const aria = el.getAttribute('aria-label');
                if (aria) return aria.trim();
                const placeholder = el.getAttribute('placeholder');
                if (placeholder) return placeholder.trim();
                const name = el.getAttribute('name');
                if (name) return name.trim();
                let node = el.parentElement;
                for (let i = 0; i < 4 && node; i++) {
                  const label = node.querySelector('label, .label, .question, .chatBot-question, p, span');
                  if (label && label.innerText && label.innerText.trim().length > 2) {
                    return label.innerText.trim();
                  }
                  node = node.parentElement;
                }
                return '';
                """,
                element,
            )
            return (label_text or "").strip()
        except WebDriverException:
            return (
                element.get_attribute("aria-label")
                or element.get_attribute("placeholder")
                or element.get_attribute("name")
                or ""
            ).strip()

    def is_screening_ui_present(self) -> bool:
        """True when Naukri chatbot / questionnaire UI is visible."""
        screening_selectors = [
            ".chatbot_MessageContainer",
            ".chatbot_InputBoxWrapper",
            ".chatbot_inputWrapper",
            "div.textArea[contenteditable='true']",
            ".botMsg.msg",
            ".chatbot-question-text",
            ".sendMsg",
            ".chatbot",
            ".chatBot",
            "[class*='chatbot']",
            "[class*='chatBot']",
            "[class*='ChatBot']",
            ".questionnaire",
            "[class*='questionnaire']",
            "[class*='screening']",
            "//*[contains(@class,'botMsg')]",
            "//*[contains(@class,'chatbot')]",
        ]
        for selector in screening_selectors:
            try:
                if selector.startswith("//"):
                    elements = self.driver.find_elements(By.XPATH, selector)
                else:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if any(el.is_displayed() for el in elements):
                    return True
            except WebDriverException:
                continue
        return False

    def get_latest_chatbot_question(self) -> str:
        """Read the latest bot question from Naukri apply chatbot."""
        selectors = [
            ".botMsg.msg",
            ".chatbot-question-text",
            "[class*='botMsg']",
            "[class*='chatbot-question']",
        ]
        for selector in selectors:
            try:
                messages = self.driver.find_elements(By.CSS_SELECTOR, selector)
                visible = [m for m in messages if m.is_displayed() and (m.text or "").strip()]
                if visible:
                    return visible[-1].text.strip()
            except WebDriverException:
                continue
        return ""

    def fill_chatbot_text(self, answer: str) -> bool:
        """Fill Naukri chatbot input and click Save/Send (enables disabled Save)."""
        input_selectors = [
            "div.textArea[contenteditable='true']",
            ".chatbot_inputText",
            ".chatbot_inputWrapper [contenteditable='true']",
            ".chatbot_InputBoxWrapper [contenteditable='true']",
            "textarea.chatbot_input",
            ".chatbot textarea",
            "[class*='chatbot'] [contenteditable='true']",
        ]
        field = None
        for selector in input_selectors:
            try:
                for el in self.driver.find_elements(By.CSS_SELECTOR, selector):
                    if el.is_displayed():
                        field = el
                        break
            except WebDriverException:
                continue
            if field:
                break
        if not field:
            return False

        try:
            self.driver.execute_script(
                """
                const el = arguments[0];
                const text = arguments[1];
                el.focus();
                el.click();
                if (el.isContentEditable) {
                  el.innerHTML = '';
                  el.innerText = text;
                  el.textContent = text;
                  el.dispatchEvent(new InputEvent('input', {
                    bubbles: true,
                    cancelable: true,
                    inputType: 'insertText',
                    data: text
                  }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                  el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
                } else {
                  el.value = text;
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                }
                """,
                field,
                answer,
            )
            time.sleep(0.6)

            send_selectors = [
                "//button[normalize-space()='Save']",
                "//button[contains(.,'Save')]",
                ".sendMsg",
                "button.sendMsg",
                ".chatbot_InputBoxWrapper button",
                "//button[contains(@class,'send')]",
                "//button[contains(text(),'Submit') or contains(text(),'Send')]",
            ]
            for _ in range(6):
                for selector in send_selectors:
                    try:
                        buttons = (
                            self.driver.find_elements(By.XPATH, selector)
                            if selector.startswith("//")
                            else self.driver.find_elements(By.CSS_SELECTOR, selector)
                        )
                        for btn in buttons:
                            if not btn.is_displayed():
                                continue
                            # Force-enable Save if Naukri left it disabled
                            self.driver.execute_script(
                                "arguments[0].removeAttribute('disabled');"
                                "arguments[0].disabled = false;"
                                "arguments[0].classList.remove('disabled');",
                                btn,
                            )
                            time.sleep(0.15)
                            if self.safe_click(btn):
                                time.sleep(1.2)
                                return True
                            try:
                                self.driver.execute_script("arguments[0].click();", btn)
                                time.sleep(1.2)
                                return True
                            except WebDriverException:
                                continue
                    except WebDriverException:
                        continue
                time.sleep(0.4)

            print("Filled chatbot text but Save/Send click may have failed.")
            return True
        except WebDriverException as error:
            print(f"fill_chatbot_text error: {error}")
            return False

    def answer_chatbot_choice_options(self, question: str, job_context: str) -> bool:
        """Click radio / chip options in Naukri chatbot using LLM/fallback to pick one."""
        option_selectors = [
            ".chatbot_MessageContainer label",
            ".chatbot label",
            "[class*='chatbot'] label",
            ".chatbot_MessageContainer [role='radio']",
            ".chatbot_MessageContainer input[type='radio']",
            ".chips li",
            ".chip",
            "//div[contains(@class,'chatbot')]//li",
            "//div[contains(@class,'chatbot')]//button[not(contains(@class,'send')) and not(contains(.,'Save'))]",
        ]
        options = []
        option_elements = []
        for selector in option_selectors:
            try:
                elements = (
                    self.driver.find_elements(By.XPATH, selector)
                    if selector.startswith("//")
                    else self.driver.find_elements(By.CSS_SELECTOR, selector)
                )
                for el in elements:
                    if not el.is_displayed():
                        continue
                    text = (el.text or "").strip()
                    if not text or len(text) > 120:
                        continue
                    lowered = text.lower()
                    if any(skip in lowered for skip in ("send", "save", "skip", "close", "cancel")):
                        continue
                    if text not in options:
                        options.append(text)
                        option_elements.append(el)
            except WebDriverException:
                continue

        if not options or not self.answerer:
            return False

        prompt = (
            f"{question}\n\nChoose exactly one option from this list and reply with ONLY that option text:\n"
            + "\n".join(f"- {opt}" for opt in options)
        )
        try:
            choice = self.answerer.answer(prompt, job_context=job_context).strip()
        except Exception as error:
            print(f"LLM choice failed: {error}")
            choice = options[0]

        choice_lower = choice.lower()
        for text, el in zip(options, option_elements):
            if text.lower() == choice_lower or text.lower() in choice_lower or choice_lower in text.lower():
                if self.safe_click(el):
                    print(f"Chose option: {text}")
                    time.sleep(1)
                    return True
        # Fallback: first option
        if self.safe_click(option_elements[0]):
            print(f"Chose fallback option: {options[0]}")
            time.sleep(1)
            return True
        return False

    def _is_chatbot_intro(self, question: str) -> bool:
        q = (question or "").lower()
        if not q:
            return True
        if "?" in q and any(
            tok in q
            for tok in ("how many", "have you", "do you", "are you", "what is", "years", "ctc", "notice")
        ):
            return False
        return any(
            phrase in q
            for phrase in (
                "thank you for showing interest",
                "kindly answer all",
                "successfully apply",
                "recruiter's questions",
            )
        )

    def answer_naukri_chatbot(self, job_title: str, company: str, max_rounds: int = 12) -> int:
        """Answer Naukri apply chatbot Q&A rounds; always try to Save after each answer."""
        if not self.answerer:
            return 0

        answered = 0
        job_context = f"{job_title} at {company}"
        seen_questions: set[str] = set()

        for _ in range(max_rounds):
            if not self.is_screening_ui_present():
                break

            question = self.get_latest_chatbot_question()
            if not question:
                time.sleep(1.5)
                question = self.get_latest_chatbot_question()
                if not question:
                    break

            if self._is_chatbot_intro(question):
                print("Chatbot intro detected — waiting for a real question...")
                time.sleep(2)
                continue

            if question in seen_questions:
                # Same question still showing — maybe Save didn't stick; try once more then stop
                break
            seen_questions.add(question)

            print(f"Chatbot question: {question[:120]}")

            if self.answer_chatbot_choice_options(question, job_context):
                answered += 1
                time.sleep(1.5)
                continue

            try:
                answer = self.answerer.answer(question, job_context=job_context)
            except Exception as error:
                print(f"Answer failed: {error}; using 0/No fallback.")
                answer = "0" if "year" in question.lower() else "No"

            if not answer:
                answer = "0" if "year" in question.lower() else "No"

            if self.fill_chatbot_text(answer):
                answered += 1
                print(f"Answered & Save clicked: {answer[:100]}")
                time.sleep(1.8)
            else:
                print("Could not find chatbot input to fill.")
                break

        return answered

    def is_field_in_screening_container(self, element) -> bool:
        try:
            return bool(
                self.driver.execute_script(
                    """
                    const el = arguments[0];
                    let node = el;
                    for (let i = 0; i < 8 && node; i++) {
                      const cls = (node.className || '').toString().toLowerCase();
                      const id = (node.id || '').toString().toLowerCase();
                      if (
                        cls.includes('chatbot') || cls.includes('chat-bot') ||
                        cls.includes('questionnaire') || cls.includes('screening') ||
                        cls.includes('apply-form') || cls.includes('applyform') ||
                        id.includes('chatbot') || id.includes('screening')
                      ) {
                        return true;
                      }
                      node = node.parentElement;
                    }
                    return false;
                    """,
                    element,
                )
            )
        except WebDriverException:
            return False

    def should_use_llm_for_question(self, question: str, element) -> bool:
        """Decide whether this input is a real screening question worth an LLM call."""
        lowered = (question or "").lower().strip()
        if len(lowered) < 3:
            # Contenteditable inside chatbot still counts
            return self.is_field_in_screening_container(element)

        skip_tokens = (
            "search",
            "keyword",
            "password",
            "otp",
            "captcha",
            "username",
            "email id",
            "enter email",
            "filter",
            "location search",
        )
        if any(token in lowered for token in skip_tokens):
            return False

        if self.is_field_in_screening_container(element):
            return True

        screening_tokens = (
            "notice",
            "ctc",
            "salary",
            "expected",
            "current ctc",
            "experience",
            "relocat",
            "join",
            "availability",
            "reason for",
            "why do you",
            "strength",
            "weakness",
            "education",
            "qualification",
            "degree",
            "are you",
            "do you have",
            "have you",
            "willing",
            "hybrid",
            "remote",
            "onsite",
            "work mode",
            "preferred location",
            "current location",
            "total year",
            "relevant year",
            "skill",
            "project",
            "describe",
            "tell us",
            "briefly",
            "comment",
            "cover letter",
            "message to recruiter",
            "additional information",
        )
        return any(token in lowered for token in screening_tokens)

    def answer_screening_questions(self, job_title: str, company: str) -> int:
        """Use LLM when apply screening / chatbot questions appear."""
        if not self.answerer:
            return 0

        # Prefer Naukri chatbot flow (bot messages + contenteditable + send)
        chatbot_answered = self.answer_naukri_chatbot(job_title, company)
        if chatbot_answered:
            return chatbot_answered

        if not self.is_screening_ui_present():
            print("No screening/chat UI detected — LLM not used.")
            return 0

        print("Screening/chat UI detected — switching to LLM for applicable questions.")
        answered = 0
        skipped = 0
        job_context = f"{job_title} at {company}"
        field_selectors = (
            "div.textArea[contenteditable='true']",
            "textarea:not([disabled])",
            "input[type='text']:not([disabled])",
            "input:not([type]):not([disabled])",
            "input[type='number']:not([disabled])",
            "input[type='tel']:not([disabled])",
            "div[contenteditable='true']",
        )

        for selector in field_selectors:
            try:
                fields = self.driver.find_elements(By.CSS_SELECTOR, selector)
            except WebDriverException:
                continue

            for field in fields:
                try:
                    if not field.is_displayed() or not field.is_enabled():
                        continue
                    existing = (field.get_attribute("value") or field.text or "").strip()
                    if existing:
                        continue

                    question = self._question_label_for_input(field) or self.get_latest_chatbot_question()
                    if not self.should_use_llm_for_question(question, field):
                        skipped += 1
                        continue

                    answer = self.answerer.answer(question or "Please provide a short professional answer.", job_context=job_context)
                    if not answer:
                        continue

                    if self.fill_chatbot_text(answer):
                        answered += 1
                        print(f"LLM answered: {(question or '')[:80]} -> {answer[:80]}")
                        time.sleep(0.8)
                        continue

                    tag = (field.tag_name or "").lower()
                    if tag == "div":
                        self.driver.execute_script(
                            """
                            const el = arguments[0];
                            const text = arguments[1];
                            el.focus();
                            el.innerText = text;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            """,
                            field,
                            answer,
                        )
                        ok = True
                    else:
                        ok = self.safe_send_keys(field, answer)

                    if ok:
                        answered += 1
                        print(f"LLM answered: {(question or '')[:80]} -> {answer[:80]}")
                        time.sleep(0.5)
                except (WebDriverException, RuntimeError, ValueError, FileNotFoundError) as error:
                    print(f"Could not answer screening field: {error}")

        if answered == 0 and skipped:
            print(f"Screening UI present but no LLM-worthy empty questions (skipped {skipped} fields).")
        elif answered == 0:
            print("Screening UI present but no empty questions found for LLM.")
        return answered

    def handle_apply_confirmation(self, job_title: str = "", company: str = ""):
        # Naukri chatbot can take a few seconds to appear after Apply
        time.sleep(3)
        try:
            self.answer_screening_questions(job_title, company)
        except Exception as error:
            print(f"Screening Q&A skipped due to error: {error}")

        confirm_selector = (
            "//button[contains(text(),'Confirm') or contains(text(),'Submit') or contains(text(),'Apply')]"
            "|//button[contains(text(),'Save') or contains(text(),'Continue') or contains(text(),'Next')]"
        )
        for button in self.driver.find_elements(By.XPATH, confirm_selector):
            if button.is_displayed():
                self.safe_click(button)
                time.sleep(1.5)
                try:
                    self.answer_screening_questions(job_title, company)
                except Exception:
                    pass

    def close_extra_tabs(self, main_window):
        try:
            for handle in self.driver.window_handles:
                if handle != main_window:
                    self.driver.switch_to.window(handle)
                    self.driver.close()
            self.driver.switch_to.window(main_window)
            time.sleep(1)
        except WebDriverException:
            pass

    def go_to_next_page(self):
        next_selectors = [
            (By.XPATH, "//a[contains(@class,'fright') and contains(text(),'Next')]"),
            (By.CSS_SELECTOR, ".pagination-next"),
            (By.XPATH, "//a[contains(text(), 'Next')]"),
            (By.CSS_SELECTOR, "[data-cy='next-page']"),
            (By.XPATH, "//a[@aria-label='Next']"),
            (By.CSS_SELECTOR, "a[aria-label='Next']"),
        ]
        next_button = self.find_element_by_multiple_selectors(next_selectors, timeout=5)
        if next_button and next_button.is_enabled() and self.safe_click(next_button):
            time.sleep(5)
            return True

        print("No more pages available.")
        return False

    def recover_from_errors(self):
        try:
            try:
                alert = self.driver.switch_to.alert
                alert.accept()
                print("Alert accepted.")
            except WebDriverException:
                pass

            self.close_extra_tabs(self.driver.window_handles[0])
            return True
        except WebDriverException as error:
            print(f"Error during recovery: {error}")
            return False

    def run(self):
        try:
            if self.login():
                self.search_jobs()
            else:
                print("Login failed. Exiting...")
        except Exception as error:
            print(f"Unexpected error: {error}")
            self.recover_from_errors()
        finally:
            try:
                if self.driver:
                    self.driver.quit()
            except WebDriverException:
                pass


def parse_args():
    parser = argparse.ArgumentParser(description="Run Naukri AutoApply.")
    parser.add_argument(
        "--init",
        action="store_true",
        help="Create .env and profile_memory.md from examples if missing. Prefer: python setup_bot.py",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate .env / profile memory without opening a browser.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Open Naukri login in a headless browser without using account credentials.",
    )
    parser.add_argument(
        "--clear-session",
        action="store_true",
        help="Delete the cached Chrome profile so the next run requires manual login again.",
    )
    parser.add_argument(
        "--ask",
        metavar="QUESTION",
        help="Test LLM + profile memory by answering one question, then exit.",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run IST scheduler (same as: python schedule_bot.py).",
    )
    parser.add_argument(
        "--now",
        "--ignore-schedule",
        dest="ignore_schedule",
        action="store_true",
        help="Run the bot immediately, ignoring IST schedule windows.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="With --schedule: wait for next window, run once, exit.",
    )
    return parser.parse_args()


def clear_chrome_profile_locks(profile_path: Path) -> None:
    for lock_name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        lock_file = profile_path / lock_name
        if lock_file.exists():
            try:
                lock_file.unlink()
            except OSError:
                pass


def create_chrome_driver(headless=False, chrome_driver_path="", chrome_profile_dir="", chrome_debugging_port=None):
    chrome_options = Options()
    profile_path = None

    if chrome_debugging_port:
        chrome_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{chrome_debugging_port}")
    else:
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-background-timer-throttling")
        chrome_options.add_argument("--disable-backgrounding-occluded-windows")
        chrome_options.add_argument("--disable-renderer-backgrounding")
        chrome_options.add_argument("--remote-allow-origins=*")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        if chrome_profile_dir:
            profile_path = Path(chrome_profile_dir).resolve()
            profile_path.mkdir(parents=True, exist_ok=True)
            clear_chrome_profile_locks(profile_path)
            chrome_options.add_argument(f"--user-data-dir={profile_path}")
            chrome_options.add_argument("--profile-directory=Default")

        if headless:
            chrome_options.add_argument("--headless=new")

    def _launch():
        if chrome_driver_path:
            return webdriver.Chrome(service=Service(chrome_driver_path), options=chrome_options)
        return webdriver.Chrome(options=chrome_options)

    try:
        return _launch()
    except WebDriverException as error:
        # Profile often locked by a leftover Chrome/chromedriver from a crashed run
        if profile_path:
            clear_chrome_profile_locks(profile_path)
            time.sleep(1)
            try:
                return _launch()
            except WebDriverException:
                pass
            hint = (
                f"\nCould not open Chrome with profile: {profile_path}\n"
                "Close ALL Chrome windows, then retry.\n"
                "Or: python Main.py --clear-session"
            )
            raise WebDriverException(f"{error}{hint}") from error
        raise


def smoke_test_browser():
    driver = create_chrome_driver(headless=True, chrome_profile_dir="")
    try:
        driver.get("https://www.naukri.com/nlogin/login")
        WebDriverWait(driver, 20).until(lambda browser: "naukri.com" in browser.current_url)
        title = driver.title or "(no title)"
        if "access denied" in title.lower():
            print("Smoke test OK: browser launched, but Naukri blocked the headless request.")
            print(f"Page title: {title}")
        else:
            print(f"Smoke test OK: {title}")
    finally:
        driver.quit()


def main():
    args = parse_args()

    if args.init:
        init_user_files()
        return

    if args.smoke_test:
        smoke_test_browser()
        return

    if args.schedule:
        from schedule_bot import main as schedule_main

        sched_argv: list[str] = []
        if args.ignore_schedule:
            sched_argv.append("--now")
        if args.once:
            sched_argv.append("--once")
        raise SystemExit(schedule_main(sched_argv))

    # --now / --ignore-schedule alone: run bot immediately (default path below)
    if args.ignore_schedule:
        print("Running now (schedule windows ignored).")

    app_config = load_config()

    if args.ask:
        if not app_config.llm.enabled:
            print("LLM_ENABLED=false in .env")
            return
        answerer = ProfileAnswerer(app_config.llm)
        print(answerer.answer(args.ask))
        return

    if args.check_config:
        print("Config OK.")
        warnings: list[str] = []
        if app_config.candidate_name:
            print(f"Candidate: {app_config.candidate_name}")
        print(f"Naukri email: {app_config.email or '(not set — REQUIRED for auto-login)'}")
        print(f"Naukri password set: {bool(app_config.password)}  (REQUIRED for auto-login)")
        if not app_config.email:
            warnings.append("NAUKRI_EMAIL is empty — set it in .env (or run setup_bot.py)")
        if not app_config.password:
            warnings.append("NAUKRI_PASSWORD is empty — set it in .env (or run setup_bot.py)")
        if app_config.profile_summary:
            print(f"Profile summary: {app_config.profile_summary}")
        if app_config.years_of_experience:
            print(f"Years of experience: {app_config.years_of_experience}")
        if app_config.min_salary_lpa:
            print(f"Min salary (LPA): {app_config.min_salary_lpa}+")
        if app_config.expected_ctc_lpa:
            print(f"Expected CTC (LPA): {app_config.expected_ctc_lpa}")
        if app_config.current_city:
            print(f"Current city: {app_config.current_city}")
        if app_config.pincode:
            print(f"Pincode: {app_config.pincode}")
        print(f"Notice period: {app_config.notice_period}")
        print(f"Willing to relocate: {app_config.willing_to_relocate}")
        if app_config.skill_hints:
            print(f"Skill hints: {', '.join(app_config.skill_hints[:15])}")
        if app_config.resume_path:
            resume_ok = Path(app_config.resume_path).is_file()
            print(f"Resume: {app_config.resume_path} (exists={resume_ok})")
            if not resume_ok:
                warnings.append(f"RESUME_PATH not found: {app_config.resume_path}")
        else:
            print("Resume: (not set - optional; run setup_bot.py to copy one)")
        print(f"Keywords: {', '.join(app_config.keywords)}")
        print(f"Locations: {', '.join(app_config.locations)}")
        print(f"Job Age (Freshness): {app_config.job_age} day(s)")
        print(f"Title Include Filters: {', '.join(app_config.title_include_keywords)}")
        print(f"Title Exclude Filters: {', '.join(app_config.title_exclude_keywords)}")
        print(f"Company Exclude Filters: {', '.join(app_config.company_exclude_keywords)}")
        print(f"Preferred company types: {', '.join(app_config.preferred_company_types)}")
        print(f"Work modes: {', '.join(app_config.work_modes)}")
        print(f"Dry run: {app_config.dry_run}")
        print(f"Chrome profile: {app_config.chrome_profile_dir}")
        print(f"LLM enabled: {app_config.llm.enabled}")
        print(f"LLM provider: {app_config.llm.provider}")
        print(f"Profile memory: {app_config.llm.profile_memory_path}")
        memory_ok = app_config.llm.profile_memory_path.is_file()
        print(f"Profile memory exists: {memory_ok}")
        if not memory_ok:
            warnings.append("Profile memory missing — run setup_bot.py or copy profile_memory.example.md")
        if app_config.llm.enabled and app_config.llm.provider == "groq":
            print(f"Groq API key set: {bool(app_config.llm.groq_api_key)}")
            print(f"Groq model: {app_config.llm.groq_model}")
            if not app_config.llm.groq_api_key:
                warnings.append("GROQ_API_KEY missing while LLM_PROVIDER=groq")
        if app_config.llm.enabled and app_config.llm.provider == "ollama":
            print(f"Ollama URL: {app_config.llm.ollama_base_url}")
            print(f"Ollama model: {app_config.llm.ollama_model}")
        if warnings:
            print("\nWarnings:")
            for item in warnings:
                print(f"  - {item}")
        return

    if args.clear_session:
        profile = Path(app_config.chrome_profile_dir)
        if profile.exists():
            import shutil

            shutil.rmtree(profile, ignore_errors=True)
            print(f"Cleared cached session: {profile}")
        else:
            print("No cached session found.")
        return

    print(f"Using Chrome profile cache: {app_config.chrome_profile_dir}")
    print("Close any other Chrome window that uses this same profile before starting.")
    if app_config.candidate_name:
        print(f"Candidate: {app_config.candidate_name}")
    if app_config.llm.enabled:
        print(f"LLM answers via {app_config.llm.provider} using {app_config.llm.profile_memory_path.name}")
    automator = NaukriAutoApply(app_config)
    automator.run()


if __name__ == "__main__":
    main()
