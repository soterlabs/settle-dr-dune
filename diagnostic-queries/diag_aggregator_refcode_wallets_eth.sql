-- =============================================================================
-- DIAGNOSTIC — unique wallets tagged with aggregator ref_codes on Ethereum
-- =============================================================================
-- Returns every (user_addr, ref_code, token) combination that ever appeared in
-- any of our four Ethereum TWA pipeline sources carrying one of the target codes:
--   1001, 1002, 1003, 1004, 1007, 1015, 1016, 1017
--
-- Sources (Ethereum only):
--   query_7640317 — sUSDS + sUSDC (erc4626, all chains; filtered here to ethereum)
--   query_7640319 — stUSDS         (ethereum only)
--   query_7640320 — USDS farms     (ethereum only: USDS-SKY, USDS-SPK, USDS-CLE)
--   query_7640321 — sp* vaults     (ethereum + avalanche; filtered here to ethereum)
--
-- PSM3 (query_7640318) is L2-only so is not included.
--
-- Output per (user_addr, ref_code, token):
--   source         — which pipeline source the row comes from
--   first_seen     — earliest dt the wallet carried this code for this token
--   last_seen      — latest dt
--   days_tagged    — number of distinct days tagged with this code
--   peak_twa       — maximum single-day TWA balance (in token shares/units)
--   total_twa_days — sum of daily TWA values (proportional to DR earned)
--
-- Sorted by ref_code, then total_twa_days desc so the largest contributors
-- appear first within each code.
-- =============================================================================

with
    target_codes (code) as (
        values (1001), (1002), (1003), (1004), (1007), (1015), (1016), (1017)
    ),

    -- -------------------------------------------------------------------------
    -- 1. sUSDS + sUSDC (erc4626) — Ethereum only
    -- -------------------------------------------------------------------------
    susds_susdc as (
        select
            'susds_susdc_eth' as source,
            user_addr,
            ref_code,
            symbol as token,
            dt,
            time_weighted_avg_balance
        from query_7640317
        where blockchain = 'ethereum'
          and ref_code in (select code from target_codes)
          and time_weighted_avg_balance > 0
    ),

    -- -------------------------------------------------------------------------
    -- 2. stUSDS — Ethereum only
    -- -------------------------------------------------------------------------
    stusds as (
        select
            'stusds_eth' as source,
            user_addr,
            ref_code,
            symbol as token,
            dt,
            time_weighted_avg_balance
        from query_7640319
        where blockchain = 'ethereum'
          and ref_code in (select code from target_codes)
          and time_weighted_avg_balance > 0
    ),

    -- -------------------------------------------------------------------------
    -- 3. USDS farms — Ethereum only (USDS-SKY, USDS-SPK, USDS-CLE)
    -- -------------------------------------------------------------------------
    farms as (
        select
            'farms_eth' as source,
            user_addr,
            ref_code,
            symbol as token,
            dt,
            time_weighted_avg_balance
        from query_7640320
        where blockchain = 'ethereum'
          and ref_code in (select code from target_codes)
          and time_weighted_avg_balance > 0
    ),

    -- -------------------------------------------------------------------------
    -- 4. sp* vaults — Ethereum only (spUSDC, spUSDT, spPYUSD, spETH)
    -- -------------------------------------------------------------------------
    sp as (
        select
            'sp_eth' as source,
            user_addr,
            ref_code,
            symbol as token,
            dt,
            time_weighted_avg_balance
        from query_7640321
        where blockchain = 'ethereum'
          and ref_code in (select code from target_codes)
          and time_weighted_avg_balance > 0
    ),

    -- -------------------------------------------------------------------------
    -- 5. Union all sources
    -- -------------------------------------------------------------------------
    all_rows as (
        select * from susds_susdc
        union all
        select * from stusds
        union all
        select * from farms
        union all
        select * from sp
    )

-- -------------------------------------------------------------------------
-- 6. Aggregate to (user_addr, ref_code, token, source)
-- -------------------------------------------------------------------------
select
    user_addr,
    ref_code,
    token,
    source,
    min(dt)                           as first_seen,
    max(dt)                           as last_seen,
    count(distinct dt)                as days_tagged,
    max(time_weighted_avg_balance)    as peak_twa,
    sum(time_weighted_avg_balance)    as total_twa_days
from all_rows
group by user_addr, ref_code, token, source
order by ref_code asc, total_twa_days desc
