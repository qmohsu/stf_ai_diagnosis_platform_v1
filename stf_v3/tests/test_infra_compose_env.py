"""PROD-10: compose variable defaults stay single-level.

podman-compose 1.5 mis-reads a nested default such as
``${A:-${B:-}}``: with ``A`` set, the container receives ``A`` + ``}``.
PROD-09 shipped one for the OpenRouter key; PROD-10's judge preflight
found the key rejected (401) inside the V3 containers.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import pathlib
import re

import pytest

_INFRA = pathlib.Path(__file__).resolve().parents[2] / "infra"
FILES = sorted(_INFRA.glob("docker-compose.v3*.yml")) + sorted(_INFRA.glob("docker-compose.vllm.yml"))

pytestmark = pytest.mark.skipif(not FILES, reason="infra/ not present (portable copy)")


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_no_nested_variable_defaults(path: pathlib.Path) -> None:
    """No ``${…${…}…}`` anywhere in the V3 / vLLM compose files."""
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        assert not re.search(r"\$\{[^}]*\$\{", line), f"{path.name}:{n}: nested default: {line.strip()}"
