#!/usr/bin/env python3
"""Minimal Notion webhook receiver for the Zoom audio pipeline."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_ENV_FILE = "/root/.notion/notion.env"
DEFAULT_WEBHOOK_ENV_FILE = "/root/.notion/notion-webhook.env"
DEFAULT_JOB_DIR = "/var/lib/zoom-audio-pipeline/jobs"
DEFAULT_RUNNER_UNIT = "notion-pipeline-poll.service"


def load_env_file(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def save_verification_token(token: str, path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"export NOTION_WEBHOOK_VERIFICATION_TOKEN={shell_quote(token)}\n",
        encoding="utf-8",
    )
    target.chmod(0o600)


def verify_signature(raw_body: bytes, signature: str | None, token: str | None) -> bool:
    if not token:
        return True
    if not signature:
        return False
    expected = "sha256=" + hmac.new(token.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def enqueue_job(payload: dict[str, Any], job_dir: str) -> Path:
    target_dir = Path(job_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    event_id = payload.get("id") or str(int(time.time() * 1000))
    event_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(event_id))
    path = target_dir / f"{int(time.time())}-{event_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def maybe_start_runner(runner_unit: str) -> None:
    subprocess.Popen(
        ["systemctl", "start", runner_unit],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "NotionWebhookReceiver/1.0"

    def respond(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.respond(200, {"ok": True})
        else:
            self.respond(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/notion/webhook":
            self.respond(404, {"ok": False, "error": "not_found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self.respond(400, {"ok": False, "error": "invalid_json"})
            return

        if "verification_token" in payload:
            save_verification_token(payload["verification_token"], self.server.webhook_env_file)
            self.respond(200, {"ok": True, "verification_token_saved": True})
            return

        load_env_file(self.server.webhook_env_file)
        token = os.environ.get("NOTION_WEBHOOK_VERIFICATION_TOKEN")
        signature = self.headers.get("X-Notion-Signature")
        if not verify_signature(raw_body, signature, token):
            self.respond(401, {"ok": False, "error": "bad_signature"})
            return

        job_path = enqueue_job(payload, self.server.job_dir)
        maybe_start_runner(self.server.runner_unit)
        self.respond(202, {"ok": True, "queued": str(job_path)})

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))


def main() -> int:
    parser = argparse.ArgumentParser(description="Receive Notion webhooks for Zoom audio pipeline.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--webhook-env-file", default=DEFAULT_WEBHOOK_ENV_FILE)
    parser.add_argument("--job-dir", default=DEFAULT_JOB_DIR)
    parser.add_argument("--runner-unit", default=DEFAULT_RUNNER_UNIT)
    args = parser.parse_args()

    load_env_file(args.env_file)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.webhook_env_file = args.webhook_env_file
    server.job_dir = args.job_dir
    server.runner_unit = args.runner_unit
    print(f"listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
