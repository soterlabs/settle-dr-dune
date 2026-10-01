"""A freshly closed month needs no data from a future day."""
import sys
from pathlib import Path
from datetime import date, datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from drhs.revenue import conversion  # noqa: E402


def test_conversion_includes_last_second_without_future_head(monkeypatch):
    cutoff = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()) - 1
    resolved = []

    def find(chain, ts):
        resolved.append(ts)
        assert ts <= cutoff  # an October 1/2 request is not available yet
        return ts

    def row(ts, assets, shares):
        return SimpleNamespace(block_time=ts, log_index=1,
                               data="0x" + f"{assets:064x}{shares:064x}")

    def query(chain, selections, start, end):
        assert end == cutoff
        return SimpleNamespace(rows=[row(cutoff - 86400, 101, 100),
                                     row(cutoff, 102, 100)])

    monkeypatch.setattr(conversion.hypersync, "find_block_at_or_before", find)
    monkeypatch.setattr(conversion.hypersync, "query_logs", query)
    result = conversion._daily_last_rate_series(
        "ethereum", "0xvault", date(2026, 9, 29), date(2026, 9, 30))
    assert resolved[-1] == cutoff
    assert result["rate"].tolist() == [1.01, 1.02]
