"""Manual-agent evaluation suite.

Measures how well a manual-search sub-agent uses the 4 manual
navigation tools to answer diagnostic inquiries.  Graded by an
LLM-as-judge (``z-ai/glm-5.1``) against a human-reviewed golden
set stored under ``golden/v1/``.

Run with::

    pytest --run-eval tests/harness/evals/

Author: Li-Ta Hsu
"""
