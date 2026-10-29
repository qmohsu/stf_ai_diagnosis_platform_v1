"""Regression tests for the native Windows API runtime."""

import asyncio
import sys
import unittest
from unittest.mock import patch

from stf_v3.windows_runtime import configure_windows_event_loop


class WindowsRuntimeTests(unittest.TestCase):
    def test_windows_uses_selector_policy_for_async_psycopg(self) -> None:
        policy = object()

        with (
            patch.object(sys, "platform", "win32"),
            patch.object(
                asyncio,
                "WindowsSelectorEventLoopPolicy",
                create=True,
                return_value=policy,
            ) as policy_factory,
            patch.object(asyncio, "set_event_loop_policy") as set_policy,
        ):
            configure_windows_event_loop()

        policy_factory.assert_called_once_with()
        set_policy.assert_called_once_with(policy)

    def test_non_windows_keeps_the_default_policy(self) -> None:
        with (
            patch.object(sys, "platform", "linux"),
            patch.object(asyncio, "set_event_loop_policy") as set_policy,
        ):
            configure_windows_event_loop()

        set_policy.assert_not_called()

    def test_server_configures_policy_before_starting_uvicorn(self) -> None:
        from stf_v3 import windows_server

        events: list[str] = []
        with (
            patch.object(
                windows_server,
                "configure_windows_event_loop",
                side_effect=lambda: events.append("policy"),
            ),
            patch.object(
                windows_server.uvicorn,
                "run",
                side_effect=lambda *args, **kwargs: events.append("uvicorn"),
            ),
        ):
            windows_server.main()

        self.assertEqual(events, ["policy", "uvicorn"])


if __name__ == "__main__":
    unittest.main()
