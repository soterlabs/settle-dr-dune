# Synthetic referral-code registry

The authoritative audited catalog is `py/drhs/synthetic_codes.py`. A code
absent from that registry is expected to come from an on-chain `Referral` or
PSM3 `Swap` event.

“Synthetic” means the numeric code itself was introduced by this pipeline. It
does not mean the attribution lacks on-chain evidence: holder codes measure
on-chain balances, while pseudo-referrals use allowlisted transaction or
delivery evidence. Conversely, `1004`, `3006`, and `4011` are **not** synthetic
codes. They are emitted on-chain and only rerouted from an intermediary to the
final recipient. `1020` is deliberately dual-origin: genuine emitted referrals
are rerouted from 2026-08-24, while the 1inch entrypoint fallback covers
otherwise-untagged eligible transactions from 2026-09-01.

| Code | Name | Mechanism | In Payable view | Meaning |
|---:|---|---|:---:|---|
| -999999 | untagged_sentinel | sentinel | No | Internal marker for balances without a referral |
| 99 | untagged_susds | reclassification | No | Untagged sUSDS bucket |
| 127 | untagged_susdc | reclassification | No | Untagged sUSDC bucket |
| 130 | untagged_spusdt | reclassification | Yes | Untagged balance in Spark's spUSDT product |
| 131 | untagged_spusdc | reclassification | Yes | Untagged balance in Spark's spUSDC product |
| 132 | untagged_sppyusd | reclassification | Yes | Untagged balance in Spark's spPYUSD product |
| 10000 | l2_default_zero | reclassification | No | L2 PSM3 code-0 balance held by an ordinary user |
| 10001 | l2_contract_zero | reclassification | No | L2 PSM3 code-0 balance held by an allowlisted protocol contract |
| 1003 | cowswap | pseudo_referral | Yes | CowSwap delivery inferred from its settlement contract |
| 1020 | skybase_oneinch | pseudo_referral | Yes | Skybase activity from emitted referrals or a 1inch entrypoint fallback |
| 3900 | osero_lifi | pseudo_referral | Yes | Osero frontend delivery inferred from Li.Fi integrator events |
| 1997 | skybase_pendle | holder | Yes | Pendle SY-sUSDS backing attributed to Skybase |
| 1998 | skybase_flagship | holder | Yes | Morpho USDS Flagship position attributed to Skybase |
| 1999 | skybase_risk_capital | holder | Yes | Morpho USDS Risk Capital position attributed to Skybase |
| 4001 | keel_solana_bridge | holder | Yes | USDS held by the Solana OFT bridge |
| 9001 | spark_aave | holder | Yes | USDS held by Aave's aEthUSDS contract |

“In Payable view” refers specifically to this repository's
`Payable by Ref Code` workbook sheet and mirrors
`drhs.revenue.monthly.NON_PAYABLE_CODES`. It is not a universal payout flag.

`settlement-cycle` consumes the workbook's `Soter by Ref Code` sheet—not the
Payable view—and applies beneficiary ownership from its own
`config/dr_ref_codes.yaml`. That separation is intentional. For example, code
`127` is excluded from this repository's Payable view but is currently mapped
to Spark by `settlement-cycle`. This registry neither changes nor overrides
that consumer-side decision.

The registry is intentionally an audit layer rather than a new runtime
dependency. Its tests compare it with every production definition, which keeps
the catalog complete without invalidating existing month-end checkpoints for a
behavior-preserving organizational change.
