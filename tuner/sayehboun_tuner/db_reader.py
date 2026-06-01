import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PHASE3_ENDING = (
    "لطفاً به این موارد پاسخ دهید تا اطلاعات شما برای بررسی نهایی آماده شود."
)
FINAL_PATIENT_MSG = "اطلاعات شما ثبت شد"


@dataclass
class SessionRecord:
    id: int
    chat_id: str
    chief_complaint: str
    current_phase: int
    messages: list[dict[str, Any]]
    demographics: dict[str, Any]
    created_at: str
    updated_at: str

    @property
    def history_taker_outputs(self) -> list[str]:
        outputs: list[str] = []
        for item in self.messages:
            if item.get("role") != "bot":
                continue
            text = (item.get("text") or "").strip()
            if "🔶" in text:
                outputs.append(text)
        return outputs

    @property
    def phases_completed(self) -> int:
        if any(item.get("role") == "formatter" for item in self.messages):
            return 3
        phase = 1
        for item in self.messages:
            if item.get("role") != "bot":
                continue
            text = item.get("text") or ""
            if "🔶" not in text:
                continue
            if PHASE3_ENDING in text:
                return 3
            if "سوالات تکمیلی (فاز بعدی)" in text:
                phase = max(phase, 1)
            if phase >= 1 and "فاز بعدی" in text and PHASE3_ENDING not in text:
                phase = max(phase, 2)
        return min(max(phase, self.current_phase), 3)

    @property
    def is_complete_enough(self) -> bool:
        if not self.chief_complaint.strip():
            return False
        if not self.history_taker_outputs:
            return False
        if self.phases_completed < 3:
            return False
        return True


def _load_demographics(conn: sqlite3.Connection, chat_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT demographics_json FROM user_profiles WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    if not row:
        return {}
    data = json.loads(row["demographics_json"])
    return {
        key: value
        for key, value in data.items()
        if not str(key).startswith("_")
    }


def _row_to_session(conn: sqlite3.Connection, row: sqlite3.Row) -> SessionRecord:
    chat_id = row["chat_id"]
    return SessionRecord(
        id=int(row["id"]),
        chat_id=chat_id,
        chief_complaint=row["chief_complaint"] or "",
        current_phase=int(row["current_phase"]),
        messages=json.loads(row["messages_json"]),
        demographics=_load_demographics(conn, chat_id),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_sessions(db_path: Path, *, complete_only: bool = True) -> list[SessionRecord]:
    if not db_path.exists():
        raise FileNotFoundError(f"Sayehboun DB not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, chat_id, current_phase, chief_complaint, messages_json,
                   created_at, updated_at
            FROM sessions
            ORDER BY id DESC
            """
        ).fetchall()
        sessions = [_row_to_session(conn, row) for row in rows]

    if complete_only:
        sessions = [s for s in sessions if s.is_complete_enough]
    return sessions


def get_session(db_path: Path, session_id: int) -> SessionRecord | None:
    if not db_path.exists():
        raise FileNotFoundError(f"Sayehboun DB not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, chat_id, current_phase, chief_complaint, messages_json,
                   created_at, updated_at
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_session(conn, row)


def build_evaluator_payload(
    session: SessionRecord,
    *,
    prompt_version: str,
    history_taker_model: str,
    current_instructions_full: str,
) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "chat_id": session.chat_id,
        "created_at": session.created_at,
        "prompt_version": prompt_version,
        "history_taker_model": history_taker_model,
        "current_instructions_full": current_instructions_full,
        "chief_complaint": session.chief_complaint,
        "demographics": session.demographics,
        "session_messages": session.messages,
        "history_taker_outputs": session.history_taker_outputs,
        "phases_completed": session.phases_completed,
    }
