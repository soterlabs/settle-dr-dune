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
  * scan window: date(ts) >= start_date AND ts < min(end_date, 2026-08-01).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

from .. import events, hypersync
from ..window import DEFAULT_END  # noqa: F401 — canonical home is drhs/window.py;
# re-exported here because every runner/template historically imports it from
# this module.

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Target:
    blockchain: str
    symbol: str
    address: str          # 0x, lower-cased on use
    decimals: int
    start_date: date


# Protocol/vault contracts that hold sUSDS/sUSDC on behalf of other users;
# counting them as depositors double-counts the positions they represent. This
# mirrors the `excluded_addresses` CTE in queries/twa_susds_susdc_erc4626.sql
# (Template A) and is applied to that source's output. Template B (stUSDS) has
# no exclusions. Lower-cased.
TEMPLATE_A_EXCLUDED: frozenset[str] = frozenset({
    "0xbc65ad17c5c0a2a4d159fa5a503f4992c7b545fe",  # sUSDC vault (holds sUSDS for sUSDC depositors)
    "0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb",  # Morpho
    "0xbe3d4ec488a0a042bb86f9176c24f8cd54018ba7",  # Pendle
    "0x00836fe54625be242bcfa286207795405ca4fd10",  # Curve PSM
})


# --- Synthetic aggregator programs (pseudo-referrals) -------------------------
# Aggregators (CowSwap, Paraswap, ...) never emit a user-level Referral event —
# their Referral events, when present, land on the router itself (see
# docs/cowswap-1003-double-attribution.md). Users receive the token as a plain
# Transfer out of the aggregator's delivery contract. A SyntheticProgram turns
# those deliveries into pseudo-referral legs *inside the same attribution
# stream* as real Referral events, so the tag (a) relabels balance instead of
# double counting it, and (b) is terminated by any later attribution signal
# (real code or another program's tag) via the TWA engine's last-wins ffill.
#
# Tagging rule per delivery tx T: wallet W gets `ref_code` iff W received the
# token FROM a program contract in T AND W's net token delta across ALL of T's
# transfers is positive. The net-delta guard drops solvers / routers / the
# settlement itself, which only forward within the tx (audit: the strict
# Deposit.owner signal tags 46/46 intermediary contracts and zero end users).
# A real Referral for the same (tx, wallet) always wins over a pseudo one.
@dataclass(frozen=True)
class SyntheticProgram:
    name: str
    ref_code: int
    contracts: frozenset[str]     # delivery contracts, lower-cased
    # Eligibility window (Atlas requires explicit program start/termination).
    # Deliveries outside [start, end) are not tagged; None = unbounded.
    start: date | None = None
    end: date | None = None

    def active_at(self, ts: int) -> bool:
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        return (self.start is None or d >= self.start) and (self.end is None or d < self.end)


# CowSwap GPv2Settlement — same address on every chain it is deployed to.
# Assumption (verified on ethereum): sUSDS only leaves the settlement contract
# during `settle()`/`swap()` executions, so no separate Trade-event check is
# needed to recognise a settlement tx.
COWSWAP = SyntheticProgram(
    "cowswap", 1003,
    frozenset({"0x9008d19f58aabd9ed0d60971565aa8510560ab41"}),
)


