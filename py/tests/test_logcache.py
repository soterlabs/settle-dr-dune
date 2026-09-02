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


def test_non_abutting_segment_is_refused_without_orphan_file():
    d = logcache.entry_dir(CHAIN, "deadbeef")
    args = {"chain": CHAIN, "selections": SEL,
            "log_fields": hypersync._DEFAULT_LOG_FIELDS}
    m = logcache.append_segment(d, None, args, [row(100)], 100, 200)
    with pytest.raises(RuntimeError, match="does not abut"):
        logcache.append_segment(d, m, args, [row(300)], 300, 400)  # gap at 201-299
    assert not (d / "seg_300_400.parquet").exists()  # refused BEFORE writing


def test_request_disjoint_above_serves_live_without_extending():
    live = fake_live({100: [row(100)], 850: [row(850)]}, head=2000)
    with mock.patch.object(hypersync, "_query_logs_live", live):
        _q(100, 600)                       # coverage [100,600]
        got = _q(800, 995)                 # gap above coverage: 601-799 unknown
    assert live.calls == [(100, 600), (800, 995)]
    assert [r.block_number for r in got.rows] == [850]
    m = logcache.load_meta(logcache.entry_dir(
        CHAIN, logcache.cache_key(CHAIN, SEL, hypersync._DEFAULT_LOG_FIELDS)))
    assert (m.cached_from, m.cached_through) == (100, 600)  # NOT extended, no crash


def test_request_disjoint_below_serves_live_without_gap_fetch():
    live = fake_live({50: [row(50)], 500: [row(500)]}, head=1000)
    with mock.patch.object(hypersync, "_query_logs_live", live):
        _q(400, 600)                       # coverage [400,600]
        got = _q(40, 80)                   # ends well below coverage
    assert live.calls == [(400, 600), (40, 80)]  # NOT (40, 399): no surplus
    assert [r.block_number for r in got.rows] == [50]
    m = logcache.load_meta(logcache.entry_dir(
        CHAIN, logcache.cache_key(CHAIN, SEL, hypersync._DEFAULT_LOG_FIELDS)))
    assert (m.cached_from, m.cached_through) == (400, 600)


def test_truncated_backfill_is_not_persisted():
    live = fake_live({500: [row(500)]}, head=1000)
    with mock.patch.object(hypersync, "_query_logs_live", live):
        _q(400, 600)                       # coverage [400,600]
        # degraded server: archive_height==0 skips the incomplete-range guard
        broken = fake_live({50: [row(50)], 500: [row(500)]}, head=0)
        with mock.patch.object(hypersync, "_query_logs_live", broken):
            got = _q(40, 600)
    assert [r.block_number for r in got.rows] == [50, 500]  # served (live+cache)
    m = logcache.load_meta(logcache.entry_dir(
        CHAIN, logcache.cache_key(CHAIN, SEL, hypersync._DEFAULT_LOG_FIELDS)))
    assert m.cached_from == 400  # the possibly-partial fetch never became coverage


def test_inverted_range_returns_empty_without_fetching():
    live = fake_live({100: [row(100)]}, head=1000)
    with mock.patch.object(hypersync, "_query_logs_live", live):
        _q(100, 600)
        got = _q(500, 100)                 # to < from: pre-cache instant empty
    assert live.calls == [(100, 600)]
    assert got.rows == []


def test_persist_failure_degrades_to_live_serving():
    live = fake_live({100: [row(100)]}, head=1000)
    with mock.patch.object(hypersync, "_query_logs_live", live), \
         mock.patch.object(logcache, "append_segment",
                           side_effect=OSError("disk full")):
        got = _q(100, 600)                 # write fails; rows already in hand
    assert [r.block_number for r in got.rows] == [100]


def test_enabled_parses_falsy_spellings(monkeypatch):
    for v, want in [("1", False), ("true", False), ("yes", False),
                    ("0", True), ("false", True), ("no", True), ("", True)]:
        monkeypatch.setenv("DRHS_NO_LOG_CACHE", v)
        assert logcache.enabled() is want, f"DRHS_NO_LOG_CACHE={v!r}"
    monkeypatch.delenv("DRHS_NO_LOG_CACHE")
    assert logcache.enabled() is True


def test_meta_under_wrong_key_dir_is_refused():
    live = fake_live({100: [row(100)]}, head=1000)
    with mock.patch.object(hypersync, "_query_logs_live", live):
        _q(100, 600)
    good = logcache.entry_dir(CHAIN, logcache.cache_key(
        CHAIN, SEL, hypersync._DEFAULT_LOG_FIELDS))
    import shutil
    swapped = good.parent / ("0" * 32)     # entry moved under a foreign key
    shutil.copytree(good, swapped)
    assert logcache.load_meta(good) is not None
    assert logcache.load_meta(swapped) is None


# --- the transaction join (LogRow.tx_to) and the cache -----------------------

