# Grove USDS farm DR coverage

The Grove staking farm at `0x4E41488C19cD35EB4de3083Fc3e204854c75c86a`
is a fourth Ethereum `StakingRewards` contract using the same Template-D event
shape as the Sky, SPK, and Chronicle farms:

- `Staked(address indexed user, uint256 amount)` changes the USDS balance;
- `Withdrawn(address indexed user, uint256 amount)` reduces it; and
- `Referral(uint16 indexed referral, address indexed user, uint256 amount)`
  supplies the code for last-referral-wins attribution.

The farm is replayed from 2026-06-01, before its first observed `Staked` event
on 2026-06-23. Its balances use the normal XR schedule and need no conversion
because the staked asset is already USDS.

## Historical audit through 2026-09-01

The farm emitted 1,339 Referral events through the August settlement cutoff:

| Code | Events | Owners | Referred deposits (USDS) |
|---:|---:|---:|---:|
| 0 | 1 | 1 | 162,000.00 |
| 1 | 1,320 | 438 | 829,423,802.89 |
| 1002 | 18 | 6 | 3,364,781.42 |

Replaying all 1,451 `Staked` and 1,192 `Withdrawn` events under the production
TWA methodology gives these previously omitted Skybase amounts:

| Month | Code 0 | Code 1 | Code 1002 | Total DR (USDS) |
|---|---:|---:|---:|---:|
| 2026-07 | 22.62 | 31,984.31 | 164.09 | 32,171.02 |
| 2026-08 | 0.00 | 29,698.52 | 96.63 | 29,795.15 |
| Total | 22.62 | 61,682.82 | 260.72 | 61,966.17 |

An additional 813.35 USDS of July/August DR is untagged (`-999999`) and is not
assigned to Skybase. These figures are historical accrual estimates, not proof
that a payment was or was not made; settlement operations must treat them as a
true-up review item.
