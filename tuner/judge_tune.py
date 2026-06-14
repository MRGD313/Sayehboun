import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from sayehboun_tuner.console import safe_print
from sayehboun_tuner.evaluator_bots import (
    check_evaluator_bot_separation,
    history_taker_evaluator_bot_id,
    judging_evaluator_bot_id,
)
from sayehboun_tuner.judging_config import load_judging_settings
from sayehboun_tuner.judging_db_reader import (
    build_judging_evaluator_payload,
    get_judging_session,
    list_judging_sessions,
)
from sayehboun_tuner.judging_prompt_versions import (
    get_current_version,
    load_manifest,
    prompt_file_for_version,
    register_version,
    set_current_version,
    update_env_judging_prompt_version,
    write_current_marker,
)
from sayehboun_tuner.judging_report import write_judging_report
from sayehboun_tuner.judging_tuned_sessions import (
    get_tune_record,
    is_session_tuned,
    list_tuned_session_ids,
    mark_session_tuned,
)
from sayehboun_tuner.metis_client import (
    extract_revised_prompt,
    fetch_bot_instructions,
    save_bot_instructions,
    send_tuner_request,
)


def _load_judging_instructions(settings) -> str:
    try:
        return fetch_bot_instructions(settings.api_key, settings.judging_bot_id)
    except Exception as err:
        version = settings.judging_prompt_version or get_current_version(settings.tuner_root)
        path = prompt_file_for_version(settings.tuner_root, version)
        if path.exists():
            print(
                f"Metis fetch failed ({err}); using local file {path}",
                flush=True,
            )
            return path.read_text(encoding="utf-8").strip()
        raise


def cmd_prompt_list(args: argparse.Namespace) -> int:
    settings = load_judging_settings()
    manifest = load_manifest(settings.tuner_root)
    current = manifest.get("current", "?")
    safe_print(f"Current judging prompt: {current}")
    safe_print("")
    for version, meta in sorted(manifest.get("versions", {}).items()):
        mark = " *" if version == current else ""
        note = meta.get("note") or ""
        safe_print(f"  {version}{mark}  {note}")
    return 0


def cmd_prompt_current(args: argparse.Namespace) -> int:
    settings = load_judging_settings()
    version = get_current_version(settings.tuner_root)
    path = prompt_file_for_version(settings.tuner_root, version)
    safe_print(f"current={version}")
    safe_print(f"file={path}")
    safe_print(f"env JUDGING_PROMPT_VERSION={settings.judging_prompt_version}")
    return 0


def cmd_prompt_set_current(args: argparse.Namespace) -> int:
    settings = load_judging_settings()
    version = args.version
    set_current_version(settings.tuner_root, version)
    update_env_judging_prompt_version(settings.sayehboun_root, version)
    write_current_marker(settings.tuner_root, version)
    safe_print(f"Current judging prompt set to {version} (manifest + .env updated)")
    return 0