def row_tx(bn, tx_to):
    return LogRow(**{**row(bn).__dict__, "tx_to": tx_to})


def test_with_tx_to_is_part_of_the_key_but_only_when_true():
    lf = hypersync._DEFAULT_LOG_FIELDS
    assert logcache.cache_key(CHAIN, SEL, lf, with_tx_to=True) != logcache.cache_key(CHAIN, SEL, lf)
    # pre-existing entries keep their key: False must hash exactly like the old 3-arg form
    assert logcache.cache_key(CHAIN, SEL, lf, with_tx_to=False) == logcache.cache_key(CHAIN, SEL, lf)


def test_join_query_never_served_from_a_plain_entry():
    """A plain Transfer scan cached first; the same selection WITH the join must
    fetch live (its rows carry tx_to), not replay the tx_to-less entry."""
    plain = fake_live({100: [row(100)]}, head=1000)
    with mock.patch.object(hypersync, "_query_logs_live", plain):
        _q(100, 500)
    joined = fake_live({100: [row_tx(100, "0xrouter")]}, head=1000)
    with mock.patch.object(hypersync, "_query_logs_live", joined):
        r1 = hypersync.query_logs(CHAIN, SEL, 100, 500, with_tx_to=True)
        r2 = hypersync.query_logs(CHAIN, SEL, 100, 500, with_tx_to=True)
    assert joined.calls == [(100, 500)]                 # live once, then cached
    assert r1.rows[0].tx_to == "0xrouter" and r2.rows[0].tx_to == "0xrouter"   # survives parquet
    # and the join flag reaches the live fetch
    seen = []
    def spy(chain, selections, fb, tb, **kw):
        seen.append(kw.get("with_tx_to")); return QueryResult(rows=[], archive_height=1000)
    with mock.patch.object(hypersync, "_query_logs_live", spy):
        hypersync.query_logs(CHAIN, SEL, 2000, 2100, with_tx_to=True)
    assert seen == [True]


def test_segment_written_before_tx_to_existed_still_reads(tmp_path):
    """Entries persisted by the pre-join code have no tx_to column: they must
    replay with tx_to=None instead of failing on a missing column."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    d = logcache.entry_dir(CHAIN, logcache.cache_key(CHAIN, SEL, hypersync._DEFAULT_LOG_FIELDS))
    d.mkdir(parents=True)
    old_cols = [c for c in logcache._columns() if c != "tx_to"]
    r = row(100)
    pq.write_table(pa.table({c: [getattr(r, c)] for c in old_cols}), d / "seg_100_700.parquet")
    m = logcache.Meta(chain=CHAIN, selections=SEL, log_fields=hypersync._DEFAULT_LOG_FIELDS,
                      cached_from=100, cached_through=700,
                      segments=[{"file": "seg_100_700.parquet", "from": 100, "to": 700}])
    logcache._write_meta(d, m)
    assert logcache.load_meta(d) is not None          # meta without with_tx_to loads (default False)
    rows = logcache.read_rows(d, m, 100, 700)
    assert len(rows) == 1 and rows[0].tx_to is None and rows[0].transaction_hash == "0x64"


def test_tx_join_incomplete_raises_instead_of_persisting_none():
    """A page with logs but no matching transactions must raise (like the
    timestamp join), never persist tx_to=None under the join key."""
    page = {"data": [{"blocks": [{"number": 100, "timestamp": 1}],
                      "transactions": [],                      # join truncated
                      "logs": [{"block_number": 100, "log_index": 0, "address": "0xtoken",
                                "topic0": "0xt0", "data": "0x", "transaction_hash": "0x64"}]}],
            "next_block": 101, "archive_height": 1000}
    class R:  # minimal requests.Response stand-in
        ok = True; status_code = 200; text = ""
        def json(self): return page
    with pytest.raises(hypersync.HyperSyncError, match="tx join incomplete"):
        hypersync._query_logs_live(CHAIN, SEL, 100, 100, with_tx_to=True, post=lambda *a, **k: R())
    # without the join the same page is fine (tx_to stays None by design)
    rows = hypersync._query_logs_live(CHAIN, SEL, 100, 100, post=lambda *a, **k: R()).rows
    assert rows[0].tx_to is None


def test_meta_omits_with_tx_to_when_false_for_pre_join_readers(tmp_path):
    import json
    d = tmp_path / "e"; d.mkdir()
    m = logcache.Meta(chain=CHAIN, selections=SEL, log_fields=["a"], cached_from=1, cached_through=2, segments=[])
    logcache._write_meta(d, m)
    assert "with_tx_to" not in json.loads((d / "meta.json").read_text())   # loadable by Meta(**json) of old code
    m2 = logcache.Meta(chain=CHAIN, selections=SEL, log_fields=["a"], cached_from=1, cached_through=2, segments=[], with_tx_to=True)
    logcache._write_meta(d, m2)
    assert json.loads((d / "meta.json").read_text())["with_tx_to"] is True
