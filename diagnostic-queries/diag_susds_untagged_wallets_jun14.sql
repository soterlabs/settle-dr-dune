-- =============================================================================
-- DIAGNOSTIC — sUSDS untagged wallets by USDS base, June 14 2026
-- -----------------------------------------------------------------------------
-- Purpose: total sUSDS USDS base exceeds known supply — investigate which
-- wallets/chains drive the untagged (ref_code -999999 / 99) portion on a
-- single day to find the double-count or mis-attributed position.
--
-- Sources:
--   query_7877542 -> Ethereum sUSDS (symbol = 'sUSDS') + Ethereum sUSDC
--   query_7877543 -> L2 sUSDS via PSM3 (arb/op/uni; base excluded)
-- Priced with sUSDS conversion rate (query_7877548).
--
-- Output: source, blockchain, user_addr, day_type, twa_shares, usds_base
--         sorted by usds_base desc so the biggest contributors are first.
-- =============================================================================
with
    rate as (
        select susds_conversion_rate
        from query_7877548
        where dt = date '2026-06-14'
        limit 1
    ),

    raw as (
        -- Ethereum sUSDS (query_7877542, symbol filter)
        select
            'susds_eth'      as source,
            blockchain,
            user_addr,
            ref_code,
            day_type,
            time_weighted_avg_balance as twa_shares
        from query_7877542
        where symbol    = 'sUSDS'
          and blockchain <> 'base'
          and dt         = date '2026-06-14'
          and ref_code   = -999999

        union all

        -- L2 sUSDS via PSM3 (query_7877543, base excluded)
        select
            'susds_l2_psm3'  as source,
            blockchain,
            user_addr,
            ref_code,
            day_type,
            time_weighted_avg_balance
        from query_7877543
        where blockchain <> 'base'
          and dt          = date '2026-06-14'
          and ref_code    = -999999
    )

select
    r.source,
    r.blockchain,
    r.user_addr,
    r.day_type,
    r.twa_shares,
    r.twa_shares * coalesce(rt.susds_conversion_rate, 1) as usds_base
from raw r
cross join rate rt
order by usds_base desc
