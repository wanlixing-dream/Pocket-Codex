# PocketCodex Stable Tailscale Serve Access Design

## Goal

Give a phone a stable, reusable PocketCodex URL that survives tunnel reconnects and normal Mac login cycles. The user should open the link once, add it to the Home Screen if desired, and never need a newly generated ntfy link for routine recovery.

## Scope

This first implementation targets macOS because the current installation is a Mac and Tailscale Serve is already enabled there. It keeps the existing Cloudflare Quick Tunnel path as an explicit temporary fallback and does not remove ntfy task or approval notifications.

Windows Tailscale Serve remains documented as a supported access option, but automated Windows service installation is outside this increment.

## User Experience

1. Install Tailscale on the Mac and phone and sign both into the same tailnet.
2. Run one PocketCodex setup command on the Mac.
3. The setup command configures `tailscale serve --bg` for `http://127.0.0.1:8765` and installs a user LaunchAgent for the PocketCodex HTTP server.
4. PocketCodex verifies the stable tailnet URL before displaying or notifying it.
5. The phone opens the stable `https://<device>.<tailnet>.ts.net/#token=<token>` URL once. The web app stores the token and removes it from the address bar as it does today.
6. If the Mac sleeps, changes networks, or temporarily disconnects, the same URL becomes usable again after the Mac and Tailscale reconnect. No replacement link is required.

For multiple computers, each computer has its own stable device hostname and PocketCodex token. A phone can bookmark or add each fixed URL to its Home Screen with the computer name.

## Components

### Tailscale Serve Setup

Add a small standard-library setup command that:

- locates the Tailscale CLI, including the macOS app bundle path;
- verifies that `BackendState` is `Running` and that the device has a DNS name;
- runs `tailscale serve --bg --yes http://127.0.0.1:8765`;
- obtains the configured HTTPS hostname from structured Tailscale status output;
- verifies both the public page and authenticated sessions API through the tailnet URL;
- optionally sends the verified stable URL through the existing ntfy configuration;
- never prints or stores the token outside the existing private runtime/config files.

If Tailscale needs login or Serve authorization, the command prints a clear actionable error and leaves the existing Quick Tunnel untouched.

### macOS LaunchAgent

Install `~/Library/LaunchAgents/com.pocketcodex.server.plist` with:

- `RunAtLoad` enabled;
- `KeepAlive` enabled for unexpected exits;
- the absolute Python and repository paths;
- stdout/stderr directed to the private PocketCodex runtime directory;
- no token, ntfy topic, or tunnel URL embedded in the plist.

The LaunchAgent starts only `remote_codex_server.py`. Tailscale manages Serve configuration and its own background lifecycle. This separation prevents a connector restart from changing the stable URL.

The setup command is idempotent and includes a documented uninstall/reset path.

### Notifications

ntfy remains responsible for task completion, approval, and exceptional connection-state notifications. For Tailscale Serve, the stable link is sent once during setup or when the token/device hostname changes. Routine reconnects do not generate a new-link notification.

Quick Tunnel retains its current new-link notification behavior because its hostname is inherently temporary.

## Access Modes

Documentation presents three explicit modes:

- `Tailscale Serve`: recommended stable private access.
- `Cloudflare managed tunnel`: future/advanced stable public access with a custom domain.
- `Cloudflare Quick Tunnel`: temporary testing and recovery only.

The project must not describe Quick Tunnel as a durable default after this change.

## Security

- Tailscale Serve is reachable only from the user's tailnet.
- PocketCodex token authentication remains required as defense in depth.
- `remote.env` and `watch.env` remain ignored and mode `0600` on POSIX systems.
- The LaunchAgent contains paths and process arguments only, never credentials.
- The setup command must redact stable hostnames and tokens from error logs where they are not needed.

## Failure Handling

- Missing Tailscale: provide the official installation instruction and make no system changes.
- Needs login: ask the user to authenticate, then safely rerun setup.
- Serve authorization required: surface the official approval URL and safely rerun setup.
- Stable URL health check fails: keep Quick Tunnel running and do not send a broken stable link.
- LaunchAgent installation fails: report the exact local path/error without changing the current access method.
- Tailscale disconnects later: keep the stable configuration; rely on Tailscale reconnection rather than rotating URLs.

## Testing

Add unit tests for:

- Tailscale CLI discovery and structured status parsing;
- login, missing-DNS, and Serve-authorization failure states;
- stable mobile URL construction without token leakage;
- idempotent LaunchAgent generation and loading;
- plist contents excluding credentials;
- successful and failed stable URL verification;
- ntfy deduplication for an unchanged stable URL.

Perform a macOS smoke test that proves:

- the LaunchAgent is running;
- local `/health` returns HTTP 200;
- the Tailscale root and authenticated sessions API return HTTP 200;
- restarting the PocketCodex server preserves the same Tailscale hostname;
- the phone can reopen the original bookmark after restart.

## Non-Goals

- Building a central multi-computer cloud control plane.
- Making Tailscale Serve publicly accessible without joining the tailnet.
- Automatically installing or authenticating Tailscale without user consent.
- Removing Quick Tunnel or Cloudflare support.
- Automating Windows service installation in this first increment.
