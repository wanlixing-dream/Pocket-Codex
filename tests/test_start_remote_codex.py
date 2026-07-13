import tempfile
import unittest
from pathlib import Path

import start_remote_codex as starter


class StartRemoteCodexTests(unittest.TestCase):
    def test_extracts_quick_tunnel_url_from_cloudflared_output(self):
        text = "Visit it at https://perspective-copyrighted-deal-maple.trycloudflare.com"

        self.assertEqual(
            starter.extract_quick_tunnel_url(text),
            "https://perspective-copyrighted-deal-maple.trycloudflare.com",
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
