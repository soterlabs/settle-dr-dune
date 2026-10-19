# Osero Yuzu looping account (code 3000): custody-perimeter tracking

**On-chain start:** 2026-10-12. **Tracking impact:** October 2026 onward.

Yuzu's looping account
[`0x015CC48cC8bC37D80AAFf4e43061dbaF94192308`](https://etherscan.io/address/0x015CC48cC8bC37D80AAFf4e43061dbaF94192308)
deposits USDS into sUSDS with Osero referral code **3000**, then supplies the
minted sUSDS to Morpho Blue as collateral. The account itself has no deployed
bytecode at the audit date; it directly initiates the sUSDS deposits and calls
execution helper `0x38b0c12ab81976e9417d4ebfe2a34db6df22e6ad`, while the Morpho position is
held under the account as `onBehalf`.

## Exhaustive Referral census

HyperSync was scanned over the complete indexed Ethereum history through
block `26,227,172` (2026-10-19). Exactly two sUSDS `Referral` events name the
account, both with code 3000:

| UTC | block | USDS assets | sUSDS shares | transaction |
|---|---:|---:|---:|---|
| 2026-10-12 07:28:47 | 26,174,897 | 3,219,998.933068 | 2,894,303.422641323 | [`0x00bd97ce…`](https://etherscan.io/tx/0x00bd97ced72f5ad1538db431ba7387ce004fa2beee8c1f3c0aea5a4238695482) |
| 2026-10-13 10:09:23 | 26,182,876 | 5,320,865.705720 | 4,782,129.170606563 | [`0xf81f4a0d…`](https://etherscan.io/tx/0xf81f4a0dfc5a982dfb0dc03bddfa81f55996c763b344b4b51b84e7be2e06708c) |

Total minted shares are **7,676,432.593247886**. Three subsequent
`SupplyCollateral` events place exactly that amount into two sUSDS-collateral
Morpho markets, with no withdrawals or liquidations:

| UTC | sUSDS shares | market id | transaction |
|---|---:|---|---|
| 2026-10-12 07:32:59 | 2,894,303.422641323 | `0x26b178d4…4d96` | [`0xfcff69b5…`](https://etherscan.io/tx/0xfcff69b578989d3596863b31db005e135ec6dfca78e2b76331ce41cc8426fb1c) |
| 2026-10-13 13:10:11 | 1,900,000.000000000 | `0x26b178d4…4d96` | [`0xdfbe7ff3…`](https://etherscan.io/tx/0xdfbe7ff305e3e79930841417b31e733d70548e824b03a0bcd3e673c241ec28ae) |
| 2026-10-13 13:12:59 | 2,882,129.170606563 | `0x3274643d…cf0b` | [`0x1993ac0d…`](https://etherscan.io/tx/0x1993ac0dd1fdb2c9d7a513a2e9c21a110c9bad75fdb2697d0b53a3630930494f) |

The account's direct sUSDS wallet balance is zero. Event-replayed Morpho
collateral equals minted shares exactly, with zero residual difference.

## Tracking model

This uses the existing `CustodyPerimeter` model already applied to the
Gauntlet `gtSkyLooping` strategy: wallet balance **W** plus the account's named
Morpho collateral **C**. Morpho's pooled token balance remains excluded, so no
other supplier or borrower balance is attributed and the shares are not
double-counted.

The Referral and first supply occur in separate transactions. That is safe:
the ordinary sUSDS mint leg establishes code 3000 for the account, and the
pipeline's last-referral-wins state carries it onto the later Morpho custody
legs. The execution helper is declared as an expected caller; any other
caller supplying on behalf of the account raises the existing review canary.

There are no events before October 2026, so this addition does not change any
report through September 2026. Code 3000 is a real emitted referral code—not a
synthetic code—and is already attributed to Osero by `settlement-cycle`.
