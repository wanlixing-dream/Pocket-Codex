# ntfy Link Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send each verified Cloudflare Quick Tunnel mobile URL to the user's ntfy topic from the cross-platform PocketCodex startup helper.

**Architecture:** Keep startup orchestration in `start_remote_codex.py` and add small standard-library helpers for public URL readiness, ntfy request construction, proxy-aware publication, and URL deduplication. Configuration comes from ignored `watch.env`; missing or failed notifications never stop PocketCodex, while an unreachable public tunnel does stop startup before a broken URL is delivered.

**Tech Stack:** Python 3.10+ standard library (`urllib`, `json`, `pathlib`, `unittest.mock`), Cloudflare Quick Tunnel, ntfy HTTP API, Markdown documentation.

---

## File Map

- Modify `start_remote_codex.py`: public URL readiness, ntfy publishing, deduplication, CLI configuration, startup integration.
- Modify `tests/test_start_remote_codex.py`: focused tests for readiness, ntfy payload/auth, deduplication, and non-fatal failure behavior.
- Modify `README.md`: Chinese quick-start instructions for automatic ntfy link updates.
- Modify `README.en.md`: English equivalent of the cross-platform notification instructions.
- Modify `docs/NOTIFICATIONS.md`: detailed `watch.env` setup and link-notification lifecycle.
- Create local ignored `watch.env`: configure this computer's real ntfy topic; never stage it.

### Task 1: Verify Public Tunnel Readiness

**Files:**
- Modify: `start_remote_codex.py:62-112`
- Test: `tests/test_start_remote_codex.py`

- [ ] **Step 1: Write failing readiness tests**

```python
from unittest.mock import Mock, patch

def test_public_url_ready_rejects_cloudflare_error(self):
    with patch.object(starter, "urlopen", side_effect=starter.URLError("not ready")):
        self.assertFalse(starter.public_url_ready("https://pending.trycloudflare.com"))

def test_wait_for_public_url_retries_until_success(self):
    process = Mock()
    process.poll.return_value = None
    with patch.object(starter, "public_url_ready", side_effect=[False, True]), patch.object(
        starter.time, "sleep"
    ):
        starter.wait_for_public_url("https://ready.trycloudflare.com", process, timeout=5)
    self.assertEqual(starter.public_url_ready.call_count, 2)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_start_remote_codex.StartRemoteCodexTests.test_public_url_ready_rejects_cloudflare_error tests.test_start_remote_codex.StartRemoteCodexTests.test_wait_for_public_url_retries_until_success -v`

Expected: errors because `public_url_ready` and `wait_for_public_url` do not exist.

- [ ] **Step 3: Implement readiness helpers**

```python
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
```

- [ ] **Step 4: Run readiness tests and verify GREEN**

Run: `python3 -m unittest tests.test_start_remote_codex.StartRemoteCodexTests.test_public_url_ready_rejects_cloudflare_error tests.test_start_remote_codex.StartRemoteCodexTests.test_wait_for_public_url_retries_until_success -v`

Expected: 2 tests pass.

### Task 2: Build and Publish ntfy Notifications

**Files:**
- Modify: `start_remote_codex.py:6-25,115-167`
- Test: `tests/test_start_remote_codex.py`

- [ ] **Step 1: Write failing payload and authentication tests**

```python
import json

def test_builds_ntfy_json_request_with_click_and_view_action(self):
    settings = {"NTFY_NOTIFY_TOPIC": "example-topic", "NTFY_BASE": "https://ntfy.sh"}
    request = starter.build_ntfy_request(settings, "https://example.trycloudflare.com/#token=fake")
    payload = json.loads(request.data)
    self.assertEqual(request.full_url, "https://ntfy.sh/")
    self.assertEqual(payload["topic"], "example-topic")
    self.assertEqual(payload["click"], "https://example.trycloudflare.com/#token=fake")
    self.assertEqual(payload["actions"][0]["action"], "view")
    self.assertEqual(payload["actions"][0]["url"], payload["click"])

def test_builds_ntfy_request_with_bearer_token(self):
    request = starter.build_ntfy_request(
        {"NTFY_NOTIFY_TOPIC": "example-topic", "NTFY_TOKEN": "fake-auth-token"},
        "https://example.trycloudflare.com/#token=fake",
    )
    self.assertEqual(request.get_header("Authorization"), "Bearer fake-auth-token")
```

- [ ] **Step 2: Run payload tests and verify RED**

Run: `python3 -m unittest tests.test_start_remote_codex.StartRemoteCodexTests.test_builds_ntfy_json_request_with_click_and_view_action tests.test_start_remote_codex.StartRemoteCodexTests.test_builds_ntfy_request_with_bearer_token -v`

