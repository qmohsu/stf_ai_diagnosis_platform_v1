"""Windows entry point for the local STF V3 API."""

import uvicorn

from stf_v3.windows_runtime import configure_windows_event_loop


def main() -> None:
    """Configure async database compatibility before Uvicorn creates a loop."""
    configure_windows_event_loop()
    uvicorn.run("stf_v3.main:app", host="127.0.0.1", port=8002)


if __name__ == "__main__":
    main()
