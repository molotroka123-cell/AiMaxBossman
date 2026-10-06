import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def small_data(tmp_path_factory):
    from pokerlora import dataset
    d = tmp_path_factory.mktemp("lora_data")
    m = dataset.build(24, 5, d, per_node_cap=4, workers=2)
    return d, m
