-- =============================================================================
-- Layer 3b: sp* vault deployment ratio per (blockchain, vault_symbol, day)
-- -----------------------------------------------------------------------------
-- Reproduces query_6398769 using entirely self-owned sources — no dependency on
-- any opaque dune.sparkdotfi.result_spark_* dataset:
--
--   vault_idle_holdings  ← inlined from query_6619793:
--                          per-day TWA of the underlying token (USDC/USDT/PYUSD)
--                          held BY the vault contract itself — i.e. assets that
--                          have arrived via deposit but not yet been deployed into
--                          a lending market. Sourced from ERC20 Transfer events
--                          TO/FROM the vault's own address.
--
--   vault_total_supply   ← query_7640321 (twa_sp_vaults.sql):
--                          sum of per-user daily sp* share TWA balances, aggregated
--                          to vault level. Matches result_spark_sp_usdc_sp_usdt_
--                          sp_eth_time_weighted_average_balance. spETH excluded.
--
-- deployment_ratio = greatest((vault_total_supply - vault_idle_holdings)
--                             / vault_total_supply, 0)
--
-- TWA formula for idle holdings matches query_6619793 exactly:
--   transaction days    : sum(balance * duration_s) / sum(duration_s)  [NOT /86400]
--   no-transaction days : forward-fill of last end-of-day balance
-- Calendar always extends to current_date (vault always exists — no balance-trim).
--
-- Units note: vault_total_supply is in vault SHARES (spUSDC, etc., 6 dec),
-- vault_idle_holdings is in UNDERLYING tokens (USDC, etc., 6 dec). Share price
-- ≈ 1 at launch and drifts slowly; this matches the approximation in query_6398769.
--
-- Output columns: blockchain, vault_symbol, dt, vault_total_supply,
--                 vault_idle_holdings, vault_deployed, deployment_ratio
-- Grain: one row per (blockchain, vault_symbol, dt). spETH excluded.
--
-- SAVED AS: query_7683727  (https://dune.com/queries/7683727)
-- =============================================================================
with
    vault_tokens (blockchain, vault_symbol, vault_addr, underlying_addr, underlying_decimals, start_date) as (
        values
            ('ethereum',    'spUSDC',  0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d, 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48, 6, date '2025-10-01'),
            ('ethereum',    'spUSDT',  0xe2e7a17dFf93280dec073C995595155283e3C372, 0xdAC17F958D2ee523a2206206994597C13D831ec7, 6, date '2025-10-01'),
            ('ethereum',    'spPYUSD', 0x80128DbB9f07b93DDE62A6daeadb69ED14a7D354, 0x6c3ea9036406852006290770BEdFcAbA0e23A0e8, 6, date '2025-12-01'),
            ('avalanche_c', 'spUSDC',  0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d, 0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E, 6, date '2025-10-08')
    ),

    -- =========================================================================
    -- Idle holdings: underlying token balance held BY each vault contract.
    -- Inlined from query_6619793 (replaces dune.sparkdotfi.result_spark_savings_
    -- v_2_vaults_time_weighted_average_holdings). spETH omitted — no underlying
    -- token flow tracked for it and query_6398769 already excludes it.
    -- =========================================================================
    underlying_transfers as (
        -- ethereum: incoming underlying (deposited into vault = idle arrives)
        select vt.blockchain, vt.vault_symbol,
               tr.evt_block_date              as dt,
               tr.evt_block_time              as ts,
               tr.evt_block_number,
               tr.evt_index,
               cast(tr.value as double) / power(10, vt.underlying_decimals) as amount_change
        from erc20_ethereum.evt_Transfer tr
        join vault_tokens vt
            on tr.contract_address = vt.underlying_addr
           and tr."to"             = vt.vault_addr
           and vt.blockchain       = 'ethereum'
        where tr.evt_block_date >= vt.start_date
          and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- ethereum: outgoing underlying (deployed to lending market = idle shrinks)
        select vt.blockchain, vt.vault_symbol,
               tr.evt_block_date,
               tr.evt_block_time, tr.evt_block_number, tr.evt_index,
               -cast(tr.value as double) / power(10, vt.underlying_decimals)
        from erc20_ethereum.evt_Transfer tr
        join vault_tokens vt
            on tr.contract_address = vt.underlying_addr
           and tr."from"           = vt.vault_addr
           and vt.blockchain       = 'ethereum'
        where tr.evt_block_date >= vt.start_date
          and tr."from" != 0x0000000000000000000000000000000000000000
        union all
        -- avalanche_c: incoming
        select vt.blockchain, vt.vault_symbol,
               date(tr.evt_block_time),
               tr.evt_block_time, tr.evt_block_number, tr.evt_index,
               cast(tr.value as double) / power(10, vt.underlying_decimals)
        from erc20_avalanche_c.evt_Transfer tr
        join vault_tokens vt
            on tr.contract_address = vt.underlying_addr
           and tr."to"             = vt.vault_addr
           and vt.blockchain       = 'avalanche_c'
        where date(tr.evt_block_time) >= vt.start_date
          and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- avalanche_c: outgoing
        select vt.blockchain, vt.vault_symbol,
               date(tr.evt_block_time),
               tr.evt_block_time, tr.evt_block_number, tr.evt_index,
               -cast(tr.value as double) / power(10, vt.underlying_decimals)
        from erc20_avalanche_c.evt_Transfer tr
        join vault_tokens vt
            on tr.contract_address = vt.underlying_addr
           and tr."from"           = vt.vault_addr
           and vt.blockchain       = 'avalanche_c'
        where date(tr.evt_block_time) >= vt.start_date
          and tr."from" != 0x0000000000000000000000000000000000000000
    ),

    running_idle as (
        select blockchain, vault_symbol, dt, ts, evt_block_number, evt_index,
               sum(amount_change) over (
                   partition by blockchain, vault_symbol
                   order by evt_block_number asc, evt_index asc
                   rows unbounded preceding
               ) as running_balance
        from underlying_transfers
    ),

    daily_end_idle as (
        select blockchain, vault_symbol, dt, running_balance as end_of_day_balance
        from (
            select blockchain, vault_symbol, dt, running_balance,
                   row_number() over (
                       partition by blockchain, vault_symbol, dt
                       order by evt_block_number desc, evt_index desc
                   ) as rn
            from running_idle
        ) t
        where rn = 1
    ),

    vault_tx_days as (
        select distinct blockchain, vault_symbol, dt from running_idle
    ),

    daily_start_idle as (
        select vd.blockchain, vd.vault_symbol, vd.dt,
               coalesce(
                   lag(dei.end_of_day_balance) over (
                       partition by vd.blockchain, vd.vault_symbol order by vd.dt
                   ), 0
               ) as start_of_day_balance
        from vault_tx_days vd
        left join daily_end_idle dei
            on vd.blockchain    = dei.blockchain
           and vd.vault_symbol  = dei.vault_symbol
           and vd.dt            = dei.dt
    ),

    all_idle_events as (
        select blockchain, vault_symbol, dt, ts, evt_block_number, evt_index, running_balance
        from running_idle
        union all
        select s.blockchain, s.vault_symbol, s.dt,
               cast(s.dt as timestamp) as ts,
               0                       as evt_block_number,
               -1                      as evt_index,
               s.start_of_day_balance  as running_balance
        from daily_start_idle s
        where s.start_of_day_balance is not null
    ),

    event_durations as (
        select blockchain, vault_symbol, dt, running_balance,
               date_diff('second', ts,
                   coalesce(
                       lead(ts) over (
                           partition by blockchain, vault_symbol, dt
                           order by evt_block_number, evt_index
                       ),
                       dt + interval '1' day
                   )
               ) as duration_seconds
        from all_idle_events
        where date(ts) = dt
    ),

    twa_tx_days as (
        -- Matches query_6619793: divide by actual covered seconds, not /86400.
        -- For a full day (start-of-day event present), sum(duration_s) = 86400.
        select blockchain, vault_symbol, dt,
               sum(running_balance * duration_seconds)
                   / nullif(sum(duration_seconds), 0) as twa_idle
        from event_durations
        group by blockchain, vault_symbol, dt
    ),

    -- Calendar always extends to current_date: vault contract always exists
    -- (mirrors query_6619793's user_date_ranges which uses greatest(max, current_date)
    -- unconditionally, unlike our user-level queries that trim exited users).
    vault_date_ranges as (
        select blockchain, vault_symbol,
               min(dt)                      as first_dt,
               greatest(max(dt), current_date) as last_dt
        from daily_end_idle
        group by blockchain, vault_symbol
    ),

    calendar as (
        select v.blockchain, v.vault_symbol, d.dt
        from vault_date_ranges v
        cross join unnest(sequence(v.first_dt, v.last_dt, interval '1' day)) as d(dt)
    ),

    vault_idle_daily as (
        select c.blockchain, c.vault_symbol, c.dt,
               coalesce(
                   t.twa_idle,
                   last_value(dei.end_of_day_balance) ignore nulls over (
                       partition by c.blockchain, c.vault_symbol
                       order by c.dt
                       rows unbounded preceding
                   ),
                   0
               ) as vault_idle_holdings
        from calendar c
        left join twa_tx_days t
            on c.blockchain    = t.blockchain
           and c.vault_symbol  = t.vault_symbol
           and c.dt            = t.dt
        left join daily_end_idle dei
            on c.blockchain    = dei.blockchain
           and c.vault_symbol  = dei.vault_symbol
           and c.dt            = dei.dt
    ),

    -- =========================================================================
    -- Vault total supply: our self-owned twa_sp_vaults (query_7640321) aggregated
    -- to vault level. Matches result_spark_sp_usdc_sp_usdt_sp_eth_time_weighted_
    -- average_balance summed across users. spETH excluded.
    -- =========================================================================
    vault_totals as (
        select blockchain,
               symbol as vault_symbol,
               dt,
               sum(time_weighted_avg_balance) as vault_total_supply
        from query_7640321
        where symbol != 'spETH'
        group by blockchain, symbol, dt
    )

-- *** DEPLOYMENT RATIO SCOPE — spUSDC ONLY ***
-- spUSDT and spPYUSD are forced to idle = 0 / deployment_ratio = 1. Those vaults
-- hold their underlying directly (no idle/deployed split), so the idle-vs-deployed
-- model — and the USDT/PYUSD-in-vault "idle" measured above — is meaningless for
-- them and was spuriously driving their ratio toward 0 (zeroing all their DR).
-- Only spUSDC keeps the computed ratio. This DIVERGES from Spark (query_6398769
-- applies the ratio to all non-spETH vaults) and is a deliberate, TO-BE-CONFIRMED
-- choice. The idle CTEs above still run for all vaults but are ignored for
-- spUSDT/spPYUSD here.
select
    vt.blockchain,
    vt.vault_symbol,
    vt.dt,
    vt.vault_total_supply,
    case
        when vt.vault_symbol in ('spUSDT', 'spPYUSD') then 0.0
        else coalesce(vi.vault_idle_holdings, 0)
    end                                                                        as vault_idle_holdings,
    case
        when vt.vault_symbol in ('spUSDT', 'spPYUSD') then vt.vault_total_supply
        else vt.vault_total_supply - coalesce(vi.vault_idle_holdings, 0)
    end                                                                        as vault_deployed,
    case
        when vt.vault_symbol in ('spUSDT', 'spPYUSD') then 1.0
        when vt.vault_total_supply > 0 then
            greatest(
                (vt.vault_total_supply - coalesce(vi.vault_idle_holdings, 0))
                    / vt.vault_total_supply,
                0
            )
        else 0
    end                                                                        as deployment_ratio
from vault_totals vt
left join vault_idle_daily vi
    on vt.blockchain   = vi.blockchain
   and vt.vault_symbol = vi.vault_symbol
   and vt.dt           = vi.dt
order by vt.dt, vt.blockchain, vt.vault_symbol
