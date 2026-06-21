"""Manage doctors table (manual seed / list / add)."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from db import Database


def main() -> int:
    load_dotenv()
    db_path = os.getenv("SQLITE_DB_PATH", "bot.db")
    db = Database(db_path)
    db.init()

    parser = argparse.ArgumentParser(description="Sayeh doctor DB (manual)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all doctors")

    add_p = sub.add_parser("add", help="Add a doctor")
    add_p.add_argument("name", help="Display name, e.g. دکتر ...")
    add_p.add_argument("code", help="Medical council code")
    add_p.add_argument("--chat-id", default="", help="Bale chat_id (optional)")

    args = parser.parse_args()

    if args.cmd == "list":
        doctors = db.list_doctors()
        if not doctors:
            print("No doctors in DB.")
            return 0
        for d in doctors:
            active = "active" if d.is_active else "inactive"
            print(
                f"id={d.id} | {d.display_name} | code={d.medical_council_code} | "
                f"chat_id={d.bale_chat_id or '-'} | {active}"
            )
        return 0

    if args.cmd == "add":
        doctor_id = db.upsert_doctor(
            display_name=args.name,
            medical_council_code=args.code,
            bale_chat_id=args.chat_id,
        )
        print(f"Added doctor id={doctor_id}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
