-- =============================================================================
-- DR revenue (MONTHLY) — USDS staking farms (Sky / Spk / Chronicle, Ethereum)
-- -----------------------------------------------------------------------------
-- Per-source DR-revenue query (see dr_rewards_monthly_susds_susdc.sql for the
-- overall split rationale). References exactly one foundational TWA query.
--
-- Grain: (month, blockchain, token, ref_code).
--
-- Pipeline: TWA balance (query_7877545) x reward rate (query_7877547, XR).
-- USDS farms need NO share->USD conversion (balance is already USDS, rate = 1),
-- so there is no conversion join here. Untagged keeps the -999999 sentinel.
--
-- SAVED AS: query_7877554  (https://dune.com/queries/7877554)
-- =============================================================================
with
    balances as (
        select
            dt,
            blockchain,
            symbol as token,
            ref_code,
            sum(time_weighted_avg_balance) as amount
        from query_7877545
        group by 1, 2, 3, 4
    ),

    accrued as (
        select
            b.dt, b.blockchain, b.token, b.ref_code, b.amount,
            b.amount / 365.0 * r.reward_per as tw_reward
        from balances b
        join query_7877547 r
            on r.reward_code = 'XR'
            and b.dt between r.start_dt and r.end_dt
    )

select
    date_trunc('month', dt) as month,
    blockchain,
    token,
    ref_code,
    sum(tw_reward) as dr_usd,        -- USDS farm: USD value == token amount (rate = 1)
    avg(amount) as avg_twa_balance
from accrued
group by 1, 2, 3, 4
order by month, blockchain, token, ref_code
