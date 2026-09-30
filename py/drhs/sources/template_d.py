"""Template D: USDS staking-farm TWA (Sky/Spk/Chronicle/Grove, Ethereum).

SNX-style StakingRewards clones with a referral-emitting wrapper. Balance is NOT
from share Transfers — it's from the staking events:
  Staked(address indexed user, uint256 amount)     -> +amount (USDS)
  Withdrawn(address indexed user, uint256 amount)   -> -amount (USDS)
ref_code from the wrapper's Referral(uint16 indexed, address indexed user,
uint256) matched by (tx_hash, user), forward-filled. Mirrors
queries/twa_usds_staking_farms.sql. No address exclusions.

All four farms share the "StakingRewards" bytecode (one decoded table on Dune),
separated by contract_address. Symbols USDS-SKY / USDS-SPK / USDS-CLE /
USDS-GROVE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

from .. import events, hypersync
from . import template_ab

DEFAULT_END = template_ab.DEFAULT_END


@dataclass(frozen=True)
class FarmTarget:
    blockchain: str
    symbol: str
    address: str       # farm (StakingRewards) contract
    decimals: int
    start_date: date


SKY = FarmTarget("ethereum", "USDS-SKY", "0x0650caf159c5a49f711e8169d4336ecb9b950275", 18, date(2024, 9, 1))
SPK = FarmTarget("ethereum", "USDS-SPK", "0x173e314c7635b45322cd8cb14f44b312e079f3af", 18, date(2024, 9, 1))
CLE = FarmTarget("ethereum", "USDS-CLE", "0x10ab606b067c9c461d8893c47c7512472e19e2ce", 18, date(2024, 9, 1))
GROVE = FarmTarget("ethereum", "USDS-GROVE", "0x4e41488c19cd35eb4de3083fc3e204854c75c86a", 18, date(2026, 6, 1))
ALL = [SKY, SPK, CLE, GROVE]


def _end_ts(end_date: date) -> int:
    eff = min(end_date, DEFAULT_END)
    return int(datetime(eff.year, eff.month, eff.day, tzinfo=timezone.utc).timestamp())


def legs_from_rows(t: FarmTarget, ref_rows, stake_rows, end_ts: int) -> pd.DataFrame:
    """Pure: Referral + Staked/Withdrawn ``LogRow``s -> balance-change legs.

    One leg per staking event (Staked +, Withdrawn -), user = indexed topic1,
    amount = first data word; ref from the tx's Referral (latest by log_index).
    """
    latest_ref = template_ab.latest_referral_from_events(ref_rows)  # code=topic1, user=topic2
    scale = 10 ** t.decimals
    start_day = t.start_date
    recs: list[dict] = []
    for r in stake_rows:
        if r.block_time >= end_ts:
            continue
        if datetime.fromtimestamp(r.block_time, tz=timezone.utc).date() < start_day:
            continue
        if r.topic0 == events.STAKED_TOPIC0:
            sign = 1.0
        elif r.topic0 == events.WITHDRAWN_TOPIC0:
            sign = -1.0
        else:
            continue
        user = events.topic_to_addr(r.topic1)
        amt = sign * events.transfer_value(r.data) / scale   # amount = first data word
        ref = latest_ref.get((r.transaction_hash, user))
        recs.append(template_ab._leg(t, user, r, amt, ref))
    if not recs:
        return pd.DataFrame()
    return pd.DataFrame(recs)


def fetch_target_rows(t: FarmTarget, end_ts: int, scan_start: date | None = None):
    """Fetch Referral rows and Staked+Withdrawn rows for the farm over the window.

    Returns (referral_rows, stake_rows); stake_rows mixes Staked and Withdrawn
    (distinguished by topic0 downstream).
    """
    start = max(t.start_date, scan_start) if scan_start else t.start_date
    start_ts = int(datetime(start.year, start.month, start.day,
                            tzinfo=timezone.utc).timestamp())
    try:
        from_block = hypersync.find_block_at_or_before(t.blockchain, start_ts)
    except hypersync.HyperSyncError:
        from_block = 0
    to_block = hypersync.find_block_at_or_before(t.blockchain, end_ts - 1)
    addr = t.address.lower()
    ref_rows = hypersync.query_logs(
        t.blockchain, [{"address": [addr], "topics": [[events.REFERRAL3_TOPIC0]]}],
        from_block, to_block,
    ).rows
    stake_rows = hypersync.query_logs(
        t.blockchain,
        [{"address": [addr], "topics": [[events.STAKED_TOPIC0, events.WITHDRAWN_TOPIC0]]}],
        from_block, to_block,
    ).rows
    return ref_rows, stake_rows


def build_legs(
    targets: list[FarmTarget], *, end_date: date = DEFAULT_END,
    excluded: frozenset[str] = frozenset(),
    scan_start: date | None = None,
) -> pd.DataFrame:
    end_ts = _end_ts(end_date)
    start_ts = int(datetime(scan_start.year, scan_start.month, scan_start.day,
                            tzinfo=timezone.utc).timestamp()) if scan_start else None
    frames = []
    for t in targets:
        ref_rows, stake_rows = fetch_target_rows(t, end_ts, scan_start)
        legs = legs_from_rows(t, ref_rows, stake_rows, end_ts)
        if start_ts is not None and not legs.empty:
            legs = legs[legs["ts"] >= start_ts].copy()
        if not legs.empty:
            frames.append(legs)
    if not frames:
        return pd.DataFrame(columns=[
            "blockchain", "contract_address", "symbol", "user_addr",
            "block", "log_index", "ts", "amount_change", "ref_code",
        ])
    legs = pd.concat(frames, ignore_index=True)
    if excluded:
        legs = legs[~legs["user_addr"].str.lower().isin(excluded)].copy()
    return legs
