"""LLM helpers for answering application screening questions.

Providers:
  - groq   — Groq cloud OpenAI-compatible API
  - ollama — local Ollama HTTP API

Profile context is built dynamically from:
  1. .env fields (CANDIDATE_NAME, PROFILE_SUMMARY, CURRENT_CITY, …)
  2. The markdown file at PROFILE_MEMORY_PATH
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class DynamicProfileSummary:
    """High-level candidate prefs from .env — works for any user without code changes."""

    candidate_name: str = ""
    profile_summary: str = ""
    years_of_experience: str = ""
    min_salary_lpa: str = ""
    expected_ctc_lpa: str = ""
    current_city: str = ""
    pincode: str = ""
    notice_period: str = ""
    willing_to_relocate: str = "Yes"
    work_modes: tuple[str, ...] = ()
    preferred_company_types: tuple[str, ...] = ()
    preferred_locations: tuple[str, ...] = ()
    target_keywords: tuple[str, ...] = ()
    skill_hints: tuple[str, ...] = ()
    resume_path: str = ""

    def to_markdown(self) -> str:
        lines = ["## Dynamic profile summary (from .env)", ""]
        if self.candidate_name:
            lines.append(f"- Name: {self.candidate_name}")
        if self.profile_summary:
            lines.append(f"- Summary: {self.profile_summary}")
        if self.years_of_experience:
            lines.append(f"- Years of experience: {self.years_of_experience}")
        if self.min_salary_lpa:
            lines.append(f"- Minimum salary (LPA): {self.min_salary_lpa}+")
        if self.expected_ctc_lpa:
            lines.append(f"- Expected CTC (LPA): {self.expected_ctc_lpa}")
        if self.current_city:
            lines.append(f"- Current city: {self.current_city}")
        if self.pincode:
            lines.append(f"- Pincode: {self.pincode}")
        if self.notice_period:
            lines.append(f"- Notice period: {self.notice_period}")
        if self.willing_to_relocate:
            lines.append(f"- Willing to relocate: {self.willing_to_relocate}")
        if self.work_modes:
            lines.append(f"- Work modes accepted: {', '.join(self.work_modes)}")
        if self.preferred_company_types:
            lines.append(f"- Preferred company types: {', '.join(self.preferred_company_types)}")
        if self.preferred_locations:
            lines.append(f"- Preferred locations: {', '.join(self.preferred_locations)}")
        if self.skill_hints:
            lines.append(f"- Known skills (quick answers): {', '.join(self.skill_hints[:30])}")
        if self.target_keywords:
            preview = ", ".join(self.target_keywords[:12])
            if len(self.target_keywords) > 12:
                preview += ", …"
            lines.append(f"- Target search keywords: {preview}")
        if self.resume_path:
            lines.append(f"- Resume on disk: {self.resume_path}")
        lines.append("")
        lines.append(
            "Prefer these .env preferences when they do not conflict with the detailed "
            "profile memory below. Do not invent CTC, notice period, or employers."
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class LLMSettings:
    enabled: bool
    provider: str
    profile_memory_path: Path
    max_tokens: int
    temperature: float
    groq_api_key: str
    groq_model: str
    groq_base_url: str
    ollama_base_url: str
    ollama_model: str
    dynamic_summary: DynamicProfileSummary = field(default_factory=DynamicProfileSummary)


class LLMProvider(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class GroqProvider:
    def __init__(self, api_key: str, model: str, base_url: str, max_tokens: int, temperature: float):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.temperature = temperature

    def complete(self, system: str, user: str) -> str:
        if not self.api_key:
            raise ValueError("GROQ_API_KEY is missing. Set it in .env")

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()


class OllamaProvider:
    def __init__(self, base_url: str, model: str, max_tokens: int, temperature: float):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
        return (data.get("message") or {}).get("content", "").strip()


def build_provider(settings: LLMSettings) -> LLMProvider:
    provider = settings.provider.strip().lower()
    if provider == "groq":
        return GroqProvider(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            base_url=settings.groq_base_url,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
        )
    if provider == "ollama":
        return OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
        )
    raise ValueError(f"Unsupported LLM_PROVIDER={settings.provider!r}. Use groq or ollama.")


SYSTEM_PROMPT = """You answer job-application screening questions for a candidate.
Use ONLY facts from the profile memory below.

