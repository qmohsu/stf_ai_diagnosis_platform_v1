"""OBD reader abstraction layer.

Provides ``OBDReader`` ABC with two concrete implementations:

* ``SimulationReader`` -- fixture-based, no hardware required.
* ``LiveReader``       -- wraps python-OBD (GPL-2.0, lazy-imported).
"""

from obd_agent.reader.base import OBDReader

__all__ = ["OBDReader"]
