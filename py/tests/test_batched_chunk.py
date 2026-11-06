"""Exact partition/aggregation properties of the low-memory batch worker."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "py"))

from run_dr_chunk_batched import combine_monthly, sub_batch  # noqa: E402


def test_secondary_partition_splits_one_outer_shard_without_overlap():
    # All addresses have the same first word, hence the same canonical /8
    # shard. The cryptographic inner hash is insensitive to patterned address
    # words and distributes this larger sample across all four batches.
    users = [f"0x00000005{i:08x}{0:024x}" for i in range(100)]
    buckets = [sub_batch(u, 4) for u in users]
    assert set(buckets) == {0, 1, 2, 3}
    assert sum(buckets.count(b) for b in range(4)) == len(users)


def test_combine_adds_subbatches_and_prior_months_once():
    def frame(month, amount):
        return pd.DataFrame([{
            "month": month, "blockchain": "base", "token": "sUSDS",
            "ref_code": 99, "dr_usd": amount, "source": "psm3",
        }])

    got = combine_monthly(
        [frame("2026-10-01", 1.25), frame("2026-10-01", 2.75)],
        frame("2026-09-01", 10.0),
    ).sort_values("month").reset_index(drop=True)
    assert list(got["month"]) == ["2026-09-01", "2026-10-01"]
    assert list(got["dr_usd"]) == [10.0, 4.0]