def synthetic_referrals(
    tr_rows, programs: tuple[SyntheticProgram, ...],
) -> dict[tuple[str, str], tuple[int, int]]:
    """Pseudo-referrals from aggregator deliveries: {(tx, wallet): (log_index, code)}.

    ``tr_rows`` is the full Transfer ``LogRow`` set for one target token.
    Same shape as ``latest_referral_from_events`` so the two merge trivially.
    Later deliveries win within a tx (mirrors latest-by-log_index for real
    Referral events).
    """
    if not programs:
        return {}
    by_contract: dict[str, SyntheticProgram] = {}
    for p in programs:
        for a in p.contracts:
            by_contract[a] = p

    # pass 1: deliveries (program contract -> wallet) per tx
    deliveries: dict[str, list[tuple[int, str, SyntheticProgram]]] = {}
    for r in tr_rows:
        frm = events.topic_to_addr(r.topic1)
        p = by_contract.get(frm)
        if p is None or r.transaction_hash is None or not p.active_at(r.block_time):
            continue
        to = events.topic_to_addr(r.topic2)
        if to == events.ZERO_ADDR or to in by_contract:
            continue
        deliveries.setdefault(r.transaction_hash, []).append((r.log_index, to, p))

    if not deliveries:
        return {}

    # pass 2: net token delta per (tx, wallet) over the delivery txs only.
    # Kept in exact int wei: token amounts (1e18+) exceed float64's 2**53
    # integer range, and rounding residues on a perfect forwarder (in == out)
    # can come out positive — which would falsely tag a solver/router.
    # NB mints (0x0 -> wallet) are not deliveries: on-chain audit shows solvers
    # always mint to themselves/intermediaries and the settlement then
    # transfers to the user, so the delivery edge is the reliable signal.
    net: dict[tuple[str, str], int] = {}
    mint_rcpts: list[tuple[str, str]] = []   # (tx, wallet) minted-to inside delivery txs
    for r in tr_rows:
        tx = r.transaction_hash
        if tx not in deliveries:
            continue
        amt = events.transfer_value(r.data)
        frm = events.topic_to_addr(r.topic1)
        to = events.topic_to_addr(r.topic2)
        if to != events.ZERO_ADDR:
            net[(tx, to)] = net.get((tx, to), 0) + amt
            if frm == events.ZERO_ADDR:
                mint_rcpts.append((tx, to))
        if frm != events.ZERO_ADDR:
            net[(tx, frm)] = net.get((tx, frm), 0) - amt

    out: dict[tuple[str, str], tuple[int, int]] = {}
    for tx, rows in deliveries.items():
        for log_index, wallet, p in rows:
            if net.get((tx, wallet), 0) <= 0:
                continue  # forwarder (solver/router/hop) — not the final holder
            key = (tx, wallet)
            prev = out.get(key)
            if prev is None or log_index > prev[0]:
                out[key] = (log_index, p.ref_code)

    # Mint-path canary: delivery-based tagging cannot see a solver minting the
    # token STRAIGHT to the end user (0x0 -> user, no program-contract
    # transfer). History audit (ethereum, Sep 2024 - Jun 2026): every
    # net-positive mint recipient inside delivery txs was an intermediary
    # contract retaining dust/inventory residue (25 events, 6 wallets, 0 end
    # users; max kept 126 sUSDS) — the final holders were still tagged via the
    # delivery edge. Warn only above a 1-token retention floor so the log
    # stays signal: an end user keeping a minted position would clear it.
    _CANARY_DUST_WEI = 10 ** 18  # 1 token; programs are 18-dec sUSDS-scoped
    missed = [(tx, w) for tx, w in mint_rcpts
              if w not in by_contract and net.get((tx, w), 0) > _CANARY_DUST_WEI
              and (tx, w) not in out]
    if missed:
        _LOG.warning(
            "synthetic_referrals: %d net-positive mint recipient(s) inside "
            "delivery txs were NOT tagged (mint-path gap, see "
            "docs/cowswap-1003-double-attribution.md) — sample: %s",
            len(missed), [f"{tx}:{w}" for tx, w in missed[:3]],
        )
    return out


def merge_referrals(
    real: dict[tuple[str, str], tuple[int, int]],
    pseudo: dict[tuple[str, str], tuple[int, int]],
) -> dict[tuple[str, str], tuple[int, int]]:
    """Real Referral events always beat pseudo-referrals for the same (tx, user)."""
    merged = dict(pseudo)
    merged.update(real)
    return merged


# --- Re-routed referral codes (aggregators that DO emit Referral events) ------
# Some aggregators deposit into the vault themselves and pass their partner
# code, so a real Referral event fires — but its `owner` is the router/executor
# that received the minted shares, not the end user (on-chain: all 926
# Referral(1004) events land on Paraswap routers; all 426 Referral(4011)
# events on 1inch executors). The router forwards the token to the user in the
# same tx, so in the plain stream the code sticks to a net-zero intermediary
# and the user stays untagged.
#
# For the allowlisted codes below, the code is RE-ROUTED: when a Referral for
# such a code lands on owner O in tx T and O is a net-zero/negative forwarder
# in T, the code is re-attached to the net-positive recipients of transfers
# FROM O in T. If O is itself net-positive (a partner vault holding for its
# users — e.g. Yearn's 1007 vaults retain 16.8M sUSDS), nothing is re-routed:
# the vault keeps its own attribution.
#
# This is the corrected version of the removed `referral_per_tx_fallback` CTE
# (see twa_susds_susdc_erc4626.sql): anchored to the emitting intermediary and
# its delivery edge, instead of re-tagging by (tx, contract) alone. Being
# anchored to real events, it needs no router address registry and survives
# router redeployments. Allowlisted per partner because re-routing shifts DR
# attribution (a payout-policy decision, not a default).
REROUTED_CODES: frozenset[int] = frozenset({
    1004,   # Paraswap (Augustus v5/v6.x, Delta)
    4011,   # 1inch (AggregationRouter v6 / Fusion executors)
})


