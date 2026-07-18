import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "start_remote_codex.ps1").read_text(encoding="utf-8")


class WindowsLauncherContractTests(unittest.TestCase):
    def test_access_modes_are_explicit_and_persisted(self):
        self.assertIn("[ValidateSet('Cloudflare', 'Tailscale')]", SCRIPT)
        self.assertIn("access-mode.txt", SCRIPT)
        self.assertRegex(SCRIPT, r"-AccessMode \{2\} -WatchdogRun")

    def test_tailscale_serve_targets_loopback(self):
        self.assertIn("@('serve', '--bg', '--yes', $LocalUrl)", SCRIPT)
        self.assertIn("$LocalUrl = 'http://127.0.0.1:8765'", SCRIPT)

    def test_scheduled_tailscale_run_only_restores_server(self):
        self.assertIn(
            "if ($resolvedMode -eq 'Tailscale' -and $WatchdogRun) {\n"
            "        Start-PocketCodex",
            SCRIPT,
        )

    def test_fixed_endpoint_requires_authenticated_api_check(self):
        self.assertIn("$BaseUrl/api/sessions", SCRIPT)
        self.assertIn("X-Remote-Codex-Token", SCRIPT)
        self.assertIn("Test-TailscaleEndpoint $baseUrl $token", SCRIPT)

    def test_cloudflare_stops_only_after_tailscale_verification(self):
        block = re.search(
            r"function Start-TailscaleAccess \{(?P<body>.*?)\n\}",
            SCRIPT,
            re.DOTALL,
        ).group("body")
        self.assertLess(block.index("Test-TailscaleEndpoint"), block.index("Stop-CloudflareTunnel"))

    def test_tokenized_url_files_are_acl_protected(self):
        self.assertIn("Protect-PrivateFile $UrlFile", SCRIPT)
        self.assertIn("Protect-PrivateFile $LastNotifiedFile", SCRIPT)
        self.assertIn("Protect-PrivateFile $envPath", SCRIPT)
        self.assertIn("Protect-PrivateFile $watchEnvPath", SCRIPT)
        self.assertIn("Protect-PrivateFile $privateLog", SCRIPT)


if __name__ == "__main__":
    unittest.main()
