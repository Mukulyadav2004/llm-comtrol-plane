"""Test bootstrap: swap Redis for fakeredis before app modules import."""
import pathlib
import sys

import fakeredis
import pytest
import redis as redis_lib

SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

_SHARED = fakeredis.FakeRedis(decode_responses=True)
redis_lib.from_url = lambda *args, **kwargs: _SHARED  # noqa: E731


@pytest.fixture(autouse=True)
def _flush_redis():
    _SHARED.flushall()
    yield
    _SHARED.flushall()
