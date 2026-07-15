import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import setup_tailscale_serve as setup


class TailscaleStatusTests(unittest.TestCase):
    def test_find_tailscale_uses_path_binary_first(self):
        with patch.object(setup.shutil, "which", return_value="/usr/local/bin/tailscale"):
            self.assertEqual(setup.find_tailscale(), Path("/usr/local/bin/tailscale"))

    def test_find_tailscale_uses_macos_app_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "Tailscale"
            executable.touch()
            with patch.object(setup.shutil, "which", return_value=None), patch.object(
                setup, "MACOS_TAILSCALE", executable
            ):
                self.assertEqual(setup.find_tailscale(), executable)

    def test_find_tailscale_fails_when_not_installed(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(
            setup.shutil, "which", return_value=None
        ), patch.object(setup, "MACOS_TAILSCALE", Path(temp) / "missing"):
            with self.assertRaises(FileNotFoundError):
                setup.find_tailscale()

    def test_stable_base_url_requires_running_backend_and_dns(self):
        status = {
            "BackendState": "Running",
            "Self": {"DNSName": "best-mac.example-tailnet.ts.net."},
        }

        self.assertEqual(
            setup.stable_base_url(status),
            "https://best-mac.example-tailnet.ts.net",
        )

    def test_stable_base_url_rejects_logged_out_device(self):
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            setup.stable_base_url({"BackendState": "NeedsLogin", "Self": {"DNSName": ""}})

    def test_stable_base_url_rejects_missing_dns_name(self):
        with self.assertRaisesRegex(RuntimeError, "stable DNS"):
            setup.stable_base_url({"BackendState": "Running", "Self": {"DNSName": ""}})


class LaunchAgentTests(unittest.TestCase):
    def test_launch_agent_contains_no_credentials(self):
        payload = setup.launch_agent_payload(
            Path("/opt/homebrew/bin/python3"),
            Path("/repo/remote_codex_server.py"),
            Path("/runtime"),
        )

        serialized = plistlib.dumps(payload)

        self.assertEqual(payload["Label"], setup.LAUNCH_AGENT_LABEL)
        self.assertTrue(payload["RunAtLoad"])
        self.assertTrue(payload["KeepAlive"])
        self.assertNotIn(b"REMOTE_CODEX_TOKEN", serialized)
        self.assertNotIn(b"NTFY_NOTIFY_TOPIC", serialized)
        self.assertNotIn(b"#token=", serialized)

    @unittest.skipIf(os.name == "nt", "POSIX file modes are not available on Windows")
    def test_write_launch_agent_is_atomic_and_world_readable_without_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "com.pocketcodex.server.plist"
            payload = setup.launch_agent_payload(
                Path("/usr/bin/python3"),
                Path("/repo/remote_codex_server.py"),
                Path("/runtime"),
            )

            setup.write_launch_agent(target, payload)

            self.assertEqual(plistlib.loads(target.read_bytes()), payload)
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            self.assertEqual(list(Path(temp).glob(".*.tmp")), [])

    def test_install_launch_agent_replaces_legacy_job_and_bootstraps(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "com.pocketcodex.server.plist"
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return None

            setup.install_launch_agent(
                Path("/usr/bin/python3"),
                Path("/repo/remote_codex_server.py"),
                Path(temp) / "runtime",
                target,
                runner=runner,
                uid=501,
            )

        commands = [call[0] for call in calls]
        self.assertIn(
            ["launchctl", "bootout", "gui/501/com.pocketcodex.server"],
            commands,
        )
        self.assertIn(["launchctl", "remove", setup.LEGACY_LAUNCH_LABEL], commands)
        self.assertIn(["launchctl", "bootstrap", "gui/501", str(target)], commands)
        self.assertIn(
            ["launchctl", "kickstart", "-k", "gui/501/com.pocketcodex.server"],
            commands,
        )


if __name__ == "__main__":
    unittest.main()
