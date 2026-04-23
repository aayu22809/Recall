from __future__ import annotations

import pytest


@pytest.fixture
def fake_embedding() -> list[float]:
    return [0.01] * 768
