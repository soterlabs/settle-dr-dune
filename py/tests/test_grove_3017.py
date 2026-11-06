"""Pinned regression for Osero's real Grove Farm Referral(3017) deposit."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "py"))

from drhs import events  # noqa: E402
from drhs.hypersync import LogRow  # noqa: E402
from drhs.sources import template_d  # noqa: E402


TX = "0x001181736457d2f9551ece0cce773275b9752c883ea358c8ec5bea6f094154db"
USER = "0xe060b80438771f13078048c3b0d930efeca6e622"
BLOCK = 26_349_987
TS = 1_793_898_011
AMOUNT = int("758dadfe06d4f0bc71440", 16)


def test_real_grove_deposit_is_attributed_to_osero_3017():
    staked = LogRow(
        BLOCK, 434, TS, template_d.GROVE.address, events.STAKED_TOPIC0,
        events.addr_to_topic(USER), None, None, f"0x{AMOUNT:064x}", TX)
    referral = LogRow(
        BLOCK, 435, TS, template_d.GROVE.address, events.REFERRAL3_TOPIC0,
        f"0x{3017:064x}", events.addr_to_topic(USER), None,
        f"0x{AMOUNT:064x}", TX)
    legs = template_d.legs_from_rows(
        template_d.GROVE, [referral], [staked], TS + 1)
    assert len(legs) == 1
    assert legs.iloc[0].user_addr == USER
    assert legs.iloc[0].ref_code == 3017
    assert legs.iloc[0].amount_change == AMOUNT / 1e18