Expected: errors because `build_ntfy_request` does not exist.

- [ ] **Step 3: Implement JSON request construction**

```python
import json
from urllib.request import ProxyHandler, Request, build_opener, urlopen

DEFAULT_WATCH_ENV = ROOT / "watch.env"

def configured_ntfy(settings: dict[str, str]) -> bool:
    topic = settings.get("NTFY_NOTIFY_TOPIC", "").strip()
    return bool(topic) and "REPLACE_WITH" not in topic.upper()


def build_ntfy_request(settings: dict[str, str], full_url: str) -> Request:
    base = (settings.get("NTFY_BASE", "").strip() or "https://ntfy.sh").rstrip("/") + "/"
    payload = {
        "topic": settings["NTFY_NOTIFY_TOPIC"].strip(),
        "title": "PocketCodex 新链接",
        "message": f"点击通知打开新的 PocketCodex 地址。\n{full_url}",
        "priority": 4,
        "tags": ["computer", "link"],
        "click": full_url,
        "actions": [{"action": "view", "label": "打开 PocketCodex", "url": full_url, "clear": True}],
    }
    headers = {"Content-Type": "application/json"}
    token = settings.get("NTFY_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return Request(base, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
```

- [ ] **Step 4: Run payload tests and verify GREEN**

Run: `python3 -m unittest tests.test_start_remote_codex.StartRemoteCodexTests.test_builds_ntfy_json_request_with_click_and_view_action tests.test_start_remote_codex.StartRemoteCodexTests.test_builds_ntfy_request_with_bearer_token -v`

Expected: 2 tests pass.

- [ ] **Step 5: Write failing publish, deduplication, and failure tests**

```python
def test_publish_mobile_url_records_success_and_deduplicates(self):
    with tempfile.TemporaryDirectory() as temp:
        runtime = Path(temp)
        settings = {"NTFY_NOTIFY_TOPIC": "example-topic"}
        opener = Mock()
        opener.open.return_value.__enter__.return_value.status = 200
        self.assertTrue(starter.publish_mobile_url(settings, runtime, "https://example/#token=fake", opener=opener))
        self.assertFalse(starter.publish_mobile_url(settings, runtime, "https://example/#token=fake", opener=opener))
        self.assertEqual(opener.open.call_count, 1)

def test_publish_failure_does_not_record_url(self):
    with tempfile.TemporaryDirectory() as temp:
        runtime = Path(temp)
        opener = Mock()
        opener.open.side_effect = starter.URLError("offline")
        with self.assertRaises(starter.URLError):
            starter.publish_mobile_url(
                {"NTFY_NOTIFY_TOPIC": "example-topic"}, runtime, "https://example/#token=fake", opener=opener
            )
        self.assertFalse((runtime / "last-notified-url.txt").exists())
```

- [ ] **Step 6: Implement proxy-aware publication and deduplication**

```python
def ntfy_opener(settings: dict[str, str]):
    proxy = settings.get("HTTPS_PROXY", "").strip()
    return build_opener(ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))


def publish_mobile_url(settings, runtime_dir, full_url, opener=None, timeout=15.0):
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
```

- [ ] **Step 7: Run publication tests and verify GREEN**

Run: `python3 -m unittest tests.test_start_remote_codex -v`

Expected: all startup-helper tests pass.

### Task 3: Integrate Notification Into Startup

**Files:**
- Modify: `start_remote_codex.py:115-215`
- Test: `tests/test_start_remote_codex.py`

- [ ] **Step 1: Write a failing non-fatal notification wrapper test**

```python
def test_notify_failure_is_logged_and_nonfatal(self):
    with tempfile.TemporaryDirectory() as temp:
        runtime = Path(temp)
        watch_env = runtime / "watch.env"
        watch_env.write_text("NTFY_NOTIFY_TOPIC=example-topic\n", encoding="utf-8")
        with patch.object(starter, "publish_mobile_url", side_effect=starter.URLError("offline")):
            self.assertFalse(
                starter.notify_mobile_url(watch_env, runtime, "https://example/#token=fake")
            )
        self.assertIn("URLError", (runtime / "notify-error.log").read_text(encoding="utf-8"))
```

- [ ] **Step 2: Implement non-fatal wrapper without logging secrets**

