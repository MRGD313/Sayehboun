import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def registry_path(tuner_root: Path) -> Path:
    return tuner_root / "judging_tuned_sessions.json"


def _empty_registry() -> dict[str, Any]:
    return {"sessions": {}}


def load_registry(tuner_root: Path) -> dict[str, Any]:
    path = registry_path(tuner_root)
    if not path.exists():
        return _empty_registry()
    return json.loads(path.read_text(encoding="utf-8"))


def save_registry(tuner_root: Path, registry: dict[str, Any]) -> None:
    path = registry_path(tuner_root)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def bootstrap_from_reports(tuner_root: Path) -> dict[str, Any]:
    registry = load_registry(tuner_root)
    if registry.get("sessions"):
        return registry

    report_dir = tuner_root / "judging_reports"
    if not report_dir.is_dir():
        return registry

    pattern = re.compile(r"^session_(\d+)_\d{8}_\d{6}\.json$")
    for path in sorted(report_dir.glob("session_*.json")):
        match = pattern.match(path.name)
        if not match:
            continue
        session_id = match.group(1)
        if session_id in registry["sessions"]:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not payload.get("revised_prompt"):
            continue
        registry["sessions"][session_id] = {
            "tuned_at": payload.get("generated_at")
            or datetime.now(timezone.utc).isoformat(),
            "report": str(path.relative_to(tuner_root)).replace("\\", "/"),
            "prompt_version": payload.get("prompt_version") or "",
        }

    if registry["sessions"]:
        save_registry(tuner_root, registry)
    return registry


def list_tuned_session_ids(tuner_root: Path) -> set[int]:
    registry = bootstrap_from_reports(tuner_root)
    return {int(sid) for sid in registry.get("sessions", {})}


def get_tune_record(tuner_root: Path, session_id: int) -> dict[str, Any] | None:
    registry = bootstrap_from_reports(tuner_root)
    return registry.get("sessions", {}).get(str(session_id))


def is_session_tuned(tuner_root: Path, session_id: int) -> bool:
    return get_tune_record(tuner_root, session_id) is not None


def mark_session_tuned(
    tuner_root: Path,
    session_id: int,
    *,
    report_path: Path,
    prompt_version: str,
) -> None:
    registry = bootstrap_from_reports(tuner_root)
    try:
        rel_report = report_path.relative_to(tuner_root)
    except ValueError:
        rel_report = report_path
    registry.setdefault("sessions", {})[str(session_id)] = {
        "tuned_at": datetime.now(timezone.utc).isoformat(),
        "report": str(rel_report).replace("\\", "/"),
        "prompt_version": prompt_version,
    }
    save_registry(tuner_root, registry)
