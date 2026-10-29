"""Regression coverage for the Windows one-time setup script."""

import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "setup_local_windows.ps1"
REPO_ROOT = Path(__file__).parents[2]
LAUNCHER = REPO_ROOT / "frontend" / "scripts" / "start-obd-expert.ps1"


def _read_launcher_source(launcher: Path, repo_root: Path) -> str:
    if not launcher.is_file() and not (repo_root / ".git").exists():
        raise unittest.SkipTest("frontend/ not present (V3 image / unbound copy)")
    return launcher.read_text(encoding="utf-8")


class SetupLocalWindowsTests(unittest.TestCase):
    def test_env_acl_does_not_depend_on_powershell_security_module(self) -> None:
        """Minimal Windows PowerShell installs may be unable to import Get-Acl."""
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("Get-Acl", source)
        self.assertNotIn("Set-Acl", source)
        self.assertNotIn("icacls.exe", source)
        self.assertIn(
            "New-Object System.Security.AccessControl.FileSecurity", source
        )
        self.assertIn("[System.IO.File]::SetAccessControl", source)
        self.assertIn(".User.Value", source)
        self.assertIn('[Environment]::GetFolderPath("LocalApplicationData")', source)
        self.assertNotIn('Join-Path $v3Root ".env"', source)
        self.assertIn('$env:PYTHONUTF8 = "1"', source)

    def test_launcher_loads_user_scoped_v3_environment(self) -> None:
        source = _read_launcher_source(LAUNCHER, REPO_ROOT)

        self.assertIn('[Environment]::GetFolderPath("LocalApplicationData")', source)
        self.assertIn("SetEnvironmentVariable", source)
        self.assertNotIn('Join-Path $v3Root ".env"', source)

    def test_launcher_check_skips_when_frontend_is_not_packaged(self) -> None:
        """PR #269 review item 2: the V3 image does not contain frontend/."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            launcher = repo_root / "frontend" / "scripts" / "start-obd-expert.ps1"

            with self.assertRaisesRegex(unittest.SkipTest, "frontend/ not present"):
                _read_launcher_source(launcher, repo_root)

    def test_launcher_check_still_fails_in_full_checkout(self) -> None:
        """A missing launcher in a complete Git checkout must not be hidden."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / ".git").touch()
            launcher = repo_root / "frontend" / "scripts" / "start-obd-expert.ps1"

            with self.assertRaises(FileNotFoundError):
                _read_launcher_source(launcher, repo_root)


if __name__ == "__main__":
    unittest.main()
