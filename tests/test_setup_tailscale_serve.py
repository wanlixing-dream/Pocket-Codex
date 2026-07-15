import argparse
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_install_launch_agent_restores_fallback_when_bootstrap_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "com.pocketcodex.server.plist"
            runtime = Path(temp) / "runtime"
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                if command[:2] == ["launchctl", "bootstrap"]:
                    raise setup.subprocess.CalledProcessError(5, command)
                return None

            with self.assertRaises(setup.subprocess.CalledProcessError):
                setup.install_launch_agent(
                    Path("/usr/bin/python3"),
                    Path("/repo/remote_codex_server.py"),
                    runtime,
                    target,
                    runner=runner,
                    uid=501,
                )

        commands = [call[0] for call in calls]
        self.assertIn(
            [
                "launchctl",
                "submit",
                "-l",
                setup.LEGACY_LAUNCH_LABEL,
                "-o",
                str(runtime / "server.log"),
                "-e",
                str(runtime / "server-error.log"),
                "--",
                "/usr/bin/python3",
                "/repo/remote_codex_server.py",
            ],
            commands,
        )

    def test_install_launch_agent_preserves_bootstrap_error_when_cleanup_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "com.pocketcodex.server.plist"
            runtime = Path(temp) / "runtime"
            calls = []
            bootstrap_error = setup.subprocess.CalledProcessError(5, ["launchctl", "bootstrap"])
            bootstrap_failed = False

            def runner(command, **kwargs):
                nonlocal bootstrap_failed
                calls.append((command, kwargs))
                if command[:2] == ["launchctl", "bootstrap"]:
                    bootstrap_failed = True
                    raise bootstrap_error
                if bootstrap_failed and command[:2] == ["launchctl", "bootout"]:
                    raise OSError("cleanup failed")
                return None

            with self.assertRaises(setup.subprocess.CalledProcessError) as caught:
                setup.install_launch_agent(
                    Path("/usr/bin/python3"),
                    Path("/repo/remote_codex_server.py"),
                    runtime,
                    target,
                    runner=runner,
                    uid=501,
                )

        self.assertIs(caught.exception, bootstrap_error)
        self.assertTrue(
            any(call[0][:2] == ["launchctl", "submit"] for call in calls),
            "fallback service was not attempted after cleanup failed",
        )

    def test_uninstall_launch_agent_boots_out_job_and_removes_plist(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "com.pocketcodex.server.plist"
            target.write_text("placeholder", encoding="utf-8")
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return None

            setup.uninstall_launch_agent(target, runner=runner, uid=501)

            self.assertFalse(target.exists())
        self.assertEqual(
            calls[0][0],
            ["launchctl", "bootout", "gui/501/com.pocketcodex.server"],
        )


class SetupFlowTests(unittest.TestCase):
    def setUp(self):
        self.status = {
            "BackendState": "Running",
            "Self": {"DNSName": "best-mac.example-tailnet.ts.net."},
        }
        self.args = argparse.Namespace(
            tailscale=Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
            python=Path("/usr/bin/python3"),
            env=Path("/repo/remote.env"),
            watch_env=Path("/repo/watch.env"),
            runtime_dir=Path("/runtime"),
            launch_agent=Path("/launch-agent.plist"),
            startup_timeout=10,
            verify_timeout=20,
            no_notify=False,
        )

    def test_stable_url_ready_uses_header_token_without_query_token(self):
        response = MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response

        self.assertTrue(
            setup.stable_url_ready(
                "https://best-mac.example-tailnet.ts.net",
                "private-token-value",
                opener=opener,
            )
        )

        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(len(requests), 2)
        self.assertNotIn("private-token-value", requests[0].full_url)
        self.assertNotIn("private-token-value", requests[1].full_url)
        self.assertEqual(
            requests[1].get_header("X-remote-codex-token"),
            "private-token-value",
        )

    def test_preflight_rejects_unmanaged_server_on_local_port(self):
        with patch.object(setup, "server_ready", return_value=True), patch.object(
            setup, "managed_job_loaded", return_value=False
        ):
            with self.assertRaisesRegex(RuntimeError, "manually started"):
                setup.ensure_safe_server_transition()

    def test_configure_stable_access_runs_full_verified_flow(self):
        with patch.object(setup, "load_tailscale_status", return_value=self.status), patch.object(
            setup, "run_tailscale"
        ) as run, patch.object(setup, "install_launch_agent") as install, patch.object(
            setup, "ensure_safe_server_transition"
        ), patch.object(
            setup, "wait_for_local_server"
        ) as wait_local, patch.object(
            setup, "wait_for_token", return_value="private-token-value"
        ), patch.object(
            setup, "wait_for_stable_url"
        ) as wait_stable, patch.object(
            setup, "notify_stable_url", return_value=True
        ) as notify:
            result = setup.configure_stable_access(self.args)

        run.assert_called_once_with(
            self.args.tailscale,
            "serve",
            "--bg",
            "--yes",
            setup.LOCAL_URL,
        )
        install.assert_called_once_with(
            self.args.python,
            setup.ROOT / "remote_codex_server.py",
            self.args.runtime_dir,
            self.args.launch_agent,
        )
        wait_local.assert_called_once_with(self.args.startup_timeout)
        wait_stable.assert_called_once_with(
            "https://best-mac.example-tailnet.ts.net",
            "private-token-value",
            self.args.verify_timeout,
        )
        notify.assert_called_once_with(
            self.args.watch_env,
            self.args.runtime_dir,
            "https://best-mac.example-tailnet.ts.net",
            "private-token-value",
        )
        self.assertTrue(result.notified)

    def test_configure_stable_access_can_skip_ntfy(self):
        self.args.no_notify = True
        with patch.object(setup, "load_tailscale_status", return_value=self.status), patch.object(
            setup, "run_tailscale"
        ), patch.object(setup, "install_launch_agent"), patch.object(
            setup, "ensure_safe_server_transition"
        ), patch.object(
            setup, "wait_for_local_server"
        ), patch.object(setup, "wait_for_token", return_value="private-token-value"), patch.object(
            setup, "wait_for_stable_url"
        ), patch.object(setup, "notify_stable_url") as notify:
            result = setup.configure_stable_access(self.args)

        notify.assert_not_called()
        self.assertFalse(result.notified)


if __name__ == "__main__":
    unittest.main()
