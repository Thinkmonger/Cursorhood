"""One-time SQLite migrations for multi-bot support."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

DEFAULT_BOT_ID = "default"
MIGRATION_KEY = "migration_multibot_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def run_migrations(conn: sqlite3.Connection) -> None:
    if conn.execute(
        "SELECT 1 FROM app_state WHERE key = ?", (MIGRATION_KEY,)
    ).fetchone():
        return

    cols = _table_columns(conn, "agent_runs")
    if "bot_id" not in cols:
        conn.execute(
            "ALTER TABLE agent_runs ADD COLUMN bot_id TEXT NOT NULL DEFAULT 'default'"
        )
        conn.execute(
            "UPDATE agent_runs SET bot_id = 'default' WHERE bot_id IS NULL OR bot_id = ''"
        )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_runs_bot_started
        ON agent_runs(bot_id, started_at DESC)
        """
    )

    now = _utc_now()
    conn.execute(
        """
        INSERT OR IGNORE INTO bots (id, name, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (DEFAULT_BOT_ID, "Default Bot", now, now),
    )

    setup = conn.execute(
        "SELECT value FROM app_state WHERE key = 'setup_complete'"
    ).fetchone()
    if setup and setup[0] == "true":
        conn.execute(
            """
            INSERT OR IGNORE INTO app_state (key, value) VALUES (?, ?)
            """,
            (f"bot:{DEFAULT_BOT_ID}:setup_complete", "true"),
        )

    profile_ack = conn.execute(
        "SELECT value FROM app_state WHERE key = 'investor_profile_acknowledged'"
    ).fetchone()
    if profile_ack:
        conn.execute(
            """
            INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)
            """,
            (
                f"bot:{DEFAULT_BOT_ID}:investor_profile_acknowledged",
                profile_ack[0],
            ),
        )

    conn.execute(
        """
        INSERT OR IGNORE INTO app_state (key, value) VALUES (?, ?)
        """,
        (f"bot:{DEFAULT_BOT_ID}:scheduler_paused", "false"),
    )

    conn.execute(
        """
        INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)
        """,
        (MIGRATION_KEY, datetime.now(timezone.utc).isoformat()),
    )


RUN_NUMBER_MIGRATION_KEY = "migration_run_number_v1"


def migrate_run_numbers(conn: sqlite3.Connection) -> None:
    if conn.execute(
        "SELECT 1 FROM app_state WHERE key = ?", (RUN_NUMBER_MIGRATION_KEY,)
    ).fetchone():
        return

    cols = _table_columns(conn, "agent_runs")
    if "run_number" not in cols:
        conn.execute("ALTER TABLE agent_runs ADD COLUMN run_number INTEGER")

    bot_rows = conn.execute(
        "SELECT DISTINCT bot_id FROM agent_runs ORDER BY bot_id"
    ).fetchall()
    for bot_row in bot_rows:
        bot_id = bot_row[0]
        runs = conn.execute(
            """
            SELECT id FROM agent_runs
            WHERE bot_id = ?
            ORDER BY id ASC
            """,
            (bot_id,),
        ).fetchall()
        for index, run_row in enumerate(runs):
            conn.execute(
                "UPDATE agent_runs SET run_number = ? WHERE id = ?",
                (index, run_row[0]),
            )

    conn.execute(
        """
        INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)
        """,
        (RUN_NUMBER_MIGRATION_KEY, _utc_now()),
    )
