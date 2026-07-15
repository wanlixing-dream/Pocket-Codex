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


if __name__ == "__main__":
    unittest.main()
