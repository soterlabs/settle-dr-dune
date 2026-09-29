"""Conservation, time weighting, fee dilution and nested-claim exclusion."""
import sys
from pathlib import Path
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drhs import events, twa
from drhs.hypersync import LogRow
from drhs.sources import skybase as s, holder, template_ab
from drhs.revenue import monthly
from drhs.window import midnight_ts

A, B = s.ADAPTERS
M = "0x" + "1" * 64
START = midnight_ts(date(2026, 2, 1))
U = 10**18

def row(topic, data, block=1, idx=0, addr=s.MORPHO_BLUE, ts=START, market=M):
    return LogRow(block, idx, ts, addr, topic, market, None, None,
                  "0x" + "".join(f"{n:064x}" for n in data))

def test_shared_market_idle_with_outside_lender_fee_borrow_and_bad_debt():
    rs = [row(s.SUPPLY, [1000*U, 1000], idx=0),
          row(s.ALLOCATE, [0, 300], idx=1, addr=A),
          row(s.ALLOCATE, [0, 200], idx=2, addr=B),
          row(s.BORROW, [0, 600*U, 600], block=2),
          row(s.ACCRUE, [0, 60*U, 100], block=3),
          row(s.LIQUIDATE, [50*U, 1, 9*U, 20*U, 1], block=4)]
    legs, states, pos = s.replay_markets(rs, s.ADAPTERS, START+1)
    st = states[M]
    assert st.supply-st.borrow == 450*U  # interest/bad debt don't create cash
    assert st.shares == 1100             # fee shares dilute both vaults
    sums = legs.groupby('ref_code').amount_change.sum()
    assert sums[1998] == pytest.approx(450*300/1100)
    assert sums[1999] == pytest.approx(450*200/1100)
    assert sums.sum() < 450              # unrelated lenders retain their share
    assert pos[A, M] == 300

def test_half_month_borrow_exclusive_end_and_daily_proration():
    mid = midnight_ts(date(2026, 2, 15))
    end = midnight_ts(date(2026, 3, 1))
    rs = [row(s.SUPPLY, [12000*U, 100], idx=0),
          row(s.ALLOCATE, [0, 100], idx=1, addr=A),
          row(s.BORROW, [0, 12000*U, 10], block=2, ts=mid),
          row(s.REPAY, [12000*U, 10], block=3, ts=end)]
    legs, _, _ = s.replay_markets(rs, s.ADAPTERS, end)
    tw = twa.compute_twa(legs, fill_through=date(2026, 2, 28))
    out = monthly.monthly_dr(tw, reclassify=monthly.reclass_none, conv_lookup=monthly.const_conv)
    assert out.dr_usd.sum() == pytest.approx(1.0)  # 12000 * half-month * .002/12

def test_adapter_burn_and_market_exit_remove_attribution():
    rs = [row(s.SUPPLY, [100*U, 100], idx=0),
          row(s.ALLOCATE, [0, 100], idx=1, addr=A),
          row(s.WITHDRAW, [0, 40*U, 40], block=2),
          row(s.DEALLOCATE, [0, 40], block=2, idx=1, addr=A),
          row(s.BURN, [60], block=3, addr=A)]
    legs, states, pos = s.replay_markets(rs, s.ADAPTERS, START+1)
    assert legs.amount_change.sum() == pytest.approx(0)
    assert states[M].shares == 60  # adapter burned its claim, not Blue's shares
    assert pos[A, M] == 0

