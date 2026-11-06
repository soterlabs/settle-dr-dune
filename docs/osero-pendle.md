# Osero-tagged sUSDS in Pendle — conservative Phase 1

## Decision and accounting boundary

This source implements only the memo's narrow, provenance-based path. A
balance becomes eligible when sUSDS emits a genuine `Referral(3017)` to one
of the reviewed strategy accounts. It remains eligible
only while an auditable transformation or custody path connects those referred
sUSDS shares to Pendle SY or matched PT/YT.

The source emits only the portion inside Pendle. Referred sUSDS still held in
the strategy wallet remains in ordinary Template A tracking. There is no new
synthetic Osero code: the real emitted code is preserved.

Skybase code 1997 continues to measure all sUSDS backing the shared SY, but is
now a residual allocation. Every positive Osero Pendle leg has an exact
negative code-1997 leg at the same block and log index:

```
Osero eligible Pendle balance + Skybase residual = total SY sUSDS backing
```

This is a beneficiary reallocation, not a new reward base. It cannot increase
aggregate Pendle DR or count the same sUSDS for both Osero and Skybase.

## Rules implemented

1. Only the exact sUSDS `Deposit` + mint `Transfer` + `Referral` event pattern
   creates tagged inventory. A code alone, an incoming transfer, or a claimed
   3000-range convention does not. Code 3017 is explicitly allowlisted; new
   Osero codes require review.
2. The `Referral` owner must be an explicitly reviewed Osero strategy. A new
   strategy requires code review; observed addresses are not enrolled
   automatically.
3. A 1:1 sUSDS-to-SY wrap preserves only the tagged portion of inventory.
   Untagged shares mixed into the same transaction do not become tagged.
4. Splitting tagged SY into matched PT and YT preserves the originating sUSDS
   share credit. Before expiry both sides must remain in the reviewed custody
   perimeter; selling or transferring either side consumes the corresponding
   credit. Incoming PT/YT never restores it.
5. Recombining matched PT/YT into SY, or unwrapping SY back to sUSDS, preserves
   provenance. Redeeming into another asset ends it. Returned wallet sUSDS is
   again paid only by Template A, not this source.
6. PT collateral in the memo's named Morpho markets remains in custody. A
   withdrawal to the strategy preserves credit; an outside withdrawal or a
   third-party liquidation consumes it. No other Morpho market is inferred.
7. Shared execution routers are transient custody only when exactly one
   reviewed strategy is implicated in the transaction. Any balance left at a
   router at transaction end is treated as disposed; ambiguous multi-strategy
   batches receive no router credit.
8. After Pendle expiry, YT is no longer required and PT alone represents the
   redemption claim. LP tokens, limit-order positions, unmatched PT, secondary
   purchases and market-value estimates are out of scope.

The implementation uses referred-first inventory and values matched PT/YT by
the originating sUSDS shares, not by PT market price. That makes additions
strictly provenance-limited. Actual redemption events replace the originating
credit with the returned SY amount; rounding or redemption loss is therefore
recognized when it occurs.

## Reviewed perimeter

- sUSDS: `0xa3931d71877c0e7a3148cb7eb4463524fec27fbd`
- Pendle SY-sUSDS: `0xbe3d4ec488a0a042bb86f9176c24f8cd54018ba7`
- PT-sUSDS-26NOV2026: `0xdc169abe56461a2e0c034da431ac2a3ebf596094`
- YT-sUSDS-26NOV2026: `0xc7b8551c6b286ce0b44952320e940bd3dee58a09`
- The four strategy accounts, three Morpho market IDs and execution contracts
  are explicit constants in `py/drhs/sources/osero_pendle.py`.

Event extraction is HyperSync-only. At implementation time no qualifying
Osero-to-Pendle flow had yet occurred, so this source produces zero Osero
Pendle accrual until a reviewed strategy executes the supported on-chain path.
The census through Ethereum block 26,354,746 found one sUSDS referral naming
any reviewed strategy: code 128 at strategy `0xe419…9cd3`. It is not an
allowlisted Osero referral and creates no credit.

## Grove Farm code 3017

Grove does not need a new attribution method. The existing farm source joins
`Referral(uint16,address,uint256)` to `Staked(address,uint256)` by transaction
hash and user, regardless of which of those two logs is emitted first. The
observed transaction
`0x001181736457d2f9551ece0cce773275b9752c883ea358c8ec5bea6f094154db`
is pinned as a regression: block 26,349,987 deposited
8,882,086.510017052 USDS into Grove Farm for
`0xe060b80438771f13078048c3b0d930efeca6e622` with real code 3017.

The report consumer must explicitly map 3017 to Osero. The current
`settlement-cycle` policy intentionally does not infer beneficiary ownership
from the 3000–3999 numeric range.
