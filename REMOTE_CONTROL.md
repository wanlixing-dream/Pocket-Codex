# PocketCodex remote access notes

The complete setup guide now lives in [README.md](./README.md) and [README.en.md](./README.en.md).

## Default connection for mainland-China users

Keep the Python server bound to loopback and start a Cloudflare Quick Tunnel from a second PowerShell:

```powershell
python .\remote_codex_server.py
cloudflared tunnel --url http://127.0.0.1:8765
```

Open the generated HTTPS URL on the phone and append the token from `remote.env` on first use:

```text
https://random-name.trycloudflare.com/#token=YOUR_REMOTE_CODEX_TOKEN
```

The phone must be able to reach `trycloudflare.com`. Some mainland-China users rely on an already-installed proxy client such as Shadowrocket. PocketCodex does not provide or configure proxy services.

Quick Tunnel is a public endpoint. Keep the token private and stop cloudflared when it is not needed.

On Windows, `start_remote_codex.ps1` starts the local server and Quick Tunnel and sends a visible tokenized link through the configured ntfy topic whenever the address changes:

```powershell
.\start_remote_codex.ps1
```

For continuous recovery when Cloudflare reclaims a temporary tunnel, install the five-minute current-user watchdog. Remove it before intentionally stopping remote access:

```powershell
.\start_remote_codex.ps1 -InstallWatchdog
.\start_remote_codex.ps1 -RemoveWatchdog
```

## Tailscale alternative

Tailscale remains available as a private-network option for users who can install it on both devices:

```powershell
tailscale serve --bg http://127.0.0.1:8765
tailscale serve status
```

Append `#token=...` to the reported tailnet URL. Tailscale adds device identity and ACLs but is not required for the default PocketCodex setup.

## What the remote service controls

- It reads recent sessions from `~/.codex/sessions`.
- It starts new work through `codex exec`.
- It continues a known local session through `codex exec resume`.
- It does not remotely control the Codex desktop GUI.
- It does not make the optional approval hook a security boundary for non-interactive remote runs.

See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the complete trust model.
