-- =============================================================================
-- DIAGNOSTIC (SIMPLE) — example wallets currently tagged ref_code = 0, by source
-- -----------------------------------------------------------------------------
-- Purpose: confirm that ref_code = 0 occurs for each Category A and Category C
-- source and give a few example wallets per source to eyeball. See
-- queries/ref_code_0_sources.md for the categorization.
--
-- DELIBERATELY SIMPLE: this only reads the (tiny) referral / swap EVENT tables.
-- ref_code is forward-filled last-wins, so a wallet's current code is just the
-- code of its LATEST referral/swap event — no transfer/balance reconstruction is
-- needed (that is what blew past Dune's stage limit). Consequently:
--   * NO balances / DR are computed (event-based only).
--   * Category B (the Template-A aggregator/intermediary fallback) is IGNORED —
--     it cannot be derived from events alone and is out of scope here.
--
-- A wallet is reported when its LATEST event code is 0 (i.e. it is currently
-- tagged 0, hence tagged 0 in 2026). `became_0_at` is that latest event's time;
-- if it is in 2026 the 0-tag was (re)set this year, otherwise it carried in.
--
-- Sources covered:
--   A: sUSDS (eth), sUSDC (eth + base/arb/op/uni), stUSDS, USDS-SKY/SPK/CLE,
--      spUSDC/spUSDT/spPYUSD/spETH (eth) + spUSDC (avax)
--   C: PSM3 sUSDS (arbitrum/optimism/unichain; Base omitted)
-- =============================================================================
with
    token_meta (blockchain, contract_address, symbol, source, category) as (
        values
            ('ethereum', 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD, 'sUSDS',   'susds_susdc', 'A'),
            ('ethereum', 0xBc65ad17c5C0a2A4D159fa5a503f4992c7B545FE, 'sUSDC',   'susds_susdc', 'A'),
            ('base',     0x3128a0f7f0ea68e7b7c9b00afa7e41045828e858, 'sUSDC',   'susds_susdc', 'A'),
            ('arbitrum', 0x940098b108fb7d0a7e374f6eded7760787464609, 'sUSDC',   'susds_susdc', 'A'),
            ('optimism', 0xcf9326e24ebffbef22ce1050007a43a3c0b6db55, 'sUSDC',   'susds_susdc', 'A'),
            ('unichain', 0x14d9143becc348920b68d123687045db49a016c6, 'sUSDC',   'susds_susdc', 'A'),
            ('ethereum', 0x99CD4Ec3f88A45940936F469E4bB72A2A701EEB9, 'stUSDS',  'stusds',      'A'),
            ('ethereum', 0x0650CAF159C5A49f711e8169D4336ECB9b950275, 'USDS-SKY','farms',       'A'),
            ('ethereum', 0x173e314C7635B45322cd8Cb14f44b312e079F3af, 'USDS-SPK','farms',       'A'),
            ('ethereum', 0x10ab606b067c9c461d8893c47c7512472e19e2ce, 'USDS-CLE','farms',       'A'),
            ('ethereum', 0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d, 'spUSDC',  'sp',          'A'),
            ('ethereum', 0xe2e7a17dFf93280dec073C995595155283e3C372, 'spUSDT',  'sp',          'A'),
            ('ethereum', 0x80128DbB9f07b93DDE62A6daeadb69ED14a7D354, 'spPYUSD', 'sp',          'A'),
            ('ethereum', 0xfE6eb3b609a7C8352A241f7F3A21CEA4e9209B8f, 'spETH',   'sp',          'A'),
            ('avalanche_c', 0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d, 'spUSDC','sp',         'A'),
            ('arbitrum', 0xdDb46999F8891663a8F2828d25298f70416d7610, 'sUSDS', 'psm3', 'C'),
            ('optimism', 0xb5B2dc7fd34C249F4be7fB1fCea07950784229e0, 'sUSDS', 'psm3', 'C'),
            ('unichain', 0xA06b10Db9F390990364A3984C04FaDf1c13691b5, 'sUSDS', 'psm3', 'C')
    ),

    -- All referral / swap events (any code), Category A + C sources. Tiny tables.
    ref_all as (
        -- A: ERC4626 / staking Referral events (owner, or user for farms)
        select 'ethereum' as blockchain, contract_address, evt_block_number, evt_index, evt_block_time, owner as user_addr, referral as ref_code from sky_ethereum.susds_evt_referral
        union all
        select 'ethereum', contract_address, evt_block_number, evt_index, evt_block_time, owner, referral from sky_ethereum.usdcvault_evt_referral
        union all
        select 'base',     contract_address, evt_block_number, evt_index, evt_block_time, owner, referral from sky_base.usdcvaultl2_evt_referral
        union all
        select 'arbitrum', contract_address, evt_block_number, evt_index, evt_block_time, owner, referral from sky_arbitrum.usdcvaultl2_evt_referral
        union all
        select 'optimism', contract_address, evt_block_number, evt_index, evt_block_time, owner, referral from sky_optimism.usdcvaultl2_evt_referral
        union all
        select 'unichain', contract_address, evt_block_number, evt_index, evt_block_time, owner, referral from sky_unichain.usdcvaultl2_evt_referral
        union all
        select 'ethereum', contract_address, evt_block_number, evt_index, evt_block_time, owner, referral from sky_ethereum.stusds_evt_referral
        union all
        select 'ethereum', contract_address, evt_block_number, evt_index, evt_block_time, "user", referral from sky_ethereum.stakingrewards_evt_referral
        union all
        select 'ethereum',    contract_address, evt_block_number, evt_index, evt_block_time, owner, referral from spark_protocol_ethereum.sparkvault_evt_referral
        union all
        select 'avalanche_c', contract_address, evt_block_number, evt_index, evt_block_time, owner, referral from spark_protocol_avalanche_c.sparkvault_evt_referral
        union all
        -- C: PSM3 L2 swaps (arb/op/uni; base omitted). Code on the Swap; contract
        -- set to the sUSDS token addr to match token_meta. referralCode < 1e9 drops
        -- malformed bytes32 mis-parses.
        select 'arbitrum', 0xdDb46999F8891663a8F2828d25298f70416d7610, s.evt_block_number, s.evt_index, s.evt_block_time, s.receiver, cast(s.referralCode as bigint)
        from spark_protocol_arbitrum.psm3_evt_swap s
        where s.contract_address = 0x2B05F8e1cACC6974fD79A673a341Fe1f58d27266
          and s.assetOut = 0xdDb46999F8891663a8F2828d25298f70416d7610 and s.referralCode < 1000000000
        union all
        select 'optimism', 0xb5B2dc7fd34C249F4be7fB1fCea07950784229e0, s.evt_block_number, s.evt_index, s.evt_block_time, s.receiver, cast(s.referralCode as bigint)
        from spark_protocol_optimism.psm3_evt_swap s
        where s.contract_address = 0xe0F9978b907853F354d79188A3dEfbD41978af62
          and s.assetOut = 0xb5B2dc7fd34C249F4be7fB1fCea07950784229e0 and s.referralCode < 1000000000
        union all
        select 'unichain', 0xA06b10Db9F390990364A3984C04FaDf1c13691b5, s.evt_block_number, s.evt_index, s.evt_block_time, s.receiver, cast(s.referralCode as bigint)
        from spark_protocol_unichain.psm3_evt_swap s
        where s.contract_address = 0x7b42Ed932f26509465F7cE3FAF76FfCe1275312f
          and s.assetOut = 0xA06b10Db9F390990364A3984C04FaDf1c13691b5 and s.referralCode < 1000000000
    ),

    -- Per-wallet event stats + a rank to find the LATEST event (its code = the
    -- wallet's current ref_code under last-wins).
    ranked as (
        select r.blockchain, r.contract_address, r.user_addr, r.ref_code, r.evt_block_time,
               count(*) over (partition by r.blockchain, r.contract_address, r.user_addr) as n_events,
               sum(case when r.ref_code = 0 then 1 else 0 end)
                   over (partition by r.blockchain, r.contract_address, r.user_addr) as n_zero_events,
               row_number()
                   over (partition by r.blockchain, r.contract_address, r.user_addr
                         order by r.evt_block_number desc, r.evt_index desc) as rn
        from ref_all r
    ),

    -- Wallets whose latest event code is 0 => currently tagged 0 (=> tagged 0 in 2026).
    tagged0 as (
        select l.blockchain, l.contract_address, l.user_addr,
               l.evt_block_time as became_0_at, l.n_events, l.n_zero_events
        from ranked l
        where l.rn = 1 and l.ref_code = 0
    ),

    -- A few examples per source/token (most recent first), plus the source total.
    examples as (
        select t0.*, m.symbol, m.source, m.category,
               count(*) over (partition by m.source, m.symbol)                                   as wallets_tagged0,
               row_number() over (partition by m.source, m.symbol order by t0.became_0_at desc)  as ex_rn
        from tagged0 t0
        join token_meta m on m.blockchain = t0.blockchain and m.contract_address = t0.contract_address
    )

select
    e.category,
    e.source,
    e.blockchain,
    e.symbol as token,
    e.wallets_tagged0,                 -- total wallets currently tagged 0 for this source/token
    e.user_addr,
    e.became_0_at,                     -- time of the wallet's latest (0) event
    (e.became_0_at >= timestamp '2026-01-01') as became_0_in_2026,
    e.n_zero_events,
    e.n_events,
    case when e.blockchain = 'ethereum' then (ec.address is not null) end as is_eth_contract
from examples e
left join ethereum.contracts ec on e.blockchain = 'ethereum' and ec.address = e.user_addr
where e.ex_rn <= 10               -- a few examples per source/token
order by e.category, e.source, e.symbol, e.became_0_at desc;
