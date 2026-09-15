# Skybase: Pendle SY and Morpho idle-USDS attribution

## Scope and codes

Added for the September 2026 settlement review, with historical accrual through
the existing exclusive cutoff **2026-09-01**. The three venues were absent from
the previous reward sources. These synthetic codes are assigned under the
operator's instruction to use the high end of Skybase's 1000–1999 range;
1997–1999 were absent from the repository's existing code/reference datasets.

| Code | Venue | Ethereum contract |
|---|---|---|
| 1997 | Pendle SY-sUSDS | `0xbe3d4ec488a0a042bb86f9176c24f8cd54018ba7` |
| 1998 | sky.money USDS Flagship | `0xe15fcc81118895b67b6647bbd393182df44e11e0` |
| 1999 | sky.money USDS Risk Capital | `0xf42bca228d9bd3e2f8ee65fec3d21de1063882d4` |

Codes are local synthetic attribution, not on-chain Referral events or proof
of governance activation. The monthly calculator rejects these codes on any
other wallet/token/chain. Reserve them in the operational code registry too.

## Pendle

Replay incoming/outgoing **sUSDS Transfer events touching SY**, retaining the
entire pre-window balance history. SY does emit accounting events (including
its ERC20 mint/burn Transfers); those are distinct from Sky Referral marking.
No PT, YT, or LP balances enter the reward base. The existing ordinary-sUSDS
exclusion of this SY remains in place. The wrapper is counted once even when
multiple markets/expiries share it; this is whole-wrapper attribution, not a
single market's TVL.

Every run separately replays SY mint/burn Transfers and compares supply to
sUSDS backing at each block end. Deficits stop the run; surplus backing raises
a warning (e.g. a direct donation). State-read verification is also available.
An upgrade that moves custody outside SY requires a new methodology.

