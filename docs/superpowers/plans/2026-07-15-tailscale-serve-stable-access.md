# PocketCodex Stable Tailscale Serve Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an idempotent macOS setup command that gives PocketCodex a fixed Tailscale Serve URL and a persistent user LaunchAgent, then document why this replaces Quick Tunnel for normal long-term use.

**Architecture:** A new standard-library `setup_tailscale_serve.py` command owns Tailscale CLI discovery, structured status validation, Serve configuration, stable URL verification, ntfy delivery, and macOS LaunchAgent installation. `remote_codex_server.py` remains the HTTP server, while Tailscale owns the fixed HTTPS hostname and connector lifecycle; Cloudflare Quick Tunnel remains an independent temporary fallback.

**Tech Stack:** Python 3.10+ standard library (`argparse`, `json`, `plistlib`, `subprocess`, `urllib`, `pathlib`, `unittest.mock`), macOS launchd, Tailscale Serve, existing PocketCodex token and ntfy helpers.

---

## File Structure

- Create `setup_tailscale_serve.py`: setup CLI, Tailscale status/configuration, stable URL checks, LaunchAgent generation/loading, uninstall command.
- Create `tests/test_setup_tailscale_serve.py`: unit coverage for status parsing, CLI discovery, plist safety, command orchestration, and failure states.
- Modify `README.md`: make Tailscale Serve the recommended durable access mode and explain Quick Tunnel's temporary role.
- Modify `docs/NOTIFICATIONS.md`: explain that ntfy sends a stable Serve link once and continues handling task/approval notifications.
- Modify `docs/ARCHITECTURE.md`: record the stable-name/connector/server lifecycle separation and security boundary.

### Task 1: Tailscale Status and Stable URL Helpers

**Files:**
- Create: `setup_tailscale_serve.py`
- Create: `tests/test_setup_tailscale_serve.py`

- [ ] **Step 1: Write failing tests for CLI discovery and status validation**

```python
def test_find_tailscale_uses_macos_app_bundle():
    with patch.object(setup.shutil, "which", return_value=None), patch.object(
        setup.Path, "is_file", return_value=True
    ):
        self.assertEqual(setup.find_tailscale(), setup.MACOS_TAILSCALE)

def test_stable_base_url_requires_running_backend_and_dns():
    self.assertEqual(
        setup.stable_base_url({"BackendState": "Running", "Self": {"DNSName": "mac.tailnet.ts.net."}}),
        "https://mac.tailnet.ts.net",
    )
    with self.assertRaises(RuntimeError):
        setup.stable_base_url({"BackendState": "NeedsLogin", "Self": {"DNSName": ""}})
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_setup_tailscale_serve.TailscaleStatusTests -v`

Expected: FAIL because `setup_tailscale_serve` and its helpers do not exist.

- [ ] **Step 3: Implement CLI discovery, structured status loading, and URL construction**

```python
MACOS_TAILSCALE = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")

def find_tailscale() -> Path:
    discovered = shutil.which("tailscale")
    if discovered:
        return Path(discovered)
    if MACOS_TAILSCALE.is_file():
        return MACOS_TAILSCALE
    raise FileNotFoundError("Tailscale is not installed.")

def stable_base_url(status: dict[str, object]) -> str:
    if status.get("BackendState") != "Running":
        raise RuntimeError("Tailscale is not connected. Sign in and rerun setup.")
    self_status = status.get("Self")
    dns_name = self_status.get("DNSName", "") if isinstance(self_status, dict) else ""
    if not isinstance(dns_name, str) or not dns_name.strip("."):
        raise RuntimeError("Tailscale did not provide a stable DNS name.")
    return f"https://{dns_name.rstrip('.')}"
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_setup_tailscale_serve.TailscaleStatusTests -v`

Expected: PASS.

- [ ] **Step 5: Commit status helpers**

```bash
git add setup_tailscale_serve.py tests/test_setup_tailscale_serve.py
git commit -m "Add Tailscale Serve status helpers"
```

### Task 2: Safe macOS LaunchAgent Installation

**Files:**
- Modify: `setup_tailscale_serve.py`
- Modify: `tests/test_setup_tailscale_serve.py`

- [ ] **Step 1: Write failing tests for plist content and atomic installation**

```python
def test_launch_agent_contains_no_credentials(self):
    payload = setup.launch_agent_payload(
        Path("/opt/homebrew/bin/python3"), Path("/repo/remote_codex_server.py"), Path("/runtime")
    )
    serialized = plistlib.dumps(payload)
    self.assertNotIn(b"REMOTE_CODEX_TOKEN", serialized)
    self.assertNotIn(b"NTFY_NOTIFY_TOPIC", serialized)
    self.assertEqual(payload["Label"], setup.LAUNCH_AGENT_LABEL)
    self.assertTrue(payload["RunAtLoad"])
    self.assertTrue(payload["KeepAlive"])
```

- [ ] **Step 2: Run the LaunchAgent tests and verify RED**

Run: `python3 -m unittest tests.test_setup_tailscale_serve.LaunchAgentTests -v`

Expected: FAIL because LaunchAgent helpers do not exist.

- [ ] **Step 3: Implement plist generation, atomic write, bootstrap, and uninstall**

