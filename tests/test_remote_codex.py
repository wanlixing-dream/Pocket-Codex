import base64
import json
import os
import queue
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
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


class DesktopSessionStoreTests(unittest.TestCase):
    def test_lists_threads_from_desktop_app_server(self):
        thread_id = "12345678-1234-1234-1234-123456789abc"

        class Client:
            def request(self, method, params):
                self.method = method
                self.params = params
                return {
                    "data": [
                        {
                            "id": thread_id,
                            "cwd": "C:\\work\\desktop-project",
                            "name": "Desktop task",
                            "preview": "Continue the desktop work",
                            "updatedAt": 1_700_000_000,
                        }
                    ]
                }

        client = Client()
        store = remote.DesktopSessionStore(lambda: client)

        sessions = store.list()

        self.assertEqual(client.method, "thread/list")
        self.assertEqual(client.params["sortKey"], "updated_at")
        self.assertEqual(sessions[0].id, thread_id)
        self.assertEqual(sessions[0].project, "desktop-project")
        self.assertEqual(sessions[0].title, "Desktop task")

    def test_exposes_desktop_thread_status_for_external_runs(self):
        thread_id = "12345678-1234-1234-1234-123456789abc"
        desktop_status = {"type": "running", "turnId": "turn-from-desktop"}

        class Client:
            def request(self, method, params):
                return {
                    "data": [
                        {
                            "id": thread_id,
                            "cwd": "C:\\work\\desktop-project",
                            "name": "Desktop task",
                            "preview": "Run from desktop",
                            "updatedAt": 1_700_000_000,
                            "status": desktop_status,
                        }
                    ]
                }

        session = remote.DesktopSessionStore(lambda: Client()).list()[0]

        self.assertEqual(session.desktop_status, desktop_status)
        self.assertEqual(session.desktop_activity, "active")

    def test_not_loaded_desktop_status_is_idle(self):
        thread_id = "12345678-1234-1234-1234-123456789abc"

        class Client:
            def request(self, method, params):
                return {
                    "data": [
                        {
                            "id": thread_id,
                            "cwd": "C:\\work\\desktop-project",
                            "name": "Desktop task",
                            "updatedAt": 1_700_000_000,
                            "status": {"type": "notLoaded"},
                        }
                    ]
                }

        session = remote.DesktopSessionStore(lambda: Client()).list()[0]

        self.assertEqual(session.desktop_activity, "idle")

    def test_unfinished_session_log_marks_desktop_thread_active(self):
        thread_id = "12345678-1234-1234-1234-123456789abc"
        with tempfile.TemporaryDirectory() as temp:
            log_path = Path(temp) / f"rollout-{thread_id}.jsonl"
            events = [
                {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}},
                {"type": "event_msg", "payload": {"type": "agent_message", "message": "桌面端正在执行"}},
            ]
            log_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in events), encoding="utf-8")

            class Client:
                def request(self, method, params):
                    return {
                        "data": [
                            {
                                "id": thread_id,
                                "cwd": "C:\\work\\desktop-project",
                                "name": "Desktop task",
                                "updatedAt": 1_700_000_000,
                                "status": {"type": "notLoaded"},
                                "path": str(log_path),
                            }
                        ]
                    }

            session = remote.DesktopSessionStore(lambda: Client()).list()[0]

        self.assertEqual(session.desktop_activity, "active")
        self.assertEqual(session.desktop_activity_source, "session_log")
        self.assertEqual(session.last_response, "桌面端正在执行")


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

            self.assertEqual(listing["folders"], [{"name": "project", "path": str(child.resolve())}])
            self.assertEqual(browser.validate(str(child)), child.resolve())
            with self.assertRaisesRegex(ValueError, "outside"):
                browser.validate(outside)


class FakeTransport:
    def __init__(self):
        self.incoming = queue.Queue()
        self.sent = []
        self.closed = False

    def send(self, message):
        self.sent.append(message)
        if "id" in message and "method" in message:
            self.incoming.put({"id": message["id"], "result": {"ok": True}})

    def receive(self):
        return self.incoming.get(timeout=2)

    def close(self):
        self.closed = True
        self.incoming.put(None)


