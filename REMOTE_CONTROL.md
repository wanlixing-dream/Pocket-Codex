# PocketCodex remote access notes

The complete setup guide now lives in [README.md](./README.md) and [README.en.md](./README.en.md).

## Stable Windows connection with Tailscale

Install Tailscale on Windows and the phone, sign into the same tailnet, then run:

```powershell
.\start_remote_codex.ps1 -InstallWatchdog -AccessMode Tailscale
```

The launcher validates the fixed HTTPS endpoint, stops the old Quick Tunnel, stores the private link under `%LOCALAPPDATA%\RemoteCodex`, and optionally sends it through ntfy. Check the non-secret state with:

```powershell
.\start_remote_codex.ps1 -Status
```

`RemoteCodexWatchdog` is a Windows scheduled task. It replaces the macOS LaunchAgent role; Windows does not support LaunchAgent or `launchctl`.

## Cloudflare temporary fallback

For a temporary public Quick Tunnel:

```powershell
.\start_remote_codex.ps1 -AccessMode Cloudflare
```

To keep rebuilding it every five minutes:

```powershell
.\start_remote_codex.ps1 -InstallWatchdog -AccessMode Cloudflare
.\start_remote_codex.ps1 -RemoveWatchdog
```

Quick Tunnel changes address and is reachable from the public internet. Keep the tokenized link private.

## What the remote service controls

- It reads recent sessions from `~/.codex/sessions`.
- It starts new work through `codex exec`.
- It continues a known local session through `codex exec resume`.
- It does not remotely control the Codex desktop GUI.
- It does not make the optional approval hook a security boundary for non-interactive remote runs.

See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the complete trust model.
