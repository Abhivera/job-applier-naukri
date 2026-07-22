"""Daily application logs under data/.

Files per day (example for 16 July 2026):
  data/16july2026Applied.txt
  data/16july2026Skipped.txt
  data/16july2026External.txt
  data/16july2026Limit.txt

Dedupes by Naukri job id (query params like sid/xp ignored), so the same
listing is not logged again across keyword/location searches the same day.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

LOG_KINDS = ("applied", "skipped", "external", "limit")

# Trailing numeric id on Naukri job-listings URLs
_JOB_ID_RE = re.compile(r"-(\d{8,})(?:/)?$", re.IGNORECASE)
_URL_IN_LINE_RE = re.compile(r"https?://[^\s|]+", re.IGNORECASE)

# fingerprint -> kind (applied/skipped/external/limit)
_seen_cache: dict[str, str] | None = None
_seen_cache_stem: str | None = None


@dataclass(frozen=True)
class LogResult:
    path: Path
    written: bool
    duplicate: bool
    fingerprint: str
    kind: str


def day_log_stem(when: datetime | None = None) -> str:
    """Date prefix for log files, e.g. 16july2026"""
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
    if not raw or raw.startswith("javascript:") or raw == "(no link)":
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


def canonicalize_job_url(url: str) -> str:
    """Strip query/hash so the same listing matches across searches."""
    full = normalize_job_url(url)
    if not full:
        return ""
    parsed = urlparse(full)
    path = (parsed.path or "").rstrip("/")
    if not path:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def job_id_from_url(url: str) -> str:
    canon = canonicalize_job_url(url)
    if not canon:
        return ""
    match = _JOB_ID_RE.search(canon)
    return match.group(1) if match else ""


def job_fingerprint(job_url: str = "", job_title: str = "", company: str = "") -> str:
    """Stable key for a listing. Prefer Naukri job id over title/company."""
    job_id = job_id_from_url(job_url)
    if job_id:
        return f"id:{job_id}"

    canon = canonicalize_job_url(job_url)
    if canon:
        return f"url:{canon.lower()}"

    title = " ".join((job_title or "").lower().split())
    company_name = " ".join((company or "").lower().split())
    if title and company_name and title != "unknown" and company_name != "unknown":
        return f"tc:{title}|{company_name}"
    return ""


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


def _parse_line_fingerprint(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return ""

    if re.search(r"\]\s+LIMIT\s+\|", stripped, flags=re.IGNORECASE):
        return "limit:daily"

    urls = _URL_IN_LINE_RE.findall(stripped)
    for url in urls:
        fp = job_fingerprint(job_url=url)
        if fp:
            return fp

    # STATUS | title | company | ...
    parts = [p.strip() for p in stripped.split("|")]
    if len(parts) >= 3:
        # parts[0] is "[time] STATUS"
        title = parts[1] if len(parts) > 1 else ""
        company = parts[2] if len(parts) > 2 else ""
        return job_fingerprint(job_title=title, company=company)
    return ""


def _load_seen_today(when: datetime | None = None) -> dict[str, str]:
    global _seen_cache, _seen_cache_stem
    stem = day_log_stem(when)
    if _seen_cache is not None and _seen_cache_stem == stem:
        return _seen_cache

    seen: dict[str, str] = {}
    for kind in LOG_KINDS:
        path = application_log_path(kind, when)
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                fp = _parse_line_fingerprint(line)
                if fp and fp not in seen:
                    seen[fp] = kind
        except OSError:
            continue

    # Legacy combined file
    legacy = DATA_DIR / f"{stem}.txt"
    if legacy.is_file():
        try:
            for line in legacy.read_text(encoding="utf-8").splitlines():
                fp = _parse_line_fingerprint(line)
                if not fp or fp in seen:
                    continue
                if re.search(r"\]\s+APPLIED\s+\|", line, flags=re.IGNORECASE):
                    seen[fp] = "applied"
                elif is_daily_limit_reason("", line) or re.search(
                    r"\]\s+LIMIT\s+\|", line, flags=re.IGNORECASE
                ):
                    seen[fp] = "limit"
                elif is_external_skip_reason("", line) or re.search(
                    r"\]\s+EXTERNAL\s+\|", line, flags=re.IGNORECASE
                ):
                    seen[fp] = "external"
                else:
                    seen[fp] = "skipped"
        except OSError:
            pass

    _seen_cache = seen
    _seen_cache_stem = stem
    return seen


def already_logged_today(
    job_url: str = "",
    job_title: str = "",
    company: str = "",
    when: datetime | None = None,
) -> str | None:
    """Return log kind if this job was already recorded today, else None."""
    fp = job_fingerprint(job_url=job_url, job_title=job_title, company=company)
    if not fp:
        return None
    return _load_seen_today(when).get(fp)


def daily_limit_already_logged(when: datetime | None = None) -> bool:
    return "limit:daily" in _load_seen_today(when)


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
    """Merge old data/<date>.txt into split files (once), then rename to .legacy.txt."""
    stem = day_log_stem(when)
    legacy = DATA_DIR / f"{stem}.txt"
    if not legacy.is_file():
        return 0

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

    _append(application_log_path("applied", when), "# Applied jobs", applied_lines)
    _append(
        application_log_path("skipped", when),
        "# Skipped jobs (filters / no apply / dry-run)",
        skipped_lines,
    )
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

    # Invalidate cache after merge
    global _seen_cache, _seen_cache_stem
    _seen_cache = None
    _seen_cache_stem = None

    backup = DATA_DIR / f"{stem}.legacy.txt"
    try:
        if backup.exists():
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
    """Count unique Applied jobs today (by job id / fingerprint)."""
    absorb_legacy_combined_log()
    seen = set()
    path = application_log_path("applied")
    if not path.is_file():
        return 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not re.search(r"\]\s+APPLIED\s+\|", line, flags=re.IGNORECASE):
                continue
            fp = _parse_line_fingerprint(line)
            if fp:
                seen.add(fp)
            else:
                seen.add(f"raw:{line.strip()}")
    except OSError:
        return 0
    return len(seen)


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
) -> LogResult:
    """Append one line unless this job (or daily limit) was already logged today."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    kind = log_kind_for_status(status, reason)
    path = application_log_path(kind)

    if kind == "limit":
        fingerprint = "limit:daily"
    else:
        fingerprint = job_fingerprint(job_url=job_url, job_title=job_title, company=company)

    seen = _load_seen_today()
    if fingerprint and fingerprint in seen:
        return LogResult(
            path=application_log_path(seen[fingerprint]),
            written=False,
            duplicate=True,
            fingerprint=fingerprint,
            kind=seen[fingerprint],
        )

    stamp = datetime.now().strftime("%H:%M:%S")
    title = " ".join((job_title or "Unknown").split())
    company_name = " ".join((company or "Unknown").split())
    reason_text = " ".join((reason or "").split())
    # Store canonical URL (no sid/xp) so future dedupe is obvious in the file
    link = canonicalize_job_url(job_url) or normalize_job_url(job_url) or "(no link)"

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

    if fingerprint:
        seen[fingerprint] = kind

    return LogResult(
        path=path,
        written=True,
        duplicate=False,
        fingerprint=fingerprint,
        kind=kind,
    )
