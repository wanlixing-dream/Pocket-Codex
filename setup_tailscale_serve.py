#!/usr/bin/env python3
"""Configure stable PocketCodex access through Tailscale Serve on macOS."""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
LOCAL_URL = "http://127.0.0.1:8765"
MACOS_TAILSCALE = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
LAUNCH_AGENT_LABEL = "com.pocketcodex.server"
LEGACY_LAUNCH_LABEL = "com.pocketcodex.remote"


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
    runner(["launchctl", "bootstrap", domain, str(target)], check=True)
    runner(
        ["launchctl", "kickstart", "-k", f"{domain}/{LAUNCH_AGENT_LABEL}"],
        check=True,
    )