# An address must own an allowlisted code in at least this many events across
# the scan window to be treated as an intermediary. An END USER can own such a
# code too (an aggregator passing receiver=user makes the Referral land on the
# user) — if that user forwards the tokens within the same tx, re-routing
# would misfire onto their transfer recipient (e.g. a pooled contract).
# Routers/executors emit the code hundreds of times; users once or twice
# (observed 4011: three owners with <3 events). Because tagging replays from
# genesis every run, a new router crosses the threshold retroactively.
MIN_INTERMEDIARY_EVENTS = 3


def rerouted_referrals(
    ref_rows, tr_rows, codes: frozenset[int],
    min_owner_events: int = MIN_INTERMEDIARY_EVENTS,
) -> dict[tuple[str, str], tuple[int, int]]:
    """Re-route intermediary-owned referral codes to the end recipients.

    Returns {(tx, wallet): (log_index, code)} in the shape of
    ``latest_referral_from_events``. Later deliveries win within a tx.
    """
    if not codes:
        return {}
    # 0. how often does each address own an allowlisted code? (intermediary test)
    owner_events: dict[str, int] = {}
    for r in ref_rows:
        if events.referral_code_from_topic(r.topic1) in codes and r.transaction_hash is not None:
            owner = events.topic_to_addr(r.topic2)
            owner_events[owner] = owner_events.get(owner, 0) + 1

    # 1. allowlisted referral events per tx: owner -> latest code by log_index
    owner_code: dict[str, dict[str, tuple[int, int]]] = {}   # tx -> owner -> (li, code)
    for r in ref_rows:
        code = events.referral_code_from_topic(r.topic1)
        if code not in codes or r.transaction_hash is None:
            continue
        owner = events.topic_to_addr(r.topic2)
        if owner_events.get(owner, 0) < min_owner_events:
            continue  # likely an end user owning the code, not a router
        per_tx = owner_code.setdefault(r.transaction_hash, {})
        prev = per_tx.get(owner)
        if prev is None or r.log_index > prev[0]:
            per_tx[owner] = (r.log_index, code)
    if not owner_code:
        return {}

    # 2. net deltas + deliveries out of the referral owners, over those txs
    net: dict[tuple[str, str], int] = {}
    deliveries: dict[str, list[tuple[int, str, str]]] = {}   # tx -> [(li, from_owner, to)]
    for r in tr_rows:
        tx = r.transaction_hash
        owners = owner_code.get(tx)
        if owners is None:
            continue
        amt = events.transfer_value(r.data)
        frm = events.topic_to_addr(r.topic1)
        to = events.topic_to_addr(r.topic2)
        if to != events.ZERO_ADDR:
            net[(tx, to)] = net.get((tx, to), 0) + amt
        if frm != events.ZERO_ADDR:
            net[(tx, frm)] = net.get((tx, frm), 0) - amt
        if frm in owners and to != events.ZERO_ADDR:
            deliveries.setdefault(tx, []).append((r.log_index, frm, to))

    # 3. re-route forwarder-owned codes to net-positive delivery recipients
    out: dict[tuple[str, str], tuple[int, int]] = {}
    for tx, rows in deliveries.items():
        owners = owner_code[tx]
        for log_index, owner, wallet in rows:
            if net.get((tx, owner), 0) > 0:
                continue  # owner retains (partner vault) — keep its attribution
            if wallet in owners or net.get((tx, wallet), 0) <= 0:
                continue  # hop to another intermediary / forwarder
            code = owners[owner][1]
            key = (tx, wallet)
            prev = out.get(key)
            if prev is None or log_index > prev[0]:
                out[key] = (log_index, code)
    return out


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

# Template E: Spark sp* vaults — structurally identical to Template A (ERC4626
# Transfer + 4-arg Referral by (tx, owner); SparkVault shares the ERC4626
# Referral topic0). No address exclusions. Decimals: sp{USDC,USDT,PYUSD}=6,
# spETH=18 (verified via tokens.erc20). spUSDC shares one address across
# ethereum + avalanche_c. Mirrors queries/twa_sp_vaults.sql.
SP_USDC_ETH = Target("ethereum", "spUSDC", "0x28b3a8fb53b741a8fd78c0fb9a6b2393d896a43d", 6, date(2024, 9, 1))
SP_USDC_AVAX = Target("avalanche_c", "spUSDC", "0x28b3a8fb53b741a8fd78c0fb9a6b2393d896a43d", 6, date(2024, 9, 1))
SP_USDT_ETH = Target("ethereum", "spUSDT", "0xe2e7a17dff93280dec073c995595155283e3c372", 6, date(2024, 9, 1))
SP_PYUSD_ETH = Target("ethereum", "spPYUSD", "0x80128dbb9f07b93dde62a6daeadb69ed14a7d354", 6, date(2024, 9, 1))
SP_ETH_ETH = Target("ethereum", "spETH", "0xfe6eb3b609a7c8352a241f7f3a21cea4e9209b8f", 18, date(2024, 9, 1))
TEMPLATE_E = [SP_USDC_ETH, SP_USDC_AVAX, SP_USDT_ETH, SP_PYUSD_ETH, SP_ETH_ETH]


