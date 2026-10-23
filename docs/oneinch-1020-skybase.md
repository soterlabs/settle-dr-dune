# Skybase code 1020: genuine sUSDS referrals

Code 1020 is attributed only from genuine
`Referral(uint16 indexed referral, address indexed owner, uint256 assets,
uint256 shares)` events emitted by the Ethereum sUSDS contract. Public 1inch
router entrypoints are not partner-specific evidence and are not used for
Skybase attribution.

## On-chain history

The first genuine event was emitted at **2026-08-24 20:58:23 UTC** (Ethereum
block 25,827,582, transaction
`0x03dd97c49ca85a1b0b805ae5dad0546897542986d0f1379bf48a091f29812869`).
Through Ethereum block 26,249,496 (2026-10-22 audit), sUSDS emitted **31**
events across 30 transactions and five owners. The owners received
107,291.3925 sUSDS and retained only 5.6537; **99.9947% was forwarded**.

The outer transaction entered through 1inch AggregationRouter v6 in 15 of the
31 events. The other 16 used different entrypoints; none used the listed v4 or
v5 routers. Conversely, public 1inch-router transactions exist without a
genuine 1020 event. Router membership is therefore neither necessary nor
sufficient evidence for Skybase attribution.

## Forwarding recovery

The ordinary event stream attaches a referral to its event `owner`. For these
transactions that owner is usually a router, executor, or adapter that hands
the shares to the final holder in the same transaction. Code 1020 is therefore
included in `template_ab.REROUTED_CODES` from its on-chain inception:

- one-off emitting adapters are accepted instead of waiting for the generic
  three-event intermediary threshold;
- net-zero forwarding hops are followed transitively;
- three observed forwarders retained 0.02–0.10% execution dust, so a remainder
  of at most 1% of gross incoming shares is still treated as forwarding;
- an owner retaining more than 1% remains an ordinary direct holder and is not
  rerouted.

The reroute only changes which holder owns the emitted code. It does not create
a second balance or reward position. The final holder participates in the same
last-referral-wins balance stream as every other real referral.

## Removal of the synthetic router rule

PR #19 introduced a provisional synthetic rule from 2026-09-01: net-positive
sUSDS/sUSDC recipients in transactions entering three public 1inch routers
were assigned code 1020 even when sUSDS emitted no such event. The PR measured
router activity but did not establish that every transaction belonged to
Skybase. The rule is removed in favour of the contract's explicit referral
signal.

For September 2026, the removed program found 19 router transactions, while
there were 11 genuine 1020 event transactions. Only three genuine-event
transactions entered through the listed routers. The populations were thus
materially different rather than two ways of identifying the same deposits.

## Measured impact

Relative to the production methodology before this change, the combined
effect of removing the synthetic router rule and recovering genuine forwarded
events is:

| Month | Code 1020 delta |
|---|---:|
| August 2026 | +37.842607 |
| September 2026 | +78.881205 |

These are reattributions, not new balances: the deltas across all referral
codes sum to zero in each month. September's positive net change to 1020 means
the recovered genuine-event balances outweigh the removed router-only tags;
the two mechanisms covered materially different holders.

Because this changes historical attribution before the September 1 boundary,
the checkpoint fingerprint intentionally rejects the pre-change checkpoint
set. Rebuild month-end state before using incremental execution for a later
close. This PR does not rewrite committed settlement outputs.
