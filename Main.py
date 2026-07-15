from __future__ import annotations

import argparse
import configparser
import os
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
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    ChromeDriverManager = None


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.ini"
DEFAULT_CHROME_PROFILE = BASE_DIR / "chrome_profile"


@dataclass(frozen=True)
class AppConfig:
    email: str
    keywords: list[str]
    locations: list[str]
    experience: str
    salary: str
    job_age: int
    title_include_keywords: list[str]
    title_exclude_keywords: list[str]
    chrome_driver_path: str
    chrome_profile_dir: str
    headless: bool
    dry_run: bool
    max_pages: int
    max_jobs_per_page: int
    max_applications: int
    wait_seconds: int
    login_timeout_seconds: int
    chrome_debugging_port: int | None


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    parser = configparser.ConfigParser()
    parser.read(path)

    email = os.getenv("NAUKRI_EMAIL", parser.get("NAUKRI", "email", fallback="")).strip()
    keywords = split_csv(parser.get("JOB_SEARCH", "keywords", fallback=""))
    locations = split_csv(parser.get("JOB_SEARCH", "locations", fallback=""))
    title_include_keywords = split_csv(parser.get("JOB_SEARCH", "title_include_keywords", fallback=""))
    title_exclude_keywords = split_csv(parser.get("JOB_SEARCH", "title_exclude_keywords", fallback=""))

    missing = []
    if not keywords:
        missing.append("JOB_SEARCH.keywords")
    if not locations:
        missing.append("JOB_SEARCH.locations")

    if missing:
        raise ValueError(f"Missing required config values: {', '.join(missing)}")

    profile_dir = parser.get("DEFAULT", "chrome_profile_dir", fallback="").strip()
    if not profile_dir:
        profile_dir = str(DEFAULT_CHROME_PROFILE)

    debugging_port_val = parser.get("DEFAULT", "chrome_debugging_port", fallback="").strip()
    chrome_debugging_port = int(debugging_port_val) if debugging_port_val.isdigit() else None

    return AppConfig(
        email=email,
        keywords=keywords,
        locations=locations,
        experience=parser.get("JOB_SEARCH", "experience", fallback="").strip(),
        salary=parser.get("JOB_SEARCH", "salary", fallback="").strip(),
        job_age=parser.getint("JOB_SEARCH", "job_age", fallback=1),
        title_include_keywords=title_include_keywords,
        title_exclude_keywords=title_exclude_keywords,
        chrome_driver_path=parser.get("DEFAULT", "chrome_driver_path", fallback="").strip(),
        chrome_profile_dir=profile_dir,
        headless=parser.getboolean("DEFAULT", "headless", fallback=False),
        dry_run=parser.getboolean("DEFAULT", "dry_run", fallback=True),
        max_pages=parser.getint("LIMITS", "max_pages", fallback=3),
        max_jobs_per_page=parser.getint("LIMITS", "max_jobs_per_page", fallback=10),
        max_applications=parser.getint("LIMITS", "max_applications", fallback=5),
        wait_seconds=parser.getint("LIMITS", "wait_seconds", fallback=20),
        login_timeout_seconds=parser.getint("LIMITS", "login_timeout_seconds", fallback=600),
        chrome_debugging_port=chrome_debugging_port,
    )


