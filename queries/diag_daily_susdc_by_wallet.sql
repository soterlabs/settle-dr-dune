-- =============================================================================
-- DIAGNOSTIC — sUSDC daily balance BY WALLET (Base excluded)
-- -----------------------------------------------------------------------------
-- For hunting "tagged sUSDS base > sUSDC supply": one row per (day, chain,
-- wallet, ref_code) so you can pick a day where the daily total exceeds on-chain
-- supply and see exactly which wallets/ref_codes make it up. References only
-- query_7640317 (single foundational), filtered to sUSDC, Base excluded.
--
-- twa_shares = daily time-weighted sUSDC share balance.
-- usds_base  = twa_shares x sUSDS->USDS rate (query_7640323).
--
-- TIP: add a `where`/`having` filter to keep results small/chartable, e.g.
--   - one day:        and b.dt = date '2026-03-15'
--   - material only:  having sum(b.time_weighted_avg_balance) > 1e6
--
-- Output: dt, blockchain, user_addr, ref_code, twa_shares, usds_base
-- =============================================================================
with
    bal as (
        select
            dt, blockchain, user_addr, ref_code,
            sum(time_weighted_avg_balance) as twa_shares
        from query_7640317
        where symbol = 'sUSDC' and blockchain <> 'base'
          and dt = date '2026-06-23'
        group by dt, blockchain, user_addr, ref_code
    )
select
    b.dt,
    b.blockchain,
    b.user_addr,
    b.ref_code,
    b.twa_shares,
    b.twa_shares * coalesce(r.susds_conversion_rate, 1) as usds_base
from bal b
left join query_7640323 r on r.dt = b.dt
order by b.dt, usds_base desc
