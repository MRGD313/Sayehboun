import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


def _message_text_by_role(messages: list[dict[str, Any]], role: str) -> str:
    for item in reversed(messages):
        if item.get("role") == role:
            return (item.get("text") or "").strip()
    return ""


def _normalize_compare_text(text: str) -> str:
    return " ".join(text.split())


def _build_judging_input_payload(
    *,
    patient_chat_id: str,
    session_id: int,
    chief_complaint: str,
    demographics: dict[str, Any],
    messages: list[dict[str, Any]],
) -> str:
    demo_lines = [f"{key}: {value}" for key, value in demographics.items()]
    # Exclude post-judgment roles from input replay (matches production payload timing).
    skip_roles = {"judgment", "judgment_to_patient", "formatter"}
    message_lines = [
        f"[{item.get('role', 'unknown')}] {item.get('text', '')}"
        for item in messages
        if item.get("role") not in skip_roles
    ]
    return (
        f"patient_chat_id: {patient_chat_id}\n"
        f"session_id: {session_id}\n"
        f"chief_complaint: {chief_complaint}\n\n"
        "demographics:\n"
        + "\n".join(demo_lines)
        + "\n\nsession_messages:\n"
        + "\n".join(message_lines)
    )


@dataclass
class JudgingSessionRecord:
    id: int
    chat_id: str
    chief_complaint: str
    messages: list[dict[str, Any]]
    demographics: dict[str, Any]
    created_at: str
    updated_at: str
    judging_bot_output: str
    doctor_final_text: str
    formatted_history: str
    judging_input_payload: str

    @property
    def doctor_action(self) -> str:
        if not self.judging_bot_output or not self.doctor_final_text:
            return "unknown"
        if _normalize_compare_text(self.judging_bot_output) == _normalize_compare_text(
            self.doctor_final_text
        ):
            return "approved"
        return "edited"

    @property
    def is_tunable(self) -> bool:
        return bool(self.judging_bot_output.strip() and self.doctor_final_text.strip())


def _row_to_judging_session(conn: sqlite3.Connection, row: sqlite3.Row) -> JudgingSessionRecord:
    chat_id = row["chat_id"]
    messages = json.loads(row["messages_json"])
    demographics = _load_demographics(conn, chat_id)
    session_id = int(row["id"])
    chief_complaint = row["chief_complaint"] or ""
    return JudgingSessionRecord(
        id=session_id,
        chat_id=chat_id,
        chief_complaint=chief_complaint,
        messages=messages,
        demographics=demographics,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        judging_bot_output=_message_text_by_role(messages, "judgment"),
        doctor_final_text=_message_text_by_role(messages, "judgment_to_patient"),
        formatted_history=_message_text_by_role(messages, "formatter"),
        judging_input_payload=_build_judging_input_payload(
            patient_chat_id=chat_id,
            session_id=session_id,
            chief_complaint=chief_complaint,
            demographics=demographics,
            messages=messages,
        ),
    )


def list_judging_sessions(db_path: Path) -> list[JudgingSessionRecord]:
    if not db_path.exists():
        raise FileNotFoundError(f"Sayehboun DB not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, chat_id, chief_complaint, messages_json, created_at, updated_at
            FROM sessions
            ORDER BY id DESC
            """
        ).fetchall()
        sessions = [_row_to_judging_session(conn, row) for row in rows]

    return [s for s in sessions if s.is_tunable]


def get_judging_session(db_path: Path, session_id: int) -> JudgingSessionRecord | None:
    if not db_path.exists():
        raise FileNotFoundError(f"Sayehboun DB not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, chat_id, chief_complaint, messages_json, created_at, updated_at
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return None
        session = _row_to_judging_session(conn, row)

    return session if session.is_tunable else None


def build_judging_evaluator_payload(
    session: JudgingSessionRecord,
    *,
    prompt_version: str,
    judging_model: str,
    current_instructions_full: str,
) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "prompt_version": prompt_version,
        "judging_model": judging_model,
        "current_prompt": current_instructions_full,
        "formatted_history": session.formatted_history,
        "judging_bot_output": session.judging_bot_output,
        "doctor_final_to_patient": session.doctor_final_text,
        "doctor_action": session.doctor_action,
    }