Rules:
- Keep answers concise. Prefer a short number or Yes/No when that fits.
- For "how many years ... in <skill/tech>": if that skill is NOT clearly in the profile, answer exactly: 0
- For yes/no skill questions: Yes only if profile supports it; otherwise No.
- Never invent employers, degrees, CTC, or notice periods. If unknown, say "Will confirm" for CTC/notice.
- Never leave the answer empty.
- Do not wrap in quotes or markdown. Do not mention that you are an AI.
- Ignore greeting text; answer only the actual question.

--- PROFILE MEMORY ---
{profile_memory}
--- END PROFILE MEMORY ---
"""


COVER_LETTER_PROMPT = """You write a highly tailored, professional cover letter or short pitch (2-3 sentences max) to a recruiter for a job application.
Highlight how the candidate's skills and experience match the job description.
Do NOT mention that you are an AI. Do not use placeholder text (like "[Recruiter Name]" or "[Date]").
Write in the first-person ("I"). Keep it brief, punchy, and professional.

--- PROFILE MEMORY ---
{profile_memory}
--- END PROFILE MEMORY ---
"""


SUITABILITY_PROMPT = """You evaluate if a job is suitable for a candidate based on the candidate's profile memory and preferences, and the job description.
Rule:
Determine if there are any critical mismatch barriers.
A critical mismatch barrier includes:
1. The job requires a technology stack that is completely different (e.g. Java, C#, PHP, SAP, Salesforce, Angular) and the candidate has no experience in it. (If candidate is a React/Node/Python developer, do not match them to pure Java/C# backend roles).
2. The job has a strict experience requirement (e.g. "minimum 8 years", "lead level", "principal") and the candidate does not meet it (candidate has 3+ years experience).
3. The job specifies a salary range or work mode that is completely incompatible with the candidate's minimum requirements.

Reply with exactly:
SUITABLE: Yes
Reason: <1-sentence positive reason>

OR:
SUITABLE: No
Reason: <1-sentence reason highlighting the critical mismatch>

--- PROFILE MEMORY ---
{profile_memory}
--- END PROFILE MEMORY ---
"""


# Fallback only when SKILL_HINTS is empty in .env
DEFAULT_SKILL_HINTS = (
    "python",
    "javascript",
    "typescript",
    "react",
    "node",
    "fastapi",
    "aws",
    "docker",
    "sql",
)


def clean_model_answer(text: str) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    for marker in ("</think>", "<think>"):
        if marker in cleaned:
            cleaned = cleaned.split(marker)[-1].strip()
    cleaned = cleaned.strip().strip('"').strip("'")
    if cleaned.lower().startswith("answer:"):
        cleaned = cleaned[7:].strip()
    return cleaned.strip()


def quick_screening_answer(question: str, summary: DynamicProfileSummary | None = None) -> str | None:
    """Instant answers for common Naukri chatbot questions (no LLM wait)."""
    q = (question or "").lower().strip()
    if not q:
        return None

    summary = summary or DynamicProfileSummary()
    years = (summary.years_of_experience or "3+").replace("years", "").strip() or "3+"
    skill_hints = summary.skill_hints or DEFAULT_SKILL_HINTS
    expected = summary.expected_ctc_lpa or summary.min_salary_lpa or ""
    relocate = (summary.willing_to_relocate or "Yes").strip() or "Yes"
    notice = (summary.notice_period or "Will confirm").strip() or "Will confirm"

    if any(
        phrase in q
        for phrase in (
            "thank you for showing interest",
            "kindly answer all",
            "successfully apply",
            "recruiter's questions",
        )
    ) and "?" not in q:
        return None

    if ("total" in q or "overall" in q) and "experience" in q and "java" not in q:
        return years.replace("+", "") if years.endswith("+") else years

    if ("how many years" in q or "years of experience" in q or "year of experience" in q) and "in " in q:
        skill_part = q.split("in ", 1)[-1]
        skill_part = re.sub(r"[?.!].*$", "", skill_part).strip()
        if any(hint in skill_part for hint in skill_hints):
            num = "".join(ch for ch in years if ch.isdigit()) or "3"
            return num
        return "0"

    if q.startswith("have you") or q.startswith("do you have") or "are you familiar" in q:
        if any(hint in q for hint in skill_hints):
            return "Yes"
        if any(
            bad in q
            for bad in ("java ", " java?", "c#", ".net", "php", "angular", "android", "ios", "sap", "salesforce")
        ):
            return "No"
        return "No"

    if "willing to relocate" in q or ("relocate" in q and ("will" in q or "are you" in q)):
        return relocate
    if "notice period" in q:
        return notice
    if "current ctc" in q or "current salary" in q:
        return "Will discuss in interview"
    if "expected ctc" in q or "expected salary" in q:
        return expected or "Will confirm"
    if "current location" in q or "current city" in q:
        return summary.current_city or "Will confirm"
    if "pincode" in q or "pin code" in q or "zip" in q:
        return summary.pincode or "Will confirm"

    return None


class ProfileAnswerer:
    def __init__(self, settings: LLMSettings):
        self.settings = settings
        self._memory_cache: str | None = None
        self._provider: LLMProvider | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def _load_memory(self) -> str:
        if self._memory_cache is not None:
            return self._memory_cache

        parts: list[str] = []
        dynamic = self.settings.dynamic_summary.to_markdown().strip()
        if dynamic:
            parts.append(dynamic)

        path = self.settings.profile_memory_path
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
        else:
            raise FileNotFoundError(
                f"Profile memory not found: {path}\n"
                "Copy profile_memory.example.md to that path, or run: python setup_bot.py"
            )

        self._memory_cache = "\n\n".join(parts)
        return self._memory_cache

    def _get_provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = build_provider(self.settings)
        return self._provider

    def answer(self, question: str, job_context: str = "") -> str:
        summary = self.settings.dynamic_summary
        quick = quick_screening_answer(question, summary=summary)
        q_lower = (question or "").lower()
        prefer_quick = quick is not None and (
            "how many years" in q_lower
            or "years of experience" in q_lower
            or q_lower.startswith("have you")
            or q_lower.startswith("do you have")
            or "willing to relocate" in q_lower
            or "current location" in q_lower
            or "pincode" in q_lower
            or "pin code" in q_lower
        )
        if prefer_quick and quick:
            return quick

        memory = self._load_memory()
        is_cover_letter = any(
            token in q_lower
            for token in (
                "cover letter",
                "message to recruiter",
                "why should we hire",
                "write a pitch",
                "briefly describe why you are a good fit",
                "why do you want to join",
                "why are you interested",
                "suitability",
                "tell us about your experience",
            )
        )
        if is_cover_letter:
            system = COVER_LETTER_PROMPT.format(profile_memory=memory)
        else:
            system = SYSTEM_PROMPT.format(profile_memory=memory)

        user_parts = [f"Question: {question.strip()}"]
        if job_context.strip():
            user_parts.append(f"Job context: {job_context.strip()}")

        if is_cover_letter:
            user_parts.append(
                "Write a tailored, professional pitch or short cover letter (maximum 2 to 3 sentences) "
                "explaining how the candidate's background matches this job."
            )
        else:
            user_parts.append(
                "Reply with only the final answer text. "
                "If the skill is not in the profile, for years questions answer 0."
            )

        try:
            raw = self._get_provider().complete(system, "\n".join(user_parts))
            cleaned = clean_model_answer(raw)
            if cleaned:
                return cleaned
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as error:
            print(f"LLM unavailable ({error}); using fallback.")

        if quick:
            return quick
        if "year" in q_lower:
            return "0"
        if q_lower.startswith("have you") or q_lower.startswith("do you"):
            return "No"
        return "Will confirm"

    def check_suitability(self, job_title: str, company: str, jd_text: str) -> tuple[bool, str]:
        """Check if the job matches the candidate's profile to prevent low-quality applies.
        Returns (is_suitable, reason).
        """
        if not self.enabled:
            return True, "LLM disabled; skipped suitability check"

        if not jd_text or len(jd_text) < 100:
            return True, "No detailed JD text available; skipped suitability check"

        memory = self._load_memory()
        system = SUITABILITY_PROMPT.format(profile_memory=memory)
        user_prompt = f"Job Title: {job_title}\nCompany: {company}\nJob Description:\n{jd_text}"

        try:
            raw = self._get_provider().complete(system, user_prompt)
            lines = [line.strip() for line in raw.split("\n") if line.strip()]
            suitable = True
            reason = "Matches candidate profile"
            for line in lines:
                if line.lower().startswith("suitable:"):
                    val = line.split(":", 1)[1].strip().lower()
                    if "no" in val:
                        suitable = False
                elif line.lower().startswith("reason:"):
                    reason = line.split(":", 1)[1].strip()
            return suitable, reason
        except Exception as error:
            print(f"Suitability check failed due to error: {error}; defaulting to True.")
            return True, f"Error in check: {error}"
