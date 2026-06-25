-- #############################################################################
-- ##                                                                         ##
-- ##   !!!  spPYUSD UNTAGGED RECLASSIFICATION  ->  ref_code 132  !!!         ##
-- ##                                                                         ##
-- ##   Any spPYUSD balance that is untagged (-999999) or carries the sUSDC   ##
-- ##   house code (127) is REASSIGNED to ref_code 132 so that it appears     ##
-- ##   as an explicit, identifiable line item in charts and reports.         ##
-- ##                                                                         ##
-- ##   Codes 130–139 are RESERVED for Spark Savings synthetic fallbacks:     ##
-- ##       130 = spUSDT untagged                                             ##
-- ##       131 = spUSDC untagged                                             ##
-- ##       132 = spPYUSD untagged                                            ##
-- ##   The final methodology for these positions (real attribution, exclude, ##
-- ##   etc.) is TBD and will be decided at a later date.                    ##
-- ##                                                                         ##
-- ##   INSPIRATION: Spark's query https://dune.com/queries/6357036/10113012  ##
-- ##   funnels untagged spUSDC into a dedicated house bucket (mirroring      ##
-- ##   sUSDC untagged -> 127 in query_5310067). We mirror the pattern but    ##
-- ##   use our own reserved range rather than assuming Spark's exact code.   ##
-- ##                                                                         ##
-- #############################################################################
-- =============================================================================
-- DIAGNOSTIC (daily USDS base, by ref_code) — spPYUSD (Spark vault)
-- -----------------------------------------------------------------------------
-- Source: query_7640321 filtered to symbol = 'spPYUSD' (Ethereum).
-- USDS base = sum( daily TWA spPYUSD shares x share->USD value (query_7640325) )
--
-- NO DEPLOYMENT-RATIO HAIRCUT: spPYUSD holds its underlying directly (no idle/
-- deployed split), so the deployment-ratio concept does not apply — the full
-- balance is DR-eligible (ratio = 1). This diverges from Spark's generic
-- treatment and is a deliberate, TO-BE-CONFIRMED choice (mirrored in the
-- pipeline: dr_rewards_monthly_sp.sql and deployment_ratio_sp.sql).
--
-- Output: dt, ref_code, usds_base   (one row per day per ref_code)
-- =============================================================================
select
    b.dt,
    -- spPYUSD untagged/127 -> 132 (codes 130-139 reserved for Spark Savings; see header).
    case when b.ref_code in (-999999, 127) then 132 else b.ref_code end as ref_code,
    sum(
        b.time_weighted_avg_balance
        * coalesce(csp.usd_value, 1)
    ) as usds_base
from query_7640321 b
left join query_7640325 csp
    on csp.dt = b.dt and csp.token_symbol = b.symbol and csp.blockchain = b.blockchain
where b.symbol = 'spPYUSD'
group by 1, 2
order by 1, 2
