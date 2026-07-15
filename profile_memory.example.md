# Profile Memory (template)

Recommended: run `python setup_bot.py` (creates this file + `.env` + resume copy).

Or copy manually:

```bash
cp profile_memory.example.md profile_memory.md
# or: cp profile_memory.example.md profiles/your-name.md
```

Then set in `.env`:

```env
PROFILE_MEMORY_PATH=profile_memory.md
```

The bot reads this file when answering Naukri screening questions (Groq / Ollama).
Keep facts accurate. Prefer short, concrete answers. Do not invent employers,
degrees, CTC, or notice periods that are blank.

---

## Identity

- Full name:
- Preferred name:
- Email:
- Phone:
- Current city / preferred work location:
- Willing to relocate: Yes / No / Open to discuss
- Work mode preference: Remote / Hybrid / Onsite / Any
- Portfolio:
- GitHub / LinkedIn:

## Professional summary

Write 3–5 lines about your role, years of experience, and main strengths.

## Experience

### Current / latest role

- Title:
- Company:
- Start date:
- End date: Present
- Location:
- Key responsibilities / impact:
  -

### Previous roles

- Title | Company | Dates | 1–2 line summary

## Projects

### Project name (url)

Short description.
Tech:

## Skills

- Primary:
- Secondary:
- Tools:
- Soft skills: (optional)

## Education

- Degree | Institution | Year | Grade/CGPA (if relevant)

## Certifications

-

## Compensation & availability

- Current CTC (LPA):
- Expected CTC (LPA):
- Notice period (days):
- Immediate joiner: Yes / No
- Preferred joining date:
- Minimum acceptable offer:

## Preferences

- Years of experience:
- Preferred roles / titles:
- Roles to avoid:
- Preferred company types: (e.g. startup, mid-size)
- Companies / company types to avoid: (optional)
- Preferred industries:
- Work mode: Remote / Hybrid / Onsite / Any
- Minimum salary you will accept:
- Preferred locations:

## Common screening answers

Use these as ground truth. The LLM should paraphrase lightly, not invent new facts.

| Topic | Your answer |
| --- | --- |
| Total years of experience | |
| Relevant years (for target stack) | |
| Target roles | |
| Current role | |
| Previous role | |
| Current location | |
| Preferred location | |
| Work mode preference | |
| Preferred company size | |
| Notice period | |
| Current CTC | |
| Expected CTC | |
| Willing to relocate | |
| Open to remote / hybrid / onsite | |
| Highest education | |
| Reason for job change | |
| Strengths | |
| Weaknesses (honest, professional) | |
| Are you comfortable with the offered salary range? | |
| Do you have experience with [stack from JD]? | Answer from Skills + Experience + Projects only |

## Free-form notes

Anything else the LLM should know:

-
-
-
