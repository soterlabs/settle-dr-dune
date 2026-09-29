"""Skybase contract attribution: Pendle backing and Morpho *unborrowed* USDS.

Replay all history, including removed adapters/markets. Morpho V2 adapters'
internal shares (Allocate/Deallocate/BurnShares) exclude donated/lost shares.
Each market's cash is apportioned using ALL outstanding market supply shares,
including fee shares and unrelated lenders. Never use vault totalAssets / debt.
See docs/skybase-pendle-morpho.md for the attribution and rounding convention.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import groupby
import logging

import pandas as pd
from eth_hash.auto import keccak

from .. import events, hypersync
from ..window import DEFAULT_END, midnight_ts
from . import holder
from .custody import MORPHO_BLUE, CREATE_MARKET_TOPIC0

LOG = logging.getLogger(__name__)
GENESIS = 18_800_000  # before Morpho deployment, USDS, SY and both vaults
SUSDS = "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
# Fetch pre-2026 history to reconstruct the opening balances exactly; the
# shared monthly calculator restricts rewards to calendar year 2026.
PENDLE = holder.HolderTarget("ethereum", "sUSDS", SUSDS,
    "0xbe3d4ec488a0a042bb86f9176c24f8cd54018ba7", 1997, 18, date(2024, 9, 1))
FLAGSHIP = holder.HolderTarget("ethereum", "USDS", holder.USDS,
    "0xe15fcc81118895b67b6647bbd393182df44e11e0", 1998, 18, date(2024, 9, 1))
RISK_CAPITAL = holder.HolderTarget("ethereum", "USDS", holder.USDS,
    "0xf42bca228d9bd3e2f8ee65fec3d21de1063882d4", 1999, 18, date(2024, 9, 1))
TARGETS = (PENDLE, FLAGSHIP, RISK_CAPITAL)
BY_CODE = {t.ref_code: t for t in TARGETS}
# Verified parentVault(), asset(), morpho() on 2026-09-15. New adapter types
# require review: do not silently omit them or assume their accounting ABI.
ADAPTERS = {
    "0xf94be39e8863183ff41194b5923627c90a34039d": FLAGSHIP.holder,
    "0xaaf8bf4b6e8ccb74b7f5e96d4a27ff967c1eef74": RISK_CAPITAL.holder,
}

def topic(signature):
    return "0x" + keccak(signature.encode()).hex()

ADD_ADAPTER = topic("AddAdapter(address)")
ALLOCATE = topic("Allocate(bytes32,uint256,uint256)")
DEALLOCATE = topic("Deallocate(bytes32,uint256,uint256)")
BURN = topic("BurnShares(bytes32,uint256)")
SUPPLY = topic("Supply(bytes32,address,address,uint256,uint256)")
WITHDRAW = topic("Withdraw(bytes32,address,address,address,uint256,uint256)")
BORROW = topic("Borrow(bytes32,address,address,address,uint256,uint256)")
REPAY = topic("Repay(bytes32,address,address,uint256,uint256)")
LIQUIDATE = topic("Liquidate(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)")
ACCRUE = topic("AccrueInterest(bytes32,uint256,uint256,uint256)")
MARKET_TOPICS = [SUPPLY, WITHDRAW, BORROW, REPAY, LIQUIDATE, ACCRUE]
ADAPTER_TOPICS = [ALLOCATE, DEALLOCATE, BURN]

def words(row):
    h = row.data.removeprefix("0x")
    if len(h) % 64:
        raise ValueError("malformed event data")
    return [int(h[i:i+64], 16) for i in range(0, len(h), 64)]

def ordered(rows):
    # query selections may overlap; do not replay a log twice.
    return sorted({(r.block_number, r.log_index): r for r in rows}.values(),
                  key=lambda r: (r.block_number, r.log_index))

@dataclass
class MarketState:
    supply: int = 0
    borrow: int = 0
    shares: int = 0

    def apply(self, r):
        w = words(r)
        if r.topic0 == SUPPLY:
            self.supply += w[0]
            self.shares += w[1]
        elif r.topic0 == WITHDRAW:
            self.supply -= w[1]
            self.shares -= w[2]
        elif r.topic0 == BORROW:
            self.borrow += w[1]
        elif r.topic0 == REPAY:
            self.borrow = max(0, self.borrow - w[0])
        elif r.topic0 == LIQUIDATE:
            self.borrow = max(0, self.borrow - w[0])
            self.borrow -= w[3]
            self.supply -= w[3]  # bad debt is not cash returned
        elif r.topic0 == ACCRUE:
            self.supply += w[1]
            self.borrow += w[1]  # interest does not create idle cash
            self.shares += w[2]

    def idle_for(self, shares):
        if min(self.supply, self.borrow, self.shares, shares) < 0:
            raise ValueError("negative Morpho state: incomplete/invalid replay")
        if self.borrow > self.supply or shares > self.shares:
            raise ValueError("Morpho conservation failure")
        return (self.supply - self.borrow) * shares // self.shares if self.shares else 0

def replay_markets(rows, adapters, end_ts):
    """Return (balance-change legs, market states, internal adapter shares).

    Collapse to block end before valuing: callbacks can temporarily change
    market cash before the matching adapter event, all at zero duration.
    """
    states = defaultdict(MarketState)
    positions = defaultdict(int)
    previous = defaultdict(int)
    recs = []
    target_by_vault = {t.holder: t for t in (FLAGSHIP, RISK_CAPITAL)}
    for _, block in groupby(ordered(rows), key=lambda r: r.block_number):
        block = list(block)
        rlast = block[-1]
        if rlast.block_time >= end_ts:
            break
        changed = set()
        for r in block:
            changed.add(r.topic1)
            if r.address.lower() == MORPHO_BLUE:
                states[r.topic1].apply(r)
            elif r.address.lower() in adapters:
                key = (r.address.lower(), r.topic1)
                w = words(r)
                if r.topic0 == ALLOCATE:
                    positions[key] += w[1]
                elif r.topic0 == DEALLOCATE:
                    positions[key] -= w[1]
                elif r.topic0 == BURN:
                    if positions[key] != w[0]:
                        raise ValueError("BurnShares does not match replay")
                    positions[key] = 0
        for market in changed:
            state = states[market]
            per_vault = defaultdict(int)
            for (adapter, mid), shares in positions.items():
                if mid == market:
                    per_vault[adapters[adapter]] += shares
            if sum(per_vault.values()) > state.shares:
                raise ValueError("tracked vaults exceed market supply shares")
            for vault, shares in per_vault.items():
                value = state.idle_for(shares)
                key = (vault, market)
                delta = value - previous[key]
                previous[key] = value
                if delta:
                    t = target_by_vault[vault]
                    recs.append(dict(blockchain=t.blockchain, contract_address=t.token,
                        symbol=t.symbol, user_addr=vault, block=rlast.block_number,
                        log_index=rlast.log_index, ts=rlast.block_time,
                        amount_change=delta / 10**t.decimals, ref_code=t.ref_code))
    return pd.DataFrame(recs), dict(states), dict(positions)

def fetch_morpho(end_ts):
    end_block = hypersync.find_block_at_or_before("ethereum", end_ts - 1)
    additions = hypersync.query_logs("ethereum", [{"address": [FLAGSHIP.holder, RISK_CAPITAL.holder],
        "topics": [[ADD_ADAPTER]]}], GENESIS, end_block).rows
    adapters = {}
    for r in additions:
        if r.block_time >= end_ts:
            continue
        a = events.topic_to_addr(r.topic1)
        if ADAPTERS.get(a) != r.address.lower():
            raise ValueError(f"Unreviewed Skybase adapter {a} for {r.address}")
        adapters[a] = r.address.lower()
    if not adapters:
        return [], {}, end_block
    ars = hypersync.query_logs("ethereum", [{"address": sorted(adapters),
        "topics": [ADAPTER_TOPICS]}], GENESIS, end_block).rows
    markets = sorted({r.topic1 for r in ars if r.block_time < end_ts})
    if not markets:
        return [], adapters, end_block
    created = hypersync.query_logs("ethereum", [{"address": [MORPHO_BLUE],
        "topics": [[CREATE_MARKET_TOPIC0], markets]}], GENESIS, end_block).rows
    tokens = {r.topic1: "0x" + r.data.removeprefix("0x")[:64][-40:] for r in created}
    if any(tokens.get(m) != holder.USDS for m in markets):
        raise ValueError("missing market creation or non-USDS loan asset")
    mrs = hypersync.query_logs("ethereum", [{"address": [MORPHO_BLUE],
        "topics": [MARKET_TOPICS, markets]}], GENESIS, end_block).rows
    return ordered(ars + mrs), adapters, end_block

def check_pendle_backing(transfers, sy_transfers, end_ts):
    """Compare raw sUSDS backing with minted-minus-burned SY at every block.
    Direct donations can cause surplus backing: warn, still measure balanceOf.
    A deficit is a hard failure requiring review of the wrapper model.
    """
    backing = supply = 0
    gaps = 0
    for _, block in groupby(ordered(transfers + sy_transfers), key=lambda r: r.block_number):
        for r in block:
            if r.block_time >= end_ts:
                continue
            frm, to = events.topic_to_addr(r.topic1), events.topic_to_addr(r.topic2)
            amount = events.transfer_value(r.data)
            if r.address.lower() == SUSDS:
                backing += amount * ((to == PENDLE.holder) - (frm == PENDLE.holder))
            else:
                zero = "0x" + "0" * 40
                supply += amount * ((frm == zero) - (to == zero))
        if backing < supply:
            raise ValueError("Pendle SY backing below supply")
        gaps += backing != supply
    if gaps:
        LOG.warning("Pendle backing/supply divergence in %s blocks; final gap=%s wei", gaps, backing-supply)
    return backing, supply, gaps

def build_legs(targets, *, end_date=DEFAULT_END, excluded=frozenset()):
    end_ts = midnight_ts(end_date)
    frames = []
    if PENDLE in targets:
        rows = holder.fetch_target_rows(PENDLE, end_ts)
        end_block = hypersync.find_block_at_or_before("ethereum", end_ts - 1)
        zero = "0x" + "0" * 64
        sy = hypersync.query_logs("ethereum", [
            {"address": [PENDLE.holder], "topics": [[events.TRANSFER_TOPIC0], [zero]]},
            {"address": [PENDLE.holder], "topics": [[events.TRANSFER_TOPIC0], [], [zero]]},
        ], GENESIS, end_block).rows
        check_pendle_backing(rows, sy, end_ts)
        frames.append(holder.legs_from_rows(PENDLE, rows, end_ts))
    vaults = [t for t in targets if t in (FLAGSHIP, RISK_CAPITAL)]
    if vaults:
        rows, adapters, _ = fetch_morpho(end_ts)
        legs, _, _ = replay_markets(rows, adapters, end_ts)
        if not legs.empty:
            frames.append(legs[legs.user_addr.isin([t.holder for t in vaults])])
        for t in vaults:
            frames.append(holder.legs_from_rows(t, holder.fetch_target_rows(t, end_ts), end_ts))
    frames = [f for f in frames if not f.empty]
    if not frames:
        return holder.legs_from_rows(targets[0], [], end_ts)
    return pd.concat(frames, ignore_index=True)
