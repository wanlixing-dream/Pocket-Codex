#!/usr/bin/env python3
"""Configure stable PocketCodex access through Tailscale Serve on macOS."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from start_remote_codex import (
    DEFAULT_ENV,
    DEFAULT_WATCH_ENV,
    default_runtime_dir,
    mobile_url,
    notify_mobile_url,
    parse_env_file,
    server_ready,
)


ROOT = Path(__file__).resolve().parent
LOCAL_URL = "http://127.0.0.1:8765"
MACOS_TAILSCALE = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
LAUNCH_AGENT_LABEL = "com.pocketcodex.server"
LEGACY_LAUNCH_LABEL = "com.pocketcodex.remote"


@dataclass(frozen=True)
class SetupResult:
    base_url: str
    notified: bool


def find_tailscale() -> Path:
    discovered = shutil.which("tailscale")
    if discovered:
        return Path(discovered)
    if MACOS_TAILSCALE.is_file():
        return MACOS_TAILSCALE
    raise FileNotFoundError(
        "Tailscale is not installed. Install it from https://tailscale.com/download and sign in."
    )


def run_tailscale(executable: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(executable), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def load_tailscale_status(executable: Path) -> dict[str, Any]:
    result = run_tailscale(executable, "status", "--json")
    status = json.loads(result.stdout)
    if not isinstance(status, dict):
        raise RuntimeError("Tailscale returned an invalid status response.")
    return status


def stable_base_url(status: dict[str, Any]) -> str:
    if status.get("BackendState") != "Running":
        raise RuntimeError("Tailscale is not connected. Sign in and rerun setup.")
    self_status = status.get("Self")
    dns_name = self_status.get("DNSName", "") if isinstance(self_status, dict) else ""
    if not isinstance(dns_name, str) or not dns_name.strip("."):
        raise RuntimeError("Tailscale did not provide a stable DNS name.")
    return f"https://{dns_name.rstrip('.')}"


def launch_agent_payload(python: Path, server: Path, runtime_dir: Path) -> dict[str, Any]:
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [str(python), str(server)],
        "WorkingDirectory": str(server.parent),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "StandardOutPath": str(runtime_dir / "server.log"),
        "StandardErrorPath": str(runtime_dir / "server-error.log"),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        },
    }


def write_launch_agent(target: Path, payload: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            plistlib.dump(payload, handle, sort_keys=True)
        temporary.chmod(0o644)
        os.replace(temporary, target)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def managed_job_loaded(
    label: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
    uid: int | None = None,
) -> bool:
    domain = f"gui/{os.getuid() if uid is None else uid}"
    result = runner(
        ["launchctl", "print", f"{domain}/{label}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def ensure_safe_server_transition() -> None:
    if not server_ready():
        return
    if managed_job_loaded(LAUNCH_AGENT_LABEL) or managed_job_loaded(LEGACY_LAUNCH_LABEL):
        return
    raise RuntimeError(
        "Port 8765 is already served by a manually started PocketCodex process. "
        "Stop that process before running setup."
    )


def install_launch_agent(
    python: Path,
    server: Path,
    runtime_dir: Path,
    target: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
    uid: int | None = None,
) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    write_launch_agent(target, launch_agent_payload(python, server, runtime_dir))
    domain = f"gui/{os.getuid() if uid is None else uid}"
    quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    runner(
        ["launchctl", "bootout", f"{domain}/{LAUNCH_AGENT_LABEL}"],
        check=False,
        **quiet,
    )
    runner(["launchctl", "remove", LEGACY_LAUNCH_LABEL], check=False, **quiet)
    try:
        runner(["launchctl", "bootstrap", domain, str(target)], check=True)
        runner(
            ["launchctl", "kickstart", "-k", f"{domain}/{LAUNCH_AGENT_LABEL}"],
            check=True,
        )
    except Exception:
        recovery_commands = [
            (
                ["launchctl", "bootout", f"{domain}/{LAUNCH_AGENT_LABEL}"],
                {"check": False, **quiet},
            ),
            (
                [
                    "launchctl",
                    "submit",
                    "-l",
                    LEGACY_LAUNCH_LABEL,
                    "-o",
                    str(runtime_dir / "server.log"),
                    "-e",
                    str(runtime_dir / "server-error.log"),
                    "--",
                    str(python),
                    str(server),
                ],
                {"check": False},
            ),
        ]
        for command, options in recovery_commands:
            try:
                runner(command, **options)
            except Exception:
                pass
        raise


def uninstall_launch_agent(
    target: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
    uid: int | None = None,
) -> None:
    domain = f"gui/{os.getuid() if uid is None else uid}"
    runner(
        ["launchctl", "bootout", f"{domain}/{LAUNCH_AGENT_LABEL}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    target.unlink(missing_ok=True)


def wait_for_local_server(timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server_ready():
            return
        time.sleep(0.5)
    raise TimeoutError(f"PocketCodex did not become ready at {LOCAL_URL}.")


def wait_for_token(env_path: Path, timeout: float) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        token = parse_env_file(env_path).get("REMOTE_CODEX_TOKEN", "")
        if token:
            return token
        time.sleep(0.2)
    raise TimeoutError(f"{env_path} was not created with REMOTE_CODEX_TOKEN.")


def stable_url_ready(
    base_url: str,
    token: str,
    *,
    opener: Any | None = None,
    timeout: float = 15.0,
) -> bool:
    client = opener or build_opener(ProxyHandler({}))
    requests = [
        Request(f"{base_url.rstrip('/')}/"),
        Request(
            f"{base_url.rstrip('/')}/api/sessions",
            headers={"X-Remote-Codex-Token": token},
        ),
    ]
    try:
        for request in requests:
            with client.open(request, timeout=timeout) as response:
                if response.status != 200:
                    return False
        return True
    except (OSError, URLError):
        return False


def wait_for_stable_url(base_url: str, token: str, timeout: float) -> None:
    deadline = time.time() + timeout
    consecutive_successes = 0
    while time.time() < deadline:
        if stable_url_ready(base_url, token):
            consecutive_successes += 1
            if consecutive_successes >= 2:
                return
        else:
            consecutive_successes = 0
        time.sleep(2)
    raise TimeoutError("The Tailscale Serve URL did not become ready.")


def notify_stable_url(
    watch_env_path: Path,
    runtime_dir: Path,
    base_url: str,
    token: str,
) -> bool:
    return notify_mobile_url(
        watch_env_path,
        runtime_dir,
        mobile_url(base_url, token),
    )


def configure_stable_access(args: argparse.Namespace) -> SetupResult:
    tailscale = args.tailscale or find_tailscale()
    base_url = stable_base_url(load_tailscale_status(tailscale))
    ensure_safe_server_transition()
    run_tailscale(tailscale, "serve", "--bg", "--yes", LOCAL_URL)
    base_url = stable_base_url(load_tailscale_status(tailscale))
    install_launch_agent(
        args.python,
        ROOT / "remote_codex_server.py",
        args.runtime_dir,
        args.launch_agent,
    )
    wait_for_local_server(args.startup_timeout)
    token = wait_for_token(args.env, args.startup_timeout)
    wait_for_stable_url(base_url, token, args.verify_timeout)
    notified = False
    if not args.no_notify:
        notified = notify_stable_url(
            args.watch_env,
            args.runtime_dir,
            base_url,
            token,
        )
    return SetupResult(base_url=base_url, notified=notified)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure stable PocketCodex access through Tailscale Serve on macOS."
    )
    parser.add_argument("--tailscale", type=Path, help="Path to the Tailscale CLI.")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--watch-env", type=Path, default=DEFAULT_WATCH_ENV)
    parser.add_argument("--runtime-dir", type=Path, default=default_runtime_dir())
    parser.add_argument(
        "--launch-agent",
        type=Path,
        default=Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist",
    )
    parser.add_argument("--startup-timeout", type=float, default=45)
    parser.add_argument("--verify-timeout", type=float, default=120)
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if sys.platform != "darwin":
        print("Error: automated Tailscale Serve setup currently supports macOS only.", file=sys.stderr)
        return 1
    try:
        tailscale = args.tailscale or find_tailscale()
        if args.uninstall:
            run_tailscale(tailscale, "serve", "--https=443", "off")
            uninstall_launch_agent(args.launch_agent)
            print("PocketCodex Tailscale Serve and LaunchAgent were removed.")
            return 0
        args.tailscale = tailscale
        result = configure_stable_access(args)
        print(f"PocketCodex stable address: {result.base_url}")
        print("Open the latest ntfy notification once; this hostname will not rotate.")
        return 0
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
