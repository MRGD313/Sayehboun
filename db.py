import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionState:
    id: int
    chat_id: str
    current_phase: int
    chief_complaint: str
    pending_field: str
    waiting_for_continue: int
    answer_buffer: list[str]
    messages: list[dict[str, Any]]
    created_at: str
    updated_at: str


@dataclass
class JudgmentReview:
    session_id: int
    patient_chat_id: str
    judgment_text: str
    status: str
    created_at: str
    updated_at: str


@dataclass
class Followup:
    session_id: int
    patient_chat_id: str
    due_at: str
    urgency_class: str
    status: str
    step: str
    trend: str
    created_at: str
    updated_at: str


@dataclass
class NpsSurvey:
    id: int
    session_id: int
    chat_id: str
    survey_type: str
    trigger_event: str
    due_at: str
    status: str
    step: str
    score: int | None
    comment: str
    inline_message_id: int | None
    created_at: str
    updated_at: str


@dataclass
class Doctor:
    id: int
    display_name: str
    medical_council_code: str
    bale_chat_id: str
    is_active: int
    created_at: str
    updated_at: str


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profiles (
                    chat_id TEXT PRIMARY KEY,
                    demographics_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    current_phase INTEGER NOT NULL DEFAULT 1,
                    chief_complaint TEXT NOT NULL DEFAULT '',
                    pending_field TEXT NOT NULL DEFAULT '',
                    waiting_for_continue INTEGER NOT NULL DEFAULT 0,
                    answer_buffer_json TEXT NOT NULL DEFAULT '[]',
                    messages_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = conn.execute("PRAGMA table_info(sessions)").fetchall()
            column_names = {row["name"] for row in columns}
            if "pending_field" not in column_names:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN pending_field TEXT NOT NULL DEFAULT ''"
                )
            if "waiting_for_continue" not in column_names:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN waiting_for_continue INTEGER NOT NULL DEFAULT 0"
                )
            if "answer_buffer_json" not in column_names:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN answer_buffer_json TEXT NOT NULL DEFAULT '[]'"
                )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_chat_id_id
                ON sessions(chat_id, id DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS judgment_reviews (
                    session_id INTEGER PRIMARY KEY,
                    patient_chat_id TEXT NOT NULL,
                    judgment_text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS followups (
                    session_id INTEGER PRIMARY KEY,
                    patient_chat_id TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    urgency_class TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    step TEXT NOT NULL DEFAULT '',
                    trend TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_followups_status_due
                ON followups(status, due_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nps_surveys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    chat_id TEXT NOT NULL,
                    survey_type TEXT NOT NULL,
                    trigger_event TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    step TEXT NOT NULL DEFAULT '',
                    score INTEGER,
                    comment TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(session_id, survey_type)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_nps_status_due
                ON nps_surveys(status, due_at)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_nps_chat_type_created
                ON nps_surveys(chat_id, survey_type, created_at)
                """
            )
            try:
                conn.execute(
                    "ALTER TABLE nps_surveys ADD COLUMN inline_message_id INTEGER"
                )
            except sqlite3.OperationalError:
                pass
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS doctors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    display_name TEXT NOT NULL,
                    medical_council_code TEXT NOT NULL DEFAULT '',
                    bale_chat_id TEXT NOT NULL DEFAULT '',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_doctors_medical_code
                ON doctors(medical_council_code)
                WHERE medical_council_code != ''
                """
            )
            self._seed_default_doctors(conn)

    def _row_to_nps_survey(self, row: sqlite3.Row) -> NpsSurvey:
        score_raw = row["score"]
        inline_raw = row["inline_message_id"] if "inline_message_id" in row.keys() else None
        return NpsSurvey(
            id=int(row["id"]),
            session_id=int(row["session_id"]),
            chat_id=row["chat_id"],
            survey_type=row["survey_type"],
            trigger_event=row["trigger_event"],
            due_at=row["due_at"],
            status=row["status"],
            step=row["step"],
            score=int(score_raw) if score_raw is not None else None,
            comment=row["comment"] or "",
            inline_message_id=int(inline_raw) if inline_raw is not None else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _seed_default_doctors(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT COUNT(*) AS n FROM doctors").fetchone()
        if row and int(row["n"]) > 0:
            return
        now = _now_iso()
        conn.execute(
            """
            INSERT INTO doctors(
                display_name, medical_council_code, bale_chat_id,
                is_active, created_at, updated_at
            )
            VALUES (?, ?, '', 1, ?, ?)
            """,
            ("دکتر محمدرضا گنج دانش", "214433", now, now),
        )

    def get_demographics(self, chat_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT demographics_json FROM user_profiles WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        if not row:
            return {}
        return json.loads(row["demographics_json"])

    def upsert_demographics(self, chat_id: str, demographics: dict[str, Any]) -> None:
        now = _now_iso()
        payload = json.dumps(demographics, ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_profiles(chat_id, demographics_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    demographics_json = excluded.demographics_json,
                    updated_at = excluded.updated_at
                """,
                (chat_id, payload, now, now),
            )

    def create_session(self, chat_id: str) -> int:
        now = _now_iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sessions(chat_id, current_phase, chief_complaint, messages_json, created_at, updated_at)
                VALUES (?, 1, '', '[]', ?, ?)
                """,
                (chat_id, now, now),
            )
            return int(cursor.lastrowid)

    def get_doctor_chat_id(self) -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("doctor_chat_id",),
            ).fetchone()
        if not row:
            return ""
        return str(row["value"]).strip()

    def set_doctor_chat_id(self, chat_id: str) -> None:
        now = _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES ('doctor_chat_id', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (chat_id, now),
            )

    def get_session_by_id(self, session_id: int) -> SessionState | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, chat_id, current_phase, chief_complaint, pending_field, waiting_for_continue, answer_buffer_json, messages_json, created_at, updated_at
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return SessionState(
            id=int(row["id"]),
            chat_id=row["chat_id"],
            current_phase=int(row["current_phase"]),
            chief_complaint=row["chief_complaint"],
            pending_field=row["pending_field"] if "pending_field" in row.keys() else "",
            waiting_for_continue=int(row["waiting_for_continue"])
            if "waiting_for_continue" in row.keys()
            else 0,
            answer_buffer=json.loads(row["answer_buffer_json"])
            if "answer_buffer_json" in row.keys()
            else [],
            messages=json.loads(row["messages_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_latest_session(self, chat_id: str) -> SessionState | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, chat_id, current_phase, chief_complaint, pending_field, waiting_for_continue, answer_buffer_json, messages_json, created_at, updated_at
                FROM sessions
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (chat_id,),
            ).fetchone()
        if not row:
            return None
        return SessionState(
            id=int(row["id"]),
            chat_id=row["chat_id"],
            current_phase=int(row["current_phase"]),
            chief_complaint=row["chief_complaint"],
            pending_field=row["pending_field"] if "pending_field" in row.keys() else "",
            waiting_for_continue=int(row["waiting_for_continue"])
            if "waiting_for_continue" in row.keys()
            else 0,
            answer_buffer=json.loads(row["answer_buffer_json"])
            if "answer_buffer_json" in row.keys()
            else [],
            messages=json.loads(row["messages_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def update_session(
        self,
        session_id: int,
        *,
        current_phase: int | None = None,
        chief_complaint: str | None = None,
        pending_field: str | None = None,
        waiting_for_continue: int | None = None,
        answer_buffer: list[str] | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        updates: list[str] = []
        params: list[Any] = []
        if current_phase is not None:
            updates.append("current_phase = ?")
            params.append(current_phase)
        if chief_complaint is not None:
            updates.append("chief_complaint = ?")
            params.append(chief_complaint)
        if pending_field is not None:
            updates.append("pending_field = ?")
            params.append(pending_field)
        if waiting_for_continue is not None:
            updates.append("waiting_for_continue = ?")
            params.append(waiting_for_continue)
        if answer_buffer is not None:
            updates.append("answer_buffer_json = ?")
            params.append(json.dumps(answer_buffer, ensure_ascii=False))
        if messages is not None:
            updates.append("messages_json = ?")
            params.append(json.dumps(messages, ensure_ascii=False))
        updates.append("updated_at = ?")
        params.append(_now_iso())
        params.append(session_id)
        set_clause = ", ".join(updates)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE sessions SET {set_clause} WHERE id = ?",
                tuple(params),
            )

    def append_message(self, session_id: int, role: str, text: str) -> None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT messages_json FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return
            messages = json.loads(row["messages_json"])
            messages.append(
                {
                    "role": role,
                    "text": text,
                    "timestamp": _now_iso(),
                }
            )
            conn.execute(
                """
                UPDATE sessions
                SET messages_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(messages, ensure_ascii=False), _now_iso(), session_id),
            )

    def upsert_judgment_review(
        self,
        session_id: int,
        patient_chat_id: str,
        judgment_text: str,
    ) -> None:
        now = _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO judgment_reviews(
                    session_id, patient_chat_id, judgment_text, status, created_at, updated_at
                )
                VALUES (?, ?, ?, 'pending', ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    patient_chat_id = excluded.patient_chat_id,
                    judgment_text = excluded.judgment_text,
                    status = 'pending',
                    updated_at = excluded.updated_at
                """,
                (session_id, patient_chat_id, judgment_text, now, now),
            )

    def get_judgment_review(self, session_id: int) -> JudgmentReview | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT session_id, patient_chat_id, judgment_text, status, created_at, updated_at
                FROM judgment_reviews
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return JudgmentReview(
            session_id=int(row["session_id"]),
            patient_chat_id=row["patient_chat_id"],
            judgment_text=row["judgment_text"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def set_judgment_review_status(self, session_id: int, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE judgment_reviews
                SET status = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (status, _now_iso(), session_id),
            )

    def get_editing_judgment_review(self) -> JudgmentReview | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT session_id, patient_chat_id, judgment_text, status, created_at, updated_at
                FROM judgment_reviews
                WHERE status = 'editing'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        return JudgmentReview(
            session_id=int(row["session_id"]),
            patient_chat_id=row["patient_chat_id"],
            judgment_text=row["judgment_text"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def set_doctor_active_review_session(self, session_id: int) -> None:
        now = _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES ('doctor_active_review_session_id', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (str(session_id), now),
            )

    def get_doctor_active_review_session_id(self) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("doctor_active_review_session_id",),
            ).fetchone()
        if not row:
            return None
        raw = str(row["value"]).strip()
        return int(raw) if raw.isdigit() else None

    def _row_to_followup(self, row: sqlite3.Row) -> Followup:
        return Followup(
            session_id=int(row["session_id"]),
            patient_chat_id=row["patient_chat_id"],
            due_at=row["due_at"],
            urgency_class=row["urgency_class"],
            status=row["status"],
            step=row["step"],
            trend=row["trend"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_followup(
        self,
        session_id: int,
        patient_chat_id: str,
        due_at: str,
        urgency_class: str,
    ) -> None:
        now = _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO followups(
                    session_id, patient_chat_id, due_at, urgency_class,
                    status, step, trend, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'scheduled', '', '', ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    patient_chat_id = excluded.patient_chat_id,
                    due_at = excluded.due_at,
                    urgency_class = excluded.urgency_class,
                    status = 'scheduled',
                    step = '',
                    trend = '',
                    updated_at = excluded.updated_at
                """,
                (session_id, patient_chat_id, due_at, urgency_class, now, now),
            )

    def get_followup(self, session_id: int) -> Followup | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT session_id, patient_chat_id, due_at, urgency_class,
                       status, step, trend, created_at, updated_at
                FROM followups
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_followup(row)

    def get_active_followup_for_chat(self, patient_chat_id: str) -> Followup | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT session_id, patient_chat_id, due_at, urgency_class,
                       status, step, trend, created_at, updated_at
                FROM followups
                WHERE patient_chat_id = ?
                  AND status IN ('scheduled', 'in_progress')
                ORDER BY session_id DESC
                LIMIT 1
                """,
                (patient_chat_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_followup(row)

    def get_due_followups(self, now_iso: str) -> list[Followup]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT session_id, patient_chat_id, due_at, urgency_class,
                       status, step, trend, created_at, updated_at
                FROM followups
                WHERE status = 'scheduled' AND due_at <= ?
                ORDER BY due_at ASC
                """,
                (now_iso,),
            ).fetchall()
        return [self._row_to_followup(row) for row in rows]

    def update_followup(
        self,
        session_id: int,
        *,
        status: str | None = None,
        step: str | None = None,
        trend: str | None = None,
    ) -> None:
        updates: list[str] = []
        params: list[Any] = []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if step is not None:
            updates.append("step = ?")
            params.append(step)
        if trend is not None:
            updates.append("trend = ?")
            params.append(trend)
        if not updates:
            return
        updates.append("updated_at = ?")
        params.append(_now_iso())
        params.append(session_id)
        set_clause = ", ".join(updates)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE followups SET {set_clause} WHERE session_id = ?",
                tuple(params),
            )

    def try_claim_due_followup(self, session_id: int) -> bool:
        """Atomically move scheduled -> in_progress (trend step)."""
        now = _now_iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE followups
                SET status = 'in_progress', step = 'trend', updated_at = ?
                WHERE session_id = ? AND status = 'scheduled'
                """,
                (now, session_id),
            )
            return cursor.rowcount > 0

    def cancel_followups_for_chat(self, patient_chat_id: str) -> None:
        now = _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE followups
                SET status = 'cancelled', updated_at = ?
                WHERE patient_chat_id = ?
                  AND status IN ('scheduled', 'in_progress')
                """,
                (now, patient_chat_id),
            )

    def _doctor_nps_exists_today(self, conn: sqlite3.Connection, doctor_chat_id: str) -> bool:
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        row = conn.execute(
            """
            SELECT 1 FROM nps_surveys
            WHERE chat_id = ? AND survey_type = 'doctor' AND created_at >= ?
            LIMIT 1
            """,
            (doctor_chat_id, start),
        ).fetchone()
        return row is not None

    def create_nps_survey(
        self,
        session_id: int,
        chat_id: str,
        survey_type: str,
        trigger_event: str,
        due_at: str,
    ) -> int | None:
        now = _now_iso()
        with self.connect() as conn:
            if survey_type == "doctor" and self._doctor_nps_exists_today(conn, chat_id):
                return None
            cursor = conn.execute(
                """
                INSERT INTO nps_surveys(
                    session_id, chat_id, survey_type, trigger_event, due_at,
                    status, step, score, comment, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'scheduled', '', NULL, '', ?, ?)
                ON CONFLICT(session_id, survey_type) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    trigger_event = excluded.trigger_event,
                    due_at = excluded.due_at,
                    status = 'scheduled',
                    step = '',
                    score = NULL,
                    comment = '',
                    updated_at = excluded.updated_at
                WHERE nps_surveys.status != 'completed'
                """,
                (session_id, chat_id, survey_type, trigger_event, due_at, now, now),
            )
            if cursor.rowcount == 0:
                row = conn.execute(
                    """
                    SELECT id FROM nps_surveys
                    WHERE session_id = ? AND survey_type = ?
                    """,
                    (session_id, survey_type),
                ).fetchone()
                return int(row["id"]) if row else None
            if cursor.lastrowid:
                return int(cursor.lastrowid)
            row = conn.execute(
                """
                SELECT id FROM nps_surveys
                WHERE session_id = ? AND survey_type = ?
                """,
                (session_id, survey_type),
            ).fetchone()
            return int(row["id"]) if row else None

    def get_nps_survey(self, survey_id: int) -> NpsSurvey | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, session_id, chat_id, survey_type, trigger_event,
                       due_at, status, step, score, comment, inline_message_id,
                       created_at, updated_at
                FROM nps_surveys
                WHERE id = ?
                """,
                (survey_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_nps_survey(row)

    def get_active_nps_for_chat(self, chat_id: str) -> NpsSurvey | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, session_id, chat_id, survey_type, trigger_event,
                       due_at, status, step, score, comment, inline_message_id,
                       created_at, updated_at
                FROM nps_surveys
                WHERE chat_id = ?
                  AND status = 'in_progress'
                ORDER BY id DESC
                LIMIT 1
                """,
                (chat_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_nps_survey(row)

    def get_due_nps_surveys(self, now_iso: str) -> list[NpsSurvey]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, chat_id, survey_type, trigger_event,
                       due_at, status, step, score, comment, inline_message_id,
                       created_at, updated_at
                FROM nps_surveys
                WHERE status = 'scheduled' AND due_at <= ?
                ORDER BY due_at ASC
                """,
                (now_iso,),
            ).fetchall()
        return [self._row_to_nps_survey(row) for row in rows]

    def try_claim_due_nps_survey(self, survey_id: int) -> bool:
        now = _now_iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE nps_surveys
                SET status = 'in_progress', step = 'score', updated_at = ?
                WHERE id = ? AND status = 'scheduled'
                """,
                (now, survey_id),
            )
            return cursor.rowcount > 0

    def update_nps_survey(
        self,
        survey_id: int,
        *,
        status: str | None = None,
        step: str | None = None,
        score: int | None = None,
        comment: str | None = None,
        inline_message_id: int | None = None,
        clear_score: bool = False,
        clear_inline_message_id: bool = False,
    ) -> None:
        updates: list[str] = []
        params: list[Any] = []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if step is not None:
            updates.append("step = ?")
            params.append(step)
        if clear_score:
            updates.append("score = NULL")
        elif score is not None:
            updates.append("score = ?")
            params.append(score)
        if comment is not None:
            updates.append("comment = ?")
            params.append(comment)
        if clear_inline_message_id:
            updates.append("inline_message_id = NULL")
        elif inline_message_id is not None:
            updates.append("inline_message_id = ?")
            params.append(inline_message_id)
        if not updates:
            return
        updates.append("updated_at = ?")
        params.append(_now_iso())
        params.append(survey_id)
        set_clause = ", ".join(updates)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE nps_surveys SET {set_clause} WHERE id = ?",
                tuple(params),
            )

    def cancel_nps_for_chat(self, chat_id: str) -> None:
        now = _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE nps_surveys
                SET status = 'cancelled', updated_at = ?
                WHERE chat_id = ?
                  AND survey_type = 'patient'
                  AND status IN ('scheduled', 'in_progress')
                """,
                (now, chat_id),
            )

    def _row_to_doctor(self, row: sqlite3.Row) -> Doctor:
        return Doctor(
            id=int(row["id"]),
            display_name=row["display_name"],
            medical_council_code=row["medical_council_code"],
            bale_chat_id=row["bale_chat_id"] or "",
            is_active=int(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    _DOCTOR_COLUMNS = (
        "id, display_name, medical_council_code, bale_chat_id, "
        "is_active, created_at, updated_at"
    )

    def upsert_doctor(
        self,
        *,
        display_name: str,
        medical_council_code: str = "",
        bale_chat_id: str = "",
        is_active: int = 1,
        doctor_id: int | None = None,
    ) -> int:
        now = _now_iso()
        with self.connect() as conn:
            if doctor_id is not None:
                conn.execute(
                    """
                    UPDATE doctors
                    SET display_name = ?, medical_council_code = ?,
                        bale_chat_id = ?, is_active = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        display_name,
                        medical_council_code,
                        bale_chat_id,
                        is_active,
                        now,
                        doctor_id,
                    ),
                )
                return doctor_id
            cursor = conn.execute(
                """
                INSERT INTO doctors(
                    display_name, medical_council_code, bale_chat_id,
                    is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    display_name,
                    medical_council_code,
                    bale_chat_id,
                    is_active,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def get_doctor_by_id(self, doctor_id: int) -> Doctor | None:
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT {self._DOCTOR_COLUMNS}
                FROM doctors WHERE id = ?
                """,
                (doctor_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_doctor(row)

    def get_doctor_by_bale_chat_id(self, bale_chat_id: str) -> Doctor | None:
        chat_id = bale_chat_id.strip()
        if not chat_id:
            return None
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT {self._DOCTOR_COLUMNS}
                FROM doctors
                WHERE bale_chat_id = ? AND is_active = 1
                LIMIT 1
                """,
                (chat_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_doctor(row)

    def get_active_doctor(self) -> Doctor | None:
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT {self._DOCTOR_COLUMNS}
                FROM doctors
                WHERE is_active = 1
                ORDER BY id ASC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        return self._row_to_doctor(row)

    def link_doctor_bale_chat_id(self, doctor_id: int, bale_chat_id: str) -> None:
        now = _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE doctors
                SET bale_chat_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (bale_chat_id.strip(), now, doctor_id),
            )

    def list_doctors(self) -> list[Doctor]:
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {self._DOCTOR_COLUMNS}
                FROM doctors
                ORDER BY id ASC
                """
            ).fetchall()
        return [self._row_to_doctor(row) for row in rows]

