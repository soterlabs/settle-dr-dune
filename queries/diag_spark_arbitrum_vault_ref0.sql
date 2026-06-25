-- =============================================================================
-- DIAGNOSTIC — is the Arbitrum sUSDC vault (UsdcVaultL2) tagged ref_code 0
--              as an sUSDS holder, and would Spark tag it the same way?
-- -----------------------------------------------------------------------------
-- Address under test (Arbitrum UsdcVaultL2):
--     0x940098b108fb7d0a7e374f6eded7760787464609
--
-- WHY WE CAN'T READ SPARK DIRECTLY:
--   Spark's L2 sUSDS per-user TWA producer is NOT in raw-queries/ (that folder
--   only has consumers + templates; none contain any PSM3 / referralCode logic).
--   Their consumer query_5310067 and its source table
--     dune.sparkdotfi.result_spark_s_usds_s_usdc_time_weighted_average_balance
--   are PRIVATE to the Spark team -> both error "does not exist / is private"
--   from our account. So we cannot pull Spark's output rows.
--
-- WHAT WE CAN DO — test the ROOT on-chain fact their logic must rely on:
--   On L2 there is no `Referral` event; the ONLY attribution mechanism is the
--   PSM3 `Swap(referralCode)` keyed by `receiver`. Whatever pipeline (ours or
--   Spark's) attributes L2 sUSDS does so by that receiver. So if the vault is the
--   on-chain receiver of PSM3 sUSDS swaps with referralCode 0, BOTH pipelines tag
--   it ref_code 0 (Spark's consumer query_5310067 applies no contract filter — it
--   only remaps -999999 -> 99 — so a ref 0 contract row passes through untouched).
--
-- RESULT (2026-06-22): 14,686 swaps, ALL referralCode = 0, ~1.37B sUSDS out.
--   => the vault is unavoidably tagged ref_code 0 as an sUSDS holder. This is a
--      double-count: the sUSDS is the vault's BACKING for user sUSDC (users are
--      already credited under their real code via the sUSDC source), not an
--      independent end-user holding.
-- =============================================================================
select
    cast(s.referralCode as varchar)      as referral_code,
    count(*)                             as n_swaps,
    sum(s.amountOut / 1e18)              as susds_out_cumulative,
    min(s.evt_block_time)                as first_swap,
    max(s.evt_block_time)                as last_swap
from spark_protocol_arbitrum.psm3_evt_swap s
where s.contract_address = 0x2B05F8e1cACC6974fD79A673a341Fe1f58d27266   -- Arbitrum PSM3
  and s.assetOut        = 0xdDb46999F8891663a8F2828d25298f70416d7610   -- Arbitrum sUSDS
  and s.receiver        = 0x940098b108fb7d0a7e374f6eded7760787464609   -- UsdcVaultL2
group by 1
order by n_swaps desc;
