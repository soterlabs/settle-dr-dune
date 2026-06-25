-- #############################################################################
-- ##                                                                         ##
-- ##   !!!  UNTAGGED BALANCE RECLASSIFICATION — MIRRORS SPARK METHODOLOGY  !!!##
-- ##                                                                         ##
-- ##   Any sUSDC balance where ref_code = -999999 (untagged — no Referral   ##
-- ##   event matched the deposit, or balance arrived via secondary transfer) ##
-- ##   is reassigned to ref_code 127 (Spark's house code for untagged sUSDC).##
-- ##                                                                         ##
-- ##   This exactly replicates Spark's own aggregation query (query_5310067  ##
-- ##   / https://dune.com/queries/5310067). ref_code 127 means "Spark house /##
-- ##   untagged sUSDC" — it is NOT a real referral partner.                  ##
-- ##                                                                         ##
-- ##   A large fraction of total sUSDC supply lands here because most        ##
-- ##   holder-days are no-transaction forward-fill days (no Referral event   ##
-- ##   fires) and secondary wallet-to-wallet transfers never emit a Referral. ##
-- ##   This is expected and consistent with Spark's numbers.                 ##
-- ##                                                                         ##
-- #############################################################################
-- =============================================================================
-- DIAGNOSTIC (daily USDS base, by ref_code) — sUSDC, all chains except Base
-- -----------------------------------------------------------------------------
-- Source: query_7640317 filtered to symbol = 'sUSDC' (Ethereum + L2 sUSDC),
-- Base excluded. sUSDC has no independent rate in Spark's pipeline, so it is
-- priced with the sUSDS conversion rate (query_7640323).
-- USDS base = sum(daily TWA sUSDC shares) x sUSDS->USDS rate.
--
-- Output: dt, ref_code, usds_base   (one row per day per ref_code)
-- =============================================================================
with
    bal as (
        select
            dt,
            -- sUSDC untagged -> 127 (see bold header note; mirrors Spark query_5310067).
            case when ref_code = -999999 then 127 else ref_code end as ref_code,
            sum(time_weighted_avg_balance) as shares
        from query_7640317
        where symbol = 'sUSDC' and blockchain <> 'base'
        group by dt, ref_code
    )
select
    b.dt,
    b.ref_code,
    b.shares * coalesce(r.susds_conversion_rate, 1) as usds_base
from bal b
left join query_7640323 r on r.dt = b.dt
order by b.dt, b.ref_code
