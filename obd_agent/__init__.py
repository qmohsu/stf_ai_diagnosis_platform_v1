"""OBD Agent -- OBD-II log analysis library and upload client.

Provides the deterministic analysis pipeline used by diagnostic_api
(format normalisation, time-series normalisation, statistics,
anomaly detection, clue generation) and the Jetson-side reference
upload client (``jetson_uploader``) that delivers end-of-trip logs
to ``POST /v2/obd/analyze``.

The live ELM327 acquisition loop (reader / snapshot poster) was
removed under APP-53 cleanup (2026-06-09): its transport targeted
``/v1/telemetry/obd_snapshot``, which was never deployed.  The 4G
end-of-trip upload path (GitHub issue #76) is the supported
ingestion route.
"""

__version__ = "0.2.0"
