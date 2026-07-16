from __future__ import annotations


def build_text_to_sql_prompt(question: str, schema: str) -> str:
    return f"""You are a SQLite Text-to-SQL generator.

Rules:
- Use only tables and columns shown in the supplied role-filtered schema.
- Return exactly one read-only query.
- Use SELECT or a CTE that ultimately returns SELECT.
- Never generate INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, REPLACE, ATTACH, DETACH, PRAGMA, VACUUM, REINDEX, or ANALYZE.
- Never invent tables or columns.
- Prefer explicit JOIN conditions.
- Use readable aliases.
- Add deterministic ordering for top, bottom, latest, or earliest questions.
- Avoid SELECT *.
- Return JSON only with:
  {{
    "sql": "...",
    "explanation": "..."
  }}
- Keep the explanation short.
- Do not claim result values before execution.
- Do not wrap the JSON in Markdown.

DATABASE SCHEMA
{schema}

USER QUESTION
{question}
"""
