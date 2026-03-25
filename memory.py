# memory.py — PostgreSQL version for Railway

import os
import json
import psycopg2
import psycopg2.extras
from urllib.parse import urlparse

DATABASE_URL = os.environ["DATABASE_URL"]

# ─── Connection ───────────────────────────────────────────────────────────────

def get_con():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


# ─── Setup ────────────────────────────────────────────────────────────────────

def init_db():
    con = get_con()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_facts (
            user_id     BIGINT NOT NULL,
            key         TEXT NOT NULL,
            value       TEXT NOT NULL,
            updated_at  TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (user_id, key)
        );

        CREATE TABLE IF NOT EXISTS summaries (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT NOT NULL,
            summary     TEXT NOT NULL,
            created_at  TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS messages (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT NOT NULL,
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            created_at  TIMESTAMP DEFAULT NOW()
        );
    """)
    con.commit()
    cur.close()
    con.close()
    print("[Memory] DB initialized.")


# ─── Facts ────────────────────────────────────────────────────────────────────

def save_facts_bulk(user_id: int, facts: dict):
    con = get_con()
    cur = con.cursor()
    for key, value in facts.items():
        cur.execute("""
            INSERT INTO user_facts (user_id, key, value, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (user_id, key)
            DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, (user_id, key, str(value)))
    con.commit()
    cur.close()
    con.close()


def get_all_facts(user_id: int) -> str:
    con = get_con()
    cur = con.cursor()
    cur.execute(
        "SELECT key, value FROM user_facts WHERE user_id = %s ORDER BY key",
        (user_id,)
    )
    rows = cur.fetchall()
    cur.close()
    con.close()
    if not rows:
        return ""
    lines = [f"- {k}: {v}" for k, v in rows]
    return "Known facts about this user:\n" + "\n".join(lines)


# ─── Messages ─────────────────────────────────────────────────────────────────

def append_message(user_id: int, role: str, content: str):
    con = get_con()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO messages (user_id, role, content) VALUES (%s, %s, %s)",
        (user_id, role, content)
    )
    con.commit()
    cur.close()
    con.close()


def get_recent_messages(user_id: int, limit: int = 10) -> list:
    con = get_con()
    cur = con.cursor()
    cur.execute("""
        SELECT role, content FROM (
            SELECT role, content, id
            FROM messages
            WHERE user_id = %s
            ORDER BY id DESC
            LIMIT %s
        ) sub ORDER BY id ASC
    """, (user_id, limit))
    rows = cur.fetchall()
    cur.close()
    con.close()
    return [{"role": r, "content": c} for r, c in rows]


def get_unsummarized_messages(user_id: int) -> list:
    """Get all messages after the last summary was created."""
    con = get_con()
    cur = con.cursor()

    # Find when last summary was created
    cur.execute("""
        SELECT created_at FROM summaries
        WHERE user_id = %s
        ORDER BY id DESC LIMIT 1
    """, (user_id,))
    row = cur.fetchone()
    last_summary_time = row[0] if row else None

    if last_summary_time:
        cur.execute("""
            SELECT role, content FROM messages
            WHERE user_id = %s AND created_at > %s
            ORDER BY id ASC
        """, (user_id, last_summary_time))
    else:
        cur.execute("""
            SELECT role, content FROM messages
            WHERE user_id = %s
            ORDER BY id ASC
        """, (user_id,))

    rows = cur.fetchall()
    cur.close()
    con.close()
    return rows


# ─── Summaries ────────────────────────────────────────────────────────────────

def save_summary(user_id: int, summary: str):
    con = get_con()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO summaries (user_id, summary) VALUES (%s, %s)",
        (user_id, summary)
    )
    con.commit()
    cur.close()
    con.close()


def get_latest_summary(user_id: int) -> str:
    con = get_con()
    cur = con.cursor()
    cur.execute("""
        SELECT summary FROM summaries
        WHERE user_id = %s
        ORDER BY id DESC LIMIT 1
    """, (user_id,))
    row = cur.fetchone()
    cur.close()
    con.close()
    return row[0] if row else ""


# ─── Auto-summarize + extract facts ───────────────────────────────────────────

SUMMARIZE_EVERY = 20  # trigger after every 20 new messages

def maybe_summarize(user_id: int, groq_client, model: str = "llama-3.1-8b-instant"):
    rows = get_unsummarized_messages(user_id)
    if len(rows) < SUMMARIZE_EVERY:
        return  # not enough new messages yet

    convo_text = "\n".join(f"{r.upper()}: {c}" for r, c in rows)
    prev_summary = get_latest_summary(user_id)

    prompt = f"""You are a memory assistant. Given this conversation, do two things:

1. Write a concise summary (max 150 words) continuing from the previous summary if given.
2. Extract key facts about the user as JSON (name, preferences, personality, goals, etc.)

Previous summary:
{prev_summary or 'None'}

New conversation:
{convo_text}

Respond ONLY in this exact JSON format, no markdown:
{{
  "summary": "...",
  "facts": {{
    "name": "...",
    "interests": "...",
    "any_other_key": "value"
  }}
}}"""

    try:
        response = groq_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600
        )
        raw = response.choices[0].message.content.strip()
        parsed = json.loads(raw)

        save_summary(user_id, parsed.get("summary", ""))
        facts = {k: v for k, v in parsed.get("facts", {}).items() if v and v != "..."}
        if facts:
            save_facts_bulk(user_id, facts)
        print(f"[Memory] Summarized for user {user_id}")

    except Exception as e:
        print(f"[Memory] Summarization failed for user {user_id}: {e}")


# ─── Build API payload ────────────────────────────────────────────────────────

def build_messages(user_id: int, system_prompt: str, recent_limit: int = 10) -> list:
    messages = []

    # 1. System prompt
    messages.append({"role": "system", "content": system_prompt})

    # 2. Long-term facts
    facts = get_all_facts(user_id)
    if facts:
        messages.append({"role": "system", "content": facts})

    # 3. Latest conversation summary
    summary = get_latest_summary(user_id)
    if summary:
        messages.append({
            "role": "system",
            "content": f"Summary of earlier conversation:\n{summary}"
        })

    # 4. Recent raw messages
    messages += get_recent_messages(user_id, limit=recent_limit)

    return messages
