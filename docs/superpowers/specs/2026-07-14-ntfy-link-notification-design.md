# PocketCodex ntfy Link Notification Design

## Goal

When `start_remote_codex.py` creates a new Cloudflare Quick Tunnel, PocketCodex sends the verified mobile URL to the user's ntfy topic. Tapping the notification or its action button opens PocketCodex on the phone.

The feature must work on macOS, Linux, and Windows without adding Python dependencies.

## Scope

- Extend the cross-platform `start_remote_codex.py` helper.
- Read ntfy settings from the existing ignored `watch.env` file.
- Wait until the public tunnel URL is reachable before notifying.
- Avoid sending the same URL more than once.
- Document setup and troubleshooting in the Chinese and English user documentation.
- Add automated tests for the new behavior.

## Non-Goals

- Do not replace Quick Tunnel with a named Cloudflare Tunnel.
- Do not change approval or task-completion notification behavior.
- Do not add ntfy, requests, or another package dependency.
- Do not commit a real topic, PocketCodex token, tunnel URL, or other local secret.

## Configuration

The helper reads `watch.env` from the repository root by default. A CLI option may select another file for testing or advanced setups.

Supported values:

```dotenv
NTFY_NOTIFY_TOPIC=replace-with-phone-subscription-topic
NTFY_BASE=https://ntfy.sh
NTFY_TOKEN=
HTTPS_PROXY=
```

`NTFY_NOTIFY_TOPIC` enables the feature. Missing or placeholder topics disable link notifications without preventing PocketCodex from starting. `NTFY_BASE` defaults to `https://ntfy.sh`. `NTFY_TOKEN` adds `Authorization: Bearer ...` for protected servers. Proxy settings follow the existing notification scripts.

The user's real settings live only in ignored `watch.env`.

## Components

### Public URL Readiness

After cloudflared prints a Quick Tunnel URL, the helper repeatedly requests the authenticated public sessions API until it returns four consecutive HTTP 200 responses or the existing tunnel timeout expires. Transient DNS failures, Cloudflare `530` responses, and connection errors reset the stability counter and are retried.

The private PocketCodex token is not needed for this readiness request because the static root is public.

### ntfy Publisher

A small standard-library function publishes to `<NTFY_BASE>/<NTFY_NOTIFY_TOPIC>` with:

- title: `PocketCodex 新链接`
- message: a short instruction plus the mobile URL
- priority: high
- tags: `computer,link`
- click target: the full mobile URL
- visible `view` action: `打开 PocketCodex`
- optional bearer authorization

The notification includes the full `#token=...` URL because the phone must store the token before PocketCodex API calls can succeed.

### Deduplication

After a successful ntfy response, the helper writes the URL to `last-notified-url.txt` in the private runtime directory. A matching URL is not sent again. Failed notifications do not update this file, allowing a later startup to retry.

### Error Handling

Tunnel creation and PocketCodex availability remain the primary startup path. Missing ntfy configuration or ntfy network failure is non-fatal:

- print a concise warning;
- append details to `notify-error.log` in the runtime directory;
- keep PocketCodex and cloudflared running;
- still print and save the mobile URL locally.

Public tunnel readiness remains fatal for startup because sending an unverified URL recreates the current broken-link experience.

## Data Flow

1. Start or reuse the local PocketCodex server.
2. Start cloudflared and parse the generated Quick Tunnel URL.
3. Poll the public root until reachable.
4. Append the PocketCodex token as a URL fragment.
5. Save the mobile URL to `remote-url.txt`.
6. Load `watch.env`.
7. Skip if ntfy is not configured or this URL was already notified.
8. Publish the notification and record the URL on success.
9. Keep both required processes monitored by the existing helper loop.

## Security

- `watch.env`, `remote.env`, runtime URL files, and error logs remain untracked.
- Tests and docs use fake topics and URLs.
- ntfy topic names and mobile URLs are treated as sensitive even when the user accepts public-topic risk.
- Authorization values must never appear in logs or exception messages.

## Tests

Automated tests will cover:

- retrying a public URL until it becomes reachable;
- generating the ntfy endpoint and required headers/actions;
- optional bearer authorization;
- missing topic skips notification;
- unchanged URL is deduplicated;
- failed publish does not update deduplication state;
- successful publish records the URL;
- notification failure does not terminate startup;
- real secrets and hostnames are absent from tracked files.

The full Python unit suite, Python compilation, JavaScript syntax check, and live local/public health checks will run before completion.

## Documentation

`README.md`, `README.en.md`, and `docs/NOTIFICATIONS.md` will explain:

- subscribe to an ntfy topic on the phone;
- configure `watch.env` without committing it;
- start PocketCodex with `start_remote_codex.py`;
- expect a new clickable notification whenever a Quick Tunnel URL changes;
- Quick Tunnel URLs remain temporary and require the helper process to stay running.

## Acceptance Criteria

- Starting PocketCodex with a configured topic sends one clickable notification containing the verified current mobile URL.
- Opening the notification loads PocketCodex and authenticates using the URL fragment.
- Restarting without a URL change does not send a duplicate.
- ntfy outages do not stop local or remote PocketCodex service.
- No real topic, token, or tunnel URL is committed.
