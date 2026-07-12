#!/usr/bin/env python3
"""Private mobile control page for continuing local Codex sessions."""

from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "remote_web"
UPLOAD_ROOT = ROOT / ".remote_uploads"
DEFAULT_ENV = ROOT / "remote.env"
SESSION_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
MAX_PROMPT_CHARS = 20_000
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_PROMPT_CHARS * 4 + MAX_IMAGES * (MAX_IMAGE_BYTES * 4 // 3 + 1024)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def ensure_token(path: Path) -> str:
    load_env(path)
    token = os.environ.get("REMOTE_CODEX_TOKEN", "").strip()
    if len(token) >= 24:
        return token
    token = secrets.token_urlsafe(32)
    path.write_text(
        "# Keep this file private. It grants access to the mobile control page.\n"
        f"REMOTE_CODEX_TOKEN={token}\n",
        encoding="ascii",
    )
    os.environ["REMOTE_CODEX_TOKEN"] = token
    return token


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("input_text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts).strip()


def _is_real_user_prompt(text: str) -> bool:
    stripped = text.lstrip()
    ignored_prefixes = (
        "# AGENTS.md instructions",
        "<environment_context>",
        "<permissions instructions>",
        "<app-context>",
        "<collaboration_mode>",
        "<skills_instructions>",
    )
    return bool(stripped) and not stripped.startswith(ignored_prefixes)


def _clean_user_prompt(text: str) -> str:
    marker = "## My request for Codex:"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text.strip()


def _shorten(text: str, limit: int = 100) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "..."


def _image_extension(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return None


def save_uploaded_images(images: Any, upload_root: Path = UPLOAD_ROOT) -> list[Path]:
    if images in (None, []):
        return []
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise ValueError(f"You can attach up to {MAX_IMAGES} images.")
    saved: list[Path] = []
    try:
        upload_root.mkdir(parents=True, exist_ok=True)
        for item in images:
            if not isinstance(item, dict) or not isinstance(item.get("data"), str):
                raise ValueError("Invalid image data.")
            encoded = item["data"].split(",", 1)[-1]
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("Invalid image encoding.") from exc
            if not data or len(data) > MAX_IMAGE_BYTES:
                raise ValueError("Each image must be 8 MB or smaller.")
            extension = _image_extension(data)
            if not extension:
                raise ValueError("Only JPEG, PNG, and WebP images are supported.")
            path = upload_root / f"{uuid.uuid4().hex}{extension}"
            path.write_bytes(data)
            saved.append(path)
        return saved
    except Exception:
        for path in saved:
            path.unlink(missing_ok=True)
        raise


@dataclass
class SessionInfo:
    id: str
    cwd: str
    project: str
    updated_at: float
    updated_label: str
    title: str
    last_prompt: str
    last_response: str


class SessionStore:
    def __init__(self, sessions_root: Path, limit: int = 30) -> None:
        self.sessions_root = sessions_root
        self.limit = limit

    def list(self) -> list[SessionInfo]:
        if not self.sessions_root.exists():
            return []
        paths = sorted(
            self.sessions_root.rglob("rollout-*.jsonl"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[: self.limit * 2]
        sessions: list[SessionInfo] = []
        seen: set[str] = set()
        for path in paths:
            session = self._read(path)
            if session and session.id not in seen:
                seen.add(session.id)
                sessions.append(session)
                if len(sessions) >= self.limit:
                    break
        return sessions

    def get(self, session_id: str) -> SessionInfo | None:
        return next((item for item in self.list() if item.id == session_id), None)

    def _read(self, path: Path) -> SessionInfo | None:
        match = SESSION_ID_RE.search(path.name)
        if not match:
            return None
        session_id = match.group(1)
        cwd = ""
        prompts: list[str] = []
        responses: list[str] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = event.get("payload") or {}
                    if event.get("type") == "session_meta":
                        session_id = payload.get("session_id") or payload.get("id") or session_id
                        cwd = str(payload.get("cwd") or cwd)
                    if event.get("type") == "response_item" and payload.get("role") == "user":
                        text = _text_from_content(payload.get("content"))
                        if _is_real_user_prompt(text):
                            prompts.append(_clean_user_prompt(text))
                    elif event.get("type") == "response_item" and payload.get("role") == "assistant":
                        text = _text_from_content(payload.get("content"))
                        if text:
                            responses.append(text)
                    elif event.get("type") == "event_msg" and payload.get("type") == "user_message":
                        text = _text_from_content(payload.get("message"))
                        if _is_real_user_prompt(text):
                            prompts.append(text)
        except OSError:
            return None

        updated = path.stat().st_mtime
        project = Path(cwd).name if cwd else "Unknown project"
        first = prompts[0] if prompts else "Codex session"
        last = prompts[-1] if prompts else first
        return SessionInfo(
            id=session_id,
            cwd=cwd,
            project=project,
            updated_at=updated,
            updated_label=datetime.fromtimestamp(updated).strftime("%m-%d %H:%M"),
            title=_shorten(first, 72),
            last_prompt=_shorten(last, 140),
            last_response=responses[-1] if responses else "",
        )


def default_folder_roots() -> list[Path]:
    configured = os.environ.get("REMOTE_CODEX_ROOTS", "").strip()
    candidates = [Path(item) for item in configured.split(os.pathsep) if item.strip()] if configured else [
        Path.home() / "Desktop",
        Path.home() / "Documents",
    ]
    return [path.resolve() for path in candidates if path.is_dir()]


class FolderBrowser:
    def __init__(self, roots: list[Path]) -> None:
        self.roots = [path.resolve() for path in roots if path.is_dir()]

    def validate(self, value: str) -> Path:
        if not value:
            raise ValueError("Please choose a folder.")
        try:
            path = Path(value).resolve(strict=True)
        except OSError as exc:
            raise ValueError("Folder not found.") from exc
        if not path.is_dir() or not any(path == root or root in path.parents for root in self.roots):
            raise ValueError("Folder is outside the allowed locations.")
        return path

    def list(self, value: str = "") -> dict[str, Any]:
        if not value:
            return {
                "current": None,
                "parent": None,
                "folders": [{"name": self._root_name(path), "path": str(path)} for path in self.roots],
            }
        current = self.validate(value)
        root = next(path for path in self.roots if current == path or path in current.parents)
        parent = str(current.parent) if current != root else ""
        try:
            children = sorted(
                (path for path in current.iterdir() if path.is_dir() and not path.name.startswith(".")),
                key=lambda path: path.name.casefold(),
            )[:250]
        except OSError as exc:
            raise ValueError("This folder cannot be read.") from exc
        return {
            "current": str(current),
            "parent": parent,
            "folders": [{"name": path.name, "path": str(path)} for path in children],
        }

    @staticmethod
    def _root_name(path: Path) -> str:
        if path.name.casefold() == "desktop":
            return "桌面"
        if path.name.casefold() == "documents":
            return "文档"
        return path.name


@dataclass
class RunInfo:
    id: str
    session_id: str
    prompt: str
    status: str = "queued"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    exit_code: int | None = None
    output: str = ""
    image_count: int = 0
    is_new: bool = False
    project: str = ""


class RunManager:
    def __init__(self, codex_command: str = "codex") -> None:
        self.codex_command = codex_command
        self.runs: dict[str, RunInfo] = {}
        self.latest_by_session: dict[str, str] = {}
        self.active_sessions: set[str] = set()
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.cancel_requested: set[str] = set()
        self.lock = threading.Lock()

    def start(self, session: SessionInfo, prompt: str, image_paths: list[Path] | None = None) -> RunInfo:
        return self._start(session, prompt, image_paths, is_new=False)

    def start_new(self, cwd: Path, prompt: str, image_paths: list[Path] | None = None) -> RunInfo:
        pending_id = f"new-{uuid.uuid4()}"
        session = SessionInfo(
            id=pending_id,
            cwd=str(cwd),
            project=cwd.name,
            updated_at=time.time(),
            updated_label=datetime.now().strftime("%m-%d %H:%M"),
            title=_shorten(prompt, 72),
            last_prompt=_shorten(prompt, 140),
            last_response="",
        )
        return self._start(session, prompt, image_paths, is_new=True)

    def _start(
        self,
        session: SessionInfo,
        prompt: str,
        image_paths: list[Path] | None,
        is_new: bool,
    ) -> RunInfo:
        images = list(image_paths or [])
        with self.lock:
            if session.id in self.active_sessions:
                raise RuntimeError("This session is already running.")
            run = RunInfo(
                id=str(uuid.uuid4()),
                session_id=session.id,
                prompt=prompt,
                image_count=len(images),
                is_new=is_new,
                project=session.project,
            )
            self.runs[run.id] = run
            self.latest_by_session[session.id] = run.id
            self.active_sessions.add(session.id)
        threading.Thread(target=self._execute, args=(run, session, images, is_new), daemon=True).start()
        return run

    def get(self, run_id: str) -> RunInfo | None:
        with self.lock:
            return self.runs.get(run_id)

    def latest(self, session_id: str) -> RunInfo | None:
        with self.lock:
            run_id = self.latest_by_session.get(session_id)
            return self.runs.get(run_id) if run_id else None

    def cancel(self, run_id: str) -> RunInfo:
        with self.lock:
            run = self.runs.get(run_id)
            if not run:
                raise KeyError("Run not found.")
            if run.status not in {"queued", "running", "cancelling"}:
                raise RuntimeError("This run has already finished.")
            run.status = "cancelling"
            self.cancel_requested.add(run_id)
            process = self.processes.get(run_id)
        if process:
            self._terminate_process(process)
        return run

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            try:
                subprocess.run(
                    [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=5,
                    check=False,
                )
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
            return
        process.terminate()

    def _command(
        self,
        session_id: str,
        image_paths: list[Path] | None = None,
        is_new: bool = False,
    ) -> list[str]:
        image_args = [part for path in (image_paths or []) for part in ("--image", str(path))]
        if is_new:
            base = [self.codex_command, "exec", "--skip-git-repo-check", *image_args, "-"]
        else:
            base = [self.codex_command, "exec", "resume", "--skip-git-repo-check", *image_args, session_id, "-"]
        if os.name != "nt" or Path(self.codex_command).suffix.lower() == ".exe":
            return base
        command_path = shutil.which(self.codex_command + ".cmd") or shutil.which(self.codex_command)
        if command_path and Path(command_path).suffix.lower() == ".cmd":
            comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
            return [comspec, "/d", "/s", "/c", command_path, *base[1:]]
        return base

    def _execute(self, run: RunInfo, session: SessionInfo, image_paths: list[Path], is_new: bool) -> None:
        run.status = "running"
        command = self._command(session.id, image_paths, is_new)
        try:
            with self.lock:
                cancelled_before_start = run.id in self.cancel_requested
            if cancelled_before_start:
                run.status = "cancelled"
                return
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            if os.name == "nt":
                creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
            process = subprocess.Popen(
                command,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=session.cwd if session.cwd and Path(session.cwd).is_dir() else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                creationflags=creationflags,
            )
            with self.lock:
                self.processes[run.id] = process
                should_cancel = run.id in self.cancel_requested
            if should_cancel:
                self._terminate_process(process)
            output, _ = process.communicate(input=run.prompt, timeout=60 * 60 * 6)
            run.exit_code = process.returncode
            run.output = (output or "")[-30_000:]
            if is_new:
                match = re.search(r"session id:\s*" + SESSION_ID_RE.pattern, output or "", re.IGNORECASE)
                if match:
                    actual_session_id = match.group(1)
                    run.session_id = actual_session_id
                    with self.lock:
                        self.latest_by_session[actual_session_id] = run.id
                elif process.returncode == 0:
                    run.output += "\nCould not determine the new session id."
                    process.returncode = 1
                    run.exit_code = 1
            with self.lock:
                was_cancelled = run.id in self.cancel_requested
            run.status = "cancelled" if was_cancelled else ("completed" if process.returncode == 0 else "failed")
        except subprocess.TimeoutExpired as exc:
            process = self.processes.get(run.id)
            if process:
                self._terminate_process(process)
            run.status = "failed"
            run.output = f"Timed out after 6 hours.\n{exc.stdout or ''}"
        except Exception as exc:  # Keep the server alive and expose a useful error.
            run.status = "failed"
            run.output = str(exc)
        finally:
            for path in image_paths:
                path.unlink(missing_ok=True)
            run.finished_at = time.time()
            with self.lock:
                self.active_sessions.discard(session.id)
                self.processes.pop(run.id, None)
                self.cancel_requested.discard(run.id)


class RemoteCodexApp:
    def __init__(
        self,
        token: str,
        session_store: SessionStore,
        run_manager: RunManager,
        folder_browser: FolderBrowser | None = None,
    ) -> None:
        self.token = token
        self.session_store = session_store
        self.run_manager = run_manager
        self.folder_browser = folder_browser or FolderBrowser(default_folder_roots())

    def authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        parsed = urlparse(handler.path)
        query_token = parse_qs(parsed.query).get("token", [""])[0]
        cookie = SimpleCookie(handler.headers.get("Cookie", ""))
        cookie_token = cookie.get("remote_codex_token")
        header_token = handler.headers.get("X-Remote-Codex-Token", "")
        supplied = header_token or query_token or (cookie_token.value if cookie_token else "")
        return hmac.compare_digest(supplied, self.token)


def make_handler(app: RemoteCodexApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "RemoteCodex/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[{self.log_date_time_string()}] {fmt % args}")

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                return self._json({"ok": True})
            if parsed.path in {"/", "/app.js", "/styles.css"}:
                return self._static(parsed.path)
            if not app.authorized(self):
                return self._json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
            if parsed.path == "/api/sessions":
                sessions = []
                for item in app.session_store.list():
                    value = asdict(item)
                    latest_run = app.run_manager.latest(item.id)
                    value["run"] = asdict(latest_run) if latest_run else None
                    sessions.append(value)
                return self._json({"sessions": sessions})
            if parsed.path == "/api/folders":
                value = parse_qs(parsed.query).get("path", [""])[0]
                try:
                    return self._json(app.folder_browser.list(value))
                except ValueError as exc:
                    return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            if parsed.path.startswith("/api/runs/"):
                run = app.run_manager.get(parsed.path.rsplit("/", 1)[-1])
                return self._json(asdict(run) if run else {"error": "Run not found"}, HTTPStatus.OK if run else HTTPStatus.NOT_FOUND)
            return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not app.authorized(self):
                return self._json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
            if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/cancel"):
                run_id = parsed.path.split("/")[-2]
                try:
                    run = app.run_manager.cancel(run_id)
                except KeyError as exc:
                    return self._json({"error": str(exc).strip("'")}, HTTPStatus.NOT_FOUND)
                except RuntimeError as exc:
                    return self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
                return self._json(asdict(run), HTTPStatus.ACCEPTED)
            if parsed.path not in {"/api/runs", "/api/sessions/new"}:
                return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_REQUEST_BYTES:
                    return self._json({"error": "Request is too large."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                session_id = str(body.get("session_id", "")).strip()
                cwd = str(body.get("cwd", "")).strip()
                prompt = str(body.get("prompt", "")).strip()
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                return self._json({"error": "Invalid request"}, HTTPStatus.BAD_REQUEST)
            images = body.get("images") or []
            if not prompt and images:
                prompt = "请查看并分析我上传的图片。"
            if not prompt or len(prompt) > MAX_PROMPT_CHARS:
                return self._json({"error": "Prompt must be between 1 and 20000 characters."}, HTTPStatus.BAD_REQUEST)
            if parsed.path == "/api/sessions/new":
                try:
                    folder = app.folder_browser.validate(cwd)
                    image_paths = save_uploaded_images(images)
                except (OSError, ValueError) as exc:
                    return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                run = app.run_manager.start_new(folder, prompt, image_paths)
                return self._json(asdict(run), HTTPStatus.ACCEPTED)
            if not SESSION_ID_RE.fullmatch(session_id):
                return self._json({"error": "Invalid session id."}, HTTPStatus.BAD_REQUEST)
            session = app.session_store.get(session_id)
            if not session:
                return self._json({"error": "Session not found."}, HTTPStatus.NOT_FOUND)
            try:
                image_paths = save_uploaded_images(images)
            except (OSError, ValueError) as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            try:
                run = app.run_manager.start(session, prompt, image_paths)
            except RuntimeError as exc:
                for path in image_paths:
                    path.unlink(missing_ok=True)
                return self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
            return self._json(asdict(run), HTTPStatus.ACCEPTED)

        def _static(self, path: str) -> None:
            filenames = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            }
            item = filenames.get(path)
            if not item:
                return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            file_path = WEB_ROOT / item[0]
            if not file_path.is_file():
                return self._json({"error": "Web assets missing"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            data = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", item[1])
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if parse_qs(urlparse(self.path).query).get("token"):
                self.send_header("Set-Cookie", f"remote_codex_token={app.token}; HttpOnly; SameSite=Strict; Path=/")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Private mobile control page for Codex sessions")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--sessions", type=Path, default=Path.home() / ".codex" / "sessions")
    parser.add_argument("--codex-command", default="codex")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = ensure_token(args.env)
    app = RemoteCodexApp(
        token,
        SessionStore(args.sessions),
        RunManager(args.codex_command),
        FolderBrowser(default_folder_roots()),
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    print(f"Remote Codex is running at http://{args.host}:{args.port}/?token={token}")
    print("Keep this token private. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
