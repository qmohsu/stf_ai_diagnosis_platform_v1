"""Specialised sub-agents built on top of the core harness.

Each module here defines a narrowly-scoped agent with its own
restricted tool set, system prompt, and structured-output contract.
Sub-agents reuse the ``LLMClient`` protocol and ``ToolRegistry`` from
``app.harness`` but run their own minimal ReAct loops — they do not
emit ``HarnessEvent`` streams or persist to the session event log.

Current sub-agents:
  - ``manual_agent``: vehicle-service-manual search specialist used
    by the evaluation suite (HARNESS-14).

Author: Li-Ta Hsu
"""