class ErrorTransport(FakeTransport):
    def send(self, message):
        self.sent.append(message)
        if "id" in message and "method" in message:
            error = {"code": -32000, "message": "desktop rejected request"}
            self.incoming.put({"id": message["id"], "error": error})


class AppServerClientTests(unittest.TestCase):
    def test_initializes_and_routes_requests_and_notifications(self):
        transport = FakeTransport()
        client = remote.AppServerClient(transport=transport)
        received = []
        client.add_listener(lambda method, params: received.append((method, params)))

        result = client.request("thread/resume", {"threadId": "thread-1"})
        transport.incoming.put({"method": "turn/completed", "params": {"turnId": "turn-1"}})
        deadline = time.time() + 1
        while not received and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(transport.sent[0]["method"], "initialize")
        self.assertTrue(transport.sent[0]["params"]["capabilities"]["experimentalApi"])
        self.assertEqual(transport.sent[1], {"method": "initialized", "params": {}})
        self.assertEqual(transport.sent[2]["method"], "thread/resume")
        self.assertEqual(received, [("turn/completed", {"turnId": "turn-1"})])
        client.close()

    def test_replies_to_unsupported_server_request_with_json_rpc_error(self):
        transport = FakeTransport()
        client = remote.AppServerClient(transport=transport)
        transport.incoming.put({"id": 90, "method": "approval/request", "params": {}})
        deadline = time.time() + 1
        while not any(item.get("id") == 90 for item in transport.sent) and time.time() < deadline:
            time.sleep(0.01)

        response = next(item for item in transport.sent if item.get("id") == 90)
        self.assertEqual(response["error"]["code"], -32601)
        self.assertIn("approval/request", response["error"]["message"])
        client.close()

    def test_exposes_app_server_request_errors(self):
        transport = ErrorTransport()
        with self.assertRaisesRegex(RuntimeError, "desktop rejected request"):
            remote.AppServerClient(transport=transport)
        self.assertTrue(transport.closed)

    def test_close_marks_client_closed_before_transport_eof(self):
        first = remote.AppServerClient(transport=FakeTransport())
        replacement = remote.AppServerClient(transport=FakeTransport())
        manager = remote.RunManager(client=first, client_factory=lambda: replacement)
        manager._get_client()

        first.close()
        selected = manager._get_client()

        self.assertTrue(first.closed)
        self.assertIs(selected, replacement)
        manager.close()


