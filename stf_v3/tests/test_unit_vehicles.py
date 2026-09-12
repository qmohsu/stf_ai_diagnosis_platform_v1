"""Offline unit tests for vehicle helpers (no database).

Author: Xiangzhu Yan
"""

import pytest
from pydantic import ValidationError

from stf_v3.vehicles.schemas import VehicleIn
from stf_v3.vehicles.service import hash_token, new_device_token


def test_vin_is_normalised_to_uppercase() -> None:
    """A lowercase VIN is accepted and stored uppercase."""
    body = VehicleIn(vin="jhmgk5830hx202404", manufacturer="Honda", model="Jazz")
    assert body.vin == "JHMGK5830HX202404"


@pytest.mark.parametrize("vin", ["JHMGK5830HX20240", "JHMGK5830HX202404I", "JHMGK5830HX20240O"])
def test_vin_rejects_wrong_length_and_forbidden_letters(vin: str) -> None:
    """VINs must be 17 chars and never contain I, O or Q (ISO 3779)."""
    with pytest.raises(ValidationError):
        VehicleIn(vin=vin, manufacturer="Honda", model="Jazz")


def test_device_token_is_long_and_hashed_deterministically() -> None:
    """Tokens are ≥ 40 chars; the stored hash is sha256 hex and stable."""
    token = new_device_token()
    assert len(token) >= 40
    assert hash_token(token) == hash_token(token)
    assert len(hash_token(token)) == 64
    assert hash_token(token) != hash_token(new_device_token())