def cmd_prompt_register(args: argparse.Namespace) -> int:
    settings = load_judging_settings()
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
        update_env_judging_prompt_version(settings.sayehboun_root, args.version)
        write_current_marker(settings.tuner_root, args.version)
    safe_print(f"Registered {args.version} -> {dest}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = load_judging_settings()
    hist_eval = history_taker_evaluator_bot_id()
    judge_eval = settings.judging_evaluator_bot_id
    safe_print("Judging tuner")
    safe_print(f"  production bot:     {settings.judging_bot_id or '(missing)'}")
    safe_print(f"  evaluator bot:      {judge_eval or '(missing)'}")
    safe_print(f"  prompt version:     {settings.judging_prompt_version}")
    safe_print(f"  instructions file:  prompts/judging_evaluator_instructions.txt")
    safe_print("")
    safe_print("History taker tuner (env check)")
    safe_print(f"  history evaluator:  {hist_eval or '(missing)'}")
    conflict = check_evaluator_bot_separation(
        history_evaluator_bot_id=hist_eval,
        judging_evaluator_bot_id=judge_eval,
    )
    if conflict:
        safe_print(f"  WARNING: {conflict}")
    elif hist_eval and judge_eval:
        safe_print("  evaluator separation: OK (different bot IDs)")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    settings = load_judging_settings()
    sessions = list_judging_sessions(settings.db_path)
    if not sessions:
        safe_print("No sessions with both judgment + doctor final found.")
        return 0
    tuned_ids = list_tuned_session_ids(settings.tuner_root)
    safe_print(
        f"{'ID':>4}  {'action':>8}  {'tuned':>5}  chief_complaint"
    )
    safe_print("-" * 72)
    for session in sessions:
        cc = session.chief_complaint.replace("\n", " ")[:44]
        tuned = "yes" if session.id in tuned_ids else "no"
        safe_print(
            f"{session.id:>4}  {session.doctor_action:>8}  {tuned:>5}  {cc}"
        )
    untuned = sum(1 for s in sessions if s.id not in tuned_ids)
    safe_print(f"\nTotal tunable: {len(sessions)} ({untuned} not yet tuned)")
    return 0


def cmd_fetch_instructions(args: argparse.Namespace) -> int:
    settings = load_judging_settings()
    instructions = fetch_bot_instructions(settings.api_key, settings.judging_bot_id)
    out = settings.tuner_root / "prompts" / f"judging_snapshot_{args.label}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(instructions, encoding="utf-8")
    print(f"Saved {len(instructions)} chars to {out}", flush=True)
    return 0


def _run_one_session(settings, session_id: int, *, force: bool = False) -> int:
    session = get_judging_session(settings.db_path, session_id)
    if not session:
        print(
            f"Session {session_id} not found or missing judgment + doctor final.",
            flush=True,
        )
        return 1
    if is_session_tuned(settings.tuner_root, session_id) and not force:
        record = get_tune_record(settings.tuner_root, session_id) or {}
        report = record.get("report", "?")
        print(
            f"Session {session_id} was already used for judging tuning ({report}). "
            "Pick another session or pass --force to run again.",
            flush=True,
        )
        return 1

    print("Fetching judging bot instructions...", flush=True)
    instructions = _load_judging_instructions(settings)
    prompt_version = settings.judging_prompt_version or get_current_version(
        settings.tuner_root
    )
    payload = build_judging_evaluator_payload(
        session,
        prompt_version=prompt_version,
        judging_model=settings.judging_model,
        current_instructions_full=instructions,
    )
    print(
        f"Calling judging evaluator for session {session_id} "
        f"(doctor_action={session.doctor_action})...",
        flush=True,
    )
    raw = send_tuner_request(
        settings.api_key,
        settings.judging_evaluator_bot_id,
        payload,
        intro=(
            "Session data below. Compare judging_bot_output with "
            "doctor_final_to_patient (ground truth) against current_prompt. "
            "Return ONLY the full revised Judging Bot system prompt.\n\n"
        ),
    )
    revised = extract_revised_prompt(raw)
    if not revised.strip():
        fail_path = settings.report_dir / f"session_{session_id}_empty_response.txt"
        fail_path.parent.mkdir(parents=True, exist_ok=True)
        fail_path.write_text(raw, encoding="utf-8")
        print("Evaluator returned empty revised prompt.", flush=True)
        print(f"Raw response saved to {fail_path}", flush=True)
        return 1

    json_path, prompt_path = write_judging_report(
        settings.report_dir,
        session_id=session_id,
        prompt_version=prompt_version,
        revised_prompt=revised,
        raw_response=raw,
        input_payload=payload,
    )
    mark_session_tuned(
        settings.tuner_root,
        session_id,
        report_path=json_path,
        prompt_version=prompt_version,
    )
    print(
        f"Done. Revised prompt: {len(revised)} chars "
        f"doctor_action={session.doctor_action}",
        flush=True,
    )
    print(f"Prompt file: {prompt_path}", flush=True)
    print(f"Metadata: {json_path}", flush=True)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_judging_settings()

    if not settings.judging_evaluator_bot_id:
        print(
            "METIS_JUDGING_EVALUATOR_BOT_ID is missing. "
            "Set your separate judging tuner bot from Metis console in .env",
            flush=True,
        )
        return 1
    if not settings.judging_bot_id:
        print("METIS_JUDGING_BOT_ID is missing in .env", flush=True)
        return 1

    conflict = check_evaluator_bot_separation(
        history_evaluator_bot_id=history_taker_evaluator_bot_id(),
        judging_evaluator_bot_id=settings.judging_evaluator_bot_id,
    )
    if conflict:
        print(conflict, flush=True)
        return 1

    session_ids: list[int] = []
    if args.session_id:
        session_ids = [args.session_id]
    elif args.last:
        sessions = list_judging_sessions(settings.db_path)
        tuned_ids = list_tuned_session_ids(settings.tuner_root)
        untuned = [s for s in sessions if s.id not in tuned_ids]
        session_ids = [s.id for s in untuned[: args.last]]
        if not session_ids:
            print("No untuned judging sessions found.", flush=True)
            return 1
        if len(session_ids) < args.last:
            print(
                f"Only {len(session_ids)} untuned session(s) available "
                f"(requested {args.last}).",
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
    settings = load_judging_settings()
    if not args.confirm:
        print("Refusing to apply without --confirm (manual promotion only).", flush=True)
        return 1

    bot_id = (args.bot_id or settings.judging_bot_id or "").strip()
    if not bot_id:
        print("Provide --bot-id or set METIS_JUDGING_BOT_ID in .env", flush=True)
        return 1

    text = Path(args.file).read_text(encoding="utf-8").strip()
    if not text:
        print("Prompt file is empty.", flush=True)
        return 1

    save_bot_instructions(settings.api_key, bot_id, text)
    print(f"Applied {len(text)} chars to bot {bot_id}", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Sayehboun judging bot prompt tuner "
            "(compares bot output vs doctor final message from bot.db)"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser(
        "list",
        help="List sessions with judgment + doctor final (approved/edited)",
    )
    p_list.set_defaults(func=cmd_list)

    p_doctor = sub.add_parser("doctor", help="Show configured bot IDs and separation check")
    p_doctor.set_defaults(func=cmd_doctor)

    p_fetch = sub.add_parser("fetch-instructions", help="Save current Metis judging prompt")
    p_fetch.add_argument("--label", default="latest", help="Snapshot filename label")
    p_fetch.set_defaults(func=cmd_fetch_instructions)

    p_run = sub.add_parser("run", help="Evaluate session(s) and produce revised judging prompt")
    p_run.add_argument("--session-id", type=int, help="Single session id from bot.db")
    p_run.add_argument("--last", type=int, help="Evaluate last N untuned judging sessions")
    p_run.add_argument(
        "--force",
        action="store_true",
        help="Re-run tuning on a session that was already used",
    )
    p_run.set_defaults(func=cmd_run)

    p_apply = sub.add_parser(
        "apply-revised",
        help="PUT revised prompt to Metis judging bot",
    )
    p_apply.add_argument("--file", required=True, help="Path to revised_prompt txt")
    p_apply.add_argument("--bot-id", help="Target Metis bot id")
    p_apply.add_argument("--confirm", action="store_true", help="Required to apply")
    p_apply.set_defaults(func=cmd_apply)

    p_prompt = sub.add_parser("prompt", help="Judging prompt versions")
    p_prompt_sub = p_prompt.add_subparsers(dest="prompt_command", required=True)

    p_pl = p_prompt_sub.add_parser("list", help="List registered judging prompt versions")
    p_pl.set_defaults(func=cmd_prompt_list)

    p_pc = p_prompt_sub.add_parser("current", help="Show current judging prompt version")
    p_pc.set_defaults(func=cmd_prompt_current)

    p_ps = p_prompt_sub.add_parser("set-current", help="Set current version (manifest + .env)")
    p_ps.add_argument("version", help="e.g. v2")
    p_ps.set_defaults(func=cmd_prompt_set_current)

    p_pr = p_prompt_sub.add_parser("register", help="Save a new judging prompt version file")
    p_pr.add_argument("version", help="e.g. v2")
    p_pr.add_argument("--file", required=True, help="Source prompt text file")
    p_pr.add_argument("--note", default="", help="Change description")
    p_pr.add_argument("--session-id", type=int, help="Source DB session id")
    p_pr.add_argument("--set-current", action="store_true", help="Also mark as current")
    p_pr.set_defaults(func=cmd_prompt_register)

    return parser


def main() -> int:
    sayehboun_root = Path(__file__).resolve().parent.parent
    load_dotenv(sayehboun_root / ".env")
    sys.path.insert(0, str(sayehboun_root))
    from metis_utils import apply_metis_direct_network

    apply_metis_direct_network()
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
