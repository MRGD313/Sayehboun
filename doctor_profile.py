"""Doctor profile helpers for patient-facing judgment delivery."""
from __future__ import annotations

from db import Database, Doctor

PATIENT_APPROVAL_PREFIX = "تایید شده توسط "


def resolve_reviewing_doctor(db: Database, bale_chat_id: str = "") -> Doctor | None:
    chat_id = bale_chat_id.strip()
    if chat_id:
        doctor = db.get_doctor_by_bale_chat_id(chat_id)
        if doctor:
            return doctor
    return db.get_active_doctor()


def format_judgment_for_patient(judgment_body: str, doctor: Doctor | None) -> str:
    body = judgment_body.strip()
    if not body:
        return body
    if not doctor:
        return body
    if body.startswith(PATIENT_APPROVAL_PREFIX):
        return body
    header = f"{PATIENT_APPROVAL_PREFIX}{doctor.display_name}"
    code = (doctor.medical_council_code or "").strip()
    if code:
        header += f" (کد نظام پزشکی : {code})"
    return f"{header}\n\n{body}"


def strip_patient_approval_header(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith(PATIENT_APPROVAL_PREFIX):
        parts = cleaned.split("\n\n", 1)
        if len(parts) == 2:
            return parts[1].strip()
    return cleaned
