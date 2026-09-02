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
  * scan window: date(ts) >= start_date AND ts < min(end_date, DEFAULT_END).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

from .. import events, hypersync
from ..window import DEFAULT_END, midnight_ts  # noqa: F401 — canonical home is drhs/window.py;
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
    # Anchored programs (multi-tenant routers such as Li.Fi): only deliveries in
    # these txs count — None = every delivery from ``contracts``. Filled by the
    # program's resolver at fetch time (see drhs.sources.lifi).
    txs: frozenset[str] | None = None

    def active_at(self, ts: int) -> bool:
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        return (self.start is None or d >= self.start) and (self.end is None or d < self.end)


# --- Entrypoint-anchored programs (adding-an-aggregator.md §C) ---------------
# Aggregators with NO fixed delivery contract: the token reaches the user from a
# per-solver executor or straight from a pool, so "received FROM a program
# contract" never fires (1inch on sUSDS-eth: only 435 of 4,025 router txs have
# a router -> user edge). Only the tx entrypoint (``tx.to``) identifies the
# program. Resolves per target — from the Transfer rows already fetched with the
# transaction join, no extra scan — into a SyntheticProgram with ``txs`` set and
# ``contracts`` EMPTY, which ``synthetic_referrals`` reads as "any incoming
# transfer (mints included) to a net-positive wallet inside an anchored tx".
# Known gap: contract-wallet / ERC-4337 users of the frontend have
# ``tx.to = EntryPoint`` and are invisible to this rule (45 such txs observed).
@dataclass(frozen=True)
class EntrypointProgram:
    name: str
    ref_code: int
    entrypoints: frozenset[str]   # router addresses, lower-cased
    start: date | None = None
    end: date | None = None

    def resolve_from_rows(self, tr_rows) -> "SyntheticProgram":
        """Pure core: Transfer rows carrying ``tx_to`` -> the anchored program."""
        txs = frozenset(r.transaction_hash for r in tr_rows
                        if r.tx_to in self.entrypoints and r.transaction_hash)
        return SyntheticProgram(self.name, self.ref_code, frozenset(),
                                start=self.start, end=self.end, txs=txs)

    def resolve(self, target: "Target", from_block: int, to_block: int, end_ts: int
                ) -> "SyntheticProgram":
        """Fetch the target's Transfer rows WITH the transaction join over the
        program's eligibility window only, and resolve. A separate, bounded
        query on purpose: joining the pipeline's full Transfer history would
        re-key its largest cache entries and maintain a second copy forever,
        for rows the window can never tag."""
        if self.start is not None:
            if midnight_ts(self.start) >= end_ts:
                # window entirely after the scan end (e.g. start = next
                # settlement): nothing can be tagged — no query at all
                return self.resolve_from_rows([])
            from_block = max(from_block, hypersync.find_block_at_or_before(
                target.blockchain, midnight_ts(self.start)))
        rows = hypersync.query_logs(
            target.blockchain,
            [{"address": [target.address.lower()], "topics": [[events.TRANSFER_TOPIC0]]}],
            from_block, to_block, with_tx_to=True,
        ).rows
        prog = self.resolve_from_rows(rows)
        _LOG.info("entrypoint[%s] %s: %d Transfer rows in window -> %d anchored txs",
                  self.name, target.blockchain, len(rows), len(prog.txs))
        return prog