Source: [Pendle sUSDS implementation](https://github.com/pendle-finance/Pendle-SY-Public/blob/main/contracts/core/StandardizedYield/implementations/Sky/PendleSUSDSSY.sol).

## Morpho: actual idle USDS, not lending receivables

At every event block:

```
market_cash = totalSupplyAssets - totalBorrowAssets
vault_market_idle = floor(market_cash * vault_adapter_shares / totalSupplyShares)
vault_eligible = USDS.balanceOf(vault) + sum(vault_market_idle)
```

The denominator includes **all lenders and protocol fee shares**, not just
these two vaults. For a vault with multiple reviewed adapters their internal
shares are combined before apportioning market cash. The formula distributes
real cash pro rata over real outstanding shares (not ERC4626 redeemable-asset
valuation with virtual shares). Integer rounding is down, at most one wei per
vault/market per observation. Borrowed funds and collateral are excluded.

Market state is replayed from creation using Supply, Withdraw, Borrow, Repay,
Liquidate and AccrueInterest. Interest increases both supply and borrow assets
equally, so creates no cash; fee-share issuance dilutes the vaults. Bad debt
decreases both asset totals, so is not treated as cash returned. Repay and
liquidation use Morpho's zero-floor subtraction convention.

Each vault has a **separate Morpho V2 adapter**:

| Vault | Adapter |
|---|---|
| Flagship | `0xf94be39e8863183ff41194b5923627c90a34039d` |
| Risk Capital | `0xaaf8bf4b6e8ccb74b7f5e96d4a27ff967c1eef74` |

Internal adapter shares come from Allocate/Deallocate/BurnShares, not merely
the Blue position's shares: donated or burned claims need not belong to the
vault. All historical AddAdapter events and adapter market events are scanned,
so current allocation lists cannot hide exited markets. New unreviewed
adapters fail explicitly, rather than disappearing from rewards. Each market's
CreateMarket record must identify USDS as its loan token.

Events sharing a block are consolidated before valuing the market position.
Transfers between a vault and its adapter therefore do not accrue rewards
twice. Direct vault idle is reconstructed independently from USDS Transfers.
Adapter wallet residue is not included: this methodology is vault wallet idle
plus the adapter's internally accounted share of market cash.

Sources: [Morpho event definitions](https://github.com/morpho-org/morpho-blue/blob/main/src/libraries/EventsLib.sol),
[adapter accounting](https://github.com/morpho-org/vault-v2/blob/main/src/adapters/MorphoMarketV1AdapterV2.sol).

## Rates, conversion and historical adjustments

These three codes use the supplied memo's **flat 0.2% annual rate / 12**:
daily contributions are `daily_TWA * conversion * 0.002 / (12 * days_in_month)`.
The full calendar month remains the denominator for launches and exits.
No historical Boosted DR or Integration Boost is added. Existing venues keep
their existing rate schedule and daily-accrual convention.

Pendle uses the repository's daily sUSDS-to-USDS conversion (last ERC4626
Deposit/Withdraw rate of each day, forward-filled). This is a daily valuation
approximation, not an exact continuous integral of share balance times price.
Morpho balances are already USDS. All balance durations are intraday weighted.

History is computed from deployment, including pre-2026 Pendle amounts. The
workbook's existing payment eligibility starts in **January 2026**; earlier
accrual remains visible in the full-history token tab, excluded from payable.
The new 2026 amounts are historical additions for the next cycle's true-up
review, not a record of transfers already paid. No payment is executed by this
change, and the frozen Skybase payment reconciliation is not rewritten.

## Run and verify

```
.venv/bin/python py/run_dr_pipeline.py --sources skybase_pendle,skybase_flagship,skybase_risk_capital --out hypersync-results/skybase-dr
.venv/bin/python py/verify_skybase_venues.py --end 2026-09-01
.venv/bin/python py/run_dr_pipeline.py
.venv/bin/python py/build_dr_comparison.py
.venv/bin/python -m pytest py/tests -q
```

The first command fills three new checkpoints. The full runner reuses existing
checkpoints and combines all sources; no old-source recomputation is needed.
The verifier compares exact integer market state, adapter shares and token
balances against independent RPC reads pinned to the same historical block.
Set `ETH_RPC` to override the public Ethereum endpoint for audit reads only.
Production computation remains HyperSync-only.

## Validation and accrual additions (2026-09-15)

- Full offline suite after rebasing onto PR #19: **132 passed**, including the new shared-market,
  outside-lender, fee-dilution, borrow, liquidation, burned-share, wallet
  relocation, referral-collision and SY reconciliation cases.
- At block **25878704** (last block before 2026-09-01), replay equals RPC
  exactly in integer units for **5 market states**, **6 adapter positions**,
  all three wallet balances, and SY total supply. The historical market set
  includes more positions than the current adapter lists.
- SY holds **83,881,484.648480 sUSDS** at that cutoff; Flagship holds
  **32,481,326.925126 USDS** directly; Risk Capital holds zero directly
  (its reward comes from its market-idle share). No sUSDS Referral events
  name SY as owner across the scanned history.
- All previous venue monthly rows retain the settled values from PR #19
  (maximum CSV round-trip difference **2.91e-11 USDS**). Only these
  three codes add rows. Existing workbook checks show no unexpected shifts;
  the existing July/August aggregator-verification limitation remains.
- A second historical audit at **2026-04-01**, block **24781026**, also matches
  exactly: four market states, five adapter positions, all wallet balances,
  and Pendle backing versus SY supply. The generated workbook preserves
  the latest settled baseline and adds only these newly replayed sources.

| Code | Jan–Aug 2026 accrual added (USDS) |
|---|---:|
| 1997 Pendle | 27,740.24 |
| 1998 Flagship | 34,229.17 |
| 1999 Risk Capital | 758.75 |
| Total | 62,728.16 |

The workbook's **Skybase Historical Additions** tab and
`hypersync-results/skybase_historical_additions.csv` provide the per-month
breakdown. Pre-2026 Pendle accrual of 1,155.86 USDS is separately visible and
excluded from the default payable view. These are accrual additions for the
next cycle's reconciliation; independently confirm any payments already made
outside this calculation before using them as transfer amounts.
