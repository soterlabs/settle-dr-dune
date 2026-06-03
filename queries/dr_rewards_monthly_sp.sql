-- =============================================================================
-- DR revenue (MONTHLY) — Spark vaults (spUSDC, spUSDT, spPYUSD, spETH)
-- -----------------------------------------------------------------------------
-- Per-source DR-revenue query (see dr_rewards_monthly_susds_susdc.sql for the
-- overall split rationale). References exactly one foundational TWA query.
--
-- Grain: (month, blockchain, token, ref_code).
--
-- !!!!!!!!!!!!!!!!!!!!!!!!!!  UNRESOLVED PLACEHOLDER  !!!!!!!!!!!!!!!!!!!!!!!!!!!!
-- sp* DEPLOYMENT RATIO is HARDCODED to 0.5 and is *deliberately wrong* so sp*
-- numbers stick out and cannot be shipped silently. Spark applies a per-day TWA
-- deployment ratio (vault_deployed / vault_total) from an OPAQUE dataset
-- (query_6398769); Amatsu used a flat 0.9. REPLACE before trusting sp* revenue.
-- !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
--
-- Pipeline: TWA balance (query_7640321) x deployment ratio x reward rate
-- (query_7640322, XR*) x sp* share->USD value (query_7640325, by dt/token/chain).
-- spETH is tracked but earns ZERO rewards (zeroed here). Untagged keeps -999999.
--
-- SAVED AS: query_7646382  (https://dune.com/queries/7646382)
-- =============================================================================
with
    params (sp_deployment_ratio) as (
        values (cast(0.5 as double))   -- TODO: replace 0.5 with the real per-day ratio
    ),

    balances as (
        select
            dt,
            blockchain,
            symbol as token,
            ref_code,
            sum(time_weighted_avg_balance) as amount
        from query_7640321
        group by 1, 2, 3, 4
    ),

    accrued as (
        select
            b.dt, b.blockchain, b.token, b.ref_code, b.amount,
            case
                when b.token = 'spETH' then 0.0   -- spETH earns zero DR
                else b.amount * p.sp_deployment_ratio / 365.0 * r.reward_per
            end as tw_reward
        from balances b
        cross join params p
        join query_7640322 r
            on r.reward_code = 'XR*'
            and b.dt between r.start_dt and r.end_dt
    ),

    daily_usd as (
        select
            a.dt, a.blockchain, a.token, a.ref_code, a.amount,
            a.tw_reward * coalesce(csp.usd_value, 1) as tw_reward_usd
        from accrued a
        left join query_7640325 csp
            on a.dt = csp.dt
            and a.token = csp.token_symbol
            and a.blockchain = csp.blockchain
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
