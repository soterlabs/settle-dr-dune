-- =============================================================================
-- Template C: PSM3 per-user daily TWA for L2 sUSDS (Base, Arbitrum, Optimism, Unichain)
-- -----------------------------------------------------------------------------
-- On L2s, sUSDS is acquired via the PSM3 `Swap` (USDS/USDC -> sUSDS), which
-- carries the reward code inline:
--   Swap(address indexed assetIn, address indexed assetOut, address sender,
--        address indexed receiver, uint256 amountIn, uint256 amountOut,
--        uint256 referralCode)
--
-- BALANCE is tracked from ordinary sUSDS ERC20 `Transfer` events (a user's
-- balance also changes via secondary transfers, not only swaps), so we use the
-- reliable erc20_<chain>.evt_Transfer spell tables filtered to the sUSDS token.
-- REF_CODE is sourced from the PSM3 `Swap.referralCode` (matched by tx_hash +
-- receiver) and forward-filled (last-referral-wins).
--
-- Output schema matches result_spark_s_usds_s_usdc_time_weighted_average_balance.
--
-- TABLE NAMES (verified against Dune <chain>.contracts + information_schema, 2026-06):
--   PSM3 swap      -> spark_protocol_<chain>.psm3_evt_swap  (contract "PSM3";
--                     also available unified as spark_protocol_multichain.psm3_evt_swap)
--   sUSDS balance  -> erc20_<chain>.evt_Transfer (canonical) filtered to the L2 sUSDS token.
-- DECIMALS: L2 sUSDS = 18 on every chain (confirmed via tokens.erc20). L2 sUSDS
-- token addresses match the repo farm config (reconstruct-128/farms.ts).
-- PARAMETERS:
--   {{end_date}} (date) - scan cutoff, exclusive. Start is hardcoded to 2024-09-01.
--   Lower {{end_date}} (e.g. 2025-01-01) for a cheap, window-capped run;
--   default 2030-01-01 = full history. The cutoff is applied at the leaf event
--   scan so it actually reduces bytes scanned / cost.
-- =============================================================================
with
    -- #########################################################################
    -- ##                                                                     ##
    -- ##   CONTRACT ADDRESS EXCLUSION LIST                                   ##
    -- ##                                                                     ##
    -- ##   The addresses below are protocol/vault contracts that hold sUSDS  ##
    -- ##   on behalf of other users. Counting them as individual depositors  ##
    -- ##   double-counts the underlying positions they represent.            ##
    -- ##   Any address here is silently dropped from the final output so     ##
    -- ##   all downstream queries (monthly DR, diagnostics) inherit the      ##
    -- ##   exclusion automatically.                                           ##
    -- ##                                                                     ##
    -- ##   Keep this list in sync with twa_susds_susdc_erc4626.sql.         ##
    -- ##   To add a new entry: append a row below and document with a label. ##
    -- ##                                                                     ##
    -- #########################################################################
    excluded_addresses (addr) as (
        values
            -- sUSDC vault contract (holds sUSDS on behalf of sUSDC depositors)
            (0xbc65ad17c5c0a2a4d159fa5a503f4992c7b545fe),
            -- Morpho protocol contract
            (0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb),
            -- Pendle protocol contract
            (0xBe3d4ec488A0a042BB86F9176C24f8CD54018BA7),
            -- Curve PSM contract
            (0x00836Fe54625BE242BcFA286207795405ca4fD10)
    ),

    token_targets (blockchain, token_symbol, token_addr, psm3_addr, decimals, start_date) as (
        values
            ('base',     'sUSDS', 0x5875eEE11Cf8398102FdAd704C9E96607675467a, 0x1601843c5E9bC251A3272907010AFa41Fa18347E, 18, date '2024-09-01'),
            ('arbitrum', 'sUSDS', 0xdDb46999F8891663a8F2828d25298f70416d7610, 0x2B05F8e1cACC6974fD79A673a341Fe1f58d27266, 18, date '2024-09-01'),
            ('optimism', 'sUSDS', 0xb5B2dc7fd34C249F4be7fB1fCea07950784229e0, 0xe0F9978b907853F354d79188A3dEfbD41978af62, 18, date '2024-09-01'),
            ('unichain', 'sUSDS', 0xA06b10Db9F390990364A3984C04FaDf1c13691b5, 0x7b42Ed932f26509465F7cE3FAF76FfCe1275312f, 18, date '2024-09-01')
    ),

    -- -------------------------------------------------------------------------
    -- 1. PSM3 Swap events that produce sUSDS (assetOut = sUSDS token) -> ref_code.
    --    receiver = the user acquiring sUSDS. Malformed large referralCodes
    --    (bytes32 mis-parse, e.g. on arbitrum) are filtered to < 1e9. Code 0 is
    --    a legitimate "untagged via PSM3" value and is kept as-is.
    -- -------------------------------------------------------------------------
    raw_referral_events as (
        -- base. referralCode cast to bigint (kept numeric to match the other
        -- templates' ref_code; safe because malformed codes are filtered to < 1e9).
        select 'base' as blockchain, s.evt_block_number, s.evt_tx_hash, s.evt_index,
               s.receiver as user_addr, tt.token_addr as contract_address,
               cast(s.referralCode as bigint) as ref_code
        from spark_protocol_base.psm3_evt_swap s
        join token_targets tt on tt.blockchain = 'base'
            and s.contract_address = tt.psm3_addr and s.assetOut = tt.token_addr
        where s.referralCode < 1000000000 and s.evt_block_time >= date '2024-09-01' and s.evt_block_time < timestamp '{{end_date}}'
        union all
        -- arbitrum
        select 'arbitrum', s.evt_block_number, s.evt_tx_hash, s.evt_index,
               s.receiver, tt.token_addr, cast(s.referralCode as bigint)
        from spark_protocol_arbitrum.psm3_evt_swap s
        join token_targets tt on tt.blockchain = 'arbitrum'
            and s.contract_address = tt.psm3_addr and s.assetOut = tt.token_addr
        where s.referralCode < 1000000000 and s.evt_block_time >= date '2024-09-01' and s.evt_block_time < timestamp '{{end_date}}'
        union all
        -- optimism
        select 'optimism', s.evt_block_number, s.evt_tx_hash, s.evt_index,
               s.receiver, tt.token_addr, cast(s.referralCode as bigint)
        from spark_protocol_optimism.psm3_evt_swap s
        join token_targets tt on tt.blockchain = 'optimism'
            and s.contract_address = tt.psm3_addr and s.assetOut = tt.token_addr
        where s.referralCode < 1000000000 and s.evt_block_time >= date '2024-09-01' and s.evt_block_time < timestamp '{{end_date}}'
        union all
        -- unichain
        select 'unichain', s.evt_block_number, s.evt_tx_hash, s.evt_index,
               s.receiver, tt.token_addr, cast(s.referralCode as bigint)
        from spark_protocol_unichain.psm3_evt_swap s
        join token_targets tt on tt.blockchain = 'unichain'
            and s.contract_address = tt.psm3_addr and s.assetOut = tt.token_addr
        where s.referralCode < 1000000000 and s.evt_block_time >= date '2024-09-01' and s.evt_block_time < timestamp '{{end_date}}'
    ),

    latest_referral_per_tx as (
        select evt_tx_hash, user_addr, contract_address, blockchain, ref_code
        from (
            select evt_tx_hash, user_addr, contract_address, blockchain, ref_code,
                   row_number() over (
                       partition by evt_tx_hash, user_addr, contract_address, blockchain
                       order by evt_index desc
                   ) as rn
            from raw_referral_events
        ) r
        where r.rn = 1
    ),

    -- -------------------------------------------------------------------------
    -- 2. sUSDS ERC20 transfers (balance) with the tx's swap ref_code attached.
    --    Uses canonical erc20_<chain>.evt_Transfer filtered to the sUSDS token.
    -- -------------------------------------------------------------------------
    raw_transfers_with_referral as (
        -- base -- incoming
        select 'base' as blockchain, tt.token_addr as contract_address,
               tr.evt_block_time as ts, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to" as user_addr,
               cast(tr.value as double) / power(10, tt.decimals) as amount_change,
               lr.ref_code
        from erc20_base.evt_Transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'base'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'base'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- base -- outgoing
        select 'base', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from", -cast(tr.value as double) / power(10, tt.decimals), lr.ref_code
        from erc20_base.evt_Transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'base'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'base'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."from" != 0x0000000000000000000000000000000000000000

        union all
        -- arbitrum -- incoming
        select 'arbitrum', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to", cast(tr.value as double) / power(10, tt.decimals), lr.ref_code
        from erc20_arbitrum.evt_Transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'arbitrum'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'arbitrum'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- arbitrum -- outgoing
        select 'arbitrum', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from", -cast(tr.value as double) / power(10, tt.decimals), lr.ref_code
        from erc20_arbitrum.evt_Transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'arbitrum'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'arbitrum'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."from" != 0x0000000000000000000000000000000000000000

        union all
        -- optimism -- incoming
        select 'optimism', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to", cast(tr.value as double) / power(10, tt.decimals), lr.ref_code
        from erc20_optimism.evt_Transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'optimism'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'optimism'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- optimism -- outgoing
        select 'optimism', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from", -cast(tr.value as double) / power(10, tt.decimals), lr.ref_code
        from erc20_optimism.evt_Transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'optimism'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'optimism'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."from" != 0x0000000000000000000000000000000000000000

        union all
        -- unichain -- incoming
        select 'unichain', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to", cast(tr.value as double) / power(10, tt.decimals), lr.ref_code
        from erc20_unichain.evt_Transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'unichain'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'unichain'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- unichain -- outgoing
        select 'unichain', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from", -cast(tr.value as double) / power(10, tt.decimals), lr.ref_code
        from erc20_unichain.evt_Transfer tr
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

    user_final_balance as (
        select blockchain, contract_address, user_addr, end_of_day_balance as final_balance
        from (
            select blockchain, contract_address, user_addr, dt, end_of_day_balance,
                   row_number() over (
                       partition by blockchain, contract_address, user_addr
                       order by dt desc
                   ) as rn
            from daily_end_balances
        ) t
        where rn = 1
    ),

    user_date_ranges as (
        -- COST/PERF: only extend the no-transaction-day fill to current_date for
        -- users who STILL hold a balance. Users whose final balance is ~0 are
        -- trimmed at their last transaction day (they accrue nothing afterward),
        -- avoiding hundreds of zero-balance idle rows per exited user across full
        -- history (the dominant cost / 30-min-timeout cause).
        select deb.blockchain, deb.contract_address, deb.user_addr,
               min(deb.dt) as first_transaction_date,
               case when ufb.final_balance > 1e-9
                    then greatest(max(deb.dt), current_date)
                    else max(deb.dt) end as last_transaction_date
        from daily_end_balances deb
        join user_final_balance ufb
            on deb.blockchain = ufb.blockchain
            and deb.contract_address = ufb.contract_address
            and deb.user_addr = ufb.user_addr
        group by deb.blockchain, deb.contract_address, deb.user_addr, ufb.final_balance
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
  and b.user_addr not in (select addr from excluded_addresses)
order by b.blockchain, b.contract_address, b.user_addr, b.dt, b.ref_code;
