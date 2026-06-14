import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def write_judging_report(
    report_dir: Path,
    *,
    session_id: int,
    prompt_version: str,
    revised_prompt: str,
    raw_response: str,
    input_payload: dict[str, Any],
) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now_stamp()
    base = report_dir / f"session_{session_id}_{stamp}"

    json_path = base.with_suffix(".json")
    prompt_path = base.with_name(base.name + "_revised_prompt.txt")

    bundle = {
        "session_id": session_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prompt_version": prompt_version,
        "input": input_payload,
        "revised_prompt": revised_prompt,
        "raw_response": raw_response,
    }
    json_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    prompt_path.write_text(revised_prompt, encoding="utf-8")
    return json_path, prompt_path
