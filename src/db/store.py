from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from src.db.migrate import (
    DEFAULT_BOT_ID,
    migrate_bot_settings_table,
    migrate_run_numbers,
    run_migrations,
)
from src.db.schema import SCHEMA_SQL
from src.paths import DB_PATH, ensure_data_dir


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def bot_state_key(bot_id: str, key: str) -> str:
    return f"bot:{bot_id}:{key}"


class Store:
    def __init__(self, db_path=DB_PATH) -> None:
        ensure_data_dir()
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            run_migrations(conn)
            migrate_run_numbers(conn)
            migrate_bot_settings_table(conn)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_state WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_state (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_bot_state(
        self, bot_id: str, key: str, default: str | None = None
    ) -> str | None:
        val = self.get_state(bot_state_key(bot_id, key))
        if val is not None:
            return val
        if bot_id == DEFAULT_BOT_ID:
            legacy = {
                "investor_profile_acknowledged": "investor_profile_acknowledged",
            }
            if key in legacy:
                return self.get_state(legacy[key], default)
        return default

    def set_bot_state(self, bot_id: str, key: str, value: str) -> None:
        self.set_state(bot_state_key(bot_id, key), value)

    def is_setup_complete(self) -> bool:
        if self.get_state("setup_complete") == "true":
            return True
        from src.settings.service import SettingsService

        conn = SettingsService().to_connections_dict()["connections"]
        return bool(
            conn.get("cursor_api_key_set")
            and (conn.get("robinhood_has_token") or conn.get("robinhood_via_cursor"))
        )

    def set_setup_complete(self, complete: bool) -> None:
        self.set_state("setup_complete", "true" if complete else "false")

    # --- bots ---

    def list_bots(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bots ORDER BY created_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_bot(self, bot_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM bots WHERE id = ?", (bot_id,)
            ).fetchone()
            return dict(row) if row else None

    def create_bot(self, bot_id: str, name: str) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO bots (id, name, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (bot_id, name, now, now),
            )
        self.set_bot_state(bot_id, "scheduler_paused", "false")
        self.set_bot_state(bot_id, "scheduler_enabled", "true")
        self.set_bot_state(bot_id, "scheduler_started", "false")
        bot = self.get_bot(bot_id)
        assert bot is not None
        return bot

    def delete_bot(self, bot_id: str) -> None:
        if bot_id == DEFAULT_BOT_ID:
            raise ValueError("Cannot delete the default bot")
        with self.connect() as conn:
            run_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM agent_runs WHERE bot_id = ?", (bot_id,)
                ).fetchall()
            ]
            for run_id in run_ids:
                conn.execute(
                    "DELETE FROM agent_events WHERE run_id = ?", (run_id,)
                )
            conn.execute("DELETE FROM agent_runs WHERE bot_id = ?", (bot_id,))
            conn.execute("DELETE FROM bot_settings WHERE bot_id = ?", (bot_id,))
            conn.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
            rows = conn.execute(
                "SELECT key FROM app_state WHERE key LIKE ?",
                (f"bot:{bot_id}:%",),
            ).fetchall()
            for row in rows:
                conn.execute("DELETE FROM app_state WHERE key = ?", (row[0],))

    def update_bot_name(self, bot_id: str, name: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE bots SET name = ?, updated_at = ? WHERE id = ?",
                (name, utc_now(), bot_id),
            )

    def rename_bot(self, old_id: str, new_id: str) -> dict[str, Any]:
        if old_id == DEFAULT_BOT_ID:
            raise ValueError("Cannot rename the default bot")
        if old_id == new_id:
            bot = self.get_bot(old_id)
            if not bot:
                raise KeyError(f"Unknown bot: {old_id}")
            return bot
        if self.get_bot(new_id):
            raise ValueError("Bot ID already in use")
        if not self.get_bot(old_id):
            raise KeyError(f"Unknown bot: {old_id}")

        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE agent_runs SET bot_id = ? WHERE bot_id = ?",
                (new_id, old_id),
            )
            conn.execute(
                "UPDATE bots SET id = ?, updated_at = ? WHERE id = ?",
                (new_id, now, old_id),
            )
            conn.execute(
                "UPDATE bot_settings SET bot_id = ? WHERE bot_id = ?",
                (new_id, old_id),
            )
            rows = conn.execute(
                "SELECT key, value FROM app_state WHERE key LIKE ?",
                (f"bot:{old_id}:%",),
            ).fetchall()
            for row in rows:
                old_key = row["key"]
                suffix = old_key.split(f"bot:{old_id}:", 1)[1]
                new_key = bot_state_key(new_id, suffix)
                conn.execute(
                    """
                    INSERT INTO app_state (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (new_key, row["value"]),
                )
                conn.execute("DELETE FROM app_state WHERE key = ?", (old_key,))

        bot = self.get_bot(new_id)
        assert bot is not None
        return bot

    # --- runs ---

    def create_run(
        self,
        bot_id: str = DEFAULT_BOT_ID,
        trigger: str = "scheduled",
        cursor_run_id: str | None = None,
        cursor_agent_id: str | None = None,
    ) -> int:
        with self.connect() as conn:
            count_row = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_runs WHERE bot_id = ?",
                (bot_id,),
            ).fetchone()
            run_number = int(count_row["c"])
            cur = conn.execute(
                """
                INSERT INTO agent_runs
                (bot_id, run_number, cursor_run_id, cursor_agent_id, trigger, started_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'running')
                """,
                (bot_id, run_number, cursor_run_id, cursor_agent_id, trigger, utc_now()),
            )
            run_id = int(cur.lastrowid)
            return run_id

    def finish_run(
        self,
        run_id: int,
        status: str,
        summary: str | None = None,
        error: str | None = None,
        cursor_run_id: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE agent_runs
                SET finished_at = ?, status = ?, summary = ?, error = ?,
                    cursor_run_id = COALESCE(?, cursor_run_id)
                WHERE id = ?
                """,
                (utc_now(), status, summary, error, cursor_run_id, run_id),
            )

    def add_event(
        self,
        run_id: int,
        event_type: str,
        payload: dict[str, Any],
        ts: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO agent_events (run_id, ts, type, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, ts or utc_now(), event_type, json.dumps(payload)),
            )
            return int(cur.lastrowid)

    def get_runs(
        self, limit: int = 50, bot_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if bot_id:
                rows = conn.execute(
                    """
                    SELECT * FROM agent_runs
                    WHERE bot_id = ?
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (bot_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM agent_runs
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_run(self, run_id: int, bot_id: str | None = None) -> dict[str, Any] | None:
        with self.connect() as conn:
            if bot_id:
                row = conn.execute(
                    "SELECT * FROM agent_runs WHERE id = ? AND bot_id = ?",
                    (run_id, bot_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM agent_runs WHERE id = ?", (run_id,)
                ).fetchone()
            return dict(row) if row else None

    def get_active_run(self, bot_id: str | None = None) -> dict[str, Any] | None:
        with self.connect() as conn:
            if bot_id:
                row = conn.execute(
                    """
                    SELECT * FROM agent_runs
                    WHERE status = 'running' AND bot_id = ?
                    ORDER BY started_at DESC
                    LIMIT 1
                    """,
                    (bot_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM agent_runs
                    WHERE status = 'running'
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                ).fetchone()
            return dict(row) if row else None

    def get_events(self, run_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_events
                WHERE run_id = ?
                ORDER BY id ASC
                """,
                (run_id,),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                result.append(item)
            return result

    def get_last_order_time(self, bot_id: str | None = None) -> str | None:
        with self.connect() as conn:
            if bot_id:
                row = conn.execute(
                    """
                    SELECT e.ts FROM agent_events e
                    JOIN agent_runs r ON r.id = e.run_id
                    WHERE r.bot_id = ?
                      AND e.type IN ('hook_allow', 'tool_call')
                      AND e.payload_json LIKE '%place_equity_order%'
                    ORDER BY e.id DESC
                    LIMIT 1
                    """,
                    (bot_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT ts FROM agent_events
                    WHERE type IN ('hook_allow', 'tool_call')
                      AND payload_json LIKE '%place_equity_order%'
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
            return row["ts"] if row else None

    def log_hook_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        run_id: int | None = None,
    ) -> None:
        if run_id is None:
            active = self.get_active_run()
            run_id = active["id"] if active else None
        if run_id is None:
            run_id = self.create_run(trigger="hook")
            self.finish_run(run_id, "finished", summary="Hook event outside active run")
        self.add_event(run_id, event_type, payload)

    def count_runs(self, bot_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_runs WHERE bot_id = ?",
                (bot_id,),
            ).fetchone()
            return int(row["c"])

    def delete_bot_runs(self, bot_id: str) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM agent_events
                WHERE run_id IN (SELECT id FROM agent_runs WHERE bot_id = ?)
                """,
                (bot_id,),
            )
            cur = conn.execute("DELETE FROM agent_runs WHERE bot_id = ?", (bot_id,))
            return int(cur.rowcount)

    def reconcile_orphaned_runs(self) -> int:
        """Mark runs left as 'running' in the DB after a restart as interrupted."""
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE agent_runs
                SET finished_at = ?, status = 'error',
                    error = COALESCE(error, 'Interrupted by server restart')
                WHERE status = 'running'
                """,
                (utc_now(),),
            )
            return int(cur.rowcount)

    def get_bot_settings(self, bot_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM bot_settings WHERE bot_id = ?",
                (bot_id,),
            ).fetchone()
            return dict(row) if row else None

    def set_bot_settings(
        self,
        bot_id: str,
        *,
        asset_class: str,
        strategy_md: str,
        limits_json: str,
        app_json: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_settings
                    (bot_id, asset_class, strategy_md, limits_json, app_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id) DO UPDATE SET
                    asset_class = excluded.asset_class,
                    strategy_md = excluded.strategy_md,
                    limits_json = excluded.limits_json,
                    app_json = excluded.app_json,
                    updated_at = excluded.updated_at
                """,
                (bot_id, asset_class, strategy_md, limits_json, app_json, utc_now()),
            )

    def update_bot_settings(self, bot_id: str, **fields: str) -> None:
        allowed = {"asset_class", "strategy_md", "limits_json", "app_json"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return
        assignments = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values())
        values.extend([utc_now(), bot_id])
        with self.connect() as conn:
            conn.execute(
                f"UPDATE bot_settings SET {assignments}, updated_at = ? WHERE bot_id = ?",
                values,
            )
