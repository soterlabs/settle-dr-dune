-- =============================================================================
-- DR revenue (MONTHLY) — sUSDS + sUSDC, all chains
-- -----------------------------------------------------------------------------
-- One of FIVE per-source DR-revenue queries. Each references EXACTLY ONE
-- foundational TWA query, so the executed plan stays under Dune's stage limit.
-- (The old combined dr_rewards_daily inlined all five foundational queries +
-- three conversions at once and failed with "this query has too many stages".)
-- Merge the five monthly outputs client-side with
-- src/scripts/combine-dr-results.ts.
--
-- Grain: (month, blockchain, token, ref_code) — directly comparable to the
-- Amatsu per-farm monthly CSVs, and only a few thousand rows.
--
-- Pipeline: TWA balance (query_7640317) x reward rate (query_7640322, by
-- token-class + date) x sUSDS share->USD rate (query_7640323, by dt).
--   sUSDS -> reward_code XR ; sUSDC -> reward_code XR*.
--   Both priced via the sUSDS conversion rate (Spark has no independent sUSDC
--   rate). Untagged reclassification mirrors query_5310067: sUSDC -> 127,
--   sUSDS -> 99.
--
-- SAVED AS: query_7646377  (https://dune.com/queries/7646377)
-- =============================================================================
with
    balances as (
        select
            dt,
            blockchain,
            symbol as token,
            case
                when symbol = 'sUSDC' and ref_code = -999999 then 127
                when symbol = 'sUSDS' and ref_code = -999999 then 99
                else ref_code
            end as ref_code,
            sum(time_weighted_avg_balance) as amount
        from query_7640317
        group by 1, 2, 3, 4
    ),

    accrued as (
        select
            b.dt, b.blockchain, b.token, b.ref_code, b.amount,
            b.amount / 365.0 * r.reward_per as tw_reward
        from balances b
        join query_7640322 r
            on r.reward_code = (case when b.token = 'sUSDC' then 'XR*' else 'XR' end)
            and b.dt between r.start_dt and r.end_dt
    ),

    daily_usd as (
        select
            a.dt, a.blockchain, a.token, a.ref_code, a.amount,
            a.tw_reward * coalesce(cs.susds_conversion_rate, 1) as tw_reward_usd
        from accrued a
        left join query_7640323 cs on a.dt = cs.dt
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
