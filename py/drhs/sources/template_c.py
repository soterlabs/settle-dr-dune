"""Template C: L2 sUSDS per-user daily TWA (Base, Arbitrum, Optimism, Unichain).

On L2s sUSDS is acquired via the PSM3 ``Swap`` (USDS/USDC -> sUSDS), which
carries the reward code inline. BALANCE is tracked from ordinary sUSDS ERC20
``Transfer`` events; REF_CODE comes from the PSM3 ``Swap.referralCode`` matched
by (tx_hash, receiver) and forward-filled (last-referral-wins).

Only the ref-code source differs from Template A/B — the transfer legs, TWA tail
and address exclusions are shared (``template_ab.transfer_legs`` /
``TEMPLATE_A_EXCLUDED``). Mirrors queries/twa_susds_psm3_l2.sql.

  Swap(address indexed assetIn, address indexed assetOut, address sender,
       address indexed receiver, uint256 amountIn, uint256 amountOut,
       uint256 referralCode)
  -> assetOut = topic2 (must be the sUSDS token), receiver = topic3,
     referralCode = last data word. Malformed codes (bytes32 mis-parse, e.g.
     arbitrum) filtered to < 1e9; code 0 is a legit "untagged via PSM3" value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

from .. import events, hypersync
from . import template_ab

DEFAULT_END = template_ab.DEFAULT_END
MAX_VALID_REF = 1_000_000_000  # referralCode >= 1e9 is a bytes32 mis-parse; drop


@dataclass(frozen=True)
class PSMTarget:
    blockchain: str
    symbol: str
    address: str       # L2 sUSDS token (balance + _leg contract_address)
    psm3_addr: str     # PSM3 contract emitting Swap (ref_code source)
    decimals: int
    start_date: date


# queries/twa_susds_psm3_l2.sql token_targets
BASE = PSMTarget("base", "sUSDS", "0x5875eee11cf8398102fdad704c9e96607675467a",
                 "0x1601843c5e9bc251a3272907010afa41fa18347e", 18, date(2024, 9, 1))
ARB = PSMTarget("arbitrum", "sUSDS", "0xddb46999f8891663a8f2828d25298f70416d7610",
                "0x2b05f8e1cacc6974fd79a673a341fe1f58d27266", 18, date(2024, 9, 1))
OPT = PSMTarget("optimism", "sUSDS", "0xb5b2dc7fd34c249f4be7fb1fcea07950784229e0",
                "0xe0f9978b907853f354d79188a3defbd41978af62", 18, date(2024, 9, 1))
UNI = PSMTarget("unichain", "sUSDS", "0xa06b10db9f390990364a3984c04fadf1c13691b5",
                "0x7b42ed932f26509465f7ce3faf76ffce1275312f", 18, date(2024, 9, 1))
ALL = [BASE, ARB, OPT, UNI]


def _end_ts(end_date: date) -> int:
    eff = min(end_date, DEFAULT_END)
    return int(datetime(eff.year, eff.month, eff.day, tzinfo=timezone.utc).timestamp())


def latest_referral_from_swaps(t: PSMTarget, swap_rows) -> dict[tuple[str, str], tuple[int, int]]:
    """PSM3 Swap events -> latest ref_code per (tx_hash, receiver) by log_index.

    Only swaps whose assetOut is this target's sUSDS token count; referralCode
    is the last data word, filtered to < 1e9.
    """
    token = t.address.lower()
    latest: dict[tuple[str, str], tuple[int, int]] = {}
    for r in swap_rows:
        if r.transaction_hash is None:
            continue
        if events.topic_to_addr(r.topic2) != token:   # assetOut must be sUSDS
            continue
        code = events.swap_referral_code(r.data)
        if code >= MAX_VALID_REF:
            continue
        receiver = events.topic_to_addr(r.topic3)
        key = (r.transaction_hash, receiver)
        prev = latest.get(key)
        if prev is None or r.log_index > prev[0]:
            latest[key] = (r.log_index, code)
    return latest


def legs_from_rows(t: PSMTarget, swap_rows, tr_rows, end_ts: int) -> pd.DataFrame:
    """Pure: raw PSM3 Swap + sUSDS Transfer ``LogRow``s -> balance-change legs."""
    return template_ab.transfer_legs(t, tr_rows, latest_referral_from_swaps(t, swap_rows), end_ts)


def fetch_target_rows(t: PSMTarget, end_ts: int):
    """Fetch raw Swap (from PSM3) + Transfer (from token) LogRows over the window.

    Mirrors template_ab.fetch_target_rows; returns (swap_rows, transfer_rows).
    """
    start_ts = int(datetime(t.start_date.year, t.start_date.month, t.start_date.day,
                            tzinfo=timezone.utc).timestamp())
    try:
        from_block = hypersync.find_block_at_or_before(t.blockchain, start_ts)
    except hypersync.HyperSyncError:
        from_block = 0
    to_block = hypersync.find_block_at_or_before(t.blockchain, end_ts - 1)
    # assetOut is the INDEXED topic2 — filter server-side to only sUSDS-producing
    # swaps (PSM3 emits a Swap for every asset pair; the sUSDS-out ones are a
    # small fraction, so this cuts the fetch by ~30x on high-volume chains).
    swap_rows = hypersync.query_logs(
        t.blockchain,
        [{"address": [t.psm3_addr.lower()],
          "topics": [[events.SWAP_TOPIC0], [], [events.addr_to_topic(t.address)]]}],
        from_block, to_block,
    ).rows
    tr_rows = hypersync.query_logs(
        t.blockchain, [{"address": [t.address.lower()], "topics": [[events.TRANSFER_TOPIC0]]}],
        from_block, to_block,
    ).rows
    return swap_rows, tr_rows


def build_legs(
    targets: list[PSMTarget], *, end_date: date = DEFAULT_END,
    excluded: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    end_ts = _end_ts(end_date)
    frames = []
    for t in targets:
        swap_rows, tr_rows = fetch_target_rows(t, end_ts)
        legs = legs_from_rows(t, swap_rows, tr_rows, end_ts)
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
