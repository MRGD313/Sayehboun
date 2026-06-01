import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from sayehboun_tuner.config import load_settings
from sayehboun_tuner.console import safe_print
from sayehboun_tuner.db_reader import build_evaluator_payload, get_session, list_sessions
from sayehboun_tuner.metis_client import (
    fetch_bot_instructions,
    parse_json_from_llm,
    save_bot_instructions,
    send_evaluator_request,
)
from sayehboun_tuner.prompt_versions import (
    get_current_version,
    load_manifest,
    register_version,
    set_current_version,
    update_env_prompt_version,
    write_current_marker,
)
from sayehboun_tuner.report import write_report
from sayehboun_tuner.tuned_sessions import (
    get_tune_record,
    is_session_tuned,
    list_tuned_session_ids,
    mark_session_tuned,
)


def cmd_prompt_list(args: argparse.Namespace) -> int:
    settings = load_settings()
    manifest = load_manifest(settings.tuner_root)
    current = manifest.get("current", "?")
    safe_print(f"Current history taker prompt: {current}")
    safe_print("")
    for version, meta in sorted(manifest.get("versions", {}).items()):
        mark = " *" if version == current else ""
        note = meta.get("note") or ""
        safe_print(f"  {version}{mark}  {note}")
    return 0


def cmd_prompt_current(args: argparse.Namespace) -> int:
    settings = load_settings()
    version = get_current_version(settings.tuner_root)
    path = settings.tuner_root / "prompts" / "versions" / f"history_taker_{version}.txt"
    safe_print(f"current={version}")
    safe_print(f"file={path}")
    safe_print(f"env PROMPT_VERSION={settings.prompt_version}")
    return 0


def cmd_prompt_set_current(args: argparse.Namespace) -> int:
    settings = load_settings()
    version = args.version
    set_current_version(settings.tuner_root, version)
    update_env_prompt_version(settings.sayehboun_root, version)
    write_current_marker(settings.tuner_root, version)
    safe_print(f"Current prompt set to {version} (manifest + .env updated)")
    return 0


