-- #############################################################################
-- ##                                                                         ##
-- ##   !!!  SPARK SAVINGS UNTAGGED RECLASSIFICATION  !!!                     ##
-- ##                                                                         ##
-- ##   Untagged (-999999) and sUSDC-house-code (127) balances for Spark      ##
-- ##   Savings vault tokens are reassigned to SYNTHETIC fallback codes so    ##
-- ##   that they appear as explicit, identifiable line items rather than      ##
-- ##   polluting the generic untagged bucket:                                 ##
-- ##                                                                         ##
-- ##       spUSDC   ->  ref_code 131                                         ##
-- ##       spUSDT   ->  ref_code 130                                         ##
-- ##       spPYUSD  ->  ref_code 132                                         ##
-- ##                                                                         ##
-- ##   We are RESERVING codes 130–139 for Spark Savings synthetic            ##
-- ##   fallbacks. The specific methodology for how untagged Spark Savings    ##
-- ##   positions should ultimately be treated (e.g. which real ref_code to   ##
-- ##   attribute them to, or whether they should be excluded) is yet to be   ##
-- ##   decided. This reclassification keeps them visible and separate so     ##
-- ##   that decision can be made later without data loss.                    ##
-- ##                                                                         ##
-- ##   INSPIRATION: Spark's own query https://dune.com/queries/6357036      ##
-- ##   funnels untagged spUSDC into a dedicated house bucket. We mirror      ##
-- ##   that pattern (see also sUSDC untagged -> 127 in query_5310067) but    ##
-- ##   use our own reserved range rather than assuming Spark's exact code.   ##
-- ##                                                                         ##
-- #############################################################################
-- =============================================================================
-- DR revenue (MONTHLY) — Spark vaults (spUSDC, spUSDT, spPYUSD, spETH)
-- -----------------------------------------------------------------------------
-- Per-source DR-revenue query (see dr_rewards_monthly_susds_susdc.sql for the
-- overall split rationale). References exactly one foundational TWA query.
--
-- Grain: (month, blockchain, token, ref_code).
--
-- Pipeline: TWA balance (query_7640321) x per-day deployment ratio
-- (query_7683727, deployment_ratio_sp.sql) x reward rate (query_7640322, XR*)
-- x sp* share->USD value (query_7640325, by dt/token/chain).
-- spETH is tracked but earns ZERO rewards (zeroed here). Untagged sp* -> reserved 130-139.
--
-- *** DEPLOYMENT RATIO SCOPE — spUSDC ONLY ***
-- The deployment-ratio haircut (query_7683727) is applied ONLY to spUSDC. spUSDT
-- and spPYUSD are treated as ratio = 1 (full balance DR-eligible). Reason: those
-- vaults hold their underlying directly and have no idle/deployed split, so the
-- "fraction deployed elsewhere" model is meaningless for them and (because idle
-- can equal supply) was spuriously zeroing their DR. This DIVERGES from Spark's
-- queries (query_6398769 / query_5358295 apply the ratio to all non-spETH vaults)
-- and is a deliberate choice — TO BE CONFIRMED against Spark's intended treatment.
--
-- *** COMPARISON WARNING — Spark's spUSDC DR query (https://dune.com/queries/6357036/10113012) ***
-- Spark's query tracks the FULL per-user spUSDC TWA balance WITHOUT subtracting the
-- vault's undeployed (idle) USDC. It is therefore NOT the DR-eligible base; it is the
-- gross deposited balance. This query applies the deployment ratio (query_7683727) so
-- that only the fraction of USDC actually deployed into a lending market earns rewards,
-- matching the methodology of query_6398769. Do NOT compare totals from the two queries
-- directly — Spark's figure will always be larger by the idle-buffer amount.
--
-- SAVED AS: query_7683760  (https://dune.com/queries/7683760)
-- =============================================================================
with
    -- Per-day deployment ratio: (vault_deployed / vault_total) per (blockchain, vault_symbol, dt).
    -- Source: deployment_ratio_sp.sql (query_7683727).
    deployment_ratios as (
        select blockchain, vault_symbol, dt, deployment_ratio
        from query_7683727
    ),

    balances as (
        select
            dt,
            blockchain,
            symbol as token,
            -- Spark Savings untagged fallback (see bold header note; codes 130-139 reserved).
            case
                when symbol = 'spUSDC'  and ref_code in (-999999, 127) then 131
                when symbol = 'spUSDT'  and ref_code in (-999999, 127) then 130
                when symbol = 'spPYUSD' and ref_code in (-999999, 127) then 132
                else ref_code
            end as ref_code,
            sum(time_weighted_avg_balance) as amount
        from query_7640321
        group by 1, 2, 3, 4
    ),

    accrued as (
        select
            b.dt, b.blockchain, b.token, b.ref_code, b.amount,
            case
                when b.token = 'spETH' then 0.0   -- spETH earns zero DR
                -- spUSDT/spPYUSD: NO deployment-ratio haircut (see bold header note).
                -- These vaults hold their underlying directly; there is no idle/deployed
                -- split, so the full balance is DR-eligible (ratio = 1).
                when b.token in ('spUSDT', 'spPYUSD') then b.amount * 1.0 / 365.0 * r.reward_per
                else b.amount * coalesce(dr.deployment_ratio, 0) / 365.0 * r.reward_per
            end as tw_reward
        from balances b
        left join deployment_ratios dr
            on b.dt          = dr.dt
           and b.blockchain   = dr.blockchain
           and b.token        = dr.vault_symbol
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
