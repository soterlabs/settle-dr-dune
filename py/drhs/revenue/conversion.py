"""Share -> asset daily conversion rates — Layer 3b.

Port of conversion_susds.sql (7877548), conversion_stusds.sql (7877549),
conversion_sp_vaults.sql (7877550). Rate = assets/shares at the LAST ERC4626
Deposit/Withdraw event of each day, forward-filled across gap days, defaulting
to 1.0. Entirely event-derived (no RPC, no price feed).

  * susds rate is applied to sUSDS AND sUSDC on all chains (Spark has no
    independent sUSDC rate); keyed on dt only.
  * stusds rate -> stUSDS; keyed on dt only.
  * sp* rate is per (blockchain, token); spETH's USD needs a WETH price in the
    SQL, but spETH DR is zeroed downstream, so the price is never used — we keep
    the event rate only.

assets and shares share the same decimals for every vault here (sUSDS/stUSDS
18/18, sp* 6/6), so the raw assets/shares ratio equals the human ratio the
decimal-adjusted TWA is multiplied by.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd

from .. import events, hypersync
from ..window import LAST_SETTLED_DAY

# Conversion series must reach the last settled day exactly — a shorter series
# makes monthly.series_conv/sp_conv fall back to 1.0 for the missing tail.
END_CAP = LAST_SETTLED_DAY


def _daily_last_rate_series(chain: str, vault: str, start: date, end: date) -> pd.DataFrame:
    """[dt, rate] for one vault: assets/shares of the last Deposit/Withdraw per
    day, forward-filled over [start, end], default 1.0."""
    start_ts = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    # SQL scans evt_block_time <= end_date + 1 day; resolve a block bound past that.
    end_plus = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=2)
    try:
        from_block = hypersync.find_block_at_or_before(chain, start_ts)
    except hypersync.HyperSyncError:
        from_block = 0
    to_block = hypersync.find_block_at_or_before(chain, int(end_plus.timestamp()))
    rows = hypersync.query_logs(
        chain,
        [{"address": [vault.lower()], "topics": [[events.DEPOSIT_TOPIC0, events.WITHDRAW_TOPIC0]]}],
        from_block, to_block,
    ).rows

    # last (by block_time, then log_index) rate per day
    best: dict[date, tuple[int, int, float]] = {}  # dt -> (block_time, log_index, rate)
    for r in rows:
        shares = events.decode_uint(r.data[2 + 64:2 + 128])
        if shares <= 0:
            continue
        assets = events.decode_uint(r.data[2:2 + 64])
        d = datetime.fromtimestamp(r.block_time, tz=timezone.utc).date()
        if d < start or d > end:
            continue
        rate = assets / shares
        cur = best.get(d)
        if cur is None or (r.block_time, r.log_index) > (cur[0], cur[1]):
            best[d] = (r.block_time, r.log_index, rate)

    out = []
    last_rate: float | None = None
    d = start
    one = timedelta(days=1)
    while d <= end:
        if d in best:
            last_rate = best[d][2]
        out.append({"dt": d, "rate": last_rate if last_rate is not None else 1.0})
        d += one
    return pd.DataFrame(out)


def susds_rates(end: date = END_CAP) -> pd.DataFrame:
    """[dt, rate] — sUSDS vault; applied to sUSDS + sUSDC (all chains)."""
    return _daily_last_rate_series(
        "ethereum", "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd",
        date(2024, 9, 4), min(end, END_CAP))


def stusds_rates(end: date = END_CAP) -> pd.DataFrame:
    """[dt, rate] — stUSDS vault."""
    return _daily_last_rate_series(
        "ethereum", "0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9",
        date(2025, 8, 25), min(end, END_CAP))


# sp* vaults: (blockchain, symbol, vault_addr, start_date) — conversion_sp_vaults.sql
_SP_VAULTS = [
    ("ethereum", "spUSDC", "0x28b3a8fb53b741a8fd78c0fb9a6b2393d896a43d", date(2025, 10, 1)),
    ("ethereum", "spUSDT", "0xe2e7a17dff93280dec073c995595155283e3c372", date(2025, 10, 1)),
    ("ethereum", "spETH", "0xfe6eb3b609a7c8352a241f7f3a21cea4e9209b8f", date(2025, 10, 1)),
    ("ethereum", "spPYUSD", "0x80128dbb9f07b93dde62a6daeadb69ed14a7d354", date(2025, 12, 1)),
    ("avalanche_c", "spUSDC", "0x28b3a8fb53b741a8fd78c0fb9a6b2393d896a43d", date(2025, 10, 8)),
]


def sp_vault_rates(end: date = END_CAP) -> pd.DataFrame:
    """[dt, blockchain, token_symbol, contract_address, conversion_rate] for sp* vaults."""
    frames = []
    for chain, symbol, vault, start in _SP_VAULTS:
        f = _daily_last_rate_series(chain, vault, start, min(end, END_CAP))
        f["blockchain"] = chain
        f["token_symbol"] = symbol
        f["contract_address"] = vault
        f = f.rename(columns={"rate": "conversion_rate"})
        frames.append(f)
    return pd.concat(frames, ignore_index=True)
