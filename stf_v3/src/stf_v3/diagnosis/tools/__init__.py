"""Agent tools (PROD-08): 6 OBD primitives, 4 manual primitives, 2 delegations.

Every tool returns text (a string) — never raw sample arrays — and is a
pure function of the run's dependency bundle (``DiagDeps``): no database
access inside a tool, so the whole set is exercisable offline with
Pydantic AI's ``TestModel`` (FM-50).

Author: Xiangzhu Yan
"""