def _end_ts(end_date: date) -> int:
    eff = min(end_date, DEFAULT_END)
    return int(datetime(eff.year, eff.month, eff.day, tzinfo=timezone.utc).timestamp())


def build_legs(
    targets: list[Target], *, end_date: date = DEFAULT_END,
    excluded: frozenset[str] = frozenset(),
    synthetic: tuple[SyntheticProgram, ...] = (),
    reroute: frozenset[int] = frozenset(),
    custody: tuple = (),
) -> pd.DataFrame:
    """Balance-change legs for ``targets``.

    ``excluded`` addresses are dropped as users (their own legs are removed).
    Because each excluded contract appears only as itself in the output, this
    is exactly equivalent to the SQL's final
    ``user_addr not in (select addr from excluded_addresses)`` filter — other
    users' legs (e.g. a transfer *from* an excluded contract *to* a real user)
    are untouched.

    ``synthetic`` programs add pseudo-referral tags for aggregator deliveries
    (see ``SyntheticProgram``); ``reroute`` re-attaches intermediary-owned
    referral codes to end recipients (see ``REROUTED_CODES``); ``custody``
    perimeters count a strategy's named Morpho position as still-held (see
    ``drhs.sources.custody``). All applied to every matching target.
    """
    frames = [_legs_for_target(t, _end_ts(end_date), synthetic, reroute, custody)
              for t in targets]
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


def fetch_target_rows(t: Target, end_ts: int):
    """Fetch the raw Referral + Transfer ``LogRow``s for ``t`` over the scan
    window. Split from the pure leg logic so fixtures can capture these rows
    and tests can replay ``legs_from_rows`` offline."""
    addr = t.address.lower()
    start_ts = int(datetime(t.start_date.year, t.start_date.month, t.start_date.day,
                            tzinfo=timezone.utc).timestamp())
    try:
        from_block = hypersync.find_block_at_or_before(t.blockchain, start_ts)
    except hypersync.HyperSyncError:
        # start_date (hardcoded 2024-09-01) can precede an L2's genesis
        # (e.g. unichain). Scan from genesis — matches Dune, which simply finds
        # no events before the chain existed.
        from_block = 0
    to_block = hypersync.find_block_at_or_before(t.blockchain, end_ts - 1)
    ref_rows = hypersync.query_logs(
        t.blockchain, [{"address": [addr], "topics": [[events.REFERRAL_TOPIC0]]}],
        from_block, to_block,
    ).rows
    tr_rows = hypersync.query_logs(
        t.blockchain, [{"address": [addr], "topics": [[events.TRANSFER_TOPIC0]]}],
        from_block, to_block,
    ).rows
    return ref_rows, tr_rows


def latest_referral_from_events(ref_rows) -> dict[tuple[str, str], tuple[int, int]]:
    """Referral events -> latest ref_code per (tx_hash, owner) by log_index.

    Owner is the indexed topic2; ref_code the indexed uint16 topic1. Returns
    {(tx, owner_addr): (log_index, code)}.
    """
    latest_ref: dict[tuple[str, str], tuple[int, int]] = {}
    for r in ref_rows:
        if r.transaction_hash is None:
            continue
        owner = events.topic_to_addr(r.topic2)
        code = events.referral_code_from_topic(r.topic1)
        key = (r.transaction_hash, owner)
        prev = latest_ref.get(key)
        if prev is None or r.log_index > prev[0]:
            latest_ref[key] = (r.log_index, code)
    return latest_ref