```python
LAUNCH_AGENT_LABEL = "com.pocketcodex.server"

def launch_agent_payload(python: Path, server: Path, runtime_dir: Path) -> dict[str, object]:
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [str(python), str(server)],
        "WorkingDirectory": str(server.parent),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(runtime_dir / "server.log"),
        "StandardErrorPath": str(runtime_dir / "server-error.log"),
    }
```

Write the plist with `plistlib.dump()` to a sibling temporary file, set mode `0644`, then use `os.replace()`. Load it with `launchctl bootout gui/<uid>/<label>` followed by `launchctl bootstrap gui/<uid> <plist>` and `launchctl kickstart -k gui/<uid>/<label>`. The uninstall path runs `bootout` and removes only the generated plist.

- [ ] **Step 4: Run LaunchAgent tests and verify GREEN**

Run: `python3 -m unittest tests.test_setup_tailscale_serve.LaunchAgentTests -v`

Expected: PASS.

- [ ] **Step 5: Commit LaunchAgent support**

```bash
git add setup_tailscale_serve.py tests/test_setup_tailscale_serve.py
git commit -m "Install persistent PocketCodex LaunchAgent"
```

### Task 3: End-to-End Serve Setup and Notification

**Files:**
- Modify: `setup_tailscale_serve.py`
- Modify: `tests/test_setup_tailscale_serve.py`

- [ ] **Step 1: Write failing orchestration tests**

```python
def test_setup_configures_serve_verifies_and_notifies(self):
    with patch.object(setup, "run_tailscale") as run, patch.object(
        setup, "verify_stable_url", return_value=True
    ), patch.object(setup, "install_launch_agent") as install, patch.object(
        setup, "notify_stable_url", return_value=True
    ):
        result = setup.configure_stable_access(self.args)
    run.assert_any_call(self.args.tailscale, "serve", "--bg", "--yes", setup.LOCAL_URL)
    install.assert_called_once()
    self.assertTrue(result.notified)
```

- [ ] **Step 2: Run orchestration tests and verify RED**

Run: `python3 -m unittest tests.test_setup_tailscale_serve.SetupFlowTests -v`

Expected: FAIL because setup orchestration does not exist.

- [ ] **Step 3: Implement setup and uninstall CLI**

The default command performs this exact sequence:

1. discover Tailscale and validate structured status;
2. configure background Serve for `http://127.0.0.1:8765`;
3. write the LaunchAgent plist;
4. stop the legacy `com.pocketcodex.remote` submitted job if present;
5. bootstrap and wait for local `/health`;
6. verify the stable root and authenticated `/api/sessions`;
7. send the stable token-fragment URL through existing ntfy helpers once;
8. print the stable hostname without printing the token.

The `--uninstall` command removes the PocketCodex LaunchAgent and resets only PocketCodex's Tailscale Serve configuration after explicit invocation.

- [ ] **Step 4: Run orchestration tests and full suite**

Run:

```bash
python3 -m unittest tests.test_setup_tailscale_serve -v
python3 -m unittest discover -s tests
python3 -m py_compile setup_tailscale_serve.py remote_codex_server.py start_remote_codex.py
```

Expected: all tests pass and compilation exits zero.

- [ ] **Step 5: Commit setup flow**

```bash
git add setup_tailscale_serve.py tests/test_setup_tailscale_serve.py
git commit -m "Configure stable Tailscale Serve access"
```

### Task 4: Chinese Documentation and Live Migration

**Files:**
- Modify: `README.md`
- Modify: `docs/NOTIFICATIONS.md`
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Update Chinese user documentation**

Document:

- why Quick Tunnel is temporary and cannot provide a durable bookmark;
- why Tailscale Serve is the recommended stable mode;
- Mac and phone installation/login prerequisites;
- the single setup command and fixed URL behavior;
- multi-computer naming/bookmark workflow;
- ntfy's new role as task/approval notification rather than rotating link delivery;
- uninstall, troubleshooting, and fallback commands.

- [ ] **Step 2: Run documentation and secret checks**

Run:

```bash
git diff --check
git ls-files -z | xargs -0 rg -n 'codexapproval_notify_|#token=[A-Za-z0-9_-]{20,}|trycloudflare\.com/#token='
```

Expected: no real topic, token, or live URL is present.

- [ ] **Step 3: Commit documentation**

```bash
git add README.md docs/NOTIFICATIONS.md docs/ARCHITECTURE.md
git commit -m "Document stable Tailscale Serve access"
```

- [ ] **Step 4: Run the setup command on the current Mac**

Run: `python3 setup_tailscale_serve.py`

Expected: LaunchAgent state is running, the old temporary helper is stopped only after Serve is configured, and the command reports a verified stable hostname without exposing the token.

- [ ] **Step 5: Verify live recovery and phone continuity**

Run local and tailnet HTTP checks, restart `com.pocketcodex.server`, and repeat the checks against the same hostname. Confirm the latest stable ntfy link and existing phone bookmark both reopen PocketCodex.

- [ ] **Step 6: Request code review and fix all Critical/High/Medium findings**

Review the complete diff from `2d2be35` to `HEAD`, with emphasis on credential leakage, launchd lifecycle, idempotency, rollback behavior, and cross-platform documentation accuracy.

- [ ] **Step 7: Push the verified main branch**

```bash
git push origin main
git status --short --branch
```

Expected: local `HEAD` equals `origin/main`, the worktree is clean, and the persistent service remains healthy.
