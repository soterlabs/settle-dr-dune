"""Independent block-pinned RPC checks of event-derived Skybase accounting.

Usage: .venv/bin/python py/verify_skybase_venues.py [--end 2026-09-01]
Production calculation uses HyperSync only; RPC is an independent audit here.
"""
import argparse
import os
import sys
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv
from eth_hash.auto import keccak

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'py'))
load_dotenv(ROOT / '.env')
if 'ETH_RPC' not in os.environ:
    # Same audit-only RPC configuration as verify_osero_custody.py.
    load_dotenv(ROOT.parent / 'settlement-cycle' / '.env')
from drhs.sources import skybase as s, holder
from drhs import events, hypersync
from drhs.window import DEFAULT_END, midnight_ts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--end', type=date.fromisoformat, default=DEFAULT_END)
    args = ap.parse_args()
    end_ts = midnight_ts(args.end)
    rows, adapters, block = s.fetch_morpho(end_ts)
    _, states, positions = s.replay_markets(rows, adapters, end_ts)
    rpc = os.environ.get('ETH_RPC', 'https://ethereum-rpc.publicnode.com')

    def call(address, signature, arg=''):
        data = '0x' + keccak(signature.encode())[:4].hex() + arg.removeprefix('0x')
        try:
            response = requests.post(rpc, json={'jsonrpc':'2.0','id':1,'method':'eth_call',
                'params':[{'to':address,'data':data},hex(block)]}, timeout=30)
        except requests.RequestException:
            raise RuntimeError('RPC transport failed') from None
        if not response.ok:
            raise RuntimeError(f'RPC HTTP {response.status_code}')
        result = response.json()
        if 'error' in result:
            raise RuntimeError(result['error'])
        h = result['result'].removeprefix('0x')
        return [int(h[i:i+64],16) for i in range(0,len(h),64)]

    for adapter, vault in adapters.items():
        assert call(adapter,'parentVault()')[0] == int(vault,16)
        assert call(adapter,'asset()')[0] == int(holder.USDS,16)
        assert call(adapter,'morpho()')[0] == int(s.MORPHO_BLUE,16)
    for mid, state in states.items():
        actual = call(s.MORPHO_BLUE, 'market(bytes32)', mid)
        assert (state.supply,state.shares,state.borrow)==tuple(actual[:3]), (mid,state,actual)
    for (adapter, mid), shares in positions.items():
        actual = call(adapter,'supplyShares(bytes32)',mid)[0]
        assert shares == actual, (adapter,mid,shares,actual)
        blue = call(s.MORPHO_BLUE,'position(bytes32,address)',mid+adapter[2:].zfill(64))[0]
        assert shares <= blue, (adapter,mid,shares,blue)
    print(f'{args.end}: exact market state ({len(states)}) and adapter shares ({len(positions)}) at block {block}')
    for t in s.TARGETS:
        rs = holder.fetch_target_rows(t,end_ts)
        balance = sum(events.transfer_value(r.data) * (
            (events.topic_to_addr(r.topic2)==t.holder) - (events.topic_to_addr(r.topic1)==t.holder))
            for r in s.ordered(rs) if r.block_time < end_ts)
        actual = call(t.token,'balanceOf(address)',t.holder[2:].zfill(64))[0]
        assert balance == actual, (t.ref_code,balance,actual)
        print(f'  code {t.ref_code}: exact wallet balance {balance/10**18:,.6f} {t.symbol}')
        if t == s.PENDLE:
            supply = call(t.holder,'totalSupply()')[0]
            if balance != supply:
                print(f'  WARNING: Pendle backing minus SY supply = {balance-supply} wei')
            else:
                print('  Pendle backing == SY totalSupply exactly')
    referrals = hypersync.query_logs('ethereum',[{'address':[s.SUSDS],
        'topics':[[events.REFERRAL_TOPIC0],[],[events.addr_to_topic(s.PENDLE.holder)]]}],
        s.GENESIS,block).rows
    print(f'  sUSDS Referral events naming SY as owner: {len(referrals)}')
    if referrals:
        print('  codes:',sorted({events.referral_code_from_topic(r.topic1) for r in referrals}))


if __name__ == '__main__':
    main()