def transfer_legs(
    t: Target, tr_rows, latest_ref: dict[tuple[str, str], tuple[int, int]], end_ts: int,
) -> pd.DataFrame:
    """Transfer ``LogRow``s + a (tx, user)->ref map -> balance-change legs.

    Shared by Template A/B (ref from Referral events) and Template C (ref from
    PSM3 Swap events): +to / -from legs (zero address never tracked), decimal-
    scaled, ref attached by (tx, user); scan window date(ts) >= start_date AND
    ts < end_ts.
    """
    scale = 10 ** t.decimals
    start_day = t.start_date
    # Column-wise accumulation: a dict per leg costs ~10x the payload in
    # object overhead (3.09M legs on psm3_base ≈ 2-3GB of dicts alone — the
    # OOM driver on the 3.7GB box). Semantics identical to the previous
    # list-of-dicts build; ref_code keeps ints + pd.NA in an object column.
    users: list[str] = []
    blocks: list[int] = []
    idxs: list[int] = []
    tss: list[int] = []
    amts: list[float] = []
    refs: list = []

    def _add(user: str, r, amount: float, ref) -> None:
        users.append(user)
        blocks.append(r.block_number)
        idxs.append(r.log_index)
        tss.append(r.block_time)
        amts.append(amount)
        refs.append(ref[1] if ref is not None else pd.NA)

    for r in tr_rows:
        if r.block_time >= end_ts:
            continue
        if datetime.fromtimestamp(r.block_time, tz=timezone.utc).date() < start_day:
            continue
        frm = events.topic_to_addr(r.topic1)
        to = events.topic_to_addr(r.topic2)
        amt = events.transfer_value(r.data) / scale
        tx = r.transaction_hash
        if to != events.ZERO_ADDR:
            _add(to, r, amt, latest_ref.get((tx, to)))
        if frm != events.ZERO_ADDR:
            _add(frm, r, -amt, latest_ref.get((tx, frm)))
    if not users:
        return pd.DataFrame()
    return pd.DataFrame({
        "blockchain": t.blockchain,
        "contract_address": t.address.lower(),
        "symbol": t.symbol,
        "user_addr": users,
        "block": blocks,
        "log_index": idxs,
        "ts": tss,
        "amount_change": amts,
        "ref_code": pd.Series(refs, dtype=object),
    })


def legs_from_rows(
    t: Target, ref_rows, tr_rows, end_ts: int,
    synthetic: tuple[SyntheticProgram, ...] = (),
    reroute: frozenset[int] = frozenset(),
    custody_rows: list = (),
) -> pd.DataFrame:
    """Pure: raw Referral + Transfer ``LogRow``s -> balance-change legs (A/B).

    ``synthetic`` programs contribute delivery pseudo-referrals; ``reroute``
    re-attaches intermediary-owned codes to end recipients. Precedence for the
    same (tx, user): real Referral > re-routed code > delivery pseudo-tag.
    ``custody_rows`` — [(CustodyPerimeter, Morpho position LogRows)] — appends
    the strategies' C-legs (see drhs.sources.custody; W-legs stay untouched).
    """
    latest = latest_referral_from_events(ref_rows)
    extra: dict[tuple[str, str], tuple[int, int]] = {}
    if synthetic:
        extra.update(synthetic_referrals(tr_rows, synthetic))
    if reroute:
        extra.update(rerouted_referrals(ref_rows, tr_rows, reroute))
    if extra:
        latest = merge_referrals(latest, extra)
    legs = transfer_legs(t, tr_rows, latest, end_ts)
    if custody_rows:
        from . import custody as custody_mod
        frames = [legs] if not legs.empty else []
        for perimeter, rows in custody_rows:
            cl = custody_mod.custody_legs(t, rows, latest, end_ts, perimeter)
            if not cl.empty:
                frames.append(cl)
        if frames:
            legs = pd.concat(frames, ignore_index=True)
    return legs


def _legs_for_target(
    t: Target, end_ts: int,
    synthetic: tuple[SyntheticProgram, ...] = (),
    reroute: frozenset[int] = frozenset(),
    custody: tuple = (),
) -> pd.DataFrame:
    ref_rows, tr_rows = fetch_target_rows(t, end_ts)
    custody_rows = []
    if custody:
        from . import custody as custody_mod
        from_block = hypersync.find_block_at_or_before(
            t.blockchain,
            int(datetime(t.start_date.year, t.start_date.month, t.start_date.day,
                         tzinfo=timezone.utc).timestamp()))
        to_block = hypersync.find_block_at_or_before(t.blockchain, end_ts - 1)
        for p in custody:
            if p.blockchain == t.blockchain and p.token == t.address.lower():
                custody_rows.append((p, custody_mod.fetch_position_rows(p, from_block, to_block)))
    return legs_from_rows(t, ref_rows, tr_rows, end_ts, synthetic, reroute, custody_rows)


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
