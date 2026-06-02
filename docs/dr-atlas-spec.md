### 2. Distribution Rewards [A.2.2.8.1](https://sky-atlas.io/#e632c38f-3e4e-4c7e-acfd-b6ec45a422e6)

**Definition**

- Rewards Prime Agents and their Integration Partners based on the `balances of USDS, sUSDS, and stUSDS` tracked by `on-chain or manually off chain` using `reward codes`.
    - **Base rate:** **0.20%** annualized
    - **Boosted rate:** Up to an additional **+0.30%** annualized (total **0.50%** annualized)
- Integrators are actors that offer access to the Sky Protocol via their frontends or infrastructure.

**Mechanism**

- [ETH Mainnet General Tracking Methodology A.2.2.8.1.2.1.2.2.1](https://sky-atlas.io/#87fd6861-ba8a-4bde-945e-ee9ad37ae3e2) - specify the Reward Code as a parameter to `depositing USDS into the Sky Savings Rate contract` or `Token Rewards Contracts`
    - `On-chain Deposit Data` is combined with `withdrawal data` to estimate `net deposits` associated with the Reward Code on a `FIFO basis`
- [CoW Swap Tracking Methodology A.2.2.8.1.2.1.2.2.2](https://sky-atlas.io/#1b5cc0ee-0ee8-467e-ab49-33c06ad417dc) - follow the same `FIFO` `net deposit logic` as general tracking
    - but are `tracked on` CoW Swap’s `solver network events`
- [Base Tracking Methodology A.2.2.8.1.2.1.2.2.3](https://sky-atlas.io/#f710bddf-dc1d-483c-9503-483574cb6333) - follow the same `FIFO` `net deposit logic` as general tracking
    - but `track` `reward codes` as a `parameter in calls` to the `Base PSM contract`.
    - Conversions from `USDS or USDC` to `sUSDS` are considered `deposits`
- Prime Agents & Operational GovOps can develop [Alternative Tracking Methods A.2.2.8.1.2.1.2.2.4](https://sky-atlas.io/#5eba1c21-4e93-4a0a-aa10-e99bcfa65f16) so long that
    - estimate USDS balances that are `FIFO` `net deposit based` to a `rewards code`.
    - `no possibility` that the same USDS balance is double counted for multiple reward codes.
    - can be either `on-chain data` or `off-chain data that can be independently verified`

```
DR = Address Net Deposits * .005 / 12
```

*Notes:* 

- *Must be paid to agent’s SubProxy during MSC*
- *Agent determines the amount of DR that is passed along to the Integrator Partner*
- *Address balances can earn IB & DR without being considered double counting*
- Fixed reward rate, meaning we can use time weighted average for an address’s balance in the month.