"""Pydantic AI diagnosis runtime (PROD-08).

``main_agent`` runs one diagnosis for a vehicle + log; ``manual_agent`` and
``obd_agent`` are the two specialist sub-agents it can delegate to (each
mounted as a tool).  ``events`` is the 10-event vocabulary PROD-11 writes
to ``audit_events`` / SSE; ``memory`` serialises a run's message history
for the ``messages`` table.

Author: Xiangzhu Yan
"""
