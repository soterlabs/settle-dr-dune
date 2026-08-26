"""Custody perimeters — named lending-venue positions counted as still-held.

Motivating case (docs/osero-looping-vault.md): Osero's gtSkyLooping strategy
mints sUSDS with Referral code 3009 and immediately supplies it to Morpho
Blue as collateral. Morpho is (correctly) in ``TEMPLATE_A_EXCLUDED`` — its
pooled balance can never be attributed — so without this extension the
collateral vanishes from attribution and the strategy's tagged balance is a
wallet residue of a few dollars.

A ``CustodyPerimeter`` declares that ONE strategy address's NAMED Morpho
position (``onBehalf == strategy``, enumerated across ALL sUSDS-collateral
markets — pinning a market id went stale within days on-chain) still belongs
to the strategy. Implementation is by construction, with no transfer/event
matching:

  * the strategy's ordinary Transfer legs are untouched (that is W, the
    wallet balance — the standard pipeline already produces them);
  * every Morpho position event adds a C-leg for the strategy:
    ``+assets`` on SupplyCollateral, ``-assets`` on WithdrawCollateral,
    ``-seizedAssets`` on Liquidate;
  * tracked balance = W + C. A supply's ``-W`` transfer leg and its ``+C``
    event leg share a block, so they cancel at zero duration inside the TWA —
    a relocation. An UNMATCHED transfer to Morpho has no C-leg and remains a
    disposal (spec §6.4). Withdrawals to another address and external
    liquidations only hit C — disposals. A self-liquidation's ``-C`` cancels
    against the returning ``+W`` transfer.

The Referral tag rides the normal (tx, owner) attachment: the supply tx
contains the strategy's own Referral event, so C-legs pick up the code and
last-referral-wins forward-fill does the rest.

Divergence from the spec's conservative ``A = min(R, H)`` cap: our engine is
last-referral-wins whole-balance, so a third-party supply onBehalf of the
strategy would inherit the tag (the spec credits custody but not the tag).
None observed on-chain; a WARNING canary fires if one ever appears.

Adding a perimeter is an ops decision (it turns unattributed collateral into
payable DR) — same sign-off bar as SyntheticProgram / REROUTED_CODES.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from .. import events, hypersync

_LOG = logging.getLogger(__name__)

MORPHO_BLUE = "0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb"
# Morpho Blue was deployed Jan 2024 (block 18,883,124) — CreateMarket lookups
# scan from just below that.
_MORPHO_GENESIS_BLOCK = 18_800_000

# keccak256 of the canonical signatures (verified against live Morpho logs):
SUPPLY_COLLATERAL_TOPIC0 = "0xa3b9472a1399e17e123f3c2e6586c23e504184d504de59cdaa2b375e880c6184"
WITHDRAW_COLLATERAL_TOPIC0 = "0xe80ebd7cc9223d7382aab2e0d1d6155c65651f83d53c8b9b06901d167e321142"
LIQUIDATE_TOPIC0 = "0xa4946ede45d0c6f06a0f5ce92c9ad3b4751452d2fe0e25010783bcab57a67e41"
# CreateMarket(bytes32 id, (loanToken, collateralToken, oracle, irm, lltv))
CREATE_MARKET_TOPIC0 = "0xac4b2400f169220b0c0afdde7a0b32e775ba727ea1cb30b35f935cdaab8683ac"


@dataclass(frozen=True)
class CustodyPerimeter:
    name: str
    blockchain: str
    token: str        # the tracked token the venue holds as collateral (lower-cased)
    strategy: str     # the position owner (onBehalf / borrower), lower-cased


# Osero (codes 3000-3999) gtSkyLooping — sUSDS looped as Morpho collateral.
# Referral code observed on-chain: 3009. Active since 2026-08-13.
OSERO_GTSKYLOOPING = CustodyPerimeter(
    "osero_gtskylooping", "ethereum",
    "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd",
    "0xea40de595f099ca04695b0ca105499e50af77f92",
)


def market_collateral_tokens(chain: str, market_ids: set[str], to_block: int) -> dict[str, str]:
    """market id -> collateral token, from Morpho CreateMarket events."""
    if not market_ids:
        return {}
    rows = hypersync.query_logs(
        chain,
        [{"address": [MORPHO_BLUE],
          "topics": [[CREATE_MARKET_TOPIC0], sorted(market_ids)]}],
        _MORPHO_GENESIS_BLOCK, to_block,
    ).rows
    out: dict[str, str] = {}
    for r in rows:
        # params tuple in data: loanToken(0), collateralToken(1), oracle, irm, lltv
        h = r.data.removeprefix("0x")
        out[r.topic1] = "0x" + h[64:128][-40:]
    return out


def fetch_position_rows(p: CustodyPerimeter, from_block: int, to_block: int):
    """The strategy's Morpho position events, restricted to markets whose
    collateral token is the perimeter token (a strategy may also use markets
    with other collateral — those must never inject legs into this token)."""
    st = events.addr_to_topic(p.strategy)
    rows = hypersync.query_logs(
        p.blockchain,
        [{"address": [MORPHO_BLUE], "topics": [[SUPPLY_COLLATERAL_TOPIC0], [], [], [st]]},
         {"address": [MORPHO_BLUE], "topics": [[WITHDRAW_COLLATERAL_TOPIC0], [], [st]]},
         {"address": [MORPHO_BLUE], "topics": [[LIQUIDATE_TOPIC0], [], [], [st]]}],
        from_block, to_block,
    ).rows
    coll = market_collateral_tokens(p.blockchain, {r.topic1 for r in rows}, to_block)
    return [r for r in rows if coll.get(r.topic1) == p.token]


def custody_legs(
    t, rows, latest_ref: dict[tuple[str, str], tuple[int, int]], end_ts: int,
    perimeter: CustodyPerimeter,
) -> pd.DataFrame:
    """Morpho position events -> C-legs for the strategy (shared leg schema).

    Ref code attaches by (tx, strategy) exactly like transfer legs, so a
    supply in the same tx as the strategy's Referral event carries the code.
    """
    scale = 10 ** t.decimals
    start_day = t.start_date
    recs: list[dict] = []
    for r in rows:
        if r.block_time >= end_ts:
            continue
        if datetime.fromtimestamp(r.block_time, tz=timezone.utc).date() < start_day:
            continue
        h = r.data.removeprefix("0x")
        if r.topic0 == SUPPLY_COLLATERAL_TOPIC0:
            amount = events.decode_uint(h[0:64]) / scale
            caller = events.topic_to_addr(r.topic2)
            if caller != perimeter.strategy:
                _LOG.warning(
                    "custody[%s]: third-party SupplyCollateral by %s in tx %s — "
                    "credited to custody; under last-referral-wins it inherits the "
                    "strategy's tag (spec would cap it). Review.",
                    perimeter.name, caller, r.transaction_hash)
        elif r.topic0 == WITHDRAW_COLLATERAL_TOPIC0:
            amount = -events.decode_uint(h[64:128]) / scale   # data: (caller, assets)
        elif r.topic0 == LIQUIDATE_TOPIC0:
            amount = -events.decode_uint(h[128:192]) / scale  # seizedAssets = word 2
        else:
            continue
        ref = latest_ref.get((r.transaction_hash, perimeter.strategy))
        recs.append({
            "blockchain": t.blockchain, "contract_address": t.address.lower(),
            "symbol": t.symbol, "user_addr": perimeter.strategy,
            "block": r.block_number, "log_index": r.log_index, "ts": r.block_time,
            "amount_change": amount,
            "ref_code": ref[1] if ref is not None else pd.NA,
        })
    if not recs:
        return pd.DataFrame()
    return pd.DataFrame(recs)
