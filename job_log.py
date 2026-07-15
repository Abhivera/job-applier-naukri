"""Daily application logs under data/.

Files per day (example for 15 July 2026):
  data/15july2026Applied.txt
  data/15july2026Skipped.txt
  data/15july2026External.txt
  data/15july2026Limit.txt
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

LOG_KINDS = ("applied", "skipped", "external", "limit")


def day_log_stem(when: datetime | None = None) -> str:
    """Date prefix for log files, e.g. 15july2026"""
    now = when or datetime.now()
    return f"{now.day}{now.strftime('%B%Y').lower()}"


def application_log_path(kind: str = "applied", when: datetime | None = None) -> Path:
    stem = day_log_stem(when)
    suffixes = {
        "applied": "Applied",
        "skipped": "Skipped",
        "external": "External",
        "limit": "Limit",
    }
    suffix = suffixes.get((kind or "applied").strip().lower(), "Applied")
    return DATA_DIR / f"{stem}{suffix}.txt"


def normalize_job_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw or raw.startswith("javascript:"):
        return ""
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("/"):
        return "https://www.naukri.com" + raw
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if "naukri.com" in raw:
        return "https://" + raw.lstrip("/")
    return raw


def is_external_skip_reason(status: str, reason: str) -> bool:
    blob = f"{status} {reason}".lower()
    return any(
        token in blob
        for token in (
            "external website",
            "external apply",
            "company site",
            "company website",
            "apply on company",
        )
    )


def is_daily_limit_reason(status: str, reason: str) -> bool:
    blob = f"{status} {reason}".lower()
    return any(
        token in blob
        for token in (
            "daily apply limit",
            "daily limit",
            "daily quota",
            "naukri daily",
            "~50/day",
            "50/day",
        )
    )


def log_kind_for_status(status: str, reason: str = "") -> str:
    status_key = (status or "").strip().lower()
    if status_key in {"applied", "apply"}:
        return "applied"
    if status_key in {"limit", "daily_limit", "quota"}:
        return "limit"
    if status_key in {"external", "external_skip"}:
        return "external"
    if status_key == "dry_run":
        return "skipped"
    if is_daily_limit_reason(status_key, reason):
        return "limit"
    if is_external_skip_reason(status_key, reason):
        return "external"
    return "skipped"


def count_lines_in_log(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            count += 1
    except OSError:
        return 0
    return count


def absorb_legacy_combined_log(when: datetime | None = None) -> int:
    """Merge old data/15july2026.txt into *Applied.txt (once), then rename to .legacy.txt."""
    stem = day_log_stem(when)
    legacy = DATA_DIR / f"{stem}.txt"
    if not legacy.is_file():
        return 0

    applied_path = application_log_path("applied", when)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    applied_lines: list[str] = []
    skipped_lines: list[str] = []
    external_lines: list[str] = []
    limit_lines: list[str] = []

    try:
        for line in legacy.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            reason_m = re.search(r"reason:\s*(.+)$", stripped, flags=re.IGNORECASE)
            reason = reason_m.group(1).strip() if reason_m else ""
            if re.search(r"\]\s+APPLIED\s+\|", stripped, flags=re.IGNORECASE):
                applied_lines.append(stripped)
            elif is_daily_limit_reason("limit", reason) or re.search(
                r"\]\s+LIMIT\s+\|", stripped, flags=re.IGNORECASE
            ):
                limit_lines.append(stripped)
            elif is_external_skip_reason("external", reason) or re.search(
                r"\]\s+EXTERNAL\s+\|", stripped, flags=re.IGNORECASE
            ):
                external_lines.append(stripped)
            else:
                skipped_lines.append(stripped)
    except OSError:
        return 0

    def _append(path: Path, header: str, rows: list[str]) -> None:
        if not rows:
            return
        with path.open("a", encoding="utf-8") as handle:
            if not path.exists() or path.stat().st_size == 0:
                handle.write(f"{header} - {datetime.now().strftime('%d %B %Y')}\n")
            for row in rows:
                handle.write(row.rstrip() + "\n")

    _append(applied_path, "# Applied jobs", applied_lines)
    _append(application_log_path("skipped", when), "# Skipped jobs (filters / no apply / dry-run)", skipped_lines)
    _append(
        application_log_path("external", when),
        "# External website applies (company site - not via Naukri)",
        external_lines,
    )
    _append(
        application_log_path("limit", when),
        "# Daily apply limit exhausted (Naukri ~50/day)",
        limit_lines,
    )

    backup = DATA_DIR / f"{stem}.legacy.txt"
    try:
        if backup.exists():
            # Append leftover combined content into existing backup, then remove source
            with backup.open("a", encoding="utf-8") as handle:
                handle.write(f"\n# merged from {legacy.name}\n")
                handle.write(legacy.read_text(encoding="utf-8"))
            legacy.unlink(missing_ok=True)
        else:
            legacy.replace(backup)
    except OSError:
        pass
    return len(applied_lines) + len(skipped_lines) + len(external_lines) + len(limit_lines)


def count_todays_applies() -> int:
    """Count rows in today's Applied log (absorbs legacy combined file first)."""
    absorb_legacy_combined_log()
    return count_lines_in_log(application_log_path("applied"))


def remaining_daily_applies(daily_limit: int) -> int:
    if daily_limit <= 0:
        return 10**9
    return max(0, daily_limit - count_todays_applies())


def record_application(
    job_title: str,
    company: str,
    job_url: str = "",
    status: str = "applied",
    reason: str = "",
) -> Path:
    """Append one line to Applied / Skipped / External / Limit dated log."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    kind = log_kind_for_status(status, reason)
    path = application_log_path(kind)
    stamp = datetime.now().strftime("%H:%M:%S")
    title = " ".join((job_title or "Unknown").split())
    company_name = " ".join((company or "Unknown").split())
    reason_text = " ".join((reason or "").split())
    link = normalize_job_url(job_url) or "(no link)"

    status_labels = {
        "applied": "APPLIED",
        "skipped": "SKIPPED",
        "external": "EXTERNAL",
        "limit": "LIMIT",
    }
    status_key = (status or "").strip().lower()
    if kind == "skipped" and status_key == "dry_run":
        status_label = "DRY_RUN"
    else:
        status_label = status_labels.get(kind, "SKIPPED")

    line = f"[{stamp}] {status_label} | {title} | {company_name} | {link}"
    if reason_text:
        line += f" | reason: {reason_text}"
    line += "\n"

    headers = {
        "applied": "# Applied jobs",
        "skipped": "# Skipped jobs (filters / no apply / dry-run)",
        "external": "# External website applies (company site - not via Naukri)",
        "limit": "# Daily apply limit exhausted (Naukri ~50/day)",
    }
    with path.open("a", encoding="utf-8") as handle:
        if not path.exists() or path.stat().st_size == 0:
            handle.write(f"{headers[kind]} - {datetime.now().strftime('%d %B %Y')}\n")
        handle.write(line)
    return path
