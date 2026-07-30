"""Offline regression tests for the Layer 2-4 revenue logic (rates + monthly).

Rates are hardcoded (exact vs Dune 7877547, values locked here). The monthly
aggregation is tested on a tiny synthetic TWA frame so the reclassification and
DR formula can't regress. Conversion / deployment / monthly full parity is
verified live against Dune (validate_conversions/deployment/monthly.py); those
need network so they're not in the offline suite.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "py"))

from drhs.revenue import monthly, rates  # noqa: E402

# reward_per values locked from the Dune 7877547 diff (all matched exactly).
LOCKED = {
    ("XR", date(2026, 1, 1)): 4.987575587290e-03,
    ("XR*", date(2026, 1, 1)): 1.998008131217e-03,
    ("XR-stUSDS", date(2026, 1, 1)): 9.995017016051e-04,
    ("XR", date(2024, 6, 1)): 5.982120698627e-03,
    ("XR-stUSDS", date(2025, 12, 31)): 5.982120698627e-03,
    # 2026-07-09 Boosted-DR termination boundary: XR 0.5% -> 0.2% (the 0.2%
    # daily rate equals XR*'s, both 0.002 APY). XR* / XR-stUSDS unaffected.
    ("XR", date(2026, 7, 8)): 4.987575587290e-03,
    ("XR", date(2026, 7, 9)): 1.998008131217e-03,
    ("XR*", date(2026, 7, 9)): 1.998008131217e-03,
    ("XR-stUSDS", date(2026, 7, 9)): 9.995017016051e-04,
}


@pytest.mark.parametrize("key,expected", list(LOCKED.items()))
def test_rates_locked(key, expected):
    code, d = key
    assert rates.daily_rate(code, d) == pytest.approx(expected, rel=1e-15)


def test_token_reward_codes():
    assert rates.TOKEN_REWARD_CODE["stUSDS"] == "XR-stUSDS"
    assert rates.TOKEN_REWARD_CODE["sUSDC"] == "XR"       # boosted to XR
    assert rates.TOKEN_REWARD_CODE["spETH"] == "XR*"


def test_monthly_reclass_and_formula():
    # one tagged (ref 5) + one untagged sUSDS holder, both 100 balance on 2 days
    twa = pd.DataFrame([
        {"blockchain": "ethereum", "contract_address": "0xt", "symbol": "sUSDS",
         "user_addr": "0xu1", "dt": "2026-02-01", "ref_code": 5, "time_weighted_avg_balance": 100.0},
        {"blockchain": "ethereum", "contract_address": "0xt", "symbol": "sUSDS",
         "user_addr": "0xu2", "dt": "2026-02-01", "ref_code": -999999, "time_weighted_avg_balance": 100.0},
        {"blockchain": "ethereum", "contract_address": "0xt", "symbol": "sUSDS",
         "user_addr": "0xu2", "dt": "2026-02-02", "ref_code": -999999, "time_weighted_avg_balance": 100.0},
    ])
    out = monthly.monthly_dr(twa, reclassify=monthly.reclass_susds_susdc,
                             conv_lookup=lambda t, c, d: 2.0)  # conversion = 2.0
    out = out.set_index("ref_code")
    # untagged sUSDS -> 99
    assert set(out.index) == {5, 99}
    rp = rates.daily_rate("XR", date(2026, 2, 1))
    assert out.loc[5, "dr_usd"] == pytest.approx(100.0 / 365 * rp * 2.0, rel=1e-12)
    # ref 99 gets two days (Feb 1 + Feb 2)
    assert out.loc[99, "dr_usd"] == pytest.approx(2 * (100.0 / 365 * rp * 2.0), rel=1e-12)


def test_sp_reclass_and_speth_zero():
    twa = pd.DataFrame([
        {"blockchain": "ethereum", "contract_address": "0xa", "symbol": "spUSDT",
         "user_addr": "0xu", "dt": "2026-02-01", "ref_code": -999999, "time_weighted_avg_balance": 100.0},
        {"blockchain": "ethereum", "contract_address": "0xb", "symbol": "spETH",
         "user_addr": "0xu", "dt": "2026-02-01", "ref_code": 5, "time_weighted_avg_balance": 100.0},
    ])
    out = monthly.monthly_dr(twa, reclassify=monthly.reclass_sp, conv_lookup=lambda t, c, d: 1.0,
                             sp_deployment={})
    idx = {(r.token, r.ref_code): r.dr_usd for r in out.itertuples()}
    assert (idx.get(("spUSDT", 130), None)) is not None      # spUSDT untagged -> 130
    assert idx[("spETH", 5)] == 0.0                          # spETH earns zero DR


# --- infra split + non-payable registry (2026-07-29) ---------------------------

def test_l2_infra_split_is_address_based():
    alm_arb = "0x92afd6f2385a90e44da3a8b60fe36f6cbe1d8709"
    user = "0x00000000000000000000000000000000000000aa"
    # infrastructure: untagged AND code-0 both land in 10001, never 99/10000
    assert monthly.reclass_psm3("sUSDS", -999999, alm_arb, "arbitrum") == 10001
    assert monthly.reclass_psm3("sUSDS", 0, alm_arb, "arbitrum") == 10001
    # a real code on an infra address is preserved (anomaly must stay visible)
    assert monthly.reclass_psm3("sUSDS", 128, alm_arb, "arbitrum") == 128
    # ordinary users unchanged: untagged -> 99, default zero -> 10000
    assert monthly.reclass_psm3("sUSDS", -999999, user, "arbitrum") == 99
    assert monthly.reclass_psm3("sUSDS", 0, user, "arbitrum") == 10000


def test_eth_infra_hook_empty_until_verified():
    user = "0x00000000000000000000000000000000000000aa"
    assert monthly.ETH_INFRA_10001 == frozenset()
    assert monthly.reclass_susds_susdc("sUSDS", -999999, user, "ethereum") == 99


def test_non_payable_registry():
    assert monthly.NON_PAYABLE_CODES == {-999999, 99, 127, 130, 131, 132, 10000, 10001}
    # payable partner / program codes must never appear in the registry
    for payable in (0, 1, 128, 197, 1001, 1002, 1003, 1004, 1007, 1016, 4001, 9001, 4011):
        assert payable not in monthly.NON_PAYABLE_CODES
