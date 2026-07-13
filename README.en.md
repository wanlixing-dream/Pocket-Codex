<div align="center">

# PocketCodex

**Connect to and continue Codex desktop tasks from a phone browser.**

[![CI](https://github.com/wanlixing-dream/Pocket-Codex/actions/workflows/ci.yml/badge.svg)](https://github.com/wanlixing-dream/Pocket-Codex/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Dependencies](https://img.shields.io/badge/Python_dependencies-none-brightgreen)

**[中文](./README.md)** · **[Architecture](./docs/ARCHITECTURE.md)** · **[Notifications and approvals](./docs/NOTIFICATIONS.md)**

</div>

> [!IMPORTANT]
> PocketCodex is an unofficial, community-built project. It is not affiliated with or endorsed by OpenAI.
> It connects to the `app-server` bundled with the Codex/ChatGPT desktop app. It does not require a separate command-line package and is not a remote screen mirror.

## What it does

- Lists recent tasks from the Codex desktop app.
- Sends a new prompt to the same desktop thread.
- Creates a desktop task in an allowed project folder.
- Uploads JPEG, PNG, or WebP images for Codex to inspect.
- Shows run status, elapsed time, and output, and can stop an active run.
- Supports browser speech recognition for voice input.
- Optionally sends completion and approval notifications through ntfy or Pushcut.

Codex, its threads, and your project files remain on your desktop. The phone is only a remote client.

## How it works

```mermaid
flowchart LR
    Phone[Phone browser] -->|HTTPS| Access{Access layer}
    Access -->|Default for mainland China| CF[Cloudflare Quick Tunnel]
    Access -->|Private-network alternative| TS[Tailscale Serve]
    TS --> Server[PocketCodex server<br/>127.0.0.1:8765]
    CF --> Server
    Server --> Bridge[Connect to desktop app-server]
    Bridge -->|thread/start| New[Create desktop task]
    Bridge -->|thread/resume + turn/start| Resume[Continue desktop task]
    New --> Store[Desktop thread store]
    Resume --> Store
    Store --> Desktop[Windows/macOS Codex or ChatGPT desktop app]
    Bridge --> Workspace[Local workspace]
    Server -.optional.-> Notify[ntfy / Pushcut]
```

See [Architecture](./docs/ARCHITECTURE.md) for component boundaries, request flows, and the security model.

## Requirements

### Desktop

- Windows 10/11 with the Microsoft Store **Codex desktop app**, or macOS with the **ChatGPT/Codex desktop app**, installed and signed in.
- Python 3.10 or newer.
- One remote access option:
  - Default setup: [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
  - Private-network alternative: [Tailscale](https://tailscale.com/download)

### Phone

- Safari, Chrome, or another modern browser.
- The phone must be able to reach the generated `trycloudflare.com` URL. Some mainland-China iPhone users use an already-installed proxy client such as Shadowrocket; PocketCodex does not provide a proxy service.
- Tailscale installed and signed in to the same tailnet only when using the alternative setup.

The Python server uses only the standard library. There is no `pip install` step.

## Quick start

### 1. Clone and verify

Windows PowerShell:

```powershell
git clone https://github.com/wanlixing-dream/Pocket-Codex.git
cd Pocket-Codex
python --version
```

macOS Terminal:

```bash
git clone https://github.com/wanlixing-dream/Pocket-Codex.git
cd Pocket-Codex
python3 --version
```

Open the desktop app, sign in, and confirm that it can create a local task. PocketCodex discovers the bundled app-server automatically: on Windows from the Microsoft Store Codex app, and on macOS from `/Applications/ChatGPT.app/Contents/Resources/codex` or `/Applications/Codex.app/Contents/Resources/codex`.

### 2. Start PocketCodex

Windows PowerShell:

```powershell
python .\remote_codex_server.py
```

macOS Terminal:

```bash
python3 remote_codex_server.py
```

The server listens only on `http://127.0.0.1:8765` by default. On first start it creates a private `remote.env` containing a random access token. Open the printed local URL on the desktop first and confirm that the desktop task list loads.

> [!WARNING]
> Never commit, screenshot, or publicly share `remote.env`. Anyone with its token may be able to send instructions to your Codex desktop threads.

### 3. Connect with Cloudflare Quick Tunnel (default)

Install `cloudflared`, keep PocketCodex running, and start a second terminal. On macOS you can install it with `brew install cloudflared`; on Windows use Cloudflare's official download page.

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

The same command works in macOS Terminal:

```bash
cloudflared tunnel --url http://127.0.0.1:8765
```

Open the generated URL on the phone and append the token once:

```text
https://random-name.trycloudflare.com/#token=YOUR_REMOTE_CODEX_TOKEN
```

The web client stores the token in that browser and removes the fragment from the address bar. Quick Tunnel URLs are public and normally change after cloudflared restarts, so never share the complete URL in chats, issues, or screenshots.

You can also use the cross-platform helper to start PocketCodex and the Quick Tunnel together:

Windows PowerShell:

```powershell
python .\start_remote_codex.py
```

macOS Terminal:

```bash
python3 start_remote_codex.py
```

The helper waits until both services are ready, prints the private phone URL, and stays in the foreground. Closing that terminal or pressing `Ctrl+C` stops the processes started by the helper.

If the phone shows Cloudflare `Error 1016 Origin DNS error` after refreshing, the old `*.trycloudflare.com` quick-tunnel hostname has usually expired or cloudflared stopped on the desktop. Start the helper again, or rerun `cloudflared tunnel --url http://127.0.0.1:8765`, then open the new URL.

### 4. Tailscale Serve (private-network alternative)

Tailscale adds device identity and tailnet ACLs. Use it when it is available and you prefer a stable private address:

```powershell
tailscale serve --bg http://127.0.0.1:8765
tailscale serve status
```

Open the reported HTTPS URL and append the token on first use:

```text
https://your-device.your-tailnet.ts.net/#token=YOUR_REMOTE_CODEX_TOKEN
```

Stop sharing with `tailscale serve reset`.

### 5. Use the mobile client

1. Select a Codex desktop thread from Recent Tasks.
2. Enter a prompt and optionally attach up to four images.
3. Send it to continue that thread through the desktop app's bundled app-server.
4. Use the `+` button to choose a folder and create a new desktop task.
5. Watch the run state, inspect output, or stop the process.


## One Phone, Multiple Computers

This works today as “one entry point per computer,” not as a central device dashboard:

- Run `remote_codex_server.py` on each Windows or macOS computer.
- Start a separate Cloudflare Quick Tunnel or Tailscale Serve URL for each computer.
- Bookmark each URL on the phone, for example “Home Mac mini” and “Office Windows”.
- Keep a separate `remote.env` token per computer; do not reuse one token everywhere.
- The same phone can save multiple entries, but the open page controls only the computer behind that URL.

A future shared device list should add device registration, names, token rotation, online checks, and revocation instead of mixing multiple computers behind one public entry point.

## Allowed project roots

The new-task folder picker only exposes `Desktop` and `Documents` by default. This is an explicit allowlist, not a refresh problem.

Complete one first start so PocketCodex generates a secure token, then add `REMOTE_CODEX_ROOTS` to `remote.env`. Separate Windows paths with semicolons and macOS/Linux paths with colons:

```dotenv
# Windows
REMOTE_CODEX_ROOTS=C:\Users\you\Desktop;C:\Users\you\source;D:\Projects

# macOS / Linux
REMOTE_CODEX_ROOTS=/Users/you/Desktop:/Users/you/Projects
```

Restart PocketCodex after changing the setting. The allowlist controls where a new task may start; it is not a filesystem sandbox for Codex and does not restrict existing threads.

## Configuration

Start from [`remote.env.example`](./remote.env.example), or let PocketCodex create `remote.env` automatically:

```dotenv
# At least 24 characters. A long random value is recommended.
REMOTE_CODEX_TOKEN=replace-with-a-long-random-token

# Optional roots for new desktop tasks.
# Windows uses semicolons; macOS/Linux use colons.
REMOTE_CODEX_ROOTS=C:\Users\you\Desktop;D:\Projects
# REMOTE_CODEX_ROOTS=/Users/you/Desktop:/Users/you/Projects
```

The real `remote.env` is excluded by `.gitignore`.

## Optional notifications and approvals

The hook scripts are optional and are not required for mobile desktop-task control:

- `watch_done.py` sends completion, failure, and usage-limit notifications.
- `watch_approve.py` forwards supported interactive Codex or Claude Code approvals to a phone or watch.
- Core mobile remote control does not require ntfy or Pushcut.
- ntfy supports Android, Wear OS, regular phone notifications, and optional Quick Tunnel new-link alerts.
- Pushcut can provide richer iPhone and Apple Watch actions; dynamic buttons and some advanced features may require Pushcut Pro.

PocketCodex uses its own app-server connection. Interactive approval requests are not automatically transferred to another open desktop window; the current remote endpoint rejects unsupported requests instead of approving them. See [Notifications and approvals](./docs/NOTIFICATIONS.md).

## Security

PocketCodex can start Codex on your machine and should be treated as a remote administration endpoint:

- Keep the server bound to `127.0.0.1`; do not expose `0.0.0.0` directly.
- The default Quick Tunnel is a public endpoint. Treat the complete tokenized URL as a password.
- When Tailscale is available, its device identity and ACLs provide an additional private-network layer.
- Do not share URLs containing `#token=` or `?token=`.
- Rotate the token and reset the tunnel if a URL or token may have leaked.
- Allow only project roots that you genuinely need remotely.
- `REMOTE_CODEX_ROOTS` limits the new-task picker only. Codex permissions are still governed by its sandbox, approval policy, and the desktop user account.
- A valid client can read recent prompt/response metadata and send new instructions.
- Stop cloudflared and PocketCodex when they are not in use; Quick Tunnel is not a private network.

## Current limitations

- PocketCodex shares persistent threads with the desktop app, but it is not a live screen mirror. Do not submit to the same thread from phone and desktop at the same time.
- The desktop `app-server` is an internal interface and may require compatibility updates after a Codex desktop release.
- Only one PocketCodex run may be active for a given thread.
- The server lists the 30 most recent desktop threads by default.
- Prompts are limited to 20,000 characters.
- Each request accepts up to four JPEG/PNG/WebP images, 8 MB each.
- Runs time out after six hours and retain the final 30,000 output characters in memory.
- A folder level shows at most 250 non-hidden subdirectories.
- Run state is in memory and is lost when the service restarts.
- `start_remote_codex.ps1` currently targets a specific Windows + cloudflared + ntfy setup; use the manual commands above for a portable installation.

## Project layout

```text
Pocket-Codex/
├── remote_codex_server.py   # API, authentication, desktop discovery, app-server client
├── remote_web/              # Mobile HTML/CSS/JavaScript client
├── start_remote_codex.ps1   # Windows Quick Tunnel helper
├── watch_approve.py         # Optional approval hook
├── watch_done.py            # Optional completion/failure hook
├── examples/                # Codex and Claude Code hook examples
├── docs/                    # Architecture and optional feature guides
└── tests/                   # Standard-library unittest suite
```

## Development

```powershell
python -m unittest discover -s tests -v
python -m py_compile remote_codex_server.py watch_approve.py watch_done.py
```

Core unit tests run without network access; desktop discovery covers Windows and macOS, while end-to-end app-server verification should be run on the target desktop OS.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| The phone shows `Unauthorized` | Reopen a URL with the current `#token=`; after rotation, clear this site's browser storage |
| Projects outside Desktop/Documents are missing | Add `REMOTE_CODEX_ROOTS` to the generated `remote.env`, then restart the server |
| The task list is empty | Complete at least one task in the Codex/ChatGPT desktop app under the same OS user |
| The phone cannot connect | Verify that the Python server is running, then inspect `tailscale serve status` or the cloudflared console |
| Desktop app-server cannot be found | Windows: install and launch Codex from Microsoft Store. macOS: install and launch ChatGPT/Codex. You can also set `REMOTE_CODEX_DESKTOP_EXE` to the bundled `codex`/`codex.exe` |
| A remote run needs approval | Return to the Codex desktop app; PocketCodex never auto-approves unsupported sensitive actions |

Contributions are welcome. Changes involving authentication, filesystem access, or command execution should document their threat model and verification evidence.

## License

[MIT](./LICENSE)
