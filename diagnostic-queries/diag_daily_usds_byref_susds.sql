-- #############################################################################
-- ##                                                                         ##
-- ##   !!!  UNTAGGED BALANCE RECLASSIFICATION — MIRRORS SPARK METHODOLOGY  !!!##
-- ##                                                                         ##
-- ##   Any sUSDS balance where ref_code = -999999 (untagged — no Referral   ##
-- ##   event matched the deposit, or balance arrived via secondary transfer) ##
-- ##   is reassigned to ref_code 99 (Spark's house code for untagged sUSDS). ##
-- ##                                                                         ##
-- ##   This exactly replicates Spark's own aggregation query (query_5310067  ##
-- ##   / https://dune.com/queries/5310067). ref_code 99 means "Spark house / ##
-- ##   untagged sUSDS" — it is NOT a real referral partner.                  ##
-- ##                                                                         ##
-- ##   A large fraction of total sUSDS supply lands here because most        ##
-- ##   holder-days are no-transaction forward-fill days (no Referral event   ##
-- ##   fires) and secondary wallet-to-wallet transfers never emit a Referral. ##
-- ##   This is expected and consistent with Spark's numbers.                 ##
-- ##                                                                         ##
-- #############################################################################
-- =============================================================================
-- DIAGNOSTIC (daily USDS base, by ref_code) — sUSDS, all chains except Base
-- -----------------------------------------------------------------------------
-- sUSDS lives in TWO foundational queries, so this unions both:
--   query_7640317 -> Ethereum sUSDS (symbol = 'sUSDS')
--   query_7640318 -> L2 sUSDS via PSM3 (base/arb/op/uni) — Base excluded here
-- USDS base = sum(daily TWA sUSDS shares) x sUSDS->USDS rate (query_7640323).
--
-- CONTRACT EXCLUSIONS: protocol/vault contract addresses that would double-count
-- the underlying positions they hold are excluded at the foundational level
-- (twa_susds_susdc_erc4626.sql and twa_susds_psm3_l2.sql). The exclusion list
-- lives in the `excluded_addresses` CTE at the top of each foundational — add
-- new addresses there, not here.
--
-- If this ever errors with "too many stages", split into two queries (one per
-- foundational) — referencing two heavy foundationals at once is the only risk.
--
-- Output: dt, ref_code, usds_base   (one row per day per ref_code)
-- =============================================================================
with
    bal as (
        select
            dt,
            -- sUSDS untagged -> 99 (see bold header note; mirrors Spark query_5310067).
            case when ref_code = -999999 then 99 else ref_code end as ref_code,
            sum(time_weighted_avg_balance) as shares
        from (
            select dt, ref_code, time_weighted_avg_balance
            from query_7640317
            where symbol = 'sUSDS' and blockchain <> 'base'
            union all
            select dt, ref_code, time_weighted_avg_balance
            from query_7640318
            where blockchain <> 'base'
        ) u
        group by dt, ref_code
    )
select
    b.dt,
    b.ref_code,
    b.shares * coalesce(r.susds_conversion_rate, 1) as usds_base
from bal b
left join query_7640323 r on r.dt = b.dt
order by b.dt, b.ref_code