def test_reallocation_wallet_and_market_are_not_added_twice():
    rs = [row(s.SUPPLY, [100*U, 100]), row(s.ALLOCATE, [0, 100], idx=1, addr=A)]
    legs, _, _ = s.replay_markets(rs, s.ADAPTERS, START+1)
    def tr(frm, to, amount, idx):
        return LogRow(1, idx, START, holder.USDS, events.TRANSFER_TOPIC0,
                      events.addr_to_topic(frm), events.addr_to_topic(to), None, f'0x{amount:064x}')
    transfers = [tr('0x'+'0'*40, s.FLAGSHIP.holder, 100*U, 2),
                 tr(s.FLAGSHIP.holder, A, 100*U, 3)]
    wallet = holder.legs_from_rows(s.FLAGSHIP, transfers, START+1)
    tw = twa.compute_twa(pd.concat([legs, wallet]), fill_through=date(2026,2,1))
    assert tw.time_weighted_avg_balance.sum() == pytest.approx(100)

def test_invalid_ownership_and_negative_replay_fail():
    rs = [row(s.SUPPLY, [100,100]), row(s.ALLOCATE,[0,101],idx=1,addr=A)]
    with pytest.raises(ValueError, match='exceed'):
        s.replay_markets(rs, s.ADAPTERS, START+1)
    with pytest.raises(ValueError):
        s.MarketState(100,101,100).idle_for(1)

def test_pendle_backing_supply_reconciliation_and_donations(caplog):
    zero = '0x'+'0'*40
    def tr(token, to, amount, idx):
        return LogRow(1,idx,START,token,events.TRANSFER_TOPIC0,
            events.addr_to_topic(zero),events.addr_to_topic(to),None,f'0x{amount:064x}')
    backing=[tr(s.SUSDS,s.PENDLE.holder,100,0)]
    sy=[tr(s.PENDLE.holder,A,100,1)]
    assert s.check_pendle_backing(backing,sy,START+1)==(100,100,0)
    assert s.check_pendle_backing([tr(s.SUSDS,s.PENDLE.holder,101,0)],sy,START+1)==(101,100,1)
    assert 'divergence' in caplog.text
    with pytest.raises(ValueError,match='below supply'):
        s.check_pendle_backing([],sy,START+1)

def test_pendle_holder_is_excluded_from_template_a():
    assert s.PENDLE.holder in template_ab.TEMPLATE_A_EXCLUDED

def test_pendle_conversion_and_reserved_code_collision():
    d = dict(blockchain='ethereum',contract_address=s.SUSDS,symbol='sUSDS',
             user_addr=s.PENDLE.holder,dt=date(2026,2,1),ref_code=1997,
             time_weighted_avg_balance=12000)
    tw = pd.DataFrame([d])
    out=monthly.monthly_dr(tw,reclassify=monthly.reclass_none,conv_lookup=lambda *a:1.1)
    assert out.dr_usd.sum()==pytest.approx(12000*1.1*.002/12/28)
    tw.loc[0,'user_addr']=A
    with pytest.raises(ValueError,match='collides'):
        monthly.monthly_dr(tw,reclassify=monthly.reclass_none,conv_lookup=monthly.const_conv)

def test_skybase_rewards_are_restricted_to_2026():
    rows = []
    for dt in (date(2025, 12, 31), date(2026, 1, 1), date(2026, 12, 31), date(2027, 1, 1)):
        rows.append(dict(blockchain='ethereum', contract_address=s.SUSDS,
            symbol='sUSDS', user_addr=s.PENDLE.holder, dt=dt, ref_code=1997,
            time_weighted_avg_balance=12000))
    out = monthly.monthly_dr(pd.DataFrame(rows), reclassify=monthly.reclass_none,
                             conv_lookup=monthly.const_conv)
    assert list(out.month) == ['2026-01-01', '2026-12-01']

def test_collateral_events_do_not_add_cash_and_duplicate_logs_are_deduped():
    rs=[row(s.SUPPLY,[100,100]),row(s.ALLOCATE,[0,100],idx=1,addr=A)]
    duplicate=rs[0]
    collateral=row(s.topic('SupplyCollateral(bytes32,address,address,uint256)'),[1000],block=2)
    legs,states,_=s.replay_markets(rs+[duplicate,collateral],s.ADAPTERS,START+1)
    assert states[M].supply == 100
    assert legs.amount_change.sum()==pytest.approx(100/U)
