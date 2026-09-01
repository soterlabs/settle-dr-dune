"""The persistent log cache: exact replay, safe-depth, contiguity, bypass."""

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "py"))

from drhs import hypersync, logcache  # noqa: E402
from drhs.hypersync import LogRow, QueryResult  # noqa: E402

SEL = [{"address": ["0xToken"], "topics": [["0xT0"]]}]
CHAIN = "ethereum"  # SAFE_DEPTH_BLOCKS[ethereum] == 300


def row(bn, li=0):
    return LogRow(block_number=bn, log_index=li, block_time=1_700_000_000 + bn,
                  address="0xtoken", topic0="0xt0", topic1=None, topic2=None,
                  topic3=None, data="0x", transaction_hash=f"0x{bn:x}")


def fake_live(rows_by_block, head):
    """A _query_logs_live stand-in returning rows within the asked range."""
    calls = []

    def _live(chain, selections, from_block, to_block, **kw):
        calls.append((from_block, to_block))
        rows = [r for bn, rs in sorted(rows_by_block.items())
                for r in rs if from_block <= bn <= to_block]
        return QueryResult(rows=rows, archive_height=head)
    _live.calls = calls
    return _live


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("DRHS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ENVIO_API_TOKEN", "test-token")
    monkeypatch.delenv("DRHS_NO_LOG_CACHE", raising=False)


def _q(from_block, to_block):
    return hypersync.query_logs(CHAIN, SEL, from_block, to_block)


def test_first_fetch_persists_safe_portion_and_replays_exactly():
    data = {100: [row(100)], 150: [row(150, 0), row(150, 1)], 990: [row(990)]}
    live = fake_live(data, head=1000)  # safe through 1000-300=700
    with mock.patch.object(hypersync, "_query_logs_live", live):
        first = _q(100, 995)
        assert [r.block_number for r in first.rows] == [100, 150, 150, 990]
        second = _q(100, 995)
    assert live.calls == [(100, 995), (701, 995)]  # only the unsafe tail refetched
    assert [(r.block_number, r.log_index) for r in second.rows] == \
        [(r.block_number, r.log_index) for r in first.rows]
    assert second.rows[1].transaction_hash == "0x96"  # full row fidelity


def test_fully_cached_request_makes_no_network_call():
    live = fake_live({100: [row(100)]}, head=1000)
    with mock.patch.object(hypersync, "_query_logs_live", live):
        _q(50, 600)
        got = _q(90, 110)
    assert live.calls == [(50, 600)]
    assert [r.block_number for r in got.rows] == [100]


def test_monthly_append_extends_coverage_upward():
    live = fake_live({100: [row(100)], 800: [row(800)], 1900: [row(1900)]}, head=1000)
    with mock.patch.object(hypersync, "_query_logs_live", live):
        _q(100, 900)                       # caches through 700
        live2 = fake_live({100: [row(100)], 800: [row(800)], 1900: [row(1900)]}, head=2500)
        with mock.patch.object(hypersync, "_query_logs_live", live2):
            got = _q(100, 2000)            # next month: caches through 2200
    assert live2.calls == [(701, 2000)]    # nothing below coverage refetched
    assert [r.block_number for r in got.rows] == [100, 800, 1900]
    m = logcache.load_meta(logcache.entry_dir(
        CHAIN, logcache.cache_key(CHAIN, SEL, hypersync._DEFAULT_LOG_FIELDS)))
    assert (m.cached_from, m.cached_through) == (100, 2000)


def test_downward_extension_backfills_contiguously():
    live = fake_live({50: [row(50)], 500: [row(500)]}, head=1000)
    with mock.patch.object(hypersync, "_query_logs_live", live):
        _q(400, 600)                       # coverage [400,600]
        got = _q(40, 600)                  # starts below coverage
    assert live.calls == [(400, 600), (40, 399)]
    assert [r.block_number for r in got.rows] == [50, 500]


def test_key_differs_on_any_selection_change():
    lf = hypersync._DEFAULT_LOG_FIELDS
    base = logcache.cache_key(CHAIN, SEL, lf)
    assert logcache.cache_key("base", SEL, lf) != base
    assert logcache.cache_key(CHAIN, [{"address": ["0xother"], "topics": [["0xT0"]]}], lf) != base
    assert logcache.cache_key(CHAIN, [{"address": ["0xTOKEN"], "topics": [["0xt0"]]}], lf) == base


def test_env_bypass_goes_straight_to_network():
    live = fake_live({100: [row(100)]}, head=1000)
    os.environ["DRHS_NO_LOG_CACHE"] = "1"
    try:
        with mock.patch.object(hypersync, "_query_logs_live", live):
            _q(100, 600)
            _q(100, 600)
    finally:
        del os.environ["DRHS_NO_LOG_CACHE"]
    assert live.calls == [(100, 600), (100, 600)]
    assert logcache.load_meta(logcache.entry_dir(
        CHAIN, logcache.cache_key(CHAIN, SEL, hypersync._DEFAULT_LOG_FIELDS))) is None


def test_injected_post_bypasses_cache():
    # fixtures/tests inject post= — their fake responses must never persist
    def fake_post(url, json=None, timeout=None, headers=None):
        class R:
            status_code = 200
            ok = True
            text = ""

            @staticmethod
            def json():
                return {"data": [], "next_block": 601, "archive_height": 1000}
        return R()
    hypersync.query_logs(CHAIN, SEL, 100, 600, post=fake_post)
    assert logcache.load_meta(logcache.entry_dir(
        CHAIN, logcache.cache_key(CHAIN, SEL, hypersync._DEFAULT_LOG_FIELDS))) is None


def test_corrupt_meta_is_refused_and_refetched(tmp_path):
    live = fake_live({100: [row(100)]}, head=1000)
    with mock.patch.object(hypersync, "_query_logs_live", live):
        _q(100, 600)
        d = logcache.entry_dir(CHAIN, logcache.cache_key(
            CHAIN, SEL, hypersync._DEFAULT_LOG_FIELDS))
        # delete a segment file out from under the meta -> load_meta refuses
        next(d.glob("seg_*.parquet")).unlink()
        assert logcache.load_meta(d) is None
        got = _q(100, 600)
    assert live.calls == [(100, 600), (100, 600)]  # full refetch, no crash
    assert [r.block_number for r in got.rows] == [100]


def test_non_abutting_segment_is_refused():
    d = logcache.entry_dir(CHAIN, "deadbeef")
    args = {"chain": CHAIN, "selections": SEL,
            "log_fields": hypersync._DEFAULT_LOG_FIELDS}
    m = logcache.append_segment(d, None, args, [row(100)], 100, 200)
    with pytest.raises(RuntimeError, match="does not abut"):
        logcache.append_segment(d, m, args, [row(300)], 300, 400)  # gap at 201-299
