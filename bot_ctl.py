"""Start/stop/restart Sayeh Bale bot (single instance)."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCK_PATH = ROOT / ".bot.instance.lock"
LEGACY_LOCK_PATH = ROOT / ".bot.lock"
APP_SCRIPT = ROOT / "app.py"


def _powershell(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def _list_bot_pids() -> list[int]:
    root_escaped = str(ROOT).replace("'", "''")
    ps = rf"""
$root = '{root_escaped}'
$pids = @()
if (Test-Path (Join-Path $root '.bot.instance.lock')) {{
    $lockPid = Get-Content (Join-Path $root '.bot.instance.lock') -ErrorAction SilentlyContinue
    if ($lockPid -match '^\d+$') {{ $pids += [int]$lockPid }}
}}
Get-CimInstance Win32_Process |
    Where-Object {{ $_.Name -in @('python.exe','py.exe') -and $_.CommandLine -match 'app\.py' }} |
    ForEach-Object {{ $pids += $_.ProcessId }}
$pids | Sort-Object -Unique
"""
    result = _powershell(ps)
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return sorted(set(pids))


def stop_bot(*, quiet: bool = False) -> int:
    pids = _list_bot_pids()
    if not pids:
        if not quiet:
            print("No Sayeh bot processes found.", flush=True)
    else:
        pid_list = ",".join(str(pid) for pid in pids)
        _powershell(
            f"Stop-Process -Id {pid_list} -Force -ErrorAction SilentlyContinue"
        )
        if not quiet:
            print(f"Stopped bot process(es): {pid_list}", flush=True)
        time.sleep(1)

    for path in (LOCK_PATH, LEGACY_LOCK_PATH):
        if path.exists():
            path.unlink(missing_ok=True)
            if not quiet:
                print(f"Removed {path.name}", flush=True)
    return 0


def start_bot(*, background: bool = False) -> int:
    if not APP_SCRIPT.is_file():
        print(f"Missing {APP_SCRIPT}", flush=True)
        return 1

    remaining = _list_bot_pids()
    if remaining:
        print(
            f"Refusing to start: bot still running (PIDs: {', '.join(map(str, remaining))}). "
            "Run: py bot_ctl.py stop",
            flush=True,
        )
        return 1

    if background:
        if os.name == "nt":
            subprocess.Popen(
                [sys.executable, str(APP_SCRIPT)],
                cwd=str(ROOT),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                [sys.executable, str(APP_SCRIPT)],
                cwd=str(ROOT),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        time.sleep(2)
        pids = _list_bot_pids()
        if pids:
            print(f"Bot started in background (PID {pids[0]}).", flush=True)
            return 0
        print("Bot start failed (no process detected).", flush=True)
        return 1

    os.execv(sys.executable, [sys.executable, str(APP_SCRIPT)])


def restart_bot(*, background: bool = False) -> int:
    print("Restarting Sayeh bot (stop all -> single instance)...", flush=True)
    stop_bot()
    return start_bot(background=background)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sayeh bot process control")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stop", help="Force stop all bot instances and remove lock files")
    p_start = sub.add_parser("start", help="Start bot if not already running")
    p_start.add_argument(
        "--background",
        action="store_true",
        help="Start detached (default: run in foreground)",
    )
    p_restart = sub.add_parser(
        "restart",
        help="Force stop all instances, then start one clean instance",
    )
    p_restart.add_argument(
        "--background",
        action="store_true",
        help="Start detached after stop",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "stop":
        return stop_bot()
    if args.command == "start":
        return start_bot(background=args.background)
    if args.command == "restart":
        return restart_bot(background=args.background)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
