# PocketCodex remote access notes

The complete setup guide now lives in [README.md](./README.md) and [README.en.md](./README.en.md).

## Default connection for mainland-China users

Keep the Python server bound to loopback and start a Cloudflare Quick Tunnel from a second terminal:

Windows PowerShell:

```powershell
python .\remote_codex_server.py
cloudflared tunnel --url http://127.0.0.1:8765
```

macOS Terminal:

```bash
python3 remote_codex_server.py
cloudflared tunnel --url http://127.0.0.1:8765
```

Open the generated HTTPS URL on the phone and append the token from `remote.env` on first use:

```text
https://random-name.trycloudflare.com/#token=YOUR_REMOTE_CODEX_TOKEN
```

The phone must be able to reach `trycloudflare.com`. Some mainland-China users rely on an already-installed proxy client such as Shadowrocket. PocketCodex does not provide or configure proxy services.

Quick Tunnel is a public endpoint. Keep the token private and stop cloudflared when it is not needed.

## Tailscale alternative

Tailscale remains available as a private-network option for users who can install it on both devices:

```powershell
tailscale serve --bg http://127.0.0.1:8765
tailscale serve status
```

Append `#token=...` to the reported tailnet URL. Tailscale adds device identity and ACLs but is not required for the default PocketCodex setup.

## What the remote service controls

- It reads threads created by the Codex/ChatGPT desktop app on Windows or macOS.
- It discovers and starts the `app-server` bundled with that desktop app; on macOS this is usually inside `ChatGPT.app/Contents/Resources/codex`.
- It creates work with `thread/start` and continues it with `thread/resume` plus `turn/start`.
- It does not require a separate command-line package, Pushcut, or ntfy for core remote control, and it does not automate the desktop with mouse input.
- It never auto-approves unsupported app-server requests.

See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the complete trust model.
