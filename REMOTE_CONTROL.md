# PocketCodex remote access notes

The complete setup guide now lives in [README.md](./README.md) and [README.en.md](./README.en.md).

## Recommended connection

Keep the Python server bound to loopback and publish it only inside your private Tailscale network:

```powershell
python .\remote_codex_server.py
tailscale serve --bg http://127.0.0.1:8765
tailscale serve status
```

Open the reported HTTPS URL on the phone and append the token from `remote.env` on first use:

```text
https://your-device.your-tailnet.ts.net/#token=YOUR_REMOTE_CODEX_TOKEN
```

## Temporary Quick Tunnel

When Tailscale is unavailable:

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

Append `#token=...` to the generated `trycloudflare.com` URL. Quick Tunnel is a public endpoint and should be used only temporarily.

## What the remote service controls

- It reads recent sessions from `~/.codex/sessions`.
- It starts new work through `codex exec`.
- It continues a known local session through `codex exec resume`.
- It does not remotely control the Codex desktop GUI.
- It does not make the optional approval hook a security boundary for non-interactive remote runs.

See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the complete trust model.
