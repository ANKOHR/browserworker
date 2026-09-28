from __future__ import annotations

import pytest

from browserworker.fixtures import FixtureServer


@pytest.fixture
def fixture_server():
    with FixtureServer() as server:
        yield server
