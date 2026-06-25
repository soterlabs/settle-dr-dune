-- =============================================================================
-- DIAGNOSTIC — spPYUSD deployment-ratio decomposition (query_7683727)
-- -----------------------------------------------------------------------------
-- WHY: spPYUSD usds_base drops to $0 for ALL ref codes after ~2026-05-13 even
-- though ~$1M of spPYUSD shares still exist. usds_base = twa_shares x
-- deployment_ratio x usd_value, and a ratio of 0 zeroes every ref bucket at
-- once. deployment_ratio = greatest((total_supply - idle)/total_supply, 0), so
-- ratio = 0 means vault_idle_holdings >= vault_total_supply.
--
-- This pulls query_7683727's own factors for spPYUSD so we can see WHY the ratio
-- is 0 and whether it's STALE or REAL:
--   * vault_total_supply  : sp* shares outstanding (from query_7640321)
--   * vault_idle_holdings : underlying PYUSD sitting in the vault (forward-filled)
--   * vault_deployed      : total - idle
--   * deployment_ratio    : the multiplier applied downstream
--
-- INTERPRETATION:
--   - If vault_idle_holdings is a FLAT forward-filled value across many days (no
--     change) while supply moves, the idle series is pinned to an old underlying
--     transfer -> forward-fill overhang / stale vintage (re-run query_7683727 and
--     query_7640321 together to confirm the same vintage).
--   - If idle genuinely tracks ~total supply (PYUSD undeployed from its market),
--     then ratio 0 is CORRECT and spPYUSD simply earns no DR for those days.
--
-- Output: dt, vault_total_supply, vault_idle_holdings, vault_deployed,
--         deployment_ratio   (one row per day)
-- =============================================================================
select
    dt,
    vault_total_supply,
    vault_idle_holdings,
    vault_deployed,
    deployment_ratio
from query_7683727
where vault_symbol = 'spPYUSD'
  and blockchain   = 'ethereum'
  and dt >= date '2026-05-01'
order by dt
