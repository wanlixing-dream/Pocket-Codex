#!/usr/bin/env python3
"""Start PocketCodex and a Cloudflare Quick Tunnel from one terminal."""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TextIO
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen


ROOT = Path(__file__).resolve().parent
DEFAULT_ENV = ROOT / "remote.env"
DEFAULT_WATCH_ENV = ROOT / "watch.env"
LOCAL_URL = "http://127.0.0.1:8765"
TRYCLOUDFLARE_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


def restrict_private_file(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise PermissionError(f"Cannot restrict private file permissions: {path}") from exc


def default_runtime_dir() -> Path:
    override = os.environ.get("POCKET_CODEX_RUNTIME_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "PocketCodex"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PocketCodex"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "pocket-codex"


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    restrict_private_file(path)
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def extract_quick_tunnel_url(text: str) -> str | None:
    match = TRYCLOUDFLARE_RE.search(text)
    return match.group(0) if match else None


def mobile_url(public_url: str, token: str) -> str:
    return f"{public_url.rstrip('/')}/#token={token}"


def configured_ntfy(settings: dict[str, str]) -> bool:
    topic = settings.get("NTFY_NOTIFY_TOPIC", "").strip()
    normalized = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    placeholder_prefixes = ("replace-with-", "your-long-random-topic", "example-")
    return bool(normalized) and not normalized.startswith(placeholder_prefixes)


def build_ntfy_request(settings: dict[str, str], full_url: str) -> Request:
    base = (settings.get("NTFY_BASE", "").strip() or "https://ntfy.sh").rstrip("/") + "/"
    payload = {
        "topic": settings["NTFY_NOTIFY_TOPIC"].strip(),
        "title": "PocketCodex 新链接",
        "message": f"点击通知打开新的 PocketCodex 地址。\n{full_url}",
        "priority": 4,
        "tags": ["computer", "link"],
        "click": full_url,
        "actions": [
            {
                "action": "view",
                "label": "打开 PocketCodex",
                "url": full_url,
                "clear": True,
            }
        ],
    }
    headers = {"Content-Type": "application/json"}
    token = settings.get("NTFY_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return Request(
        base,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )


def ntfy_opener(settings: dict[str, str]):
    proxy = settings.get("HTTPS_PROXY", "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else {}
    return build_opener(ProxyHandler(proxies))


def publish_mobile_url(
    settings: dict[str, str],
    runtime_dir: Path,
    full_url: str,
    opener=None,
    timeout: float = 15.0,
) -> bool:
    if not configured_ntfy(settings):
        return False
    notified_path = runtime_dir / "last-notified-url.txt"
    if notified_path.exists() and notified_path.read_text(encoding="utf-8").strip() == full_url:
        return False
    client = opener or ntfy_opener(settings)
    with client.open(build_ntfy_request(settings, full_url), timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"ntfy returned HTTP {response.status}")
    notified_path.write_text(full_url + "\n", encoding="utf-8")
    return True


def notify_mobile_url(
    watch_env_path: Path,
    runtime_dir: Path,
    full_url: str,
    timeout: float = 15.0,
) -> bool:
    try:
        settings = parse_env_file(watch_env_path)
        if not configured_ntfy(settings):
            return False
        return publish_mobile_url(settings, runtime_dir, full_url, timeout=timeout)
    except Exception as exc:
        with (runtime_dir / "notify-error.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{type(exc).__name__}: ntfy link notification failed\n")
        print("Warning: ntfy link notification failed; PocketCodex is still running.", file=sys.stderr)
        return False


def server_ready(token: str, timeout: float = 2.0) -> bool:
    request = Request(f"{LOCAL_URL}/api/sessions", headers={"X-Remote-Codex-Token": token})
    try:
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 500
    except (OSError, URLError):
        return False


def public_url_ready(public_url: str, timeout: float = 5.0) -> bool:
    try:
        with urlopen(public_url, timeout=timeout) as response:
            return 200 <= response.status < 400
    except (OSError, URLError):
        return False


def wait_for_public_url(public_url: str, process: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"cloudflared exited early with code {process.returncode}.")
        if public_url_ready(public_url):
            return
        time.sleep(1)
    raise TimeoutError(f"Cloudflare Quick Tunnel did not become reachable: {public_url}")


def wait_for_token(env_path: Path, deadline: float) -> str:
    while time.time() < deadline:
        token = parse_env_file(env_path).get("REMOTE_CODEX_TOKEN", "")
        if token:
            return token
        time.sleep(0.2)
    raise TimeoutError(f"{env_path} was not created with REMOTE_CODEX_TOKEN.")


def wait_for_server(env_path: Path, timeout: float) -> str:
    deadline = time.time() + timeout
    token = wait_for_token(env_path, deadline)
    while time.time() < deadline:
        if server_ready(token):
            return token
        time.sleep(0.5)
    raise TimeoutError(f"PocketCodex did not become ready at {LOCAL_URL}.")


def stream_process_output(stream: TextIO, log_path: Path, lines: "queue.Queue[str]") -> None:
    with log_path.open("a", encoding="utf-8") as log_file:
        for line in stream:
            log_file.write(line)
            log_file.flush()
            lines.put(line)


def wait_for_tunnel_url(process: subprocess.Popen[str], lines: "queue.Queue[str]", timeout: float) -> str:
    deadline = time.time() + timeout
    recent = ""
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"cloudflared exited early with code {process.returncode}.")
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            continue
        recent = (recent + line)[-4000:]
        url = extract_quick_tunnel_url(line)
        if url:
            return url
    raise TimeoutError(f"cloudflared did not print a trycloudflare.com URL. Recent output:\n{recent}")


def start_processes(args: argparse.Namespace) -> tuple[list[subprocess.Popen[str]], str]:
    runtime_dir = Path(args.runtime_dir).expanduser()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    try:
        runtime_dir.chmod(0o700)
    except OSError:
        pass

    env_path = Path(args.env).expanduser()
    started: list[subprocess.Popen[str]] = []
    token = parse_env_file(env_path).get("REMOTE_CODEX_TOKEN", "")

    if token and server_ready(token):
        print(f"PocketCodex is already running at {LOCAL_URL}")
    else:
        server_log = runtime_dir / "server.log"
        server_error_log = runtime_dir / "server-error.log"
        print(f"Starting PocketCodex at {LOCAL_URL}")
        server = subprocess.Popen(
            [sys.executable, str(ROOT / "remote_codex_server.py")],
            cwd=str(ROOT),
            stdout=server_log.open("a", encoding="utf-8"),
            stderr=server_error_log.open("a", encoding="utf-8"),
            text=True,
        )
        started.append(server)
        token = wait_for_server(env_path, args.startup_timeout)

    cloudflared = shutil.which(args.cloudflared)
    if not cloudflared:
        raise FileNotFoundError(f"cloudflared was not found: {args.cloudflared}")

    tunnel_log = runtime_dir / "cloudflared.log"
    line_queue: queue.Queue[str] = queue.Queue()
    print("Starting Cloudflare Quick Tunnel")
    tunnel = subprocess.Popen(
        [cloudflared, "tunnel", "--url", LOCAL_URL],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    started.append(tunnel)
    assert tunnel.stdout is not None
    threading.Thread(target=stream_process_output, args=(tunnel.stdout, tunnel_log, line_queue), daemon=True).start()

    public_url = wait_for_tunnel_url(tunnel, line_queue, args.tunnel_timeout)
    wait_for_public_url(public_url, tunnel, args.tunnel_timeout)
    full_url = mobile_url(public_url, token)
    (runtime_dir / "remote-url.txt").write_text(full_url + "\n", encoding="utf-8")
    notify_mobile_url(
        Path(args.watch_env).expanduser(),
        runtime_dir,
        full_url,
        timeout=args.notify_timeout,
    )
    return started, full_url


def stop_processes(processes: list[subprocess.Popen[str]]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Start PocketCodex with a Cloudflare Quick Tunnel.")
    parser.add_argument("--cloudflared", default="cloudflared", help="cloudflared executable name or path.")
    parser.add_argument("--env", default=str(DEFAULT_ENV), help="Path to remote.env.")
    parser.add_argument("--watch-env", default=str(DEFAULT_WATCH_ENV), help="Path to optional watch.env ntfy settings.")
    parser.add_argument("--runtime-dir", default=str(default_runtime_dir()), help="Directory for local logs and URL file.")
    parser.add_argument("--startup-timeout", type=float, default=45, help="Seconds to wait for PocketCodex.")
    parser.add_argument("--tunnel-timeout", type=float, default=60, help="Seconds to wait for the Quick Tunnel URL.")
    parser.add_argument("--notify-timeout", type=float, default=15, help="Seconds allowed for the optional ntfy publish.")
    args = parser.parse_args()

    processes: list[subprocess.Popen[str]] = []
    try:
        processes, full_url = start_processes(args)
        print()
        print("Open this private URL on your phone:")
        print(full_url)
        print()
        print("Keep this terminal open. Press Ctrl+C to stop PocketCodex and the tunnel started here.")
        while True:
            for process in processes:
                if process.poll() is not None:
                    raise RuntimeError(f"A required process exited with code {process.returncode}.")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStopping PocketCodex helper.")
        stop_processes(processes)
        return 0
    except Exception as exc:
        stop_processes(processes)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
