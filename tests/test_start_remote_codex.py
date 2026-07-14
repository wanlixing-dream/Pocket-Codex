import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import start_remote_codex as starter


class StartRemoteCodexTests(unittest.TestCase):
    def test_extracts_quick_tunnel_url_from_cloudflared_output(self):
        text = "Visit it at https://example-quick-tunnel-name.trycloudflare.com"

        self.assertEqual(
            starter.extract_quick_tunnel_url(text),
            "https://example-quick-tunnel-name.trycloudflare.com",
        )

    def test_builds_mobile_url_with_token_fragment(self):
        self.assertEqual(
            starter.mobile_url("https://example.trycloudflare.com/", "secret-token"),
            "https://example.trycloudflare.com/#token=secret-token",
        )

    def test_parses_remote_env_without_comments(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "remote.env"
            path.write_text(
                "\n".join(
                    [
                        "# local secret",
                        "REMOTE_CODEX_TOKEN='abc123'",
                        "REMOTE_CODEX_ROOTS=/Users/me/Desktop:/Users/me/Projects",
                    ]
                ),
                encoding="utf-8",
            )

            values = starter.parse_env_file(path)

        self.assertEqual(values["REMOTE_CODEX_TOKEN"], "abc123")
        self.assertEqual(values["REMOTE_CODEX_ROOTS"], "/Users/me/Desktop:/Users/me/Projects")

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not available on Windows")
    def test_parse_env_file_restricts_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "watch.env"
            path.write_text("NTFY_NOTIFY_TOPIC=private-topic\n", encoding="utf-8")
            path.chmod(0o644)

            starter.parse_env_file(path)

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_public_url_ready_rejects_network_error(self):
        ready = getattr(starter, "public_url_ready", None)
        self.assertIsNotNone(ready)

        with patch.object(starter, "urlopen", side_effect=starter.URLError("not ready")):
            self.assertFalse(ready("https://pending.trycloudflare.com"))

    def test_wait_for_public_url_retries_until_success(self):
        wait_for_public_url = getattr(starter, "wait_for_public_url", None)
        self.assertIsNotNone(wait_for_public_url)
        process = Mock()
        process.poll.return_value = None

        with patch.object(starter, "public_url_ready", side_effect=[False, True]) as ready, patch.object(
            starter.time, "sleep"
        ):
            wait_for_public_url("https://ready.trycloudflare.com", process, timeout=5)

        self.assertEqual(ready.call_count, 2)

    def test_builds_ntfy_json_request_with_click_and_view_action(self):
        build_request = getattr(starter, "build_ntfy_request", None)
        self.assertIsNotNone(build_request)
        full_url = "https://example.trycloudflare.com/#token=fake"

        request = build_request(
            {"NTFY_NOTIFY_TOPIC": "unit-test-topic-73f54d", "NTFY_BASE": "https://ntfy.sh"},
            full_url,
        )
        payload = json.loads(request.data)

        self.assertEqual(request.full_url, "https://ntfy.sh/")
        self.assertEqual(payload["topic"], "unit-test-topic-73f54d")
        self.assertEqual(payload["click"], full_url)
        self.assertEqual(payload["actions"][0]["action"], "view")
        self.assertEqual(payload["actions"][0]["url"], full_url)

    def test_builds_ntfy_request_with_bearer_token(self):
        build_request = getattr(starter, "build_ntfy_request", None)
        self.assertIsNotNone(build_request)

        request = build_request(
            {"NTFY_NOTIFY_TOPIC": "unit-test-topic-73f54d", "NTFY_TOKEN": "fake-auth-token"},
            "https://example.trycloudflare.com/#token=fake",
        )

        self.assertEqual(request.get_header("Authorization"), "Bearer fake-auth-token")

    def test_publish_mobile_url_records_success_and_deduplicates(self):
        publish = getattr(starter, "publish_mobile_url", None)
        self.assertIsNotNone(publish)
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            settings = {"NTFY_NOTIFY_TOPIC": "unit-test-topic-73f54d"}
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.status = 200
            full_url = "https://example.trycloudflare.com/#token=fake"

            self.assertTrue(publish(settings, runtime, full_url, opener=opener))
            self.assertFalse(publish(settings, runtime, full_url, opener=opener))

            self.assertEqual(opener.open.call_count, 1)
            self.assertEqual((runtime / "last-notified-url.txt").read_text(encoding="utf-8").strip(), full_url)

    def test_publish_failure_does_not_record_url(self):
        publish = getattr(starter, "publish_mobile_url", None)
        self.assertIsNotNone(publish)
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            opener = Mock()
            opener.open.side_effect = starter.URLError("offline")

            with self.assertRaises(starter.URLError):
                publish(
                    {"NTFY_NOTIFY_TOPIC": "unit-test-topic-73f54d"},
                    runtime,
                    "https://example.trycloudflare.com/#token=fake",
                    opener=opener,
                )

            self.assertFalse((runtime / "last-notified-url.txt").exists())

    def test_publish_without_topic_is_skipped(self):
        publish = getattr(starter, "publish_mobile_url", None)
        self.assertIsNotNone(publish)
        opener = Mock()

        with tempfile.TemporaryDirectory() as temp:
            self.assertFalse(
                publish({}, Path(temp), "https://example.trycloudflare.com/#token=fake", opener=opener)
            )

        opener.open.assert_not_called()

    def test_notify_failure_is_logged_without_sensitive_details(self):
        notify = getattr(starter, "notify_mobile_url", None)
        self.assertIsNotNone(notify)
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            watch_env = runtime / "watch.env"
            watch_env.write_text("NTFY_NOTIFY_TOPIC=unit-test-topic-73f54d\n", encoding="utf-8")

            with patch.object(
                starter, "publish_mobile_url", side_effect=starter.URLError("secret detail")
            ), patch.object(starter.sys, "stderr", new_callable=io.StringIO) as stderr:
                self.assertFalse(
                    notify(watch_env, runtime, "https://example.trycloudflare.com/#token=fake")
                )

            error_log = (runtime / "notify-error.log").read_text(encoding="utf-8")
            self.assertIn("Warning: ntfy link notification failed", stderr.getvalue())
            self.assertIn("URLError", error_log)
            self.assertNotIn("secret detail", error_log)
            self.assertNotIn("#token=", error_log)

    def test_configured_ntfy_rejects_documented_placeholders(self):
        for topic in (
            "replace-with-phone-subscription-topic",
            "REPLACE_WITH_ANOTHER_LONG_RANDOM_TOPIC",
            "your-long-random-topic",
            "example-topic",
        ):
            with self.subTest(topic=topic):
                self.assertFalse(starter.configured_ntfy({"NTFY_NOTIFY_TOPIC": topic}))

    def test_invalid_watch_env_is_nonfatal_and_sanitized(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            watch_env = runtime / "watch.env"
            watch_env.write_bytes(b"\xff\xfe")

            with patch.object(starter.sys, "stderr", new_callable=io.StringIO) as stderr:
                try:
                    result = starter.notify_mobile_url(
                        watch_env,
                        runtime,
                        "https://example.trycloudflare.com/#token=fake",
                    )
                except Exception as exc:
                    self.fail(f"invalid optional watch.env escaped notify_mobile_url: {type(exc).__name__}")

            self.assertFalse(result)
            self.assertIn("Warning: ntfy link notification failed", stderr.getvalue())
            error_log = (runtime / "notify-error.log").read_text(encoding="utf-8")
            self.assertIn("UnicodeDecodeError", error_log)
            self.assertNotIn("#token=", error_log)

    def test_start_processes_verifies_and_notifies_mobile_url(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / "runtime"
            remote_env = Path(temp) / "remote.env"
            remote_env.write_text("REMOTE_CODEX_TOKEN=fake-token\n", encoding="utf-8")
            watch_env = Path(temp) / "watch.env"
            args = Mock(
                runtime_dir=str(runtime),
                env=str(remote_env),
                watch_env=str(watch_env),
                cloudflared="cloudflared",
                startup_timeout=5,
                tunnel_timeout=7,
                notify_timeout=3,
            )
            tunnel = MagicMock()
            tunnel.stdout = Mock()
            public_url = "https://example.trycloudflare.com"
            full_url = f"{public_url}/#token=fake-token"

            with patch.object(starter, "server_ready", return_value=True), patch.object(
                starter.shutil, "which", return_value="/usr/bin/cloudflared"
            ), patch.object(starter.subprocess, "Popen", return_value=tunnel), patch.object(
                starter.threading, "Thread"
            ), patch.object(starter, "wait_for_tunnel_url", return_value=public_url), patch.object(
                starter, "wait_for_public_url"
            ) as wait_for_public, patch.object(starter, "notify_mobile_url") as notify:
                with patch.object(starter.sys, "stdout", new_callable=io.StringIO):
                    processes, result_url = starter.start_processes(args)

            self.assertEqual(processes, [tunnel])
            self.assertEqual(result_url, full_url)
            wait_for_public.assert_called_once_with(public_url, tunnel, args.tunnel_timeout)
            notify.assert_called_once_with(watch_env, runtime, full_url, timeout=args.notify_timeout)
            self.assertEqual((runtime / "remote-url.txt").read_text(encoding="utf-8").strip(), full_url)
