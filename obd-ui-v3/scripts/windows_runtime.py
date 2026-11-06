"""Native Windows runtime compatibility helpers."""

import asyncio
import sys


def configure_windows_event_loop() -> None:
    """Use the selector loop required by psycopg's async connections."""
    if sys.platform != "win32":
        return

    policy_factory = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_factory is not None:
        asyncio.set_event_loop_policy(policy_factory())
