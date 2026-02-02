"""OBD Agent -- standalone OBD-II telemetry collector.

Reads data from ELM327 adapters (or simulation) and POSTs
sanitised OBDSnapshot JSON to the diagnostic_api service.

This package is intentionally separate from diagnostic_api
because python-OBD carries a GPL-2.0 licence.
"""

__version__ = "0.1.0"