# Skybase's 1inch program: swaps entering through the 1inch AggregationRouter
# (v4 / v5 / v6 — the entrypoints ops listed, 2026-09-02). PROVISIONAL start =
# the first unsettled month, so enabling it re-attributes no paid month by
# default; ops picks the eligibility start from the full-history measurement in
# docs/oneinch-1020-skybase.md. NB 1inch's own code 4011 (executor Referral,
# re-routed) keeps precedence in the few txs that carry it (24 of 4,025).
ONEINCH_SKYBASE = EntrypointProgram(
    "oneinch_skybase", 1020,
    frozenset({
        "0x1111111254fb6c44bac0bed2854e76f90643097d",  # AggregationRouter v4
        "0x1111111254eeb25477b68fb85ed929f73a960582",  # AggregationRouter v5
        "0x111111125421ca6dc452d289314280a0f8842a65",  # AggregationRouter v6
    }),
    start=date(2026, 9, 1),
)


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
    by_contract: dict[str, list[SyntheticProgram]] = {}
    by_tx: dict[str, list[SyntheticProgram]] = {}    # entrypoint programs: tx -> programs
    for p in programs:
        for a in p.contracts:
            by_contract.setdefault(a, []).append(p)
        if not p.contracts and p.txs is not None:
            for tx in p.txs:
                by_tx.setdefault(tx, []).append(p)

    # pass 1: deliveries per tx — (program contract -> wallet), or for
    # entrypoint programs ANY incoming transfer (mints included) inside one of
    # the program's txs; the net-delta guard below still drops forwarders.
    deliveries: dict[str, list[tuple[int, str, SyntheticProgram]]] = {}
    for r in tr_rows:
        if r.transaction_hash is None:
            continue
        frm = events.topic_to_addr(r.topic1)
        if frm not in by_contract and r.transaction_hash not in by_tx:
            continue  # nobody's delivery — the common case, skip the rest
        to = events.topic_to_addr(r.topic2)
        if to == events.ZERO_ADDR or to in by_contract:
            continue
        # a contract shared by several programs yields one candidate per
        # program at the same log_index; the LAST program in the tuple wins
        # (pass 3 uses >=), matching the pre-multi-program behaviour.
        cands = list(by_contract.get(frm, ())) + list(by_tx.get(r.transaction_hash, ()))
        for p in cands:
            if not p.active_at(r.block_time):
                continue
            if p.txs is not None and r.transaction_hash not in p.txs:
                continue  # multi-tenant router: not one of this program's txs
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
            if prev is None or log_index >= prev[0]:   # ties: last program wins
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
    """Real Referral events always beat pseudo-referrals for the same (tx, user).

    An override is legitimate but worth seeing: for an anchored program it
    means the router put a partner code on the END USER in a program tx (e.g.
    Li.Fi's own 4012 via a receiver=user deposit), so the program tag lost.
    """
    merged = dict(pseudo)
    merged.update(real)
    overridden = [(k, pseudo[k][1], real[k][1]) for k in pseudo.keys() & real.keys()
                  if pseudo[k][1] != real[k][1]]
    if overridden:
        _LOG.warning(
            "merge_referrals: %d pseudo-referral tag(s) overridden by a real Referral "
            "on the same (tx, user) — sample (tx, user, pseudo->real): %s",
            len(overridden), [(k[0], k[1], f"{a}->{b}") for k, a, b in overridden[:3]])
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
    3006,   # Osero via Jumper Earn (Li.Fi): the deposit adapter 0xe69b860f… (one
            # address on ethereum/base/arbitrum/optimism) mints with 3006 and hands
            # the shares to the LiFiDiamond, which delivers them to the user — a
            # TWO-hop forward, hence the hop-following below. sUSDS-eth + sUSDC L2s.
            # See docs/osero-codes.md.
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

# Codes whose re-route FOLLOWS net-zero forwarders: owner -> hop -> ... -> user.
# The default (1004, 4011) is the one-hop rule the settled months were paid
# under — router -> user directly — and must stay byte-identical. 3006 needs
# two hops (Jumper's deposit adapter -> LiFiDiamond -> user). Opt-in per code
# so enabling it for a new partner can never re-attribute a settled month of
# another.
REROUTE_FOLLOW_HOPS: frozenset[int] = frozenset({3006})


