-- =============================================================================
-- DR reward-rate table ("Accessibility Rewards" XR family)
-- -----------------------------------------------------------------------------
-- Reproduces the XR / XR* / XR-stUSDS rows of Spark's query_5353955
-- (its `static_rewards` block). This is the rate applied to Distribution Rewards.
--
-- FULLY TRANSPARENT: no table dependency at all — pure hardcoded APY values
-- converted to an annualized daily rate with the same formula Spark uses
-- (apyToAnnualizedDailyRate): 365 * (exp(ln(1+apy)/365) - 1).
--
-- IMPORTANT — the DR rate depends ONLY on (token-class, date), NOT on ref_code.
-- The Spark/non-Spark "referral_type" distinction is a display label and does not
-- change the rate. (This corrects the code-tier approximation in
-- src/scripts/reconstruct-128/rates.ts.)
--
-- token -> reward_code mapping (applied downstream in dr_rewards_daily.sql):
--   stUSDS                                  -> XR-stUSDS
--   sUSDS, USDS-SKY, USDS-SPK, USDS-CLE     -> XR
--   sUSDC                                   -> XR  (boosted; see note below)
--   spUSDC, spUSDT, spPYUSD, spETH          -> XR*
--
-- sUSDC RATE NOTE: sUSDC is eligible for the full XR rate (50 bps / 0.5% APY
-- from 2026) rather than the XR* alternative rate (20 bps / 0.2% APY), because
-- the sUSDC vault holds sUSDS as its underlying asset — sUSDS depositors inside
-- sUSDC are therefore earning via the sUSDS mechanism and qualify for XR.
-- Sky Atlas reference: https://sky-atlas.io/#f2b3688f-e9e0-4159-9af3-0502141babab
-- =============================================================================
with
    reward_rates (reward_code, reward_description, reward_per_apy, start_dt, end_dt) as (
        values
            ('XR',        'Accessibility Rewards (sUSDS/USDS-SKY/USDS-SPK)',                0.006, date '2024-01-01', date '2025-12-31'),
            ('XR',        'Accessibility Rewards (sUSDS/USDS-SKY/USDS-SPK)',                0.005, date '2026-01-01', date '2030-12-31'),
            ('XR-stUSDS', 'Accessibility Rewards (stUSDS)',                                 0.006, date '2024-01-01', date '2025-12-31'),
            ('XR-stUSDS', 'Accessibility Rewards (stUSDS)',                                 0.001, date '2026-01-01', date '2030-12-31'),
            ('XR*',       'Accessibility Rewards Alternative (spUSDC/spUSDT/spPYUSD/spETH)', 0.006, date '2024-01-01', date '2025-12-31'),
            ('XR*',       'Accessibility Rewards Alternative (spUSDC/spUSDT/spPYUSD/spETH)', 0.002, date '2026-01-01', date '2030-12-31')
    )

select
    reward_code,
    reward_description,
    start_dt,
    end_dt,
    case
        when reward_per_apy = 0 then 0.0
        else 365.0 * (exp(ln(cast(1.0 + reward_per_apy as double)) / 365.0) - 1.0)
    end as reward_per
from reward_rates
order by start_dt desc, reward_code;
