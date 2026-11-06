"""Exact partition/aggregation properties of the low-memory batch worker."""

from __future__ import annotations

import hashlib
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "py"))

import run_dr_chunk_batched as batched  # noqa: E402

combine_monthly = batched.combine_monthly
sub_batch = batched.sub_batch


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


def test_temporary_checkpoint_identity_tracks_the_worker_implementation(monkeypatch):
    monkeypatch.setattr(batched, "checkpoint_fingerprint",
                        lambda _name, _shard: "canonical")
    identity = batched._identity("chunk", "0/8", date(2026, 11, 1), 4)
    expected = hashlib.sha256(Path(batched.__file__).read_bytes()).hexdigest()
    assert identity["fingerprint"] == "canonical"
    assert identity["worker_fingerprint"] == expected


@pytest.mark.parametrize("cutoff", [date(2026, 8, 1), date(2026, 10, 31)])
def test_batched_worker_rejects_unsupported_or_partial_month_cutoffs(cutoff):
    with pytest.raises(SystemExit, match="supported first-of-month"):
        batched.validate_cutoff(cutoff)


def test_batched_worker_accepts_supported_month_boundary():
    batched.validate_cutoff(date(2026, 11, 1))
