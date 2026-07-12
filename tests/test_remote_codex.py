import base64
import json
import tempfile
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import remote_codex_server as remote


class SessionStoreTests(unittest.TestCase):
    def test_lists_session_with_human_prompts_and_ignores_injected_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session_id = "12345678-1234-1234-1234-123456789abc"
            path = root / f"rollout-2026-01-01-{session_id}.jsonl"
            events = [
                {"type": "session_meta", "payload": {"id": session_id, "cwd": "C:\\work\\demo"}},
                {"type": "response_item", "payload": {"role": "user", "content": [{"type": "input_text", "text": "# AGENTS.md instructions\nignore"}]}},
                {"type": "response_item", "payload": {"role": "user", "content": [{"type": "input_text", "text": "Build the dashboard"}]}},
                {"type": "event_msg", "payload": {"type": "user_message", "message": "Continue with tests"}},
                {"type": "response_item", "payload": {"role": "assistant", "content": [{"type": "output_text", "text": "Tests are complete"}]}},
            ]
            path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")

            sessions = remote.SessionStore(root).list()

            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].id, session_id)
            self.assertEqual(sessions[0].project, "demo")
            self.assertEqual(sessions[0].title, "Build the dashboard")
            self.assertEqual(sessions[0].last_prompt, "Continue with tests")
            self.assertEqual(sessions[0].last_response, "Tests are complete")

    def test_keeps_full_last_response(self):
        with tempfile.TemporaryDirectory() as temp:
            session_id = "12345678-1234-1234-1234-123456789abc"
            path = Path(temp) / f"rollout-{session_id}.jsonl"
            response = "long response " * 100
            events = [
                {"type": "session_meta", "payload": {"id": session_id, "cwd": temp}},
                {"type": "response_item", "payload": {"role": "assistant", "content": [{"text": response}]}},
            ]
            path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")

            self.assertEqual(remote.SessionStore(Path(temp)).list()[0].last_response, response.strip())


class ImageUploadTests(unittest.TestCase):
    def test_saves_supported_image_with_random_name(self):
        with tempfile.TemporaryDirectory() as temp:
            data = b"\x89PNG\r\n\x1a\n" + b"test"
            images = [{"name": "unsafe name.png", "data": base64.b64encode(data).decode()}]

            paths = remote.save_uploaded_images(images, Path(temp))

            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0].suffix, ".png")
            self.assertNotIn("unsafe", paths[0].name)
            self.assertEqual(paths[0].read_bytes(), data)

    def test_rejects_unsupported_file(self):
        with tempfile.TemporaryDirectory() as temp:
            images = [{"data": base64.b64encode(b"not an image").decode()}]
            with self.assertRaisesRegex(ValueError, "JPEG, PNG, and WebP"):
                remote.save_uploaded_images(images, Path(temp))


class FolderBrowserTests(unittest.TestCase):
    def test_lists_children_and_rejects_outside_folder(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            child = root / "project"
            child.mkdir()
            browser = remote.FolderBrowser([root])

            listing = browser.list(str(root))

            self.assertEqual(listing["folders"], [{"name": "project", "path": str(child)}])
            self.assertEqual(browser.validate(str(child)), child)
            with self.assertRaisesRegex(ValueError, "outside"):
                browser.validate(outside)


class RunManagerTests(unittest.TestCase):
    @patch("remote_codex_server.subprocess.Popen")
    def test_resumes_exact_session_via_stdin(self, popen):
        popen.return_value.returncode = 0
        popen.return_value.communicate.return_value = ("finished", None)
        session = remote.SessionInfo(
            id="12345678-1234-1234-1234-123456789abc",
            cwd=str(Path.cwd()),
            project="demo",
            updated_at=0,
            updated_label="now",
            title="title",
            last_prompt="last",
            last_response="response",
        )
        manager = remote.RunManager("codex")
        result = manager.start(session, "continue")
        deadline = time.time() + 2
        while result.status in {"queued", "running"} and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(result.status, "completed")
        popen.assert_called_once()
        args, _ = popen.call_args
        expected = manager._command(session.id)
        self.assertEqual(args[0], expected)
        popen.return_value.communicate.assert_called_once_with(input="continue", timeout=60 * 60 * 6)

    @patch("remote_codex_server.subprocess.Popen")
    def test_attaches_and_cleans_up_images(self, popen):
        popen.return_value.returncode = 0
        popen.return_value.communicate.return_value = ("finished", None)
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.png"
            image.write_bytes(b"image")
            session = remote.SessionInfo(
                id="12345678-1234-1234-1234-123456789abc",
                cwd=str(Path.cwd()),
                project="demo",
                updated_at=0,
                updated_label="now",
                title="title",
                last_prompt="last",
                last_response="response",
            )
            manager = remote.RunManager("codex")
            result = manager.start(session, "inspect", [image])
            deadline = time.time() + 2
            while result.status in {"queued", "running"} and time.time() < deadline:
                time.sleep(0.01)

            command = popen.call_args.args[0]
            self.assertEqual(result.image_count, 1)
            self.assertIn("--image", command)
            self.assertIn(str(image), command)
            self.assertFalse(image.exists())

    def test_cancel_marks_run_and_terminates_process(self):
        manager = remote.RunManager("codex")
        run = remote.RunInfo(id="run-1", session_id="session-1", prompt="wrong message", status="running")
        process = Mock()
        manager.runs[run.id] = run
        manager.active_sessions.add(run.session_id)
        manager.processes[run.id] = process

        with patch.object(manager, "_terminate_process") as terminate:
            result = manager.cancel(run.id)

        self.assertEqual(result.status, "cancelling")
        self.assertIn(run.id, manager.cancel_requested)
        terminate.assert_called_once_with(process)

    @patch("remote_codex_server.subprocess.Popen")
    def test_creates_new_session_and_captures_real_id(self, popen):
        session_id = "87654321-4321-4321-4321-cba987654321"
        popen.return_value.returncode = 0
        popen.return_value.communicate.return_value = (f"session id: {session_id}\ncreated", None)
        with tempfile.TemporaryDirectory() as temp:
            manager = remote.RunManager("codex")
            result = manager.start_new(Path(temp), "start here")
            deadline = time.time() + 2
            while result.status in {"queued", "running"} and time.time() < deadline:
                time.sleep(0.01)

            command = popen.call_args.args[0]
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.session_id, session_id)
            self.assertTrue(result.is_new)
            self.assertNotIn("resume", command)
            self.assertEqual(popen.call_args.kwargs["cwd"], temp)


class HttpAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        store = remote.SessionStore(Path(self.temp.name))
        app = remote.RemoteCodexApp("a" * 32, store, remote.RunManager(), remote.FolderBrowser([Path(self.temp.name)]))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), remote.make_handler(app))
        self.thread = __import__("threading").Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def test_static_page_is_public_but_api_requires_header_token(self):
        self.assertEqual(urlopen(self.base + "/").status, 200)
        with self.assertRaises(HTTPError) as denied:
            urlopen(self.base + "/api/sessions")
        self.assertEqual(denied.exception.code, 401)

        request = Request(self.base + "/api/sessions", headers={"X-Remote-Codex-Token": "a" * 32})
        self.assertEqual(json.load(urlopen(request)), {"sessions": []})

    def test_folder_listing_requires_token_and_stays_in_root(self):
        request = Request(
            self.base + "/api/folders",
            headers={"X-Remote-Codex-Token": "a" * 32},
        )
        data = json.load(urlopen(request))
        self.assertEqual(data["folders"][0]["path"], self.temp.name)


if __name__ == "__main__":
    unittest.main()
