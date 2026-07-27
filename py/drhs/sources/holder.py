"""Template F — class-D contract-tagged holders: the FULL token balance of one
contract attributed to a synthetic ref code.

Ports of dr_rewards_monthly_usds_aave.sql (9001, Aave aEthUSDS) and
dr_rewards_monthly_usds_ref4001.sql (4001, Solana OFT bridge), with one
deliberate methodology change (2026-07-27, PR #10): **intraday TWA** through
the shared engine, like every other venue — the Dune queries snapshot the
balance at END OF DAY, which under-counts ~20% on heavy-intraday-flow months
(measured: 9001 $906k -> $999k full history; inside the 2026 payable window
the difference is noise, +$819/-$601). Payments reconcile against this clean
methodology.

Same build_legs(targets, end_date=..., excluded=...) surface as the other
templates so run_source.SPECS and the chunked pipeline treat it uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

from .. import events, hypersync
from .template_ab import DEFAULT_END, _end_ts


@dataclass(frozen=True)
class HolderTarget:
    blockchain: str
    symbol: str            # token symbol carried into the TWA schema
    token: str             # ERC20 whose balance is tracked (lower-cased on use)
    holder: str            # the contract whose full balance gets the code
    ref_code: int          # synthetic code attributed to the holder
    decimals: int
    start_date: date


USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"

# USDS in Aave aEthUSDS — synthetic 9001 (query 7877569 lineage).
AAVE_USDS = HolderTarget(
    "ethereum", "USDS", USDS,
    "0x32a6268f9ba3642dda7892add74f1d34469a4259", 9001, 18, date(2024, 9, 1))
# USDS in the Solana OFT bridge — synthetic 4001 (query 7877570 lineage).
BRIDGE_USDS = HolderTarget(
    "ethereum", "USDS", USDS,
    "0x1e1d42781fc170ef9da004fb735f56f0276d01b8", 4001, 18, date(2024, 9, 1))


def fetch_target_rows(t: HolderTarget, end_ts: int):
    """Transfer ``LogRow``s touching the holder (both directions), deduped.

    The two topic selections overlap on self-transfers; query_logs already
    returns each log once per selection, so dedupe by (block, log_index).
    """
    start_ts = int(datetime(t.start_date.year, t.start_date.month, t.start_date.day,
                            tzinfo=timezone.utc).timestamp())
    from_block = hypersync.find_block_at_or_before(t.blockchain, start_ts)
    to_block = hypersync.find_block_at_or_before(t.blockchain, end_ts - 1)
    ht = events.addr_to_topic(t.holder)
    rows = hypersync.query_logs(t.blockchain, [
        {"address": [t.token.lower()], "topics": [[events.TRANSFER_TOPIC0], [ht]]},
        {"address": [t.token.lower()], "topics": [[events.TRANSFER_TOPIC0], [], [ht]]},
    ], from_block, to_block).rows
    seen: set[tuple[int, int]] = set()
    out = []
    for r in rows:
        key = (r.block_number, r.log_index)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def legs_from_rows(t: HolderTarget, tr_rows, end_ts: int) -> pd.DataFrame:
    """Pure: Transfer ``LogRow``s -> the holder's signed balance-change legs.

    One user only (the holder); every leg carries the synthetic ref_code, so
    the TWA engine attributes the whole balance to it from the first event.
    """
    scale = 10 ** t.decimals
    holder = t.holder.lower()
    recs = []
    for r in tr_rows:
        if r.block_time >= end_ts:
            continue
        frm = events.topic_to_addr(r.topic1)
        to = events.topic_to_addr(r.topic2)
        amt = events.transfer_value(r.data) / scale
        delta = (amt if to == holder else 0.0) - (amt if frm == holder else 0.0)
        if delta == 0.0:
            continue  # neither side, or a self-transfer
        recs.append({
            "blockchain": t.blockchain, "contract_address": t.token.lower(),
            "symbol": t.symbol, "user_addr": holder, "block": r.block_number,
            "log_index": r.log_index, "ts": r.block_time,
            "amount_change": delta, "ref_code": t.ref_code,
        })
    if not recs:
        return pd.DataFrame(columns=[
            "blockchain", "contract_address", "symbol", "user_addr",
            "block", "log_index", "ts", "amount_change", "ref_code",
        ])
    return pd.DataFrame(recs)


def build_legs(
    targets: list[HolderTarget], *, end_date: date = DEFAULT_END,
    excluded: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    end_ts = _end_ts(end_date)
    frames = [legs_from_rows(t, fetch_target_rows(t, end_ts), end_ts) for t in targets]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=[
            "blockchain", "contract_address", "symbol", "user_addr",
            "block", "log_index", "ts", "amount_change", "ref_code",
        ])
    legs = pd.concat(frames, ignore_index=True)
    if excluded:
        legs = legs[~legs["user_addr"].str.lower().isin(excluded)].copy()
    return legs
