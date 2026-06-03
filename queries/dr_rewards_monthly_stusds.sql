-- =============================================================================
-- DR revenue (MONTHLY) — stUSDS (Ethereum)
-- -----------------------------------------------------------------------------
-- Per-source DR-revenue query (see dr_rewards_monthly_susds_susdc.sql for the
-- overall split rationale). References exactly one foundational TWA query.
--
-- Grain: (month, blockchain, token, ref_code).
--
-- Pipeline: TWA balance (query_7640319) x reward rate (query_7640322,
-- XR-stUSDS) x stUSDS share->USD rate (query_7640324, by dt).
-- Untagged stUSDS keeps the -999999 sentinel (no reclassification in Spark).
--
-- SAVED AS: query_7646379  (https://dune.com/queries/7646379)
-- =============================================================================
with
    balances as (
        select
            dt,
            blockchain,
            symbol as token,
            ref_code,
            sum(time_weighted_avg_balance) as amount
        from query_7640319
        group by 1, 2, 3, 4
    ),

    accrued as (
        select
            b.dt, b.blockchain, b.token, b.ref_code, b.amount,
            b.amount / 365.0 * r.reward_per as tw_reward
        from balances b
        join query_7640322 r
            on r.reward_code = 'XR-stUSDS'
            and b.dt between r.start_dt and r.end_dt
    ),

    daily_usd as (
        select
            a.dt, a.blockchain, a.token, a.ref_code, a.amount,
            a.tw_reward * coalesce(ct.stusds_conversion_rate, 1) as tw_reward_usd
        from accrued a
        left join query_7640324 ct on a.dt = ct.dt
    )

select
    date_trunc('month', dt) as month,
    blockchain,
    token,
    ref_code,
    sum(tw_reward_usd) as dr_usd,
    avg(amount) as avg_twa_balance
from daily_usd
group by 1, 2, 3, 4
order by month, blockchain, token, ref_code
