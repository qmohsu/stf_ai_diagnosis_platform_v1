"""Abstract base class for OBD-II readers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple


class OBDReader(ABC):
    """Unified interface for reading OBD-II data.

    Concrete implementations: ``SimulationReader`` (fixture-based) and
    ``LiveReader`` (python-OBD hardware wrapper).
    """

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the OBD adapter."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the connection."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return ``True`` if the adapter connection is active."""

    @abstractmethod
    async def read_dtcs(self) -> List[Tuple[str, str]]:
        """Read active Diagnostic Trouble Codes.

        Returns a list of ``(code, description)`` tuples.
        """

    @abstractmethod
    async def read_pid(self, name: str) -> Optional[Tuple[float, str]]:
        """Read a single PID by canonical name.

        Returns ``(value, unit)`` or ``None`` if the PID is unavailable.
        """

    @abstractmethod
    async def read_supported_pids(self) -> List[str]:
        """Return the list of PID names supported by the vehicle."""

    @abstractmethod
    async def read_freeze_frame(self) -> Dict[str, Tuple[float, str]]:
        """Read freeze-frame data captured at the time of the last DTC.

        Returns ``{pid_name: (value, unit)}``.
        """
