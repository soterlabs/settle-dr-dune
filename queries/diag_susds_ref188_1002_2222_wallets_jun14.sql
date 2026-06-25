-- =============================================================================
-- DIAGNOSTIC — sUSDS ref_codes 188 / 1002 / 2222 wallets by USDS base, June 14 2026
-- -----------------------------------------------------------------------------
-- Same structure as diag_susds_ref224_wallets_jun14.sql but filtered to
-- ref_code IN (188, 1002, 2222). ref_code column included in output so rows
-- from different codes are distinguishable.
--
-- Sources:
--   query_7640317 -> Ethereum sUSDS (symbol = 'sUSDS')
--   query_7640318 -> L2 sUSDS via PSM3 (arb/op/uni; base excluded)
-- Priced with sUSDS conversion rate (query_7640323).
--
-- Output: source, blockchain, user_addr, ref_code, day_type, twa_shares, usds_base
--         sorted by ref_code, then usds_base desc
-- =============================================================================
with
    rate as (
        select susds_conversion_rate
        from query_7640323
        where dt = date '2026-06-14'
        limit 1
    ),

    raw as (
        select
            'susds_eth'     as source,
            blockchain,
            user_addr,
            ref_code,
            day_type,
            time_weighted_avg_balance as twa_shares
        from query_7640317
        where symbol     = 'sUSDS'
          and blockchain <> 'base'
          and dt          = date '2026-06-14'
          and ref_code    in (188, 1002, 2222)

        union all

        select
            'susds_l2_psm3' as source,
            blockchain,
            user_addr,
            ref_code,
            day_type,
            time_weighted_avg_balance
        from query_7640318
        where blockchain <> 'base'
          and dt          = date '2026-06-14'
          and ref_code    in (188, 1002, 2222)
    )

select
    r.source,
    r.blockchain,
    r.user_addr,
    r.ref_code,
    r.day_type,
    r.twa_shares,
    r.twa_shares * coalesce(rt.susds_conversion_rate, 1) as usds_base
from raw r
cross join rate rt
order by r.ref_code, usds_base desc