def rerouted_referrals(
    ref_rows, tr_rows, codes: frozenset[int],
    min_owner_events: int = MIN_INTERMEDIARY_EVENTS,
    follow_hops: frozenset[int] = REROUTE_FOLLOW_HOPS,
) -> dict[tuple[str, str], tuple[int, int]]:
    """Re-route intermediary-owned referral codes to the end recipients.

    Returns {(tx, wallet): (log_index, code)} in the shape of
    ``latest_referral_from_events``. Later deliveries win within a tx. Codes in
    ``follow_hops`` chase net-zero forwarders transitively; all others use the
    direct owner -> recipient edge only.
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

    # 2. net deltas + the outgoing-transfer graph, over those txs
    net: dict[tuple[str, str], int] = {}
    outgoing: dict[str, dict[str, list[tuple[int, str]]]] = {}   # tx -> from -> [(li, to)]
    for r in tr_rows:
        tx = r.transaction_hash
        if tx not in owner_code:
            continue
        amt = events.transfer_value(r.data)
        frm = events.topic_to_addr(r.topic1)
        to = events.topic_to_addr(r.topic2)
        if to != events.ZERO_ADDR:
            net[(tx, to)] = net.get((tx, to), 0) + amt
        if frm != events.ZERO_ADDR:
            net[(tx, frm)] = net.get((tx, frm), 0) - amt
            if to != events.ZERO_ADDR:
                outgoing.setdefault(tx, {}).setdefault(frm, []).append((r.log_index, to))

    # 3. re-route forwarder-owned codes to net-positive delivery recipients.
    #    Direct rule (default): recipients of transfers FROM the owner; a
    #    recipient that is itself a forwarder (net <= 0) is a hop and is NOT
    #    tagged. For codes in ``follow_hops`` the hop is followed instead —
    #    its own outgoing transfers are walked until net-positive END
    #    recipients are reached (Jumper 3006: adapter -> LiFiDiamond -> user).
    #    The tag's log_index is the final delivery edge's.
    out: dict[tuple[str, str], tuple[int, int]] = {}
    for tx, owners in owner_code.items():
        edges = outgoing.get(tx)
        if not edges:
            continue
        for owner, (_li, code) in owners.items():
            if net.get((tx, owner), 0) > 0:
                continue  # owner retains (partner vault) — keep its attribution
            chase = code in follow_hops
            seen = {owner}
            # (address, log_index of the edge that reached it): a hop's onward
            # transfers count only if they come AFTER it received the shares —
            # on a shared router (the LiFiDiamond) an unrelated earlier
            # delivery out of the same hop must not inherit the code.
            frontier: list[tuple[str, int]] = [(owner, -1)]
            while frontier:
                nxt: list[tuple[str, int]] = []
                for hop, since in frontier:
                    for log_index, wallet in edges.get(hop, ()):
                        if log_index <= since or wallet in seen or wallet in owners:
                            continue  # before the hop was funded / cycle / another owner
                        if net.get((tx, wallet), 0) <= 0:
                            seen.add(wallet)
                            if chase:
                                nxt.append((wallet, log_index))   # forwarder hop — keep following
                            continue
                        key = (tx, wallet)
                        prev = out.get(key)
                        if prev is None or log_index > prev[0]:
                            out[key] = (log_index, code)
                frontier = nxt
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


def target_block_range(t: Target, end_ts: int) -> tuple[int, int]:
    """[from_block, to_block] of ``t``'s scan window. start_date (hardcoded
    2024-09-01) can precede an L2's genesis (e.g. unichain): scan from genesis
    — matches Dune, which simply finds no events before the chain existed."""
    try:
        from_block = hypersync.find_block_at_or_before(t.blockchain, midnight_ts(t.start_date))
    except hypersync.HyperSyncError:
        from_block = 0
    return from_block, hypersync.find_block_at_or_before(t.blockchain, end_ts - 1)


def fetch_target_rows(t: Target, end_ts: int):
    """Fetch the raw Referral + Transfer ``LogRow``s for ``t`` over the scan
    window. Split from the pure leg logic so fixtures can capture these rows
    and tests can replay ``legs_from_rows`` offline."""
    addr = t.address.lower()
    from_block, to_block = target_block_range(t, end_ts)
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
    synthetic: tuple = (),
    reroute: frozenset[int] = frozenset(),
    custody: tuple = (),
) -> pd.DataFrame:
    ref_rows, tr_rows = fetch_target_rows(t, end_ts)
    # anchored programs (lifi.IntegratorProgram, EntrypointProgram) resolve per
    # target — each fetches its own bounded anchor data — into a concrete
    # SyntheticProgram (tx set + delivery contracts).
    unresolved = [p for p in synthetic if not isinstance(p, SyntheticProgram)]
    custody_rows = []
    if custody or unresolved:
        from_block, to_block = target_block_range(t, end_ts)
    if custody:
        from . import custody as custody_mod
        for p in custody:
            if p.blockchain == t.blockchain and p.token == t.address.lower():
                custody_rows.append((p, custody_mod.fetch_position_rows(p, from_block, to_block)))
    if unresolved:
        synthetic = tuple(p if isinstance(p, SyntheticProgram)
                          else p.resolve(t, from_block, to_block, end_ts) for p in synthetic)
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
