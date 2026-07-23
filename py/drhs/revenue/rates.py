"""DR reward-rate table (XR / XR* / XR-stUSDS) — Layer 3a.

Port of queries/rates_dr.sql (Dune 7877547): FULLY hardcoded APY values, no data
dependency. APY -> annualized daily rate via Spark's apyToAnnualizedDailyRate:
    reward_per = 365 * (exp(ln(1 + apy) / 365) - 1)

The rate depends ONLY on (reward_code, date), never on ref_code. Token ->
reward_code mapping (applied when joining to the TWA):
    stUSDS                                    -> XR-stUSDS
    sUSDS, sUSDC, USDS-SKY, USDS-SPK, USDS-CLE-> XR   (sUSDC boosted to XR: its
                                                       vault holds sUSDS)
    spUSDC, spUSDT, spPYUSD, spETH            -> XR*
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd

# (reward_code, reward_description, apy, start_dt, end_dt) — verbatim from rates_dr.sql
REWARD_SCHEDULE = [
    ("XR", "Accessibility Rewards (sUSDS/USDS-SKY/USDS-SPK)", 0.006, date(2024, 1, 1), date(2025, 12, 31)),
    ("XR", "Accessibility Rewards (sUSDS/USDS-SKY/USDS-SPK)", 0.005, date(2026, 1, 1), date(2030, 12, 31)),
    ("XR-stUSDS", "Accessibility Rewards (stUSDS)", 0.006, date(2024, 1, 1), date(2025, 12, 31)),
    ("XR-stUSDS", "Accessibility Rewards (stUSDS)", 0.001, date(2026, 1, 1), date(2030, 12, 31)),
    ("XR*", "Accessibility Rewards Alternative (spUSDC/spUSDT/spPYUSD/spETH)", 0.006, date(2024, 1, 1), date(2025, 12, 31)),
    ("XR*", "Accessibility Rewards Alternative (spUSDC/spUSDT/spPYUSD/spETH)", 0.002, date(2026, 1, 1), date(2030, 12, 31)),
]

TOKEN_REWARD_CODE = {
    "stUSDS": "XR-stUSDS",
    "sUSDS": "XR", "sUSDC": "XR",
    "USDS-SKY": "XR", "USDS-SPK": "XR", "USDS-CLE": "XR",
    "spUSDC": "XR*", "spUSDT": "XR*", "spPYUSD": "XR*", "spETH": "XR*",
}


def apy_to_daily(apy: float) -> float:
    if apy == 0:
        return 0.0
    return 365.0 * (math.exp(math.log(1.0 + apy) / 365.0) - 1.0)


def rates_frame() -> pd.DataFrame:
    """The rates_dr.sql output: one row per (reward_code, schedule window)."""
    rows = [
        {"reward_code": rc, "reward_description": desc,
         "start_dt": s, "end_dt": e, "reward_per": apy_to_daily(apy)}
        for rc, desc, apy, s, e in REWARD_SCHEDULE
    ]
    return pd.DataFrame(rows)


def daily_rate(reward_code: str, dt: date) -> float:
    """Annualized daily reward rate for a reward_code active on ``dt``."""
    for rc, _desc, apy, s, e in REWARD_SCHEDULE:
        if rc == reward_code and s <= dt <= e:
            return apy_to_daily(apy)
    return 0.0