```python
def notify_mobile_url(watch_env_path: Path, runtime_dir: Path, full_url: str, timeout: float = 15.0) -> bool:
    settings = parse_env_file(watch_env_path)
    if not configured_ntfy(settings):
        return False
    try:
        return publish_mobile_url(settings, runtime_dir, full_url, timeout=timeout)
    except Exception as exc:
        with (runtime_dir / "notify-error.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{type(exc).__name__}: ntfy link notification failed\n")
        print("Warning: ntfy link notification failed; PocketCodex is still running.", file=sys.stderr)
        return False
```

- [ ] **Step 3: Wire readiness and notification into `start_processes`**

After `wait_for_tunnel_url`:

```python
public_url = wait_for_tunnel_url(tunnel, line_queue, args.tunnel_timeout)
wait_for_public_url(public_url, tunnel, args.tunnel_timeout)
full_url = mobile_url(public_url, token)
(runtime_dir / "remote-url.txt").write_text(full_url + "\n", encoding="utf-8")
notify_mobile_url(Path(args.watch_env).expanduser(), runtime_dir, full_url, timeout=args.notify_timeout)
```

Add parser options:

```python
parser.add_argument("--watch-env", default=str(DEFAULT_WATCH_ENV), help="Path to optional watch.env ntfy settings.")
parser.add_argument("--notify-timeout", type=float, default=15, help="Seconds allowed for the optional ntfy publish.")
```

- [ ] **Step 4: Run targeted and full tests**

Run: `python3 -m unittest tests.test_start_remote_codex -v`

Expected: all startup-helper tests pass.

Run: `python3 -m unittest discover -s tests -v`

Expected: full suite passes.

- [ ] **Step 5: Commit tested implementation**

```bash
git add start_remote_codex.py tests/test_start_remote_codex.py
git commit -m "Send Quick Tunnel links through ntfy"
```

### Task 4: Configure This Mac and Document Users

**Files:**
- Create locally, ignored: `watch.env`
- Modify: `README.md:148-166,246-256,288-297`
- Modify: `README.en.md:134-150,222-232,284-293`
- Modify: `docs/NOTIFICATIONS.md:18-67,93-106`

- [ ] **Step 1: Create the ignored local ntfy configuration**

```dotenv
WATCH_TRANSPORT=ntfy
NTFY_BASE=https://ntfy.sh
```

Add `NTFY_NOTIFY_TOPIC` as the third key using the private topic supplied in this thread. Do not record that value in this tracked plan or any other tracked file.

Verify: `git check-ignore -v watch.env`

Expected: `.gitignore` matches `*.env`.

- [ ] **Step 2: Update Chinese and English quick starts**

Document copying `watch.env.example`, setting `WATCH_TRANSPORT=ntfy` and `NTFY_NOTIFY_TOPIC`, subscribing on the phone, and running `start_remote_codex.py`. State that the helper waits for HTTP readiness and sends a clickable notification only when the URL changes.

- [ ] **Step 3: Update detailed notification documentation**

Add a dedicated "Quick Tunnel 新链接" section explaining the ntfy `Click` and `view` action, runtime deduplication file, non-fatal ntfy failures, and Quick Tunnel process lifetime.

- [ ] **Step 4: Run documentation and secret checks**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `git ls-files -z | xargs -0 rg -n 'codexapproval_notify_|#token=[A-Za-z0-9_-]{20,}|trycloudflare\.com/#token='`

Expected: no matches containing this machine's topic, token, or live host.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md README.en.md docs/NOTIFICATIONS.md
git commit -m "Document ntfy Quick Tunnel link updates"
```

### Task 5: Live Verification and Delivery

**Files:**
- Runtime only: ignored `watch.env`, runtime logs and URL files.

- [ ] **Step 1: Stop the existing helper and start the updated helper**

Run: `python3 start_remote_codex.py`

Expected: local server starts, cloudflared registers a tunnel, public URL becomes reachable, and helper remains running.

- [ ] **Step 2: Verify local and public APIs**

Run authenticated local and public `/api/sessions` checks without printing the token.

Expected: both return HTTP 200.

- [ ] **Step 3: Verify ntfy delivery**

Expected: the phone topic receives `PocketCodex 新链接`; tapping the notification or `打开 PocketCodex` opens the newly verified URL.

- [ ] **Step 4: Run final quality gates**

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile remote_codex_server.py start_remote_codex.py watch_approve.py watch_done.py
node --check remote_web/app.js
git diff --check
git status --short --branch
```

Expected: all tests and syntax checks pass; only intended tracked changes are present; `watch.env` remains ignored.

- [ ] **Step 5: Review, push, and report**

Request a code review of the implementation diff, fix all Critical/High/Medium findings, push `main`, and report the commit hashes plus live HTTP status without exposing the private token.
