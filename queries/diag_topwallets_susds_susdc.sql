-- =============================================================================
-- DIAGNOSTIC (top wallets) — sUSDS + sUSDC, Base excluded
-- -----------------------------------------------------------------------------
-- One of SIX per-source top-wallet queries used to hunt double-counting in the
-- DR USDS base. References EXACTLY ONE foundational TWA query (query_7640317) so
-- the plan stays under Dune's stage limit; pre-aggregates to one row per
-- (wallet, ref_code, token, chain) and caps at the top 300, so the result is
-- tiny and there is no timeout risk. Merge the six outputs client-side with
-- src/scripts/combine-top-wallets.ts.
--
-- usds = daily TWA shares x sUSDS->USDS rate (query_7640323). Ranked by the
-- average daily USDS balance. `usds_days` (= sum of daily USDS) is each wallet's
-- total contribution to the reward base — the figure that drives the headline
-- total — so a single contract address with an enormous usds_days is the prime
-- double-count suspect.
--
-- Output: source, user_addr, ref_code, token, blockchain,
--         avg_daily_usds, max_daily_usds, usds_days, active_days
-- =============================================================================
with
    daily as (
        select
            b.user_addr, b.ref_code, b.symbol as token, b.blockchain, b.dt,
            b.time_weighted_avg_balance * coalesce(r.susds_conversion_rate, 1) as usds
        from query_7640317 b
        left join query_7640323 r on r.dt = b.dt
        where b.blockchain <> 'base'
    )
select
    'susds_susdc' as source,
    user_addr,
    ref_code,
    token,
    blockchain,
    avg(usds)          as avg_daily_usds,
    max(usds)          as max_daily_usds,
    sum(usds)          as usds_days,
    count(distinct dt) as active_days
from daily
group by user_addr, ref_code, token, blockchain
order by avg_daily_usds desc
limit 300