class DesktopExecutableTests(unittest.TestCase):
    def test_explicit_desktop_executable_takes_priority(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "codex.exe"
            executable.write_bytes(b"")
            with patch.dict(os.environ, {"REMOTE_CODEX_DESKTOP_EXE": str(executable)}):
                self.assertEqual(remote.locate_codex_desktop_executable(), executable.resolve())

    def test_uses_latest_bundled_desktop_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            old = Path(temp) / "OpenAI" / "Codex" / "bin" / "1" / "codex.exe"
            new = Path(temp) / "OpenAI" / "Codex" / "bin" / "2" / "codex.exe"
            old.parent.mkdir(parents=True)
            new.parent.mkdir(parents=True)
            old.write_bytes(b"")
            new.write_bytes(b"")
            os.utime(old, (1, 1))
            os.utime(new, (2, 2))
            with patch.dict(os.environ, {"LOCALAPPDATA": temp}, clear=False):
                os.environ.pop("REMOTE_CODEX_DESKTOP_EXE", None)
                self.assertEqual(remote.locate_codex_desktop_executable(), new.resolve())

    def test_appx_fallback_selects_resources_app_server_not_gui(self):
        with tempfile.TemporaryDirectory() as temp:
            install_root = Path(temp) / "OpenAI.Codex"
            gui = install_root / "app" / "Codex.exe"
            app_server = install_root / "app" / "resources" / "codex.exe"
            gui.parent.mkdir(parents=True)
            app_server.parent.mkdir(parents=True)
            gui.write_bytes(b"")
            app_server.write_bytes(b"")

            class Result:
                returncode = 0
                stdout = str(install_root)

            with patch.dict(os.environ, {"LOCALAPPDATA": temp}, clear=False), patch.object(
                remote.subprocess, "run", return_value=Result()
            ), patch.object(remote.sys, "platform", "win32"), patch.object(
                remote.subprocess, "CREATE_NO_WINDOW", 0, create=True
            ):
                os.environ.pop("REMOTE_CODEX_DESKTOP_EXE", None)
                self.assertEqual(remote.locate_codex_desktop_executable(), app_server.resolve())

    def test_macos_fallback_selects_chatgpt_app_resource_codex(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp) / "ChatGPT.app"
            executable = app / "Contents" / "Resources" / "codex"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"")

            with patch.object(remote, "MACOS_APP_CANDIDATES", [app], create=True), patch.dict(
                os.environ, {"LOCALAPPDATA": ""},
                clear=False,
            ), patch.object(remote.sys, "platform", "darwin"):
                os.environ.pop("REMOTE_CODEX_DESKTOP_EXE", None)
                self.assertEqual(remote.locate_codex_desktop_executable(), executable.resolve())


class FakeAppServerClient:
    def __init__(self, auto_complete=True):
        self.auto_complete = auto_complete
        self.requests = []
        self.listener = None
        self.turn_number = 0
        self.closed = False

    def add_listener(self, listener):
        self.listener = listener

    def close(self):
        self.closed = True

    def request(self, method, params, timeout=30):
        self.requests.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "87654321-4321-4321-4321-cba987654321"}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "turn/start":
            self.turn_number += 1
            turn_id = f"turn-{self.turn_number}"
            if self.auto_complete:
                threading.Timer(0.02, self.complete, args=(turn_id,)).start()
            return {"turn": {"id": turn_id}}
        if method == "turn/interrupt":
            threading.Timer(0.01, self.complete, args=(params["turnId"], "interrupted")).start()
            return {}
        raise AssertionError(method)

    def complete(self, turn_id, status="completed"):
        if status == "completed":
            self.listener(
                "item/agentMessage/delta",
                {"threadId": "session-1", "turnId": turn_id, "delta": "partial"},
            )
            self.listener(
                "item/completed",
                {"turnId": turn_id, "item": {"type": "agentMessage", "text": "finished"}},
            )
        self.listener("turn/completed", {"turn": {"id": turn_id, "status": status}})


class ImmediateCompleteClient(FakeAppServerClient):
    def request(self, method, params, timeout=30):
        if method != "turn/start":
            return super().request(method, params, timeout)
        self.requests.append((method, params))
        turn_id = "turn-immediate"
        self.listener(
            "item/completed",
            {
                "threadId": params["threadId"],
                "turnId": turn_id,
                "item": {"type": "agentMessage", "text": "instant"},
            },
        )
        self.listener(
            "turn/completed",
            {"threadId": params["threadId"], "turn": {"id": turn_id, "status": "completed"}},
        )
        return {"turn": {"id": turn_id}}


