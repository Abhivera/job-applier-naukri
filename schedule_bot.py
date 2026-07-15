#!/usr/bin/env python3
"""Run AutoApplyNukari only during Indian-time (IST) windows.

Default windows (Asia/Kolkata):
  09:30–11:00
  13:00–14:00
  17:30–18:00

Usage:
  python schedule_bot.py
  python schedule_bot.py --once   # run only the next window, then exit

Configure in .env:
  SCHEDULE_ENABLED=true
  SCHEDULE_TIMEZONE=Asia/Kolkata
  SCHEDULE_WINDOWS=09:30-11:00,13:00-14:00,17:30-18:00
  DAILY_APPLY_LIMIT=50
  MAX_APPLICATIONS=15
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
ENV_EXAMPLE = BASE_DIR / ".env.example"
MAIN_PY = BASE_DIR / "Main.py"

# Fallback when tzdata is missing on Windows
IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def resolve_timezone(tz_name: str):
    name = (tz_name or "Asia/Kolkata").strip()
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    if name in {"Asia/Kolkata", "Asia/Calcutta", "IST"}:
        return IST
    raise RuntimeError(
        f"Timezone {name!r} unavailable. Install tzdata: pip install tzdata"
    )


@dataclass(frozen=True)
class TimeWindow:
    start_minutes: int  # minutes from midnight
    end_minutes: int

    @property
    def label(self) -> str:
        return f"{self._fmt(self.start_minutes)}-{self._fmt(self.end_minutes)}"

    @staticmethod
    def _fmt(total: int) -> str:
        hours, minutes = divmod(total, 60)
        return f"{hours:02d}:{minutes:02d}"

    def contains(self, now: datetime) -> bool:
        current = now.hour * 60 + now.minute
        return self.start_minutes <= current < self.end_minutes

    def next_start_after(self, now: datetime) -> datetime:
        """Next datetime (in now.tzinfo) when this window starts."""
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            minutes=self.start_minutes
        )
        if now < today_start:
            return today_start
        return today_start + timedelta(days=1)


def load_env() -> None:
    if load_dotenv is None:
        raise ImportError("python-dotenv required. pip install -r requirements.txt")
    if ENV_EXAMPLE.exists():
        load_dotenv(ENV_EXAMPLE, override=False)
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=True)


def env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def parse_windows(raw: str) -> list[TimeWindow]:
    windows: list[TimeWindow] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            raise ValueError(f"Bad SCHEDULE_WINDOWS entry (use HH:MM-HH:MM): {part}")
        start_s, end_s = part.split("-", 1)
        start = _parse_hhmm(start_s.strip())
        end = _parse_hhmm(end_s.strip())
        if end <= start:
            raise ValueError(f"Window end must be after start: {part}")
        windows.append(TimeWindow(start, end))
    if not windows:
        raise ValueError("SCHEDULE_WINDOWS is empty")
    return sorted(windows, key=lambda w: w.start_minutes)


def _parse_hhmm(value: str) -> int:
    hours_s, minutes_s = value.split(":")
    hours, minutes = int(hours_s), int(minutes_s)
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"Invalid time: {value}")
    return hours * 60 + minutes


def now_in_tz(tz_name: str) -> datetime:
    return datetime.now(resolve_timezone(tz_name))


def active_window(now: datetime, windows: list[TimeWindow]) -> TimeWindow | None:
    for window in windows:
        if window.contains(now):
            return window
    return None


def next_window_start(now: datetime, windows: list[TimeWindow]) -> tuple[TimeWindow, datetime]:
    candidates = [(w, w.next_start_after(now)) for w in windows]
    window, start = min(candidates, key=lambda item: item[1])
    return window, start


def sleep_until(target: datetime, tz_name: str) -> None:
    while True:
        now = now_in_tz(tz_name)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        # Wake periodically so Ctrl+C is responsive and clock drift is fine
        chunk = min(remaining, 30.0)
        hrs, rem = divmod(int(remaining), 3600)
        mins, secs = divmod(rem, 60)
        print(
            f"[{now.strftime('%Y-%m-%d %H:%M:%S %Z')}] "
            f"Sleeping {hrs:02d}:{mins:02d}:{secs:02d} until {target.strftime('%H:%M %Z')} …",
            flush=True,
        )
        time.sleep(chunk)


def run_bot_once() -> int:
    print(f"\n=== Starting bot run at {datetime.now().isoformat(timespec='seconds')} ===\n", flush=True)
    result = subprocess.run([sys.executable, str(MAIN_PY)], cwd=str(BASE_DIR))
    print(f"\n=== Bot finished with exit code {result.returncode} ===\n", flush=True)
    return result.returncode


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IST schedule runner for Naukri AutoApply.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Wait for the next (or current) window, run once, then exit.",
    )
    parser.add_argument(
        "--now",
        "--ignore-schedule",
        dest="ignore_schedule",
        action="store_true",
        help="Ignore IST windows; run the bot immediately once, then exit.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print schedule status and exit (no bot run).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env()
    args = parse_args(argv)

    tz_name = env_str("SCHEDULE_TIMEZONE", "Asia/Kolkata") or "Asia/Kolkata"
    windows_raw = env_str(
        "SCHEDULE_WINDOWS",
        "09:30-11:00,13:00-14:00,17:30-18:00",
    )
    windows = parse_windows(windows_raw)
    daily_limit = env_str("DAILY_APPLY_LIMIT", "50")
    max_apps = env_str("MAX_APPLICATIONS", "15")

    now = now_in_tz(tz_name)
    print("=" * 60)
    print(" Naukri AutoApply - IST scheduler")
    print("=" * 60)
    print(f"Timezone : {tz_name}")
    print(f"Now      : {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Windows  : {', '.join(w.label for w in windows)}")
    print(f"Limits   : MAX_APPLICATIONS={max_apps}, DAILY_APPLY_LIMIT={daily_limit}")
    print("Naukri typically allows ~50 applications per account per day.")

    if args.ignore_schedule:
        print("Mode     : --now (ignore schedule windows)")
        print("Press Ctrl+C to stop.\n")
        return run_bot_once()

    print("Press Ctrl+C to stop.\n")

    current = active_window(now, windows)
    if current:
        print(f"Currently INSIDE window: {current.label}")
    else:
        nxt, start = next_window_start(now, windows)
        print(f"Next window: {nxt.label} (starts {start.strftime('%Y-%m-%d %H:%M %Z')})")

    if args.status:
        return 0

    ran_in_window: str | None = None

    while True:
        now = now_in_tz(tz_name)
        window = active_window(now, windows)

        if window is None:
            ran_in_window = None
            nxt, start = next_window_start(now, windows)
            print(f"Outside schedule. Next: {nxt.label} at {start.strftime('%Y-%m-%d %H:%M %Z')}")
            sleep_until(start, tz_name)
            continue

        # One bot run per window (avoid restart loops inside the same slot)
        if ran_in_window == window.label:
            # Wait until this window ends, then look for the next
            end_dt = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
                minutes=window.end_minutes
            )
            if now < end_dt:
                print(f"Already ran in {window.label}. Waiting until window ends…")
                sleep_until(end_dt, tz_name)
            if args.once:
                return 0
            continue

        print(f"Window active: {window.label} — launching bot")
        run_bot_once()
        ran_in_window = window.label

        if args.once:
            return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nScheduler stopped.")
        raise SystemExit(0)
