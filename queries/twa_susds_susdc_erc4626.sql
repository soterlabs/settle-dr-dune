-- =============================================================================
-- Template A: ERC4626 per-user daily TWA for sUSDS + sUSDC (all chains)
-- -----------------------------------------------------------------------------
-- Mechanism: balance tracked from ERC20 `Transfer` events; ref_code sourced from
-- the separate `Referral(uint16 indexed referral, address indexed owner, ...)`
-- event, matched to its transfers by (tx_hash, owner, contract) and then
-- forward-filled (last-referral-wins). This is the SAME structure as the
-- stUSDS reference query_5358161 (see raw-queries/query_5358161.txt).
--
-- Output schema matches dune.sparkdotfi.result_spark_s_usds_s_usdc_time_weighted_average_balance.
-- Untagged balances keep the raw -999999 sentinel (reclassified downstream).
--
-- TABLE NAMES (all verified against Dune `<chain>.contracts` + information_schema,
-- 2026-06; see queries/README.md for the lookup method):
--   sUSDS eth          -> sky_ethereum.susds_evt_{transfer,referral}
--   sUSDC eth          -> sky_ethereum.usdcvault_evt_{transfer,referral}   (contract "UsdcVault")
--   sUSDC base/arb/op/uni -> sky_<chain>.usdcvaultl2_evt_{transfer,referral} (contract "UsdcVaultL2")
-- DECIMALS: sUSDS=18, sUSDC=18 on EVERY chain (confirmed via tokens.erc20).
-- PARAMETERS:
--   {{end_date}} (date) - scan cutoff, exclusive. Start is hardcoded to 2024-09-01.
--   Lower {{end_date}} (e.g. 2025-01-01) for a cheap, window-capped run;
--   default 2030-01-01 = full history. The cutoff is applied at the leaf event
--   scan so it actually reduces bytes scanned / cost.
-- =============================================================================
with
    token_targets (blockchain, token_symbol, token_addr, decimals, start_date) as (
        values
            ('ethereum', 'sUSDS', 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD, 18, date '2024-09-01'),
            ('ethereum', 'sUSDC', 0xBc65ad17c5C0a2A4D159fa5a503f4992c7B545FE, 18, date '2024-09-01'),
            ('base',     'sUSDC', 0x3128a0f7f0ea68e7b7c9b00afa7e41045828e858, 18, date '2024-09-01'),
            ('arbitrum', 'sUSDC', 0x940098b108fb7d0a7e374f6eded7760787464609, 18, date '2024-09-01'),
            ('optimism', 'sUSDC', 0xcf9326e24ebffbef22ce1050007a43a3c0b6db55, 18, date '2024-09-01'),
            ('unichain', 'sUSDC', 0x14d9143becc348920b68d123687045db49a016c6, 18, date '2024-09-01')
    ),

    -- -------------------------------------------------------------------------
    -- 1. Referral events (one row per Referral emission), per chain/contract.
    --    referral param = ref_code, owner = the depositing user.
    -- -------------------------------------------------------------------------
    raw_referral_events as (
        -- sUSDS ethereum
        select 'ethereum' as blockchain, evt_block_number, evt_tx_hash, evt_index,
               owner as user_addr, contract_address, referral as ref_code
        from sky_ethereum.susds_evt_referral
        union all
        -- sUSDC ethereum  (contract "UsdcVault")
        select 'ethereum', evt_block_number, evt_tx_hash, evt_index,
               owner, contract_address, referral
        from sky_ethereum.usdcvault_evt_referral
        union all
        -- sUSDC base  (contract "UsdcVaultL2")
        select 'base', evt_block_number, evt_tx_hash, evt_index,
               owner, contract_address, referral
        from sky_base.usdcvaultl2_evt_referral
        union all
        -- sUSDC arbitrum
        select 'arbitrum', evt_block_number, evt_tx_hash, evt_index,
               owner, contract_address, referral
        from sky_arbitrum.usdcvaultl2_evt_referral
        union all
        -- sUSDC optimism
        select 'optimism', evt_block_number, evt_tx_hash, evt_index,
               owner, contract_address, referral
        from sky_optimism.usdcvaultl2_evt_referral
        union all
        -- sUSDC unichain
        select 'unichain', evt_block_number, evt_tx_hash, evt_index,
               owner, contract_address, referral
        from sky_unichain.usdcvaultl2_evt_referral
    ),

    -- One ref_code per (tx, user, contract): latest by evt_index (last-referral-wins within tx)
    latest_referral_per_tx as (
        select evt_tx_hash, user_addr, contract_address, blockchain, ref_code
        from (
            select
                evt_tx_hash, user_addr, contract_address, blockchain, ref_code,
                row_number() over (
                    partition by evt_tx_hash, user_addr, contract_address, blockchain
                    order by evt_index desc
                ) as rn
            from raw_referral_events
        ) r
        where r.rn = 1
    ),

    -- -------------------------------------------------------------------------
    -- 2. Transfer events -> signed balance changes, with the tx's ref_code attached.
    --    Incoming (+) to `to`, outgoing (-) from `from`. Mint/burn (0x0) excluded.
    -- -------------------------------------------------------------------------
    raw_transfers_with_referral as (
        -- sUSDS ethereum (CONFIRMED) -- incoming
        select 'ethereum' as blockchain, tt.token_addr as contract_address,
               tr.evt_block_time as ts, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to" as user_addr,
               cast(tr.value as double) / power(10, tt.decimals) as amount_change,
               lr.ref_code
        from sky_ethereum.susds_evt_transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'ethereum'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'ethereum'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- sUSDS ethereum (CONFIRMED) -- outgoing
        select 'ethereum', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from",
               -cast(tr.value as double) / power(10, tt.decimals),
               lr.ref_code
        from sky_ethereum.susds_evt_transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'ethereum'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'ethereum'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."from" != 0x0000000000000000000000000000000000000000

        union all
        -- sUSDC ethereum -- incoming
        select 'ethereum', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to",
               cast(tr.value as double) / power(10, tt.decimals),
               lr.ref_code
        from sky_ethereum.usdcvault_evt_transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'ethereum'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'ethereum'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- sUSDC ethereum -- outgoing
        select 'ethereum', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from",
               -cast(tr.value as double) / power(10, tt.decimals),
               lr.ref_code
        from sky_ethereum.usdcvault_evt_transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'ethereum'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'ethereum'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."from" != 0x0000000000000000000000000000000000000000

        union all
        -- sUSDC base -- incoming
        select 'base', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to",
               cast(tr.value as double) / power(10, tt.decimals),
               lr.ref_code
        from sky_base.usdcvaultl2_evt_transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'base'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'base'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- sUSDC base -- outgoing
        select 'base', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from",
               -cast(tr.value as double) / power(10, tt.decimals),
               lr.ref_code
        from sky_base.usdcvaultl2_evt_transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'base'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'base'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."from" != 0x0000000000000000000000000000000000000000

        union all
        -- sUSDC arbitrum -- incoming
        select 'arbitrum', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to",
               cast(tr.value as double) / power(10, tt.decimals),
               lr.ref_code
        from sky_arbitrum.usdcvaultl2_evt_transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'arbitrum'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'arbitrum'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- sUSDC arbitrum -- outgoing
        select 'arbitrum', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from",
               -cast(tr.value as double) / power(10, tt.decimals),
               lr.ref_code
        from sky_arbitrum.usdcvaultl2_evt_transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'arbitrum'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'arbitrum'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."from" != 0x0000000000000000000000000000000000000000

        union all
        -- sUSDC optimism -- incoming
        select 'optimism', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to",
               cast(tr.value as double) / power(10, tt.decimals),
               lr.ref_code
        from sky_optimism.usdcvaultl2_evt_transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'optimism'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'optimism'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- sUSDC optimism -- outgoing
        select 'optimism', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from",
               -cast(tr.value as double) / power(10, tt.decimals),
               lr.ref_code
        from sky_optimism.usdcvaultl2_evt_transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'optimism'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'optimism'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."from" != 0x0000000000000000000000000000000000000000

        union all
        -- sUSDC unichain -- incoming
        select 'unichain', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to",
               cast(tr.value as double) / power(10, tt.decimals),
               lr.ref_code
        from sky_unichain.usdcvaultl2_evt_transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'unichain'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'unichain'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- sUSDC unichain -- outgoing
        select 'unichain', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from",
               -cast(tr.value as double) / power(10, tt.decimals),
               lr.ref_code
        from sky_unichain.usdcvaultl2_evt_transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'unichain'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'unichain'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."from" != 0x0000000000000000000000000000000000000000
    ),

    -- =========================================================================
    -- SHARED TWA TAIL (identical to query_5358161). Do not diverge between files.
    -- =========================================================================
    running_balances as (
        select
            blockchain, contract_address, user_addr, date(ts) as dt, ts,
            evt_block_number, evt_tx_hash, evt_index, amount_change,
            sum(amount_change) over (
                partition by blockchain, contract_address, user_addr
                order by evt_block_number asc, evt_index asc rows unbounded preceding
            ) as running_balance,
            coalesce(
                last_value(ref_code) ignore nulls over (
                    partition by blockchain, contract_address, user_addr
                    order by evt_block_number asc, evt_index asc rows unbounded preceding
                ), -999999
            ) as current_ref_code
        from raw_transfers_with_referral
    ),

    daily_end_balances as (
        select blockchain, contract_address, user_addr, dt,
               running_balance as end_of_day_balance,
               current_ref_code as end_of_day_ref_code
        from (
            select blockchain, contract_address, user_addr, dt, running_balance, current_ref_code,
                   row_number() over (
                       partition by blockchain, contract_address, user_addr, dt
                       order by evt_block_number desc, evt_index desc
                   ) as rn
            from running_balances
        ) t
        where rn = 1
    ),

    user_days as (
        select distinct blockchain, contract_address, user_addr, dt from running_balances
    ),

    daily_start_balances as (
        select
            ud.blockchain, ud.contract_address, ud.user_addr, ud.dt,
            coalesce(
                lag(deb.end_of_day_balance, 1) over (
                    partition by ud.blockchain, ud.contract_address, ud.user_addr order by ud.dt
                ), 0
            ) as start_of_day_balance,
            lag(deb.end_of_day_ref_code, 1) over (
                partition by ud.blockchain, ud.contract_address, ud.user_addr order by ud.dt
            ) as start_of_day_ref_code
        from user_days ud
        left join daily_end_balances deb
            on ud.blockchain = deb.blockchain and ud.contract_address = deb.contract_address
            and ud.user_addr = deb.user_addr and ud.dt = deb.dt
    ),

    time_weighted_balances_with_daily_start as (
        select blockchain, contract_address, user_addr, dt, ts,
               evt_block_number, evt_tx_hash, evt_index, running_balance, current_ref_code,
               'transaction' as event_type
        from running_balances
        union all
        select s.blockchain, s.contract_address, s.user_addr, s.dt,
               cast(s.dt as timestamp) as ts,
               0 as evt_block_number,
               from_hex('0000000000000000000000000000000000000000000000000000000000000000') as evt_tx_hash,
               -1 as evt_index,
               s.start_of_day_balance as running_balance,
               coalesce(s.start_of_day_ref_code, -999999) as current_ref_code,
               'daily_start' as event_type
        from daily_start_balances s
        where s.start_of_day_balance is not null
    ),

    time_weighted_balances_with_end as (
        select blockchain, contract_address, user_addr, dt, ts,
               evt_block_number, evt_tx_hash, evt_index, running_balance, current_ref_code, event_type,
               date_diff('second', ts,
                   coalesce(
                       lead(ts, 1) over (
                           partition by blockchain, contract_address, user_addr, dt
                           order by evt_block_number asc, evt_index asc
                       ),
                       dt + interval '1' day
                   )
               ) as duration_seconds
        from time_weighted_balances_with_daily_start
    ),

    daily_referral_segments as (
        select blockchain, contract_address, user_addr, dt, current_ref_code,
               case when 86400 > 0 then sum(running_balance * duration_seconds) / 86400.0 else 0 end
                   as segment_time_weighted_balance,
               sum(duration_seconds) as segment_duration_seconds,
               sum(running_balance * duration_seconds) as segment_balance_time_product
        from time_weighted_balances_with_end
        where date(ts) = dt
        group by blockchain, contract_address, user_addr, dt, current_ref_code
    ),

    user_date_ranges as (
        select blockchain, contract_address, user_addr,
               min(dt) as first_transaction_date,
               greatest(max(dt), current_date) as last_transaction_date
        from daily_end_balances
        group by 1, 2, 3
    ),

    complete_user_dates as (
        select u.blockchain, u.contract_address, u.user_addr, d.dt
        from user_date_ranges u
        cross join unnest(sequence(u.first_transaction_date, u.last_transaction_date, interval '1' day)) as d(dt)
    ),

    complete_daily_balances as (
        select
            c.blockchain, c.contract_address, c.user_addr, c.dt,
            case when drs.user_addr is not null then 'transaction_day' else 'no_transaction_day' end as day_type,
            coalesce(
                drs.current_ref_code,
                last_value(d.end_of_day_ref_code) ignore nulls over (
                    partition by c.blockchain, c.contract_address, c.user_addr order by c.dt rows unbounded preceding
                ),
                -999999
            ) as ref_code,
            coalesce(
                drs.segment_time_weighted_balance,
                last_value(d.end_of_day_balance) ignore nulls over (
                    partition by c.blockchain, c.contract_address, c.user_addr order by c.dt rows unbounded preceding
                )
            ) as time_weighted_avg_balance,
            drs.segment_duration_seconds,
            drs.segment_balance_time_product
        from complete_user_dates c
        left join daily_referral_segments drs
            on c.blockchain = drs.blockchain and c.contract_address = drs.contract_address
            and c.user_addr = drs.user_addr and c.dt = drs.dt
        left join daily_end_balances d
            on c.blockchain = d.blockchain and c.contract_address = d.contract_address
            and c.user_addr = d.user_addr and c.dt = d.dt
    )

-- Final output: per-user daily TWA, with symbol attached from token_targets.
select
    b.blockchain,
    b.contract_address,
    tt.token_symbol as symbol,
    b.user_addr,
    b.dt,
    b.ref_code,
    b.time_weighted_avg_balance,
    b.day_type,
    b.segment_duration_seconds,
    b.segment_balance_time_product
from complete_daily_balances b
join token_targets tt
    on b.blockchain = tt.blockchain and b.contract_address = tt.token_addr
where b.time_weighted_avg_balance > 0
order by b.blockchain, b.contract_address, b.user_addr, b.dt, b.ref_code;
