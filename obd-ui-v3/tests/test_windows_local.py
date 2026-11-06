"""Offline contracts for the frontend-owned Windows development launcher."""

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


FRONTEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = FRONTEND_ROOT / "scripts"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowsLocalTests(unittest.TestCase):
    def test_setup_targets_sibling_backend_and_keeps_owner_url_out_of_api_env(self):
        source = (SCRIPTS / "setup_local_windows.ps1").read_text(encoding="utf-8")

        self.assertIn('$v3Root = Join-Path $repoRoot "stf_v3"', source)
        self.assertIn('"STF_V3_DATABASE_URL=${appUrl}"', source)
        self.assertNotIn('"STF_V3_OWNER_DATABASE_URL=${ownerUrl}"', source)
        self.assertIn('$env:STF_V3_DATABASE_URL = $ownerUrl', source)
        self.assertIn("New-Object System.Security.AccessControl.FileSecurity", source)
        self.assertIn("[System.IO.File]::SetAccessControl", source)
        self.assertIn('[Environment]::GetFolderPath("LocalApplicationData")', source)
        self.assertIn('$env:PYTHONUTF8 = "1"', source)

    def test_launcher_uses_frontend_owned_server_and_app_credentials_only(self):
        source = (SCRIPTS / "start-obd-expert.ps1").read_text(encoding="utf-8")

        self.assertIn('Join-Path $PSScriptRoot "windows_server.py"', source)
        self.assertNotIn("stf_v3.windows_server", source)
        self.assertIn('"STF_V3_DATABASE_URL"', source)
        self.assertNotIn('"STF_V3_OWNER_DATABASE_URL"', source)
        self.assertIn("[Environment]::SetEnvironmentVariable", source)

    def test_windows_runtime_sets_selector_policy_before_server_start(self):
        runtime = _load_script("windows_runtime")
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(asyncio, "WindowsSelectorEventLoopPolicy", create=True) as factory,
            mock.patch.object(asyncio, "set_event_loop_policy") as set_policy,
        ):
            runtime.configure_windows_event_loop()

        set_policy.assert_called_once_with(factory.return_value)

    def test_windows_server_configures_loop_before_uvicorn(self):
        calls = []
        runtime = SimpleNamespace(
            configure_windows_event_loop=lambda: calls.append("configure")
        )
        uvicorn = SimpleNamespace(
            run=lambda app, **kwargs: calls.append(("run", app, kwargs))
        )
        with mock.patch.dict(
            sys.modules, {"windows_runtime": runtime, "uvicorn": uvicorn}
        ):
            server = _load_script("windows_server")
            server.main()

        self.assertEqual(calls[0], "configure")
        self.assertEqual(
            calls[1],
            ("run", "stf_v3.main:app", {"host": "127.0.0.1", "port": 8002}),
        )


if __name__ == "__main__":
    unittest.main()
