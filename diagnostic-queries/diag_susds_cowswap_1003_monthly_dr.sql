-- =============================================================================
-- Diagnostic: monthly DR rewards for sUSDS — CowSwap — ref_code 1003 (multi-chain)
-- =============================================================================
-- Chains: ethereum, base, arbitrum.
--   (Optimism is EXCLUDED — CoW Protocol / GPv2Settlement is not deployed there,
--    so there are no CowSwap trades to attribute. Verified via Dune catalog.)
--
-- Methodology mirrors the Amatsu Python snippet, generalised per chain:
--   * Identify txs that touch the CowSwap GPv2Settlement contract on that chain.
--   * Inside those txs, find the event that PRODUCES sUSDS for a user, and tag
--     that user with synthetic ref 1003:
--       - ethereum: sUSDS ERC4626 Deposit (mint)        -> tag Deposit.owner
--       - base/arb: PSM3 Swap with assetOut = sUSDS      -> tag Swap.receiver
--   * 1003 is forward-filled through the TWA calc; the tag is STICKY (survives
--     later untagged deposits/swaps) and is overwritten only by a later real,
--     non-zero referral code on that chain (sUSDS Referral on ETH, PSM3
--     referralCode on L2) — matching Amatsu's hasBeenTagged logic.
--
--   Rate:       sUSDS -> XR  (0.4% APY 2024-2025 non-Spark | 0.5% APY 2026+)
--   Conversion: single sUSDS share->USDS rate (Ethereum vault), applied to ALL
--               chains — same as dr_rewards_monthly_susds_susdc.sql.
--   Formula:    twa_shares / 365 * reward_per * conversion_rate = dr_usd/day
--
-- CowSwap settlement (all chains): 0x9008d19f58aabd9ed0d60971565aa8510560ab41
-- sUSDS:  ETH 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
--         BASE 0x5875eEE11Cf8398102FdAd704C9E96607675467a
--         ARB  0xdDb46999F8891663a8F2828d25298f70416d7610
-- PSM3:   BASE 0x1601843c5E9bC251A3272907010AFa41Fa18347E
--         ARB  0x2B05F8e1cACC6974fD79A673a341Fe1f58d27266
-- NOTE: L2 CowSwap->sUSDS volume is tiny vs Ethereum (~0.6%); included for
--       completeness. sUSDS is 18 decimals on every chain.
-- =============================================================================
with
    -- =========================================================================
    -- 1. CowSwap-routed sUSDS production events  ->  synthetic ref 1003
    --    (blockchain, evt_tx_hash, user_addr) = the wallet credited with sUSDS.
    -- =========================================================================
    cowswap_deposits as (
        -- ethereum: sUSDS ERC4626 Deposit (mint), tag owner
        select 'ethereum' as blockchain, d.evt_tx_hash, d.owner as user_addr
        from sky_ethereum.susds_evt_deposit d
        where d.evt_tx_hash in (
            select evt_tx_hash from gnosis_protocol_v2_ethereum.gpv2settlement_evt_trade
        )
        union all
        -- base: PSM3 Swap producing sUSDS, tag receiver
        select 'base', s.evt_tx_hash, s.receiver
        from spark_protocol_base.psm3_evt_swap s
        where s.assetOut = 0x5875eEE11Cf8398102FdAd704C9E96607675467a
          and s.evt_tx_hash in (
            select evt_tx_hash from gnosis_protocol_v2_base.gpv2settlement_evt_trade
          )
        union all
        -- arbitrum: PSM3 Swap producing sUSDS, tag receiver
        select 'arbitrum', s.evt_tx_hash, s.receiver
        from spark_protocol_arbitrum.psm3_evt_swap s
        where s.assetOut = 0xdDb46999F8891663a8F2828d25298f70416d7610
          and s.evt_tx_hash in (
            select evt_tx_hash from gnosis_protocol_v2_arbitrum.gpv2settlement_evt_trade
          )
    ),

    wallets_cowswap as (
        select distinct blockchain, user_addr
        from cowswap_deposits
    ),

    -- =========================================================================
    -- 2a. Latest real (non-zero) referral per (chain, tx, user) for tagged
    --     wallets. Overwrites 1003 last-wins; zero/untagged never overwrites.
    --       ethereum: sUSDS Referral event
    --       base/arb: PSM3 Swap.referralCode (well-formed, non-zero)
    -- =========================================================================
    real_refs as (
        select blockchain, evt_tx_hash, user_addr, ref_code
        from (
            select 'ethereum' as blockchain, evt_tx_hash, owner as user_addr,
                   referral as ref_code,
                   row_number() over (
                       partition by evt_tx_hash, owner order by evt_index desc
                   ) as rn
            from sky_ethereum.susds_evt_referral
            where contract_address = 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
              and referral <> 0
              and owner in (select user_addr from wallets_cowswap where blockchain = 'ethereum')
        ) t where rn = 1
        union all
        select blockchain, evt_tx_hash, user_addr, ref_code
        from (
            select 'base' as blockchain, evt_tx_hash, receiver as user_addr,
                   cast(referralCode as bigint) as ref_code,
                   row_number() over (
                       partition by evt_tx_hash, receiver order by evt_index desc
                   ) as rn
            from spark_protocol_base.psm3_evt_swap
            where assetOut = 0x5875eEE11Cf8398102FdAd704C9E96607675467a
              and referralCode <> 0 and referralCode < 1000000000
              and receiver in (select user_addr from wallets_cowswap where blockchain = 'base')
        ) t where rn = 1
        union all
        select blockchain, evt_tx_hash, user_addr, ref_code
        from (
            select 'arbitrum' as blockchain, evt_tx_hash, receiver as user_addr,
                   cast(referralCode as bigint) as ref_code,
                   row_number() over (
                       partition by evt_tx_hash, receiver order by evt_index desc
                   ) as rn
            from spark_protocol_arbitrum.psm3_evt_swap
            where assetOut = 0xdDb46999F8891663a8F2828d25298f70416d7610
              and referralCode <> 0 and referralCode < 1000000000
              and receiver in (select user_addr from wallets_cowswap where blockchain = 'arbitrum')
        ) t where rn = 1
    ),

    -- =========================================================================
    -- 2b. Signed sUSDS transfers for tagged wallets, per chain.
    --     ref_code_on_tx priority: CowSwap-produced sUSDS -> 1003;
    --     else real non-zero referral for this (chain, tx, user); else NULL.
    -- =========================================================================
    raw_transfers as (
        -- ethereum -- incoming
        select 'ethereum' as blockchain, tr.evt_block_time as ts,
               tr.evt_block_number, tr.evt_index, tr."to" as user_addr,
               cast(tr.value as double) / 1e18 as amount_change,
               coalesce(
                   case when exists (
                       select 1 from cowswap_deposits cd
                       where cd.blockchain = 'ethereum'
                         and cd.evt_tx_hash = tr.evt_tx_hash
                         and cd.user_addr   = tr."to"
                   ) then 1003 end,
                   rr.ref_code
               ) as ref_code_on_tx
        from sky_ethereum.susds_evt_transfer tr
        left join real_refs rr on rr.blockchain = 'ethereum'
            and rr.evt_tx_hash = tr.evt_tx_hash and rr.user_addr = tr."to"
        where tr.contract_address = 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
          and tr."to" in (select user_addr from wallets_cowswap where blockchain = 'ethereum')
          and tr."to" != 0x0000000000000000000000000000000000000000
          and tr."to" != tr."from"
        union all
        -- ethereum -- outgoing
        select 'ethereum', tr.evt_block_time, tr.evt_block_number, tr.evt_index,
               tr."from", -cast(tr.value as double) / 1e18,
               coalesce(
                   case when exists (
                       select 1 from cowswap_deposits cd
                       where cd.blockchain = 'ethereum'
                         and cd.evt_tx_hash = tr.evt_tx_hash
                         and cd.user_addr   = tr."from"
                   ) then 1003 end,
                   rr.ref_code
               )
        from sky_ethereum.susds_evt_transfer tr
        left join real_refs rr on rr.blockchain = 'ethereum'
            and rr.evt_tx_hash = tr.evt_tx_hash and rr.user_addr = tr."from"
        where tr.contract_address = 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
          and tr."from" in (select user_addr from wallets_cowswap where blockchain = 'ethereum')
          and tr."from" != 0x0000000000000000000000000000000000000000
          and tr."from" != tr."to"

        union all
        -- base -- incoming
        select 'base', tr.evt_block_time, tr.evt_block_number, tr.evt_index,
               tr."to", cast(tr.value as double) / 1e18,
               coalesce(
                   case when exists (
                       select 1 from cowswap_deposits cd
                       where cd.blockchain = 'base'
                         and cd.evt_tx_hash = tr.evt_tx_hash
                         and cd.user_addr   = tr."to"
                   ) then 1003 end,
                   rr.ref_code
               )
        from erc20_base.evt_Transfer tr
        left join real_refs rr on rr.blockchain = 'base'
            and rr.evt_tx_hash = tr.evt_tx_hash and rr.user_addr = tr."to"
        where tr.contract_address = 0x5875eEE11Cf8398102FdAd704C9E96607675467a
          and tr."to" in (select user_addr from wallets_cowswap where blockchain = 'base')
          and tr."to" != 0x0000000000000000000000000000000000000000
          and tr."to" != tr."from"
        union all
        -- base -- outgoing
        select 'base', tr.evt_block_time, tr.evt_block_number, tr.evt_index,
               tr."from", -cast(tr.value as double) / 1e18,
               coalesce(
                   case when exists (
                       select 1 from cowswap_deposits cd
                       where cd.blockchain = 'base'
                         and cd.evt_tx_hash = tr.evt_tx_hash
                         and cd.user_addr   = tr."from"
                   ) then 1003 end,
                   rr.ref_code
               )
        from erc20_base.evt_Transfer tr
        left join real_refs rr on rr.blockchain = 'base'
            and rr.evt_tx_hash = tr.evt_tx_hash and rr.user_addr = tr."from"
        where tr.contract_address = 0x5875eEE11Cf8398102FdAd704C9E96607675467a
          and tr."from" in (select user_addr from wallets_cowswap where blockchain = 'base')
          and tr."from" != 0x0000000000000000000000000000000000000000
          and tr."from" != tr."to"

        union all
        -- arbitrum -- incoming
        select 'arbitrum', tr.evt_block_time, tr.evt_block_number, tr.evt_index,
               tr."to", cast(tr.value as double) / 1e18,
               coalesce(
                   case when exists (
                       select 1 from cowswap_deposits cd
                       where cd.blockchain = 'arbitrum'
                         and cd.evt_tx_hash = tr.evt_tx_hash
                         and cd.user_addr   = tr."to"
                   ) then 1003 end,
                   rr.ref_code
               )
        from erc20_arbitrum.evt_Transfer tr
        left join real_refs rr on rr.blockchain = 'arbitrum'
            and rr.evt_tx_hash = tr.evt_tx_hash and rr.user_addr = tr."to"
        where tr.contract_address = 0xdDb46999F8891663a8F2828d25298f70416d7610
          and tr."to" in (select user_addr from wallets_cowswap where blockchain = 'arbitrum')
          and tr."to" != 0x0000000000000000000000000000000000000000
          and tr."to" != tr."from"
        union all
        -- arbitrum -- outgoing
        select 'arbitrum', tr.evt_block_time, tr.evt_block_number, tr.evt_index,
               tr."from", -cast(tr.value as double) / 1e18,
               coalesce(
                   case when exists (
                       select 1 from cowswap_deposits cd
                       where cd.blockchain = 'arbitrum'
                         and cd.evt_tx_hash = tr.evt_tx_hash
                         and cd.user_addr   = tr."from"
                   ) then 1003 end,
                   rr.ref_code
               )
        from erc20_arbitrum.evt_Transfer tr
        left join real_refs rr on rr.blockchain = 'arbitrum'
            and rr.evt_tx_hash = tr.evt_tx_hash and rr.user_addr = tr."from"
        where tr.contract_address = 0xdDb46999F8891663a8F2828d25298f70416d7610
          and tr."from" in (select user_addr from wallets_cowswap where blockchain = 'arbitrum')
          and tr."from" != 0x0000000000000000000000000000000000000000
          and tr."from" != tr."to"
    ),

    -- =========================================================================
    -- 3. TWA tail — partitioned by (blockchain, user_addr)
    -- =========================================================================
    running_balances as (
        select
            blockchain, user_addr, ts, evt_block_number, evt_index,
            date(ts) as dt,
            sum(amount_change) over (
                partition by blockchain, user_addr
                order by evt_block_number asc, evt_index asc
                rows unbounded preceding
            ) as running_balance,
            coalesce(
                last_value(ref_code_on_tx) ignore nulls over (
                    partition by blockchain, user_addr
                    order by evt_block_number asc, evt_index asc
                    rows unbounded preceding
                ), -999999
            ) as current_ref_code
        from raw_transfers
    ),

    daily_end_balances as (
        select blockchain, user_addr, dt,
               running_balance   as end_of_day_balance,
               current_ref_code  as end_of_day_ref_code
        from (
            select blockchain, user_addr, dt, running_balance, current_ref_code,
                   row_number() over (
                       partition by blockchain, user_addr, dt
                       order by evt_block_number desc, evt_index desc
                   ) as rn
            from running_balances
        ) t
        where rn = 1
    ),

    user_days as (
        select distinct blockchain, user_addr, dt from running_balances
    ),

    daily_start_balances as (
        select
            ud.blockchain, ud.user_addr, ud.dt,
            coalesce(
                lag(deb.end_of_day_balance) over (
                    partition by ud.blockchain, ud.user_addr order by ud.dt
                ), 0
            ) as start_of_day_balance,
            lag(deb.end_of_day_ref_code) over (
                partition by ud.blockchain, ud.user_addr order by ud.dt
            ) as start_of_day_ref_code
        from user_days ud
        left join daily_end_balances deb
            on ud.blockchain = deb.blockchain and ud.user_addr = deb.user_addr
           and ud.dt = deb.dt
    ),

    events_with_daily_start as (
        select blockchain, user_addr, ts, evt_block_number, evt_index,
               running_balance, current_ref_code, dt
        from running_balances
        union all
        select blockchain, user_addr,
               cast(dt as timestamp),
               0, -1,
               start_of_day_balance,
               coalesce(start_of_day_ref_code, -999999),
               dt
        from daily_start_balances
        where start_of_day_balance is not null
    ),

    events_with_duration as (
        select blockchain, user_addr, ts, dt, evt_block_number, evt_index,
               running_balance, current_ref_code,
               date_diff('second', ts,
                   coalesce(
                       lead(ts) over (
                           partition by blockchain, user_addr, dt
                           order by evt_block_number asc, evt_index asc
                       ),
                       dt + interval '1' day
                   )
               ) as duration_seconds
        from events_with_daily_start
    ),

    daily_ref_segments as (
        select blockchain, user_addr, dt, current_ref_code,
               sum(running_balance * duration_seconds) / 86400.0 as twa_balance
        from events_with_duration
        where date(ts) = dt
        group by blockchain, user_addr, dt, current_ref_code
    ),

    user_final_balance as (
        select blockchain, user_addr, end_of_day_balance as final_balance
        from (
            select blockchain, user_addr, dt, end_of_day_balance,
                   row_number() over (
                       partition by blockchain, user_addr order by dt desc
                   ) as rn
            from daily_end_balances
        ) t
        where rn = 1
    ),

    user_date_ranges as (
        select deb.blockchain, deb.user_addr,
               min(deb.dt) as first_dt,
               case when ufb.final_balance > 1e-9
                    then greatest(max(deb.dt), current_date)
                    else max(deb.dt)
               end as last_dt
        from daily_end_balances deb
        join user_final_balance ufb
            on deb.blockchain = ufb.blockchain and deb.user_addr = ufb.user_addr
        group by deb.blockchain, deb.user_addr, ufb.final_balance
    ),

    date_spine as (
        select u.blockchain, u.user_addr, d.dt
        from user_date_ranges u
        cross join unnest(sequence(u.first_dt, u.last_dt, interval '1' day)) as d(dt)
    ),

    complete_daily_all as (
        select
            sp.blockchain,
            sp.user_addr,
            sp.dt,
            case
                when seg.dt is not null
                    then seg.twa_balance
                when drs_any.dt is not null
                    then 0.0
                else
                    last_value(deb.end_of_day_balance) ignore nulls over (
                        partition by sp.blockchain, sp.user_addr
                        order by sp.dt
                        rows unbounded preceding
                    )
            end as twa_balance,
            coalesce(
                last_value(deb.end_of_day_ref_code) ignore nulls over (
                    partition by sp.blockchain, sp.user_addr
                    order by sp.dt
                    rows unbounded preceding
                ),
                -999999
            ) as forwarded_ref_code
        from date_spine sp
        left join (select distinct blockchain, user_addr, dt from daily_ref_segments) drs_any
            on sp.blockchain = drs_any.blockchain and sp.user_addr = drs_any.user_addr
           and sp.dt = drs_any.dt
        left join daily_ref_segments seg
            on sp.blockchain = seg.blockchain and sp.user_addr = seg.user_addr
           and sp.dt = seg.dt and seg.current_ref_code = 1003
        left join daily_end_balances deb
            on sp.blockchain = deb.blockchain and sp.user_addr = deb.user_addr
           and sp.dt = deb.dt
    ),

    complete_daily as (
        select blockchain, user_addr, dt, twa_balance
        from complete_daily_all
        where forwarded_ref_code = 1003
          and twa_balance > 0
    ),

    -- =========================================================================
    -- 4. XR rate — sUSDS (XR: 0.4% APY 2024-2025 non-Spark | 0.5% APY 2026+)
    -- =========================================================================
    xr_rates (start_dt, end_dt, apy, reward_per) as (
        values
            (date '2024-01-01', date '2025-12-31',
             0.004,
             365.0 * (exp(ln(1.004) / 365.0) - 1.0)),
            (date '2026-01-01', date '2030-12-31',
             0.005,
             365.0 * (exp(ln(1.005) / 365.0) - 1.0))
    ),

    -- =========================================================================
    -- 5. sUSDS conversion rate (Ethereum vault) — applied to ALL chains
    -- =========================================================================
    susds_raw_rates as (
        select evt_block_time,
               cast(assets as double) / cast(shares as double) as rate
        from sky_ethereum.susds_evt_deposit
        where shares > 0
        union all
        select evt_block_time,
               cast(assets as double) / cast(shares as double)
        from sky_ethereum.susds_evt_withdraw
        where shares > 0
    ),

    daily_conversion_raw as (
        select date(evt_block_time) as dt,
               max_by(rate, evt_block_time) as rate
        from susds_raw_rates
        group by date(evt_block_time)
    ),

    conversion_spine as (
        select dt
        from unnest(sequence(
            date '2024-09-04',
            current_date,
            interval '1' day
        )) as t(dt)
    ),

    daily_conversion as (
        select s.dt,
               coalesce(
                   r.rate,
                   last_value(r.rate) ignore nulls over (
                       order by s.dt rows unbounded preceding
                   ),
                   1.0
               ) as susds_rate
        from conversion_spine s
        left join daily_conversion_raw r on s.dt = r.dt
    ),

    -- =========================================================================
    -- 6. Daily DR
    -- =========================================================================
    daily_dr as (
        select
            c.blockchain,
            c.user_addr,
            c.dt,
            c.twa_balance                                              as twa_shares,
            c.twa_balance * conv.susds_rate                            as twa_usds,
            c.twa_balance / 365.0 * xr.reward_per * conv.susds_rate    as dr_usd,
            xr.apy,
            xr.reward_per,
            conv.susds_rate
        from complete_daily c
        join xr_rates        xr   on c.dt between xr.start_dt and xr.end_dt
        join daily_conversion conv on c.dt = conv.dt
    )

-- =========================================================================
-- 7. Monthly totals — per chain AND an all-chain rollup (blockchain = 'ALL')
-- =========================================================================
select
    month,
    coalesce(blockchain, 'ALL')      as blockchain,
    1003                             as ref_code,
    'sUSDS'                          as token,
    count(distinct user_addr)        as active_wallets,
    round(avg(twa_usds),    2)       as avg_daily_twa_usds,
    round(sum(twa_usds),    2)       as sum_twa_usds,
    round(sum(dr_usd),      4)       as dr_usd,
    min(apy)                         as apy,
    min(reward_per)                  as reward_per,
    avg(susds_rate)                  as avg_conversion_rate
from (
    select date_trunc('month', dt) as month, blockchain, user_addr,
           twa_usds, dr_usd, apy, reward_per, susds_rate
    from daily_dr
) m
group by grouping sets (
    (month, blockchain),
    (month)
)
order by month, blockchain
