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
