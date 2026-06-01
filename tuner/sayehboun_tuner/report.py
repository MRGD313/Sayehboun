import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def write_report(
    report_dir: Path,
    *,
    session_id: int,
    evaluation: dict[str, Any],
    raw_response: str,
) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now_stamp()
    base = report_dir / f"session_{session_id}_{stamp}"

    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")

    bundle = {
        "session_id": session_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation": evaluation,
        "raw_response": raw_response,
    }
    json_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(evaluation), encoding="utf-8")
    return json_path, md_path


def render_markdown(evaluation: dict[str, Any]) -> str:
    lines: list[str] = []
    session_id = evaluation.get("session_id", "?")
    lines.append(f"# History Taker Tuner Report — session {session_id}")
    lines.append("")

    scores = evaluation.get("scores") or {}
    lines.append("## Scores")
    lines.append("")
    lines.append(f"- **Overall:** {scores.get('overall_score', '?')} / 5")
    lines.append(f"- **Pass:** {scores.get('pass', '?')}")
    for key in [
        "necessary_question_coverage",
        "phase_discipline",
        "format_compliance",
        "no_diagnosis_or_prescription",
        "persian_patient_friendly",
        "decision_support_quality",
    ]:
        if key in scores:
            lines.append(f"- {key}: {scores[key]}")
    lines.append("")

    for section, title in [
        ("top_issues", "Top issues"),
        ("top_strengths", "Top strengths"),
    ]:
        items = evaluation.get(section) or []
        if items:
            lines.append(f"## {title}")
            lines.append("")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")

    comparison = evaluation.get("comparison") or {}
    missing = comparison.get("missing_necessary") or []
    if missing:
        lines.append("## Missing necessary questions")
        lines.append("")
        for item in missing:
            lines.append(f"- {item}")
        lines.append("")

    remaining = evaluation.get("necessary_questions_remaining") or []
    if remaining:
        lines.append("## Necessary questions remaining (detail)")
        lines.append("")
        for item in remaining:
            if isinstance(item, dict):
                q = item.get("question_text", "")
                why = item.get("why_necessary", "")
                status = item.get("status", "")
                lines.append(f"- [{status}] {q}")
                if why:
                    lines.append(f"  - Why: {why}")
            else:
                lines.append(f"- {item}")
        lines.append("")

    summary = evaluation.get("revision_summary") or {}
    if any(summary.get(k) for k in ("why_changed", "expected_improvements", "risks_or_tradeoffs")):
        lines.append("## Revision summary")
        lines.append("")
        for label, key in [
            ("Why changed", "why_changed"),
            ("Expected improvements", "expected_improvements"),
            ("Risks / tradeoffs", "risks_or_tradeoffs"),
        ]:
            items = summary.get(key) or []
            if items:
                lines.append(f"### {label}")
                for item in items:
                    lines.append(f"- {item}")
                lines.append("")

    diff = evaluation.get("diff_highlights") or []
    if diff:
        lines.append("## Diff highlights")
        lines.append("")
        for item in diff:
            lines.append(f"- {item}")
        lines.append("")

    revised = evaluation.get("revised_instructions_full") or ""
    lines.append("## Revised instructions (full prompt)")
    lines.append("")
    lines.append("```text")
    lines.append(revised)
    lines.append("```")
    lines.append("")
    return "\n".join(lines)
