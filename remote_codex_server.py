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
from typing import Any, Callable, TextIO
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


def locate_codex_desktop_executable() -> Path:
    """Locate the executable bundled with Codex Desktop without using PATH."""
    configured = os.environ.get("REMOTE_CODEX_DESKTOP_EXE", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        if path.is_file():
            return path
        raise FileNotFoundError(f"REMOTE_CODEX_DESKTOP_EXE does not exist: {path}")

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates = sorted(
            (Path(local_app_data) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return candidates[0].resolve()

    if os.name == "nt":
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        try:
            result = subprocess.run(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "(Get-AppxPackage -Name OpenAI.Codex | Select-Object -First 1 -ExpandProperty InstallLocation)",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            install_location = result.stdout.strip()
            install_root = Path(install_location) if install_location else None
            if result.returncode == 0 and install_root and install_root.is_dir():
                app_server = install_root / "app" / "resources" / "codex.exe"
                if app_server.is_file():
                    return app_server.resolve()
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise FileNotFoundError(
        "Codex Desktop was not found. Install the Windows Codex app or set REMOTE_CODEX_DESKTOP_EXE."
    )


class NDJSONTransport:
    def __init__(self, executable: Path) -> None:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(
            [
                str(executable),
                "-c",
                "features.code_mode_host=true",
                "app-server",
                "--stdio",
                "--analytics-default-enabled",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        if not self.process.stdin or not self.process.stdout:
            raise RuntimeError("Could not open Codex Desktop app-server pipes.")
        self.reader: TextIO = self.process.stdout
        self.writer: TextIO = self.process.stdin
        self.error_reader: TextIO | None = self.process.stderr
        self.last_error = ""
        if self.error_reader:
            threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        assert self.error_reader is not None
        for line in self.error_reader:
            text = line.strip()
            if text:
                self.last_error = (self.last_error + "\n" + text)[-4000:].strip()

    def send(self, message: dict[str, Any]) -> None:
        self.writer.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.writer.flush()

    def receive(self) -> dict[str, Any] | None:
        line = self.reader.readline()
        if not line:
            return None
        return json.loads(line)

    def close(self) -> None:
        try:
            if not self.writer.closed:
                self.writer.close()
        except OSError:
            pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for stream in (self.reader, self.error_reader):
            try:
                if stream and not stream.closed:
                    stream.close()
            except OSError:
                pass


class AppServerClient:
    """Thread-safe JSON-RPC-like client for the Codex Desktop app-server."""

    def __init__(self, executable: Path | None = None, transport: Any | None = None) -> None:
        self.transport = transport or NDJSONTransport(executable or locate_codex_desktop_executable())
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._next_id = 1
        self._pending: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._closed_error: Exception | None = None
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {"name": "pocket-codex", "title": "PocketCodex", "version": "1.0.0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            self.notify("initialized", {})
        except Exception:
            self.transport.close()
            raise

    def add_listener(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        with self._state_lock:
            self._listeners.append(listener)

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed_error is not None

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30) -> Any:
        event = threading.Event()
        holder: dict[str, Any] = {}
        with self._state_lock:
            if self._closed_error:
                raise RuntimeError(str(self._closed_error))
            request_id = self._next_id
            self._next_id += 1
            self._pending[request_id] = (event, holder)
        self._send({"id": request_id, "method": method, "params": params or {}})
        if not event.wait(timeout):
            with self._state_lock:
                self._pending.pop(request_id, None)
            raise TimeoutError(f"Codex Desktop did not answer {method} within {timeout:g} seconds.")
        if "error" in holder:
            error = holder["error"]
            detail = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise RuntimeError(f"Codex Desktop {method} failed: {detail}")
        return holder.get("result")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"method": method, "params": params or {}})

    def close(self) -> None:
        self._fail_connection(RuntimeError("PocketCodex closed the Codex Desktop app-server connection."))
        self.transport.close()

    def _fail_connection(self, exc: Exception) -> None:
        with self._state_lock:
            if self._closed_error is not None:
                return
            self._closed_error = exc
            pending = list(self._pending.values())
            self._pending.clear()
            listeners = list(self._listeners)
        for event, holder in pending:
            holder["error"] = {"message": str(exc)}
            event.set()
        for listener in listeners:
            try:
                listener("app-server/closed", {"error": str(exc)})
            except Exception:
                continue

    def _send(self, message: dict[str, Any]) -> None:
        with self._write_lock:
            self.transport.send(message)

    def _read_loop(self) -> None:
        try:
            while True:
                message = self.transport.receive()
                if message is None:
                    detail = str(getattr(self.transport, "last_error", "") or "").strip()
                    suffix = f" Last error: {detail}" if detail else ""
                    raise RuntimeError(f"Codex Desktop app-server closed the connection.{suffix}")
                if "id" in message and ("result" in message or "error" in message):
                    with self._state_lock:
                        pending = self._pending.pop(message["id"], None)
                    if pending:
                        event, holder = pending
                        holder.update(message)
                        event.set()
                    continue
                if "id" in message and "method" in message:
                    self._send(
                        {
                            "id": message["id"],
                            "error": {"code": -32601, "message": f"Unsupported server request: {message['method']}"},
                        }
                    )
                    continue
                method = message.get("method")
                if isinstance(method, str):
                    params = message.get("params") if isinstance(message.get("params"), dict) else {}
                    with self._state_lock:
                        listeners = list(self._listeners)
                    for listener in listeners:
                        try:
                            listener(method, params)
                        except Exception:
                            continue
        except Exception as exc:
            self._fail_connection(exc)


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


class DesktopSessionStore:
    """Session facade backed by the Codex Desktop app-server thread index."""

    def __init__(self, client_provider: Callable[[], AppServerClient], limit: int = 30) -> None:
        self.client_provider = client_provider
        self.limit = limit

    def list(self) -> list[SessionInfo]:
        result = self.client_provider().request(
            "thread/list",
            {
                "archived": False,
                "cursor": None,
                "limit": self.limit,
                "modelProviders": None,
                "sortKey": "updated_at",
            },
        )
        threads = result.get("data", []) if isinstance(result, dict) else []
        return [session for item in threads if isinstance(item, dict) and (session := self._from_thread(item))]

    def get(self, session_id: str) -> SessionInfo | None:
        return next((item for item in self.list() if item.id == session_id), None)

    @staticmethod
    def _from_thread(thread: dict[str, Any]) -> SessionInfo | None:
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not SESSION_ID_RE.fullmatch(thread_id):
            return None
        cwd = str(thread.get("cwd") or "")
        preview = str(thread.get("preview") or "").strip()
        title = str(thread.get("name") or "").strip() or preview or "Codex desktop task"
        raw_updated = thread.get("updatedAt") or thread.get("createdAt") or time.time()
        try:
            updated = float(raw_updated)
        except (TypeError, ValueError):
            updated = time.time()
        return SessionInfo(
            id=thread_id,
            cwd=cwd,
            project=Path(cwd).name if cwd else "Codex Desktop",
            updated_at=updated,
            updated_label=datetime.fromtimestamp(updated).strftime("%m-%d %H:%M"),
            title=_shorten(title, 72),
            last_prompt=_shorten(preview or title, 140),
            last_response="",
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
    def __init__(
        self,
        client: AppServerClient | None = None,
        client_factory: Callable[[], AppServerClient] | None = None,
        turn_timeout: float = 60 * 60 * 6,
        interrupt_grace: float = 10,
    ) -> None:
        self.client = client
        self.client_factory = client_factory or AppServerClient
        self.turn_timeout = turn_timeout
        self.interrupt_grace = interrupt_grace
        self.runs: dict[str, RunInfo] = {}
        self.latest_by_session: dict[str, str] = {}
        self.active_sessions: dict[str, str] = {}
        self.cancel_requested: set[str] = set()
        self.timing_out: set[str] = set()
        self.turns_by_run: dict[str, str] = {}
        self.runs_by_turn: dict[str, str] = {}
        self.generation_by_run: dict[str, int] = {}
        self.done_events: dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._listener_installed = False
        self._client_generation = 0

    def _get_client(self) -> AppServerClient:
        return self._get_client_with_generation()[0]

    def _get_client_with_generation(self) -> tuple[AppServerClient, int]:
        with self._client_lock:
            with self.lock:
                current = self.client
                listener_installed = self._listener_installed
            if current is not None and not getattr(current, "closed", False):
                if not listener_installed:
                    with self.lock:
                        self._client_generation += 1
                        generation = self._client_generation
                        self._listener_installed = True
                    current.add_listener(
                        lambda method, params, generation=generation: self._on_notification(
                            generation, method, params
                        )
                    )
                with self.lock:
                    generation = self._client_generation
                return current, generation
            replacement = self.client_factory()
            with self.lock:
                previous = self.client
                self._client_generation += 1
                generation = self._client_generation
                self.client = replacement
                self._listener_installed = True
            replacement.add_listener(
                lambda method, params, generation=generation: self._on_notification(generation, method, params)
            )
            if previous is not None:
                previous.close()
            return replacement, generation

    def close(self) -> None:
        with self._client_lock:
            with self.lock:
                client = self.client
                self.client = None
                self._listener_installed = False
                self._client_generation += 1
            if client is not None:
                client.close()

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
            self.active_sessions[session.id] = run.id
            self.done_events[run.id] = threading.Event()
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
            turn_id = self.turns_by_run.get(run_id)
            session_id = run.session_id
        if turn_id:
            try:
                self._get_client().request("turn/interrupt", {"threadId": session_id, "turnId": turn_id})
            except Exception as exc:
                with self.lock:
                    run.output = f"Could not interrupt the Codex Desktop turn: {exc}"
                    run.status = "failed"
                    run.finished_at = time.time()
                    self.done_events[run_id].set()
        return run

    def _execute(self, run: RunInfo, session: SessionInfo, image_paths: list[Path], is_new: bool) -> None:
        run.status = "running"
        try:
            with self.lock:
                cancelled_before_start = run.id in self.cancel_requested
            if cancelled_before_start:
                run.status = "cancelled"
                return
            client, generation = self._get_client_with_generation()
            with self.lock:
                self.generation_by_run[run.id] = generation
            if is_new:
                thread_result = client.request(
                    "thread/start",
                    {
                        "cwd": session.cwd,
                        "model": None,
                        "modelProvider": None,
                        "approvalPolicy": None,
                        "sandbox": None,
                        "config": {},
                        "personality": None,
                        "ephemeral": False,
                        "threadSource": "user",
                        "experimentalRawEvents": False,
                    },
                )
                thread_id = self._result_id(thread_result, "thread")
                if not thread_id:
                    raise RuntimeError("Codex Desktop did not return a thread id.")
                with self.lock:
                    if self.active_sessions.get(session.id) != run.id:
                        raise RuntimeError("PocketCodex lost ownership of the pending desktop task.")
                    owner = self.active_sessions.get(thread_id)
                    if owner is not None and owner != run.id:
                        raise RuntimeError("This desktop thread is already running.")
                    self.active_sessions.pop(session.id, None)
                    self.active_sessions[thread_id] = run.id
                    self.latest_by_session.pop(session.id, None)
                    self.latest_by_session[thread_id] = run.id
                    run.session_id = thread_id
            else:
                client.request(
                    "thread/resume",
                    {
                        "threadId": session.id,
                        "history": None,
                        "path": None,
                        "model": None,
                        "modelProvider": None,
                        "cwd": session.cwd or None,
                        "approvalPolicy": None,
                        "sandbox": None,
                        "config": None,
                        "developerInstructions": None,
                        "personality": None,
                        "excludeTurns": True,
                    },
                )
                thread_id = session.id

            inputs: list[dict[str, Any]] = [{"type": "text", "text": run.prompt, "text_elements": []}]
            inputs.extend({"type": "localImage", "path": str(path.resolve())} for path in image_paths)
            turn_result = client.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "clientUserMessageId": str(uuid.uuid4()),
                    "input": inputs,
                    "cwd": session.cwd or None,
                    "model": None,
                    "effort": None,
                    "serviceTier": None,
                    "collaborationMode": None,
                },
            )
            turn_id = self._result_id(turn_result, "turn")
            if not turn_id:
                raise RuntimeError("Codex Desktop did not return a turn id.")
            with self.lock:
                self.turns_by_run[run.id] = turn_id
                self.runs_by_turn[turn_id] = run.id
                should_cancel = run.id in self.cancel_requested
            if should_cancel:
                client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
            done_event = self.done_events[run.id]
            if not done_event.wait(self.turn_timeout):
                with self.lock:
                    timed_out = not done_event.is_set()
                    if timed_out:
                        self.timing_out.add(run.id)
                if timed_out:
                    try:
                        client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
                        if not done_event.wait(self.interrupt_grace):
                            client.close()
                    except Exception as exc:
                        client.close()
                        run.output = (run.output + f"\nCould not interrupt timed-out turn: {exc}").strip()
                    run.status = "failed"
                    run.exit_code = 1
                    run.output = (run.output + "\nTimed out after 6 hours.").strip()
        except Exception as exc:  # Keep the server alive and expose a useful error.
            run.status = "failed"
            run.exit_code = 1
            run.output = str(exc)
        finally:
            for path in image_paths:
                path.unlink(missing_ok=True)
            if run.finished_at is None:
                run.finished_at = time.time()
            with self.lock:
                for thread_id in {session.id, run.session_id}:
                    if self.active_sessions.get(thread_id) == run.id:
                        self.active_sessions.pop(thread_id, None)
                self.cancel_requested.discard(run.id)
                self.timing_out.discard(run.id)
                self.generation_by_run.pop(run.id, None)
                turn_id = self.turns_by_run.pop(run.id, None)
                if turn_id:
                    self.runs_by_turn.pop(turn_id, None)
                self.done_events.pop(run.id, None)

    @staticmethod
    def _result_id(result: Any, key: str) -> str:
        if not isinstance(result, dict):
            return ""
        nested = result.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("id"), str):
            return nested["id"]
        value = result.get(f"{key}Id") or result.get("id")
        return value if isinstance(value, str) else ""

    def _on_notification(self, generation: int, method: str, params: dict[str, Any]) -> None:
        if method == "app-server/closed":
            message = str(params.get("error") or "Codex Desktop app-server closed unexpectedly.")
            with self.lock:
                runs = [
                    self.runs[run_id]
                    for run_id in self.done_events
                    if run_id in self.runs
                    and self.generation_by_run.get(run_id) == generation
                    and self.runs[run_id].status in {"queued", "running", "cancelling"}
                ]
                events = [self.done_events[run.id] for run in runs]
                for run in runs:
                    run.status = "failed"
                    run.exit_code = 1
                    run.output = message[-30_000:]
                    run.finished_at = time.time()
            for event in events:
                event.set()
            return

        turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
        turn_id = params.get("turnId") or turn.get("id")
        if not isinstance(turn_id, str):
            return
        with self.lock:
            run_id = self.runs_by_turn.get(turn_id)
            if not run_id:
                thread_id = params.get("threadId")
                if isinstance(thread_id, str):
                    candidate_id = self.latest_by_session.get(thread_id)
                    candidate = self.runs.get(candidate_id) if candidate_id else None
                    if (
                        candidate
                        and self.generation_by_run.get(candidate_id) == generation
                        and candidate.status in {"queued", "running", "cancelling"}
                    ):
                        run_id = candidate_id
                        self.turns_by_run[candidate_id] = turn_id
                        self.runs_by_turn[turn_id] = candidate_id
            run = self.runs.get(run_id) if run_id else None
            if not run or self.generation_by_run.get(run.id) != generation:
                return

            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if item.get("type") == "agentMessage":
                text = item.get("text") or item.get("message") or ""
                if isinstance(text, str) and text:
                    run.output = text[-30_000:]
            elif method in {"item/agentMessage/delta", "agentMessage/delta"}:
                delta = params.get("delta")
                if isinstance(delta, str):
                    run.output = (run.output + delta)[-30_000:]

            event = None
            if method == "turn/completed":
                if run.id in self.timing_out:
                    event = self.done_events.get(run.id)
                else:
                    status = str(turn.get("status") or params.get("status") or "completed").lower()
                    cancelled = run.id in self.cancel_requested
                    if cancelled or status in {"interrupted", "cancelled", "canceled"}:
                        run.status = "cancelled"
                        run.exit_code = None
                    elif status in {"completed", "success", "succeeded"}:
                        run.status = "completed"
                        run.exit_code = 0
                    else:
                        run.status = "failed"
                        run.exit_code = 1
                        error = turn.get("error") or params.get("error")
                        if error:
                            detail = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                            run.output = (run.output + ("\n" if run.output else "") + detail)[-30_000:]
                    run.finished_at = time.time()
                    event = self.done_events.get(run.id)
        if event:
            event.set()


class RemoteCodexApp:
    def __init__(
        self,
        token: str,
        session_store: SessionStore | DesktopSessionStore,
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
                try:
                    sessions = []
                    for item in app.session_store.list():
                        value = asdict(item)
                        latest_run = app.run_manager.latest(item.id)
                        value["run"] = asdict(latest_run) if latest_run else None
                        sessions.append(value)
                except (OSError, RuntimeError, TimeoutError) as exc:
                    return self._json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
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
            try:
                session = app.session_store.get(session_id)
            except (OSError, RuntimeError, TimeoutError) as exc:
                return self._json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
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
    parser = argparse.ArgumentParser(description="Private mobile control page for Codex Desktop sessions")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--desktop-exe", type=Path, help="Path to the codex.exe bundled with Codex Desktop")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = ensure_token(args.env)
    client_factory = lambda: AppServerClient(args.desktop_exe or locate_codex_desktop_executable())
    run_manager = RunManager(client_factory=client_factory)
    app = RemoteCodexApp(
        token,
        DesktopSessionStore(run_manager._get_client),
        run_manager,
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
        run_manager.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
