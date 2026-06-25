-- =============================================================================
-- DIAGNOSTIC — spPYUSD ref-128 wallets: factor decomposition (May 2026 onward)
-- -----------------------------------------------------------------------------
-- WHY: the by-ref chart shows spPYUSD ref_code 128 AND untagged both dropping
-- to $0 after ~2026-05-13, even though ~$1M of spPYUSD still exists on-chain.
--
-- KEY INSIGHT: if the balance had merely "become untagged", the untagged bucket
-- would be NON-zero. Both buckets being $0 at once means a MULTIPLICATIVE factor
-- is going to zero for ALL ref codes — almost certainly the deployment ratio.
-- usds_base = twa_shares x deployment_ratio x usd_value, and the monthly/diag
-- queries use coalesce(deployment_ratio, 0): if query_7683727 has no spPYUSD row
-- (or returns 0) after May 13, every ref bucket zeroes simultaneously while the
-- TWA shares (query_7640321) are untouched.
--
-- This query decomposes the three factors per (wallet, day) so we can see WHICH
-- one drops:
--   twa_shares       -> does the raw balance persist after May 13?  (query_7640321)
--   deployment_ratio -> prime suspect; null/0 after May 13?         (query_7683727)
--   usd_value        -> share->USD price present?                   (query_7640325)
--   usds_base        -> the product actually charted.
--
-- Target wallets = any wallet that held spPYUSD under ref_code 128 during
-- May 2026. If rows simply STOP for a wallet (rather than continuing with a
-- zeroed factor), the cause is upstream trimming/filtering in twa_sp_vaults.sql
-- (the balance>0 filter + exited-user date-range trim) — escalate to a raw
-- erc20-Transfer trace next.
--
-- Output: dt, user_addr, ref_code, day_type, twa_shares, deployment_ratio,
--         usd_value, usds_base   (one row per wallet per day)
-- =============================================================================
with
    target_wallets as (
        select distinct user_addr
        from query_7640321
        where symbol = 'spPYUSD'
          and ref_code = 128
          and dt >= date '2026-05-01'
          and dt <  date '2026-06-01'
    )
select
    b.dt,
    b.user_addr,
    b.ref_code,
    b.day_type,
    b.time_weighted_avg_balance                      as twa_shares,
    dr.deployment_ratio,
    csp.usd_value,
    b.time_weighted_avg_balance
        * coalesce(dr.deployment_ratio, 0)
        * coalesce(csp.usd_value, 1)                 as usds_base
from query_7640321 b
join target_wallets w
    on b.user_addr = w.user_addr
left join query_7683727 dr
    on dr.dt = b.dt and dr.blockchain = b.blockchain and dr.vault_symbol = b.symbol
left join query_7640325 csp
    on csp.dt = b.dt and csp.token_symbol = b.symbol and csp.blockchain = b.blockchain
where b.symbol = 'spPYUSD'
  and b.dt >= date '2026-05-01'
order by b.user_addr, b.dt
