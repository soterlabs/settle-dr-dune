"""Template A/B source: balance from ERC20/ERC4626 ``Transfer``, ref_code from a
separate ``Referral(uint16 indexed, address indexed owner, ...)`` event matched
by (tx_hash, owner), forward-filled downstream (last-referral-wins).

Covers stUSDS (Template B, the reference) and sUSDS/sUSDC (Template A) — they
are the same structure per queries/README.md. Emits balance-change **legs** for
``twa.compute_twa``.

Mirrors queries/twa_stusds.sql:
  * +leg for ``to`` (when to != 0x0), -leg for ``from`` (when from != 0x0);
    the zero address is never tracked as a user, but mints/burns still move the
    real counterparty's balance.
  * a leg's ref_code = the referral named for THAT user in the SAME tx (latest
    by log_index), else NA (ffilled in the TWA engine).
  * scan window: date(ts) >= start_date AND ts < min(end_date, 2026-07-01).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

from .. import events, hypersync

# Deployed cutoff: events on/after 2026-07-01 are out of the settled window.
DEFAULT_END = date(2026, 7, 1)


@dataclass(frozen=True)
class Target:
    blockchain: str
    symbol: str
    address: str          # 0x, lower-cased on use
    decimals: int
    start_date: date


# --- Target matrix (queries/README.md §Target matrix) ------------------------
# NB: sUSDC is 18 decimals on EVERY chain (verified via tokens.erc20), not 6.
STUSDS = Target("ethereum", "stUSDS", "0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9", 18, date(2024, 9, 1))

# Template A: sUSDS (eth) + sUSDC (eth + L2s). Same ERC4626 Transfer + Referral
# structure as stUSDS — validated engine, target defs only.
SUSDS_ETH = Target("ethereum", "sUSDS", "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd", 18, date(2024, 9, 1))
SUSDC_ETH = Target("ethereum", "sUSDC", "0xbc65ad17c5c0a2a4d159fa5a503f4992c7b545fe", 18, date(2024, 9, 1))
SUSDC_BASE = Target("base", "sUSDC", "0x3128a0f7f0ea68e7b7c9b00afa7e41045828e858", 18, date(2024, 9, 1))
SUSDC_ARB = Target("arbitrum", "sUSDC", "0x940098b108fb7d0a7e374f6eded7760787464609", 18, date(2024, 9, 1))
SUSDC_OPT = Target("optimism", "sUSDC", "0xcf9326e24ebffbef22ce1050007a43a3c0b6db55", 18, date(2024, 9, 1))
SUSDC_UNI = Target("unichain", "sUSDC", "0x14d9143becc348920b68d123687045db49a016c6", 18, date(2024, 9, 1))

TEMPLATE_A_SUSDC = [SUSDC_ETH, SUSDC_BASE, SUSDC_ARB, SUSDC_OPT, SUSDC_UNI]


def _end_ts(end_date: date) -> int:
    eff = min(end_date, DEFAULT_END)
    return int(datetime(eff.year, eff.month, eff.day, tzinfo=timezone.utc).timestamp())


def build_legs(targets: list[Target], *, end_date: date = DEFAULT_END) -> pd.DataFrame:
    frames = [_legs_for_target(t, _end_ts(end_date)) for t in targets]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=[
            "blockchain", "contract_address", "symbol", "user_addr",
            "block", "log_index", "ts", "amount_change", "ref_code",
        ])
    return pd.concat(frames, ignore_index=True)


def _legs_for_target(t: Target, end_ts: int) -> pd.DataFrame:
    addr = t.address.lower()
    start_ts = int(datetime(t.start_date.year, t.start_date.month, t.start_date.day,
                            tzinfo=timezone.utc).timestamp())
    from_block = hypersync.find_block_at_or_before(t.blockchain, start_ts)
    # highest block strictly before the exclusive end
    to_block = hypersync.find_block_at_or_before(t.blockchain, end_ts - 1)

    # 1) Referral events -> latest per (tx_hash, owner) by log_index.
    ref_res = hypersync.query_logs(
        t.blockchain,
        [{"address": [addr], "topics": [[events.REFERRAL_TOPIC0]]}],
        from_block, to_block,
    )
    latest_ref: dict[tuple[str, str], tuple[int, int]] = {}  # (tx,user)->(log_index, code)
    for r in ref_res.rows:
        if r.transaction_hash is None:
            continue
        owner = events.topic_to_addr(r.topic2)
        code = events.referral_code_from_topic(r.topic1)
        key = (r.transaction_hash, owner)
        prev = latest_ref.get(key)
        if prev is None or r.log_index > prev[0]:
            latest_ref[key] = (r.log_index, code)

    # 2) Transfer events -> +to / -from legs, decimal-scaled, ref by (tx, user).
    tr_res = hypersync.query_logs(
        t.blockchain,
        [{"address": [addr], "topics": [[events.TRANSFER_TOPIC0]]}],
        from_block, to_block,
    )
    scale = 10 ** t.decimals
    start_day = t.start_date
    recs: list[dict] = []
    for r in tr_res.rows:
        if r.block_time >= end_ts:
            continue
        if datetime.fromtimestamp(r.block_time, tz=timezone.utc).date() < start_day:
            continue
        frm = events.topic_to_addr(r.topic1)
        to = events.topic_to_addr(r.topic2)
        value = events.transfer_value(r.data)
        amt = value / scale
        tx = r.transaction_hash
        if to != events.ZERO_ADDR:
            ref = latest_ref.get((tx, to))
            recs.append(_leg(t, to, r, amt, ref))
        if frm != events.ZERO_ADDR:
            ref = latest_ref.get((tx, frm))
            recs.append(_leg(t, frm, r, -amt, ref))

    if not recs:
        return pd.DataFrame()
    return pd.DataFrame(recs)


def _leg(t: Target, user: str, r, amount: float, ref: tuple[int, int] | None) -> dict:
    return {
        "blockchain": t.blockchain,
        "contract_address": t.address.lower(),
        "symbol": t.symbol,
        "user_addr": user,
        "block": r.block_number,
        "log_index": r.log_index,
        "ts": r.block_time,
        "amount_change": amount,
        "ref_code": ref[1] if ref is not None else pd.NA,
    }
