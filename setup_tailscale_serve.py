#!/usr/bin/env python3
"""Configure stable PocketCodex access through Tailscale Serve on macOS."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
LOCAL_URL = "http://127.0.0.1:8765"
MACOS_TAILSCALE = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")


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
