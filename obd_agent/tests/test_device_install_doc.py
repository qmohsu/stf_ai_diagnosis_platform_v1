"""PROD-07 T-13: the device install guide must keep the sections the
failure-mode review made mandatory (FM-5, 7, 14, 16, 17, 18, 20, 22, 24,
29, 35).  A missing heading fails CI; example tokens must be fakes.
"""

from __future__ import annotations

import re
from pathlib import Path

_GUIDE = Path(__file__).resolve().parents[2] / "docs" / "v3_device_install.md"

# (heading fragment, failure mode it guards)
_REQUIRED_SECTIONS = [
    ("开始前先回答", "FM-17"),          # 5 device facts before the guide is final
    ("第一步：设备能不能连到 V3", "FM-14"),   # curl health first
    ("配置文件", "FM-22"),               # env file + chmod 600
    ("定时补传", "FM-20"),               # cron primary + check after reboot
    ("重启后确认", "FM-20"),
    ("退出码", "FM-35"),
    ("回滚", "FM-16"),                   # rollback = remove the env file
    ("换车", "FM-5"),                    # revoke first
    ("每周检查", "FM-7"),                # rejected dir + last-seen caveat
    ("最近活跃", "FM-29"),
    ("我们要日志时", "FM-18"),
    ("旧系统退役", "FM-24"),
]

_FAKE_TOKEN_MARKERS = ("<", "示例", "EXAMPLE", "xxxx")


def test_install_guide_has_every_required_section() -> None:
    """Each mandated section heading / phrase is present."""
    text = _GUIDE.read_text(encoding="utf-8")
    missing = [f"{frag} ({fm})" for frag, fm in _REQUIRED_SECTIONS if frag not in text]
    assert not missing, f"install guide lacks: {missing}"


def test_install_guide_tokens_are_placeholders() -> None:
    """No real-looking device token (43-char urlsafe) appears in the guide."""
    text = _GUIDE.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "STF_V3_DEVICE_TOKEN=" in line:
            value = line.split("=", 1)[1].strip()
            assert any(m in value for m in _FAKE_TOKEN_MARKERS), line
    assert not re.search(r"STF_V3_DEVICE_TOKEN=[A-Za-z0-9_-]{43}", text)


def test_install_guide_has_no_vin() -> None:
    """No 17-char VIN-shaped string outside the documented fake fixtures."""
    text = _GUIDE.read_text(encoding="utf-8")
    for m in re.findall(r"\b[A-HJ-NPR-Z0-9]{17}\b", text):
        assert m in ("JHMGK5830HX202404", "1HGCM82633A123456"), m
