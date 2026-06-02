-- =============================================================================
-- Validation: diff our self-owned TWA queries vs. Spark's opaque datasets
-- -----------------------------------------------------------------------------
-- Run this on Dune AFTER saving the foundational queries and substituting their
-- query IDs below. It aggregates both sides to (dt, blockchain, token, ref_code)
-- and reports per-row differences for a chosen month. Differences concentrated
-- on multi-ref_code addresses confirm the attribution model is the only
-- meaningful difference; large structural gaps indicate a table-name / decimals
-- / chain-coverage problem to fix.
--
-- SUBSTITUTE:
--   query_XXXXXXX -> saved query ID of queries/twa_susds_susdc_erc4626.sql
--   (repeat for sp* using twa_sp_vaults.sql vs the sp dataset; see bottom)
--   :target_month -> e.g. date '2026-03-01'
--
-- Spark side mirrors query_5310067's untagged reclassification so ref_codes
-- line up: sUSDC -999999 -> 127, sUSDS -999999 -> 99.
-- =============================================================================

with
    -- ---- OUR SIDE: aggregate the self-owned per-user TWA to the dataset grain ----
    ours as (
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
        from query_XXXXXXX            -- TODO: saved ID of twa_susds_susdc_erc4626.sql
        group by 1, 2, 3, 4
    ),

    -- ---- SPARK SIDE: the opaque dataset, same grain + same reclassification ----
    spark as (
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
        from dune.sparkdotfi.result_spark_s_usds_s_usdc_time_weighted_average_balance
        group by 1, 2, 3, 4
    ),

    diff as (
        select
            coalesce(o.dt, s.dt) as dt,
            coalesce(o.blockchain, s.blockchain) as blockchain,
            coalesce(o.token, s.token) as token,
            coalesce(o.ref_code, s.ref_code) as ref_code,
            o.amount as ours_amount,
            s.amount as spark_amount,
            coalesce(o.amount, 0) - coalesce(s.amount, 0) as delta,
            case
                when s.amount is null or s.amount = 0 then null
                else (coalesce(o.amount, 0) - s.amount) / s.amount * 100
            end as delta_pct
        from ours o
        full outer join spark s
            on o.dt = s.dt and o.blockchain = s.blockchain
            and o.token = s.token and o.ref_code = s.ref_code
    )

select *
from diff
where dt >= :target_month
  and dt < date_add('month', 1, :target_month)
  and abs(delta) > 1            -- ignore sub-token dust
order by abs(delta) desc;

-- =============================================================================
-- For sp* vaults, swap the two source CTEs:
--   ours:  from query_YYYYYYY  (saved ID of twa_sp_vaults.sql)
--   spark: from dune.sparkdotfi.result_spark_sp_usdc_sp_usdt_sp_eth_time_weighted_average_balance
-- and drop the -999999 reclassification (sp* uses plain ref_codes).
-- For stUSDS: ours = twa_stusds.sql vs spark = query_5358290 / query_5358161.
-- =============================================================================
