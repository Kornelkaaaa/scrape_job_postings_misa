"""Shared pytest fixtures.

LEARNING NOTES:
- conftest.py is a special pytest file: anything defined here is available
  to every test in this folder WITHOUT imports.
- A "fixture" (@pytest.fixture) is reusable setup a test asks for just by
  naming it as a parameter: def test_x(fixture, make_source) - pytest sees
  the names and injects them.
- The tests never hit the network: adapters' parse() functions are fed saved
  API responses from tests/fixtures/. Fast, free, and they don't break when
  a website is down.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.config import Source  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture():
    def _load(name: str):
        path = FIXTURES / name
        if name.endswith(".json"):
            return json.loads(path.read_text(encoding="utf-8"))
        return path.read_text(encoding="utf-8")
    return _load


@pytest.fixture
def make_source():
    def _make(**overrides) -> Source:
        defaults = dict(name="TestSource", type="json_api", opportunity_type="job")
        defaults.update(overrides)
        return Source(**defaults)
    return _make
