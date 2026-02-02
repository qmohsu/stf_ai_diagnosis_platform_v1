"""Agent configuration via environment variables.

Uses pydantic-settings so every field can be overridden with an
env var.  Simulation is the zero-hardware default.

Note: ``env_prefix`` is empty, so field names map directly to env vars
(e.g. ``LOG_LEVEL``, ``DRY_RUN``).  In container mode each service has
its own env namespace.  In host mode the agent reads ``obd_agent/.env``
which is separate from ``infra/.env``, so collisions are unlikely.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class AgentSettings(BaseSettings):
    """OBD Agent runtime settings."""

    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}

    # -- adapter / vehicle --------------------------------------------------
    obd_port: str = Field(
        default="sim",
        description="Serial port for ELM327, or 'sim' for simulation mode",
    )
    obd_baudrate: int = Field(default=115200, description="Serial baud rate")
    vehicle_id: str = Field(
        default="V-SIM-001",
        description="Pseudonymous vehicle identifier",
    )

    # -- simulation ---------------------------------------------------------
    obd_sim_scenario: str = Field(
        default="misfire",
        description="Simulation scenario name (from simulation_scenarios.json)",
    )

    # -- API ----------------------------------------------------------------
    diagnostic_api_base_url: str = Field(
        default="http://127.0.0.1:8000",
        description="Base URL of the diagnostic_api service",
    )
    snapshot_interval_seconds: int = Field(
        default=30,
        description="Seconds between snapshots in continuous mode",
    )

    # -- behaviour ----------------------------------------------------------
    dry_run: bool = Field(
        default=False,
        description="Validate snapshot locally; never POST to API",
    )
    log_level: str = Field(default="INFO", description="Logging level")
    log_format: str = Field(
        default="console",
        description="Log output format: 'console' or 'json'",
    )
    max_retry_attempts: int = Field(
        default=3,
        description="Max HTTP retry attempts before buffering",
    )
    offline_buffer_max: int = Field(
        default=100,
        description="Max snapshots to buffer when API is unreachable",
    )

    # -- derived ------------------------------------------------------------
    @property
    def is_simulation(self) -> bool:
        """Return ``True`` when the agent is running in simulation mode."""
        return self.obd_port.strip().lower() == "sim"
