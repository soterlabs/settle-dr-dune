-- =============================================================================
-- DIAGNOSTIC — sample PSM3 L2 swap tx hashes for ref_code = 0 (2026)
-- =============================================================================
-- Returns 10 randomly-sampled PSM3 Swap events per chain (arbitrum, optimism,
-- unichain) from 2026 where referralCode = 0. Use these to manually verify
-- that the wallets and transactions look like genuine end-user activity and
-- not aggregator/router noise.
--
-- Addresses sourced from twa_susds_psm3_l2.sql (token_targets CTE):
--   arbitrum : PSM3 0x2B05F8e1cACC6974fD79A673a341Fe1f58d27266
--              sUSDS 0xdDb46999F8891663a8F2828d25298f70416d7610
--   optimism : PSM3 0xe0F9978b907853F354d79188A3dEfbD41978af62
--              sUSDS 0xb5B2dc7fd34C249F4be7fB1fCea07950784229e0
--   unichain : PSM3 0x7b42Ed932f26509465F7cE3FAF76FfCe1275312f
--              sUSDS 0xA06b10Db9F390990364A3984C04FaDf1c13691b5
--
-- Base is excluded (its PSM3 history times out in single-query context).
-- =============================================================================

with
    raw as (
        select 'arbitrum' as blockchain,
               s.evt_tx_hash, s.evt_block_time, s.receiver as user_addr,
               s.amountOut / 1e18 as amount_susds,
               rand() as r
        from spark_protocol_arbitrum.psm3_evt_swap s
        where s.contract_address = 0x2B05F8e1cACC6974fD79A673a341Fe1f58d27266
          and s.assetOut           = 0xdDb46999F8891663a8F2828d25298f70416d7610
          and s.referralCode       = 0
          and s.evt_block_time    >= timestamp '2026-01-01'

        union all

        select 'optimism',
               s.evt_tx_hash, s.evt_block_time, s.receiver,
               s.amountOut / 1e18,
               rand()
        from spark_protocol_optimism.psm3_evt_swap s
        where s.contract_address = 0xe0F9978b907853F354d79188A3dEfbD41978af62
          and s.assetOut           = 0xb5B2dc7fd34C249F4be7fB1fCea07950784229e0
          and s.referralCode       = 0
          and s.evt_block_time    >= timestamp '2026-01-01'

        union all

        select 'unichain',
               s.evt_tx_hash, s.evt_block_time, s.receiver,
               s.amountOut / 1e18,
               rand()
        from spark_protocol_unichain.psm3_evt_swap s
        where s.contract_address = 0x7b42Ed932f26509465F7cE3FAF76FfCe1275312f
          and s.assetOut           = 0xA06b10Db9F390990364A3984C04FaDf1c13691b5
          and s.referralCode       = 0
          and s.evt_block_time    >= timestamp '2026-01-01'
    ),

    ranked as (
        select blockchain, evt_tx_hash, evt_block_time, user_addr, amount_susds,
               row_number() over (partition by blockchain order by r) as rn
        from raw
    )

select
    blockchain,
    evt_tx_hash,
    evt_block_time,
    user_addr,
    round(amount_susds, 4) as amount_susds
from ranked
where rn <= 10
order by blockchain, evt_block_time;