def cmd_prompt_register(args: argparse.Namespace) -> int:
    settings = load_settings()
    source = Path(args.file)
    if not source.is_absolute():
        source = (Path.cwd() / source).resolve()
    dest = register_version(
        settings.tuner_root,
        args.version,
        source_file=source,
        note=args.note or "",
        source_session_id=args.session_id,
        set_current=args.set_current,
    )
    if args.set_current:
        update_env_prompt_version(settings.sayehboun_root, args.version)
        write_current_marker(settings.tuner_root, args.version)
    safe_print(f"Registered {args.version} -> {dest}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    settings = load_settings()
    sessions = list_sessions(settings.db_path, complete_only=not args.all)
    if not sessions:
        safe_print("No sessions found.")
        return 0
    tuned_ids = list_tuned_session_ids(settings.tuner_root)
    safe_print(f"{'ID':>4}  {'phase':>5}  {'outputs':>7}  {'tuned':>5}  chief_complaint")
    safe_print("-" * 80)
    for session in sessions:
        cc = session.chief_complaint.replace("\n", " ")[:48]
        tuned = "yes" if session.id in tuned_ids else "no"
        safe_print(
            f"{session.id:>4}  {session.phases_completed:>5}  "
            f"{len(session.history_taker_outputs):>7}  {tuned:>5}  {cc}"
        )
    untuned = sum(1 for s in sessions if s.id not in tuned_ids)
    safe_print(f"\nTotal: {len(sessions)} ({untuned} not yet tuned)")
    return 0


def cmd_fetch_instructions(args: argparse.Namespace) -> int:
    settings = load_settings()
    instructions = fetch_bot_instructions(
        settings.api_key,
        settings.history_taker_bot_id,
    )
    out = settings.project_root / "prompts" / f"history_taker_snapshot_{args.label}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(instructions, encoding="utf-8")
    print(f"Saved {len(instructions)} chars to {out}", flush=True)
    return 0


def _run_one_session(settings, session_id: int, *, force: bool = False) -> int:
    session = get_session(settings.db_path, session_id)
    if not session:
        print(f"Session {session_id} not found.", flush=True)
        return 1
    if is_session_tuned(settings.tuner_root, session_id) and not force:
        record = get_tune_record(settings.tuner_root, session_id) or {}
        report = record.get("report", "?")
        print(
            f"Session {session_id} was already used for tuning ({report}). "
            "Pick another session or pass --force to run again.",
            flush=True,
        )
        return 1
    if not session.is_complete_enough and not settings.allow_incomplete:
        print(
            f"Session {session_id} is not complete enough "
            f"(need CC, phase-3-style outputs). Use --allow-incomplete to force.",
            flush=True,
        )
        return 1

    print(f"Fetching history taker instructions...", flush=True)
    instructions = fetch_bot_instructions(
        settings.api_key,
        settings.history_taker_bot_id,
    )
    payload = build_evaluator_payload(
        session,
        prompt_version=settings.prompt_version or get_current_version(settings.tuner_root),
        history_taker_model=settings.history_taker_model,
        current_instructions_full=instructions,
    )
    print(
        f"Calling evaluator bot for session {session_id} "
        f"(messages={len(session.messages)}, outputs={len(session.history_taker_outputs)})...",
        flush=True,
    )
    raw = send_evaluator_request(
        settings.api_key,
        settings.evaluator_bot_id,
        payload,
    )
    try:
        evaluation = parse_json_from_llm(raw)
    except Exception as err:
        fail_path = settings.report_dir / f"session_{session_id}_parse_error.txt"
        fail_path.parent.mkdir(parents=True, exist_ok=True)
        fail_path.write_text(raw, encoding="utf-8")
        print(f"Failed to parse JSON: {err}", flush=True)
        print(f"Raw response saved to {fail_path}", flush=True)
        return 1

    prompt_version = settings.prompt_version or get_current_version(settings.tuner_root)
    json_path, md_path = write_report(
        settings.report_dir,
        session_id=session_id,
        evaluation=evaluation,
        raw_response=raw,
    )
    mark_session_tuned(
        settings.tuner_root,
        session_id,
        report_path=json_path,
        prompt_version=prompt_version,
    )
    scores = evaluation.get("scores") or {}
    print(f"Done. pass={scores.get('pass')} overall={scores.get('overall_score')}", flush=True)
    print(f"JSON: {json_path}", flush=True)
    print(f"Report: {md_path}", flush=True)

    revised = evaluation.get("revised_instructions_full") or ""
    if revised.strip():
        revised_path = md_path.with_name(md_path.stem + "_revised_instructions.txt")
        revised_path.write_text(revised.strip(), encoding="utf-8")
        print(f"Revised prompt: {revised_path}", flush=True)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.allow_incomplete = args.allow_incomplete

    if not settings.evaluator_bot_id:
        print(
            "METIS_EVALUATOR_BOT_ID is missing. Create evaluator bot in Metis and set .env",
            flush=True,
        )
        return 1

    session_ids: list[int] = []
    if args.session_id:
        session_ids = [args.session_id]
    elif args.last:
        sessions = list_sessions(settings.db_path, complete_only=True)
        tuned_ids = list_tuned_session_ids(settings.tuner_root)
        untuned = [s for s in sessions if s.id not in tuned_ids]
        session_ids = [s.id for s in untuned[: args.last]]
        if not session_ids:
            print("No untuned complete sessions found.", flush=True)
            return 1
        if len(session_ids) < args.last:
            print(
                f"Only {len(session_ids)} untuned session(s) available (requested {args.last}).",
                flush=True,
            )
    else:
        print("Specify --session-id ID or --last N", flush=True)
        return 1

    exit_code = 0
    for sid in session_ids:
        code = _run_one_session(settings, sid, force=args.force)
        if code != 0:
            exit_code = code
    return exit_code


def cmd_apply(args: argparse.Namespace) -> int:
    settings = load_settings()
    if not args.confirm:
        print("Refusing to apply without --confirm (manual promotion only).", flush=True)
        return 1

    bot_id = (args.bot_id or settings.staging_bot_id or "").strip()
    if not bot_id:
        print("Provide --bot-id or set METIS_HISTORY_TAKER_STAGING_BOT_ID in .env", flush=True)
        return 1

    text = Path(args.file).read_text(encoding="utf-8").strip()
    if not text:
        print("Prompt file is empty.", flush=True)
        return 1

    if bot_id == settings.history_taker_bot_id and not args.allow_production:
        print(
            "Target is production history taker bot. "
            "Use --allow-production if you really mean it.",
            flush=True,
        )
        return 1

    save_bot_instructions(settings.api_key, bot_id, text)
    print(f"Applied {len(text)} chars to bot {bot_id}", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sayehboun history taker prompt tuner (reads sessions from Sayehboun bot.db)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List DB sessions eligible for tuning")
    p_list.add_argument("--all", action="store_true", help="Include incomplete sessions")
    p_list.set_defaults(func=cmd_list)

    p_fetch = sub.add_parser("fetch-instructions", help="Save current Metis history taker prompt")
    p_fetch.add_argument("--label", default="latest", help="Snapshot filename label")
    p_fetch.set_defaults(func=cmd_fetch_instructions)

    p_run = sub.add_parser("run", help="Evaluate session(s) and produce revised prompt")
    p_run.add_argument("--session-id", type=int, help="Single session id from bot.db")
    p_run.add_argument("--last", type=int, help="Evaluate last N complete sessions")
    p_run.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Allow sessions without full phase-3 completion",
    )
    p_run.add_argument(
        "--force",
        action="store_true",
        help="Re-run tuning on a session that was already used",
    )
    p_run.set_defaults(func=cmd_run)

    p_apply = sub.add_parser(
        "apply-revised",
        help="PUT revised prompt to a Metis bot (staging by default)",
    )
    p_apply.add_argument("--file", required=True, help="Path to revised_instructions txt")
    p_apply.add_argument("--bot-id", help="Target Metis bot id")
    p_apply.add_argument("--confirm", action="store_true", help="Required to apply")
    p_apply.add_argument(
        "--allow-production",
        action="store_true",
        help="Allow applying to METIS_HISTORY_TAKER_BOT_ID",
    )
    p_apply.set_defaults(func=cmd_apply)

    p_prompt = sub.add_parser("prompt", help="History taker prompt versions")
    p_prompt_sub = p_prompt.add_subparsers(dest="prompt_command", required=True)

    p_pl = p_prompt_sub.add_parser("list", help="List registered prompt versions")
    p_pl.set_defaults(func=cmd_prompt_list)

    p_pc = p_prompt_sub.add_parser("current", help="Show current prompt version")
    p_pc.set_defaults(func=cmd_prompt_current)

    p_ps = p_prompt_sub.add_parser("set-current", help="Set current version (manifest + .env)")
    p_ps.add_argument("version", help="e.g. v2")
    p_ps.set_defaults(func=cmd_prompt_set_current)

    p_pr = p_prompt_sub.add_parser("register", help="Save a new prompt version file")
    p_pr.add_argument("version", help="e.g. v3")
    p_pr.add_argument("--file", required=True, help="Source prompt text file")
    p_pr.add_argument("--note", default="", help="Change description")
    p_pr.add_argument("--session-id", type=int, help="Source DB session id")
    p_pr.add_argument("--set-current", action="store_true", help="Also mark as current")
    p_pr.set_defaults(func=cmd_prompt_register)

    return parser


def main() -> int:
    sayehboun_root = Path(__file__).resolve().parent.parent
    load_dotenv(sayehboun_root / ".env")
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