class NaukriAutoApply:
    def __init__(self, app_config: AppConfig):
        self.config = app_config
        self.driver = None
        self.wait = None
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
                # Still on the login URL usually means not finished.
                # Exception: some post-login redirects briefly keep query params.
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

    def login(self):
        print("Opening Naukri homepage to check cached session...")
        self.driver.get("https://www.naukri.com/")
        time.sleep(3)

        if self.is_logged_in():
            print("Already logged in via cached Chrome profile.")
            return True

        print("No cached session found. Opening login page...")
        self.driver.get("https://www.naukri.com/nlogin/login")
        time.sleep(2)
        print("Enter your Naukri credentials manually in Chrome.")
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

    def process_job_listings(self):
        if not self.ensure_session_active():
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
                if applied_count >= self.config.max_applications:
                    print(f"Reached application limit: {applied_count}.")
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

    def is_title_matching(self, job_title: str) -> bool:
        if not job_title:
            return False

        title_lower = job_title.lower()

        # Check exclusion list first
        for exclude_keyword in self.config.title_exclude_keywords:
            if exclude_keyword.lower() in title_lower:
                return False

        # Check inclusion list if it's set
        if self.config.title_include_keywords:
            for include_keyword in self.config.title_include_keywords:
                if include_keyword.lower() in title_lower:
                    return True
            return False

        return True

    def process_single_job(self, job, index):
        try:
            self.driver.execute_script("arguments[0].scrollIntoView(true);", job)
            time.sleep(1)

            job_link, job_title = self.extract_job_link(job)
            company = self.extract_company(job)
            if not job_link:
                print(f"No clickable link found for job {index}: {job_title} at {company}")
                return 0

            # Filter by title keywords to only apply to relevant roles
            if not self.is_title_matching(job_title):
                print(f"Skipping job {index}: '{job_title}' at {company} (does not match title filters)")
                return 0

            print(f"Opening job details for: {job_title} at {company}")
            main_window = self.driver.current_window_handle
            opened = self.open_job_in_new_tab(job_link)
            if not opened:
                print(f"Could not open job details for: {job_title}")
                return 0

            applied = self.try_apply(job_title, company)
            self.close_extra_tabs(main_window)
            return applied
        except WebDriverException as error:
            print(f"Error processing job {index}: {error}")
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

    def try_apply(self, job_title, company):
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
        for apply_button in self.driver.find_elements(By.XPATH, apply_selector):
            if not (apply_button.is_displayed() and apply_button.is_enabled()):
                continue

            if self.config.dry_run:
                print(f"Dry run: would apply to {job_title} at {company}")
                return 0

            if self.safe_click(apply_button):
                self.handle_apply_confirmation()
                print(f"Successfully applied to: {job_title} at {company}")
                time.sleep(3)
                return 1

        print(f"No apply button found for: {job_title} at {company}")
        return 0

    def handle_apply_confirmation(self):
        time.sleep(2)
        confirm_selector = (
            "//button[contains(text(),'Confirm') or contains(text(),'Submit') or contains(text(),'Apply')]"
        )
        for button in self.driver.find_elements(By.XPATH, confirm_selector):
            if button.is_displayed():
                self.safe_click(button)
                time.sleep(1)

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
        "--check-config",
        action="store_true",
        help="Validate config.ini without opening a browser.",
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
    return parser.parse_args()


def create_chrome_driver(headless=False, chrome_driver_path="", chrome_profile_dir="", chrome_debugging_port=None):
    chrome_options = Options()

    if chrome_debugging_port:
        chrome_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{chrome_debugging_port}")
        profile_path = None
    else:
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-background-timer-throttling")
        chrome_options.add_argument("--disable-backgrounding-occluded-windows")
        chrome_options.add_argument("--disable-renderer-backgrounding")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        profile_path = None
        if chrome_profile_dir:
            profile_path = Path(chrome_profile_dir).resolve()
            profile_path.mkdir(parents=True, exist_ok=True)
            # Stale locks from a crashed run prevent Chrome from starting.
            for lock_name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
                lock_file = profile_path / lock_name
                if lock_file.exists():
                    try:
                        lock_file.unlink()
                    except OSError:
                        pass
            chrome_options.add_argument(f"--user-data-dir={profile_path}")
            chrome_options.add_argument("--profile-directory=Default")

        if headless:
            chrome_options.add_argument("--headless=new")

    try:
        if chrome_driver_path:
            return webdriver.Chrome(service=Service(chrome_driver_path), options=chrome_options)
        return webdriver.Chrome(options=chrome_options)
    except WebDriverException as error:
        hint = ""
        if profile_path:
            hint = (
                f"\nCould not open Chrome with profile: {profile_path}\n"
                "Close other Chrome/chromedriver windows using this profile, then retry.\n"
                "Or clear the session with: python Main.py --clear-session"
            )
        raise WebDriverException(f"{error}{hint}") from error


def smoke_test_browser():
    # Headless + no profile; only verifies Chrome launches.
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

    if args.smoke_test:
        smoke_test_browser()
        return

    app_config = load_config()

    if args.check_config:
        print("Config OK.")
        print(f"Keywords: {', '.join(app_config.keywords)}")
        print(f"Locations: {', '.join(app_config.locations)}")
        print(f"Job Age (Freshness): {app_config.job_age} day(s)")
        print(f"Title Include Filters: {', '.join(app_config.title_include_keywords)}")
        print(f"Title Exclude Filters: {', '.join(app_config.title_exclude_keywords)}")
        print(f"Dry run: {app_config.dry_run}")
        print(f"Chrome profile: {app_config.chrome_profile_dir}")
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
    automator = NaukriAutoApply(app_config)
    automator.run()


if __name__ == "__main__":
    main()
