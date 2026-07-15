# Profiles

Store your filled profile memory here (or use root `profile_memory.md`).

```bash
python setup_bot.py
# or:
cp ../profile_memory.example.md your-name.md
```

Set in `.env`:

```env
PROFILE_MEMORY_PATH=profiles/your-name.md
CANDIDATE_NAME=Your Name
PROFILE_SUMMARY=Short pitch for LLM answers.
SKILL_HINTS=python, react, sql
RESUME_PATH=resumes/your-name.pdf
```

- `profile_memory.example.md` — blank template (tracked)
- `example-candidate.md` — fake filled sample (tracked)
- Your real profile — local only; do not commit CTC / phone / secrets