class RunManagerTests(unittest.TestCase):
    @staticmethod
    def wait_for(result, statuses=("queued", "running", "cancelling")):
        deadline = time.time() + 2
        while result.status in statuses and time.time() < deadline:
            time.sleep(0.01)

    def test_resumes_exact_desktop_thread_and_starts_turn(self):
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
        client = FakeAppServerClient()
        manager = remote.RunManager(client=client)
        result = manager.start(session, "continue")
        self.wait_for(result)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output, "finished")
        self.assertEqual(client.requests[0][0], "thread/resume")
        self.assertEqual(client.requests[0][1]["threadId"], session.id)
        self.assertEqual(client.requests[0][1]["cwd"], session.cwd)
        self.assertEqual(client.requests[1][0], "turn/start")
        self.assertEqual(
            client.requests[1][1]["input"],
            [{"type": "text", "text": "continue", "text_elements": []}],
        )
        self.assertTrue(client.requests[1][1]["clientUserMessageId"])

    def test_handles_turn_completion_before_start_response(self):
        session = remote.SessionInfo(
            "12345678-1234-1234-1234-123456789abc",
            str(Path.cwd()),
            "demo",
            0,
            "now",
            "title",
            "last",
            "",
        )
        manager = remote.RunManager(client=ImmediateCompleteClient())

        result = manager.start(session, "quick task")
        self.wait_for(result)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output, "instant")

    def test_attaches_local_image_and_cleans_it_up(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.png"
            image.write_bytes(b"image")
            expected_image_path = str(image.resolve())
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
            client = FakeAppServerClient()
            manager = remote.RunManager(client=client)
            result = manager.start(session, "inspect", [image])
            self.wait_for(result)

            self.assertEqual(result.image_count, 1)
            turn_input = client.requests[1][1]["input"]
            self.assertEqual(turn_input[1], {"type": "localImage", "path": expected_image_path})
            deadline = time.time() + 1
            while image.exists() and time.time() < deadline:
                time.sleep(0.01)
            self.assertFalse(image.exists())

    def test_cancel_interrupts_desktop_turn(self):
        client = FakeAppServerClient(auto_complete=False)
        manager = remote.RunManager(client=client)
        session = remote.SessionInfo("session-1", str(Path.cwd()), "demo", 0, "now", "title", "last", "")
        run = manager.start(session, "stop later")
        deadline = time.time() + 1
        while run.id not in manager.turns_by_run and time.time() < deadline:
            time.sleep(0.01)
        result = manager.cancel(run.id)
        self.wait_for(result)

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(client.requests[-1][0], "turn/interrupt")

    def test_app_server_disconnect_fails_active_run_immediately(self):
        client = FakeAppServerClient(auto_complete=False)
        manager = remote.RunManager(client=client)
        session = remote.SessionInfo("session-1", str(Path.cwd()), "demo", 0, "now", "title", "last", "")
        run = manager.start(session, "keep running")
        deadline = time.time() + 1
        while run.id not in manager.turns_by_run and time.time() < deadline:
            time.sleep(0.01)

        manager._on_notification(manager._client_generation, "app-server/closed", {"error": "desktop connection closed"})
        self.wait_for(run)

        self.assertEqual(run.status, "failed")
        self.assertIn("desktop connection closed", run.output)

    def test_disconnect_does_not_overwrite_completed_run(self):
        manager = remote.RunManager(client=FakeAppServerClient(auto_complete=False))
        run = remote.RunInfo("run-complete", "thread-1", "done", status="completed", output="success")
        manager.runs[run.id] = run
        manager.done_events[run.id] = threading.Event()

        manager._on_notification(manager._client_generation, "app-server/closed", {"error": "late disconnect"})

        self.assertEqual(run.status, "completed")
        self.assertEqual(run.output, "success")

    def test_new_thread_migrates_active_lock_to_real_id(self):
        client = FakeAppServerClient(auto_complete=False)
        manager = remote.RunManager(client=client)
        with tempfile.TemporaryDirectory() as temp:
            run = manager.start_new(Path(temp), "start here")
            deadline = time.time() + 1
            while run.id not in manager.turns_by_run and time.time() < deadline:
                time.sleep(0.01)

            self.assertIn(run.session_id, manager.active_sessions)
            self.assertFalse(any(item.startswith("new-") for item in manager.active_sessions))
            same_thread = remote.SessionInfo(run.session_id, temp, "demo", 0, "now", "title", "last", "")
            with self.assertRaisesRegex(RuntimeError, "already running"):
                manager.start(same_thread, "second turn")
            manager.cancel(run.id)
            self.wait_for(run)

    def test_new_thread_collision_preserves_other_run_ownership(self):
        real_thread_id = "87654321-4321-4321-4321-cba987654321"
        manager = remote.RunManager(client=FakeAppServerClient(auto_complete=False))
        manager.active_sessions[real_thread_id] = "other-run"

        with tempfile.TemporaryDirectory() as temp:
            run = manager.start_new(Path(temp), "start here")
            self.wait_for(run)

        self.assertEqual(run.status, "failed")
        self.assertIn("already running", run.output)
        self.assertTrue(run.session_id.startswith("new-"))
        self.assertEqual(manager.active_sessions[real_thread_id], "other-run")
        self.assertFalse(any(key.startswith("new-") for key in manager.active_sessions))

    def test_stale_client_close_notification_does_not_fail_replacement_run(self):
        first = FakeAppServerClient(auto_complete=False)
        replacement = FakeAppServerClient(auto_complete=False)
        replacement.turn_number = 10
        manager = remote.RunManager(client=first, client_factory=lambda: replacement)
        old_session = remote.SessionInfo("session-1", str(Path.cwd()), "demo", 0, "now", "title", "last", "")
        old_run = manager.start(old_session, "old connection task")
        deadline = time.time() + 1
        while old_run.id not in manager.turns_by_run and time.time() < deadline:
            time.sleep(0.01)
        stale_listener = first.listener
        first.closed = True
        manager._get_client()

        session = remote.SessionInfo("session-2", str(Path.cwd()), "demo", 0, "now", "title", "last", "")
        run = manager.start(session, "replacement connection task")
        deadline = time.time() + 1
        while run.id not in manager.turns_by_run and time.time() < deadline:
            time.sleep(0.01)

        turns_before = dict(manager.turns_by_run)
        runs_before = dict(manager.runs_by_turn)
        stale_listener(
            "item/agentMessage/delta",
            {"threadId": session.id, "turnId": "stale-old-turn", "delta": "stale output"},
        )
        stale_listener("app-server/closed", {"error": "stale disconnect"})
        self.wait_for(old_run)

        self.assertEqual(old_run.status, "failed")
        self.assertIn("stale disconnect", old_run.output)
        self.assertEqual(run.status, "running")
        self.assertNotIn("stale output", run.output)
        self.assertEqual(manager.turns_by_run, turns_before)
        self.assertEqual(manager.runs_by_turn, runs_before)
        manager.cancel(run.id)
        self.wait_for(run)

    def test_timeout_interrupts_turn_before_releasing_lock(self):
        client = FakeAppServerClient(auto_complete=False)
        manager = remote.RunManager(client=client, turn_timeout=0.02, interrupt_grace=0.2)
        session = remote.SessionInfo("session-1", str(Path.cwd()), "demo", 0, "now", "title", "last", "")

        run = manager.start(session, "slow task")
        self.wait_for(run)

        self.assertEqual(run.status, "failed")
        self.assertTrue(any(method == "turn/interrupt" for method, _ in client.requests))
        self.assertIn("Timed out", run.output)

    def test_close_closes_desktop_client(self):
        client = FakeAppServerClient()
        manager = remote.RunManager(client=client)

        manager.close()

        self.assertTrue(client.closed)

    def test_creates_new_desktop_thread_and_captures_real_id(self):
        session_id = "87654321-4321-4321-4321-cba987654321"
        with tempfile.TemporaryDirectory() as temp:
            client = FakeAppServerClient()
            manager = remote.RunManager(client=client)
            result = manager.start_new(Path(temp), "start here")
            self.wait_for(result)

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.session_id, session_id)
            self.assertTrue(result.is_new)
            self.assertEqual(client.requests[0][0], "thread/start")
            self.assertEqual(client.requests[0][1]["cwd"], temp)
            self.assertEqual(client.requests[0][1]["threadSource"], "user")


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
        self.assertEqual(data["folders"][0]["path"], str(Path(self.temp.name).resolve()))


if __name__ == "__main__":
    unittest.main()
