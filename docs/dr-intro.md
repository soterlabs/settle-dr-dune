# Distribution Rewards Tracking — Background & Scope

## Background

Distribution Rewards (DR) is a primitive designed by Sky to reward Prime Agents and their Integration Partners based on the balances of Sky tokens — namely USDS, sUSDS, and stUSDS (and other derivatives) — that are attributable to them through their frontend or smart contract.

Some smart contracts holding Sky tokens support Sky referral codes and emit `Referral` events when tokens are deposited. These events contain the referral code of the integrator and the amount of the allocation. By reading onchain data, anyone can calculate the amount and duration of new demand originated from a given integrator ([example tx][example-tx]).

## Scope of Work

The goal of this project is to re-create onchain rewards tracking across several reward types, assets, and chains. The roadmap has three main phases:

**1. Dune Dashboard**
Build a comprehensive Dune dashboard comparable to the [Spark Distribution Rewards dashboard][spark-dune]. The Spark dashboard contains much of the logic needed for all DR rewards tracking — not just Spark's — so the first objective is to fork, organize, and document it so it can serve as a solid foundation.

**2. Documentation**
Produce documentation for users, stakeholders, and contributors:
- *User docs* — already started ([Guide on Distribution Rewards][guide-dr]); to be updated to be more specific and reflect the complexities/nuances in our Dune dashboard calculations as we understand them better.
- *Stakeholder/contributor spec* — expand the existing [Atlas summary of the DR specification][atlas-spec] to cover concrete methodology, all assets and their nuances, and DR aspects not captured in the dashboard. The [Spark Distribution Rewards Dune Documentation][spark-dune-docs] will be a useful starting point.

**3. Migration / Custom Frontend (future)**
Once the Dune dashboard meets baseline requirements, evaluate migrating off Dune to enable richer dashboards and a custom frontend where integrators can audit their rewards and submit corrections — similar to [Amatsu's tool][amatsu].

## Links

[guide-dr]: https://planet-plane-4a8.notion.site/Guide-on-Distribution-Rewards-33ad79b5de38800ba191ce236ab18cfd
[amatsu]: https://strata.denna.io/oea/distribution-rewards-payouts
[spark-dune]: https://dune.com/sparkdotfi/spark-distribution-rewards
[example-tx]: https://etherscan.io/tx/0x62e78d67835591066b1b9303c5c7b4518d6f80af80cad46bd240ca0222b5b958
[atlas-spec]: ./dr-atlas-spec.md
[spark-dune-docs]: #
