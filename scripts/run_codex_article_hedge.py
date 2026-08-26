#!/usr/bin/env python3
"""Race delayed Codex attempts and an isolated OpenRouter fallback."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO


@dataclass
class Worker:
    name: str
    process: subprocess.Popen[bytes]
    output: Path
    log: Path
    deadline: float
    work_dir: Path | None = None
    handled: bool = False


class ShutdownRequested(Exception):
    pass


def request_shutdown(signum: int, _frame: object) -> None:
    raise ShutdownRequested(f"received_signal_{signum}")


def valid_output(path: Path, minimum_bytes: int) -> bool:
    try:
        if path.stat().st_size < minimum_bytes:
            return False
        return bool(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError):
        return False


def terminate_worker(worker: Worker, grace_seconds: float = 1.0) -> None:
    if worker.process.poll() is not None:
        try:
            os.killpg(worker.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        return
    try:
        os.killpg(worker.process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        worker.process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(worker.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        worker.process.wait(timeout=grace_seconds)


def start_worker(
    *,
    name: str,
    command: list[str],
    output: Path,
    log: Path,
    deadline: float,
    stdin_path: Path | None = None,
    work_dir: Path | None = None,
) -> Worker:
    stdin_handle: IO[bytes] | None = stdin_path.open("rb") if stdin_path else None
    log_handle = log.open("wb")
    try:
        process = subprocess.Popen(
            command,
            stdin=stdin_handle,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        if stdin_handle:
            stdin_handle.close()
        log_handle.close()
    return Worker(name, process, output, log, deadline, work_dir)


def codex_command(args: argparse.Namespace, output: Path) -> list[str]:
    return [
        args.codex_bin,
        "--disable",
        "shell_tool",
        "-a",
        "never",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--model",
        args.model,
        "-c",
        f'model_reasoning_effort="{args.reasoning}"',
        "--cd",
        str(args.run_dir),
        "--sandbox",
        "read-only",
        "--output-last-message",
        str(output),
        "-",
    ]


def make_attempt_output(run_dir: Path, name: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=f".codex-{name}.", suffix=".md", dir=run_dir)
    os.close(descriptor)
    return Path(raw_path)


def make_fallback_dir(run_dir: Path) -> Path:
    path = Path(tempfile.mkdtemp(prefix=".openrouter-fallback.", dir=run_dir))
    for name in ("transcript.md", "verification.md", "teacher-notes.md", "status.json"):
        source = run_dir / name
        if source.exists():
            shutil.copy2(source, path / name)
    return path


def commit_winner(worker: Worker, output: Path, last_output: Path, winner_lock: Path) -> None:
    winner_lock.mkdir()
    temporary = output.with_name(output.name + ".winner.tmp")
    shutil.copyfile(worker.output, temporary)
    os.replace(temporary, output)
    shutil.copyfile(output, last_output)


def write_result(path: Path, result: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--prompt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--last-output", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning", default="high")
    parser.add_argument("--fallback-script", required=True)
    parser.add_argument("--hedge-delay", type=float, default=65.0)
    parser.add_argument("--fallback-after", type=float, default=145.0)
    parser.add_argument("--primary-timeout", type=float, default=900.0)
    parser.add_argument("--hedge-timeout", type=float, default=80.0)
    parser.add_argument("--fallback-timeout", type=float, default=900.0)
    parser.add_argument("--poll-interval", type=float, default=0.25)
    parser.add_argument("--minimum-bytes", type=int, default=200)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.hedge_delay < 0 or args.fallback_after < args.hedge_delay:
        raise SystemExit("fallback-after must be greater than or equal to hedge-delay")
    if min(args.primary_timeout, args.hedge_timeout, args.fallback_timeout, args.poll_interval) <= 0:
        raise SystemExit("timeouts and poll-interval must be positive")
    if args.minimum_bytes <= 0:
        raise SystemExit("minimum-bytes must be positive")

    run_dir = args.run_dir.resolve()
    winner_lock = run_dir / ".codex-article-winner"
    if winner_lock.exists():
        shutil.rmtree(winner_lock)

    started = time.monotonic()
    workers: list[Worker] = []
    codex_started = 0
    fallback_started = False
    winner: Worker | None = None
    last_error = "no_valid_article_output"

    primary_output = make_attempt_output(run_dir, "primary")
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    workers.append(start_worker(
        name="codex_primary",
        command=codex_command(args, primary_output),
        output=primary_output,
        log=run_dir / "codex-article-attempt-1.log",
        deadline=started + args.primary_timeout,
        stdin_path=args.prompt,
    ))
    codex_started = 1

    try:
        while winner is None:
            now = time.monotonic()
            elapsed = now - started

            completed: list[Worker] = []
            for worker in workers:
                if worker.handled:
                    continue
                if worker.process.poll() is None and now >= worker.deadline:
                    terminate_worker(worker)
                    last_error = f"{worker.name}_timeout"
                if worker.process.poll() is not None:
                    worker.handled = True
                    if worker.process.returncode == 0 and valid_output(worker.output, args.minimum_bytes):
                        completed.append(worker)
                    elif worker.process.returncode != 0:
                        last_error = f"{worker.name}_exit_{worker.process.returncode}"
                    else:
                        last_error = f"{worker.name}_invalid_output"

            if completed:
                completed.sort(key=lambda item: item.output.stat().st_mtime_ns)
                winner = completed[0]
                commit_winner(winner, args.output, args.last_output, winner_lock)
                break

            if codex_started == 1 and elapsed >= args.hedge_delay:
                hedge_output = make_attempt_output(run_dir, "hedge")
                try:
                    workers.append(start_worker(
                        name="codex_hedge",
                        command=codex_command(args, hedge_output),
                        output=hedge_output,
                        log=run_dir / "codex-article-attempt-2.log",
                        deadline=now + args.hedge_timeout,
                        stdin_path=args.prompt,
                    ))
                except Exception:
                    hedge_output.unlink(missing_ok=True)
                    raise
                codex_started = 2

            if not fallback_started and elapsed >= args.fallback_after:
                fallback_dir = make_fallback_dir(run_dir)
                fallback_output = fallback_dir / "roadmap-article.md"
                try:
                    workers.append(start_worker(
                        name="openrouter_fallback",
                        command=[args.fallback_script, str(fallback_dir)],
                        output=fallback_output,
                        log=run_dir / "openrouter-article-fallback.log",
                        deadline=now + args.fallback_timeout,
                        work_dir=fallback_dir,
                    ))
                except Exception:
                    shutil.rmtree(fallback_dir, ignore_errors=True)
                    raise
                fallback_started = True

            if fallback_started and all(worker.handled for worker in workers):
                break
            time.sleep(args.poll_interval)
    except (ShutdownRequested, KeyboardInterrupt) as error:
        last_error = str(error)
        winner = None
    except Exception as error:
        last_error = f"runner_error_{type(error).__name__}"
        winner = None
    finally:
        for worker in workers:
            terminate_worker(worker)

    duration = max(0, round(time.monotonic() - started, 3))
    result: dict[str, object] = {
        "status": "done" if winner else "failed",
        "winner": winner.name if winner else "",
        "provider": "openrouter_fallback" if winner and winner.name == "openrouter_fallback" else "codex_cli",
        "codex_attempts_started": codex_started,
        "fallback_started": fallback_started,
        "duration_seconds": duration,
        "last_error": "" if winner else last_error,
        "winner_log": str(winner.log) if winner else "",
    }
    write_result(args.result, result)

    for worker in workers:
        if worker.output != args.output:
            worker.output.unlink(missing_ok=True)
        if worker.work_dir:
            if winner and winner.name == "openrouter_fallback":
                for name in ("openrouter-article.prompt.md", "openrouter-article.response.json"):
                    source = worker.work_dir / name
                    if source.exists():
                        shutil.copy2(source, run_dir / name)
            shutil.rmtree(worker.work_dir, ignore_errors=True)
    shutil.rmtree(winner_lock, ignore_errors=True)
    return 0 if winner else 1


if __name__ == "__main__":
    raise SystemExit(main())
