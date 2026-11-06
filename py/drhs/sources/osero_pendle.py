"""Osero-tagged sUSDS carried through a fixed Pendle Phase-1 perimeter.

Only genuine sUSDS ``Referral`` mints to a reviewed strategy create credit.
The credit follows 1:1 sUSDS -> SY wraps, matched PT/YT, and PT held in the
strategy's named Morpho positions.  Unknown outflows, sales, redemptions to
USDS/DAI and third-party liquidations consume credit; unrelated inflows never
restore it.  This deliberately excludes LPs, limit orders and market-priced
unmatched PT (the memo's optional Phase 2).

The output is *only* the tagged balance that has entered Pendle.  Wallet sUSDS
continues to be handled by Template A.  ``skybase.py`` appends the positive
Osero legs and their exact inverse to synthetic Skybase code 1997, making 1997
the residual SY backing rather than a second claim on the same shares.

The current integration has emitted no qualifying Osero/Pendle transaction.
The reviewed strategy list below comes from the supplied methodology memo;
adding a strategy, router, maturity or Morpho market requires a code review.
Option B (a Referral owned by a router) is intentionally unsupported: the
recommended Option A must name the strategy directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import groupby

import pandas as pd
from eth_hash.auto import keccak

from .. import events, hypersync
from ..window import midnight_ts
from . import custody


def _topic(signature: str) -> str:
    return "0x" + keccak(signature.encode()).hex()


SUSDS = "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
SY = "0xbe3d4ec488a0a042bb86f9176c24f8cd54018ba7"
SKYBASE_PENDLE_CODE = 1997
# Real on-chain Osero code confirmed by the supplied Grove transaction.  Do
# not infer ownership from the numeric 3000-range; settlement-cycle applies
# the same explicit-beneficiary policy.
OSERO_CODES = frozenset({3017})
PT = "0xdc169abe56461a2e0c034da431ac2a3ebf596094"
YT = "0xc7b8551c6b286ce0b44952320e940bd3dee58a09"
EXPIRY_TS = 1_795_651_200  # 2026-11-26 00:00 UTC
GENESIS = 20_800_000       # before sUSDS; required for referred-first inventory

SY_DEPOSIT = _topic("Deposit(address,address,address,uint256,uint256)")
SY_REDEEM = _topic("Redeem(address,address,address,uint256,uint256)")
YT_MINT = _topic("Mint(address,address,address,uint256,uint256)")
YT_BURN = _topic("Burn(address,address,uint256,uint256)")

MORPHO_MARKETS = frozenset({
    "0x22fe4c688161863c73571a2b8db65cc11a4a6bceee4f0dea4aa83e4b784f1bd9",
    "0x9a8dab6de1059e6dc66c34b358764b70effb289e514e237f3161ffb09eaaf24f",
    "0x0367161b7cb23dfd03747dd3815d1c445b0437b648a6d50346249c6e7b92ff1b",
})

# Explicit Phase-1 perimeter from the supplied memo.  A Referral alone cannot
# silently enroll a new strategy in a payable alternative methodology.
STRATEGIES = frozenset({
    "0xe4193315ec09becc08471d418731087dd6f19cd3",
    "0x9544c9e11e0d0d51b4657ac04a81736ecab8b7dd",
    "0xdb82b3b0eef713a4428fac2277882a9a9be1313f",
    "0x6d45a5f761584c8552f398b6d136f633f9e2d41e",
})
EXECUTION_CONTRACTS = frozenset({
    "0xae40ce54b83d4b35989535d194a212fd0d422f3a",  # observed looping router
    "0x888888888889758f76e7103c6cbf23abbf58f946",  # Pendle Router V4
    "0x000000000000c9b3e2c3ec88b1b4c0cd853f4321",  # Pendle Limit Router
})


@dataclass
class State:
    wallet: float = 0.0       # referred sUSDS not yet wrapped (Template A pays it)
    sy: float = 0.0           # tagged SY shares inside the Pendle perimeter
    pair_units: float = 0.0   # total provenance-bearing PT/YT units minted
    pt_units: float = 0.0     # provenance-bearing PT still in reviewed custody
    yt_units: float = 0.0     # provenance-bearing YT still in reviewed custody
    pair_shares: float = 0.0  # originating sUSDS shares for pair_units
    code: int | None = None
    emitted: float = 0.0
    after_expiry: bool = False

    @property
    def pendle(self) -> float:
        # Share-origin accounting is intentionally conservative about new
        # inflows: only shares moved from a proven Referral mint enter here.
        if self.pair_units <= 0:
            pair = 0.0
        else:
            controlled = self.pt_units if self.after_expiry else min(
                self.pt_units, self.yt_units)
            pair = self.pair_shares * controlled / self.pair_units
        return max(0.0, self.sy + pair)

    def dispose_pair(self, pt: float = 0.0, yt: float = 0.0) -> None:
        """Remove each controlled side independently; incoming never restores it."""
        self.pt_units = max(0.0, self.pt_units - max(0.0, pt))
        if not self.after_expiry:
            self.yt_units = max(0.0, self.yt_units - max(0.0, yt))

    def redeem_pair(self, units: float, sy_out: float = 0.0) -> float:
        """Remove a controlled matched claim and return the units redeemed."""
        controlled = self.pt_units if self.after_expiry else min(
            self.pt_units, self.yt_units)
        if units <= 0 or self.pair_units <= 0 or controlled <= 0:
            return 0.0
        used = min(controlled, units)
        shares = self.pair_shares * used / self.pair_units
        self.pair_units -= used
        self.pair_shares -= shares
        self.pt_units -= used
        if not self.after_expiry:
            self.yt_units -= used
        if sy_out > 0:
            # Exact Pendle redemption amount.  Any difference from the
            # originating share credit is rounding/post-expiry disposal.
            self.sy += sy_out * used / units
        return used


def _word(data: str, i: int) -> int:
    h = data.removeprefix("0x")
    return int(h[i * 64:(i + 1) * 64] or "0", 16)


def _addr(topic: str | None) -> str:
    return events.topic_to_addr(topic)


def _ordered(rows):
    return sorted({(r.block_number, r.log_index): r for r in rows}.values(),
                  key=lambda r: (r.block_number, r.log_index))


def _valid_tagged_mints(tx_rows, strategy: str) -> list[tuple[int, float]]:
    """Return ``(code, shares)`` for exact Deposit/Transfer/Referral triplets."""
    token_rows = [r for r in tx_rows if r.address.lower() == SUSDS]
    out = []
    for i, r in enumerate(token_rows):
        if r.topic0 != events.REFERRAL_TOPIC0 or _addr(r.topic2) != strategy:
            continue
        code = events.referral_code_from_topic(r.topic1)
        if code not in OSERO_CODES or i < 2:
            continue
        dep, mint = token_rows[i - 2:i]
        shares = _word(r.data, 1)
        if not (
            dep.topic0 == events.DEPOSIT_TOPIC0
            and _addr(dep.topic2) == strategy
            and _word(dep.data, 1) == shares
            and mint.topic0 == events.TRANSFER_TOPIC0
            and _addr(mint.topic1) == events.ZERO_ADDR
            and _addr(mint.topic2) == strategy
            and events.transfer_value(mint.data) == shares
        ):
            continue
        out.append((code, shares / 1e18))
    return out


def _strategies_in_tx(tx_rows) -> set[str]:
    found = set()
    for r in tx_rows:
        for topic in (r.topic1, r.topic2, r.topic3):
            if topic is None:
                continue
            a = _addr(topic)
            if a in STRATEGIES:
                found.add(a)
    return found


def _matching_transfer(tx_rows, token: str, frm: set[str], to: str,
                       raw: int, used: set[tuple[int, int]]):
    for r in tx_rows:
        key = (r.block_number, r.log_index)
        if key in used or r.address.lower() != token or r.topic0 != events.TRANSFER_TOPIC0:
            continue
        if _addr(r.topic1) in frm and _addr(r.topic2) == to and events.transfer_value(r.data) == raw:
            used.add(key)
            return r
    return None


def replay(rows, end_ts: int, strategies: frozenset[str] = STRATEGIES) -> pd.DataFrame:
    """Pure event replay -> positive Osero Pendle balance-change legs.

    The state is a referred-first tagged inventory, not a fungible-wallet
    last-referral-wins balance.  Recognized transformations move that inventory;
    unrelated inflows are ignored and unrecognized outflows consume it.
    """
    states = {s: State() for s in strategies}
    recs: list[dict] = []
    rows = [r for r in _ordered(rows) if r.block_time < end_ts]
    for _tx, group in groupby(rows, key=lambda r: r.transaction_hash):
        tx_rows = list(group)
        if not tx_rows or tx_rows[0].transaction_hash is None:
            continue
        implicated = _strategies_in_tx(tx_rows)
        for strategy in sorted(implicated & strategies):
            st = states[strategy]
            st.after_expiry = tx_rows[-1].block_time >= EXPIRY_TS
            # A shared router belongs to the account only when this transaction
            # names one reviewed strategy.  Multi-strategy batches get no
            # router custody credit.
            members = {strategy} | (set(EXECUTION_CONTRACTS) if len(implicated) == 1 else set())
            before = st.pendle
            old_code = st.code
            used: set[tuple[int, int]] = set()

            for code, shares in _valid_tagged_mints(tx_rows, strategy):
                st.wallet += shares
                st.code = code

            # sUSDS -> SY (only tokenIn=sUSDS, exactly 1:1 in the reviewed SY).
            for r in tx_rows:
                if r.address.lower() != SY or r.topic0 != SY_DEPOSIT:
                    continue
                caller, receiver, token_in = _addr(r.topic1), _addr(r.topic2), _addr(r.topic3)
                amount_in_raw, sy_out_raw = _word(r.data, 0), _word(r.data, 1)
                amount_in, sy_out = amount_in_raw / 1e18, sy_out_raw / 1e18
                if token_in != SUSDS or caller not in members or receiver not in members:
                    continue
                if _matching_transfer(tx_rows, SUSDS, members, SY, amount_in_raw, used) is None:
                    continue
                tagged = min(st.wallet, amount_in)
                st.wallet -= tagged
                st.sy += tagged * sy_out / amount_in if amount_in else 0.0

            # SY -> matched PT/YT.
            for r in tx_rows:
                if r.address.lower() != YT or r.topic0 != YT_MINT:
                    continue
                caller, recv_pt, recv_yt = _addr(r.topic1), _addr(r.topic2), _addr(r.topic3)
                sy_in_raw, py_out_raw = _word(r.data, 0), _word(r.data, 1)
                sy_in, py_out = sy_in_raw / 1e18, py_out_raw / 1e18
                if caller not in members or recv_pt not in members or recv_yt not in members:
                    continue
                if _matching_transfer(tx_rows, SY, members, YT, sy_in_raw, used) is None:
                    continue
                tagged = min(st.sy, sy_in)
                st.sy -= tagged
                units = py_out * tagged / sy_in if sy_in else 0.0
                st.pair_units += units
                st.pt_units += units
                st.yt_units += units
                st.pair_shares += tagged

            # Matched PT/YT -> SY.
            for r in tx_rows:
                if r.address.lower() != YT or r.topic0 != YT_BURN:
                    continue
                caller, receiver = _addr(r.topic1), _addr(r.topic2)
                py_in_raw, sy_out_raw = _word(r.data, 0), _word(r.data, 1)
                py_in, sy_out = py_in_raw / 1e18, sy_out_raw / 1e18
                if caller not in members or receiver not in members:
                    continue
                pt = _matching_transfer(tx_rows, PT, members, YT, py_in_raw, used)
                yt = True if r.block_time >= EXPIRY_TS else _matching_transfer(
                    tx_rows, YT, members, YT, py_in_raw, used)
                if pt is not None and yt:
                    st.redeem_pair(py_in, sy_out)

            # SY -> sUSDS preserves the tag in the wallet; other redemptions
            # dispose it.  Template A resumes paying any returned wallet shares.
            for r in tx_rows:
                if r.address.lower() != SY or r.topic0 != SY_REDEEM:
                    continue
                caller, receiver, token_out = _addr(r.topic1), _addr(r.topic2), _addr(r.topic3)
                sy_in, amount_out = _word(r.data, 0) / 1e18, _word(r.data, 1) / 1e18
                if caller not in members:
                    continue
                tagged = min(st.sy, sy_in)
                st.sy -= tagged
                if token_out == SUSDS and receiver in members and sy_in:
                    st.wallet += amount_out * tagged / sy_in

            # Named Morpho collateral is custody, not a sale.  A withdrawal to
            # an outside receiver or third-party liquidation consumes PT credit.
            for r in tx_rows:
                if r.address.lower() != custody.MORPHO_BLUE or r.topic1 not in MORPHO_MARKETS:
                    continue
                if r.topic0 == custody.SUPPLY_COLLATERAL_TOPIC0 and _addr(r.topic3) == strategy:
                    amount_raw = _word(r.data, 0)
                    _matching_transfer(tx_rows, PT, members, custody.MORPHO_BLUE,
                                       amount_raw, used)
                elif r.topic0 == custody.WITHDRAW_COLLATERAL_TOPIC0 and _addr(r.topic2) == strategy:
                    # Morpho indexes (id, onBehalf, receiver); caller and
                    # assets are the two data words.
                    receiver = _addr(r.topic3)
                    amount = _word(r.data, 1) / 1e18
                    if receiver not in members:
                        st.dispose_pair(pt=amount)
                elif r.topic0 == custody.LIQUIDATE_TOPIC0 and _addr(r.topic3) == strategy:
                    caller = _addr(r.topic2)
                    seized = _word(r.data, 2) / 1e18
                    if caller not in members:
                        st.dispose_pair(pt=seized)

            # Every remaining outward movement is a disposal.  Internal account
            # moves are ignored; incoming assets never create credit.
            pt_out = yt_out = 0.0
            router_net = {a: {SUSDS: 0.0, SY: 0.0, PT: 0.0, YT: 0.0}
                          for a in EXECUTION_CONTRACTS if a in members}
            for r in tx_rows:
                if r.topic0 != events.TRANSFER_TOPIC0 or r.address.lower() not in (SUSDS, SY, PT, YT):
                    continue
                frm, to = _addr(r.topic1), _addr(r.topic2)
                amount = events.transfer_value(r.data) / 1e18
                for router in router_net:
                    router_net[router][r.address.lower()] += amount * ((to == router) - (frm == router))
                key = (r.block_number, r.log_index)
                if key in used or frm not in members or to in members:
                    continue
                if r.address.lower() == SUSDS:
                    st.wallet = max(0.0, st.wallet - amount)
                elif r.address.lower() == SY:
                    st.sy = max(0.0, st.sy - amount)
                elif r.address.lower() == PT:
                    pt_out += amount
                else:
                    yt_out += amount
            st.dispose_pair(pt=pt_out, yt=yt_out)

            # Execution contracts leave the account at tx end.  Any positive
            # balance they retained is conservatively disposed.
            for net in router_net.values():
                st.wallet = max(0.0, st.wallet - max(0.0, net[SUSDS]))
                st.sy = max(0.0, st.sy - max(0.0, net[SY]))
                p, q = max(0.0, net[PT]), max(0.0, net[YT])
                st.dispose_pair(pt=p, yt=q)

            after = st.pendle
            delta = after - before
            # A zero leg is meaningful if a future reviewed code set contains
            # more than one code: it changes attribution without changing the
            # held amount.
            if abs(delta) > 1e-12 or (after > 0 and st.code != old_code):
                last = tx_rows[-1]
                recs.append({
                    "blockchain": "ethereum", "contract_address": SUSDS,
                    "symbol": "sUSDS", "user_addr": strategy,
                    "block": last.block_number, "log_index": last.log_index,
                    "ts": last.block_time, "amount_change": delta,
                    "ref_code": st.code if st.code is not None else pd.NA,
                })

    columns = ["blockchain", "contract_address", "symbol", "user_addr",
               "block", "log_index", "ts", "amount_change", "ref_code"]
    return pd.DataFrame(recs, columns=columns)


def fetch_rows(end_date: date):
    end_ts = midnight_ts(end_date)
    to_block = hypersync.find_block_at_or_before("ethereum", end_ts - 1)
    strategies = [events.addr_to_topic(s) for s in sorted(STRATEGIES)]
    accounts = [events.addr_to_topic(s) for s in sorted(STRATEGIES | EXECUTION_CONTRACTS)]

    def touching(address: str, counterparties: list[str]):
        # Two indexed filters are dramatically smaller than downloading every
        # Transfer ever emitted by sUSDS/SY/PT/YT. Deduplication in replay()
        # handles self/internal transfers returned by both selections.
        return [
            {"address": [address],
             "topics": [[events.TRANSFER_TOPIC0], counterparties]},
            {"address": [address],
             "topics": [[events.TRANSFER_TOPIC0], [], counterparties]},
        ]

    rows = []
    rows += hypersync.query_logs("ethereum", [
        *touching(SUSDS, accounts + [events.addr_to_topic(SY)]),
        {"address": [SUSDS],
         "topics": [[events.DEPOSIT_TOPIC0, events.REFERRAL_TOPIC0], [], strategies]},
    ], GENESIS, to_block).rows
    sy_parties = accounts + [events.addr_to_topic(YT)]
    rows += hypersync.query_logs("ethereum", [
        *touching(SY, sy_parties),
        {"address": [SY], "topics": [[SY_DEPOSIT, SY_REDEEM], accounts]},
        {"address": [SY], "topics": [[SY_DEPOSIT, SY_REDEEM], [], accounts]},
    ], GENESIS, to_block).rows
    asset_parties = accounts + [events.addr_to_topic(YT),
                                events.addr_to_topic(custody.MORPHO_BLUE)]
    rows += hypersync.query_logs(
        "ethereum", touching(PT, asset_parties), GENESIS, to_block).rows
    rows += hypersync.query_logs("ethereum", [
        *touching(YT, accounts + [events.addr_to_topic(YT)]),
        {"address": [YT], "topics": [[YT_MINT, YT_BURN], accounts]},
    ], GENESIS, to_block).rows
    rows += hypersync.query_logs("ethereum", [
        {"address": [custody.MORPHO_BLUE],
         "topics": [[custody.SUPPLY_COLLATERAL_TOPIC0,
                     custody.LIQUIDATE_TOPIC0], sorted(MORPHO_MARKETS), [], strategies]},
        {"address": [custody.MORPHO_BLUE],
         "topics": [[custody.WITHDRAW_COLLATERAL_TOPIC0],
                    sorted(MORPHO_MARKETS), strategies]},
    ], GENESIS, to_block).rows
    return rows, end_ts


def build_legs(end_date: date) -> pd.DataFrame:
    rows, end_ts = fetch_rows(end_date)
    return replay(rows, end_ts)


def skybase_deductions(osero_legs: pd.DataFrame) -> pd.DataFrame:
    """Exact inverse legs: code 1997 receives only residual SY backing."""
    if osero_legs.empty:
        return osero_legs.copy()
    out = osero_legs.copy()
    out["user_addr"] = SY
    out["ref_code"] = SKYBASE_PENDLE_CODE
    out["amount_change"] = -out["amount_change"]
    return out
