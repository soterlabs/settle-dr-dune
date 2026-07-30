"""Monthly DR revenue (USD) per (month, blockchain, token, ref_code) — Layer 2+3.

Port of the dr_rewards_monthly_*.sql family (Dune 7877552–579). For each source:

  1. reclassify the untagged sentinel (-999999) and any source-specific codes;
  2. amount = Σ time_weighted_avg_balance per (dt, blockchain, token, ref_code)
     over twa > 0 rows;
  3. tw_reward = amount / 365 * reward_per(reward_code(token), dt)     [XR/XR*/…]
     — sp* special-cases: spETH -> 0, spUSDT/spPYUSD no deployment haircut,
       spUSDC -> amount * deployment_ratio / 365 * reward_per;
  4. tw_reward_usd = tw_reward * conversion_rate(token, chain, dt);
  5. dr_usd = Σ tw_reward_usd per (month, blockchain, token, ref_code).

Reclassification by source (mirrors the SQL exactly):
  susds_susdc : sUSDS -999999->99, sUSDC -999999->127
  stusds/farms: none (untagged stays -999999; resolved downstream)
  psm3 (L2)   : -999999->99; code 0 -> 10001 if user in the chain's ALM/vault/
                psm list else 10000
  sp          : spUSDC {-999999,127}->131, spUSDT->130, spPYUSD->132
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable

import pandas as pd

from . import rates

# psm3 code-0 -> 10001 address lists (else 10000), per chain. Lower-cased.
PSM3_CODE0_10001 = {
    "base": {
        "0x2917956eff0b5eaf030abdb4ef4296df775009ca", "0x3128a0f7f0ea68e7b7c9b00afa7e41045828e858",
        "0x1601843c5e9bc251a3272907010afa41fa18347e", "0x2c776041ccfe903071af44aa147368a9c8eea518",
        "0xc3bef21ea7deb5c34cf33e918c8e28972c8048ed", "0x1647d5950dee7332f748b5d02ff4abe7ddcaff6b",
    },
    "arbitrum": {
        "0x92afd6f2385a90e44da3a8b60fe36f6cbe1d8709", "0x940098b108fb7d0a7e374f6eded7760787464609",
        "0x2b05f8e1cacc6974fd79a673a341fe1f58d27266", "0x6c247b1f6182318877311737bac0844baa518f5e",
        "0x52aa899454998be5b000ad077a46bbe360f4e497",
    },
    "optimism": {
        "0x876664f0c9ff24d1aa355ce9f1680ae1a5bf36fb", "0xcf9326e24ebffbef22ce1050007a43a3c0b6db55",
        "0xe0f9978b907853f354d79188a3defbd41978af62",
    },
    "unichain": {
        "0x345e368fccd62266b3f5f37c9a131fd1c39f5869", "0x14d9143becc348920b68d123687045db49a016c6",
        "0x7b42ed932f26509465f7ce3faf76ffce1275312f",
    },
}

SP_UNTAGGED = {"spUSDC": 131, "spUSDT": 130, "spPYUSD": 132}

# Ref codes that are SYNTHETIC AND UNPAID — tracked for completeness but
# mapping to NO beneficiary. Payment tooling (the workbook's Payable view,
# any payout export) must exclude them; their dr_usd is notional only.
#   -999999 sentinel | 99 untagged sUSDS | 127 untagged sUSDC
#   130/131/132 untagged sp* | 10000 L2 default-zero users (no integrator)
#   10001 smart-contract-held L2 sUSDS (the value's real DR is paid in its
#   own venue, e.g. sUSDC holders)
# Paid/payable synthetic codes (1003 cow, 1004 paraswap, 4011 1inch,
# 9001 Aave, 4001 bridge) must NEVER be added here.
NON_PAYABLE_CODES = frozenset({-999999, 99, 127, 130, 131, 132, 10000, 10001})


def _month(dt_s: str) -> str:
    return dt_s[:7] + "-01"


# ---- reclassification --------------------------------------------------------
def reclass_susds_susdc(symbol, ref, user, chain):
    if ref == -999999:
        return 99 if symbol == "sUSDS" else (127 if symbol == "sUSDC" else ref)
    return ref


def reclass_psm3(symbol, ref, user, chain):
    if ref == -999999:
        return 99
    if ref == 0:
        return 10001 if user.lower() in PSM3_CODE0_10001.get(chain, set()) else 10000
    return ref


def reclass_sp(symbol, ref, user, chain):
    if symbol in SP_UNTAGGED and ref in (-999999, 127):
        return SP_UNTAGGED[symbol]
    return ref


def reclass_none(symbol, ref, user, chain):
    return ref


def monthly_dr(
    twa: pd.DataFrame,
    *,
    reclassify: Callable[[str, int, str, str], int],
    conv_lookup: Callable[[str, str, str], float],
    sp_deployment: dict[tuple[str, str, str], float] | None = None,
) -> pd.DataFrame:
    """Compute monthly DR (USD) from a source's TWA frame.

    ``conv_lookup(token, chain, dt_str) -> rate`` (1.0 for USDS farms).
    ``sp_deployment`` present => sp* reward rules; keyed (chain, symbol, dt_str).
    """
    if twa.empty:
        return pd.DataFrame(columns=["month", "blockchain", "token", "ref_code", "dr_usd"])
    df = twa[twa["time_weighted_avg_balance"] > 0].copy()
    df["dt_s"] = df["dt"].astype(str).str[:10]
    df["ref2"] = [reclassify(s, int(r), u, c)
                  for s, r, u, c in zip(df["symbol"], df["ref_code"], df["user_addr"], df["blockchain"])]

    # daily amount per (dt, blockchain, token, ref_code)
    daily = (df.groupby(["dt_s", "blockchain", "symbol", "ref2"])["time_weighted_avg_balance"]
             .sum().reset_index().rename(columns={"symbol": "token", "time_weighted_avg_balance": "amount"}))

    def _tw_reward(row) -> float:
        token, amount, dt_s, chain = row["token"], row["amount"], row["dt_s"], row["blockchain"]
        d = date.fromisoformat(dt_s)
        rp = rates.daily_rate(rates.TOKEN_REWARD_CODE.get(token, "XR"), d)
        if sp_deployment is not None:
            if token == "spETH":
                return 0.0
            if token in ("spUSDT", "spPYUSD"):
                return amount / 365.0 * rp
            ratio = sp_deployment.get((chain, token, dt_s), 0.0)  # spUSDC
            return amount * ratio / 365.0 * rp
        return amount / 365.0 * rp

    daily["tw_reward"] = daily.apply(_tw_reward, axis=1)
    daily["tw_reward_usd"] = [
        tw * conv_lookup(tok, ch, dts)
        for tw, tok, ch, dts in zip(daily["tw_reward"], daily["token"], daily["blockchain"], daily["dt_s"])
    ]
    daily["month"] = daily["dt_s"].map(_month)
    out = (daily.groupby(["month", "blockchain", "token", "ref2"])["tw_reward_usd"]
           .sum().reset_index()
           .rename(columns={"ref2": "ref_code", "tw_reward_usd": "dr_usd"}))
    return out.sort_values(["month", "blockchain", "token", "ref_code"]).reset_index(drop=True)


# ---- conversion-lookup builders ---------------------------------------------
def const_conv(_token, _chain, _dt) -> float:
    return 1.0


def series_conv(rate_frame: pd.DataFrame, col: str) -> Callable[[str, str, str], float]:
    """dt-only conversion (susds/stusds): {dt_str: rate}, default 1.0."""
    m = {str(dt)[:10]: float(v) for dt, v in zip(rate_frame["dt"], rate_frame[col])}
    return lambda _token, _chain, dt: m.get(dt, 1.0)


def sp_conv(sp_frame: pd.DataFrame) -> Callable[[str, str, str], float]:
    """sp* conversion keyed (chain, symbol, dt) -> conversion_rate, default 1.0."""
    m = {(c, s, str(dt)[:10]): float(v) for c, s, dt, v in zip(
        sp_frame["blockchain"], sp_frame["token_symbol"], sp_frame["dt"], sp_frame["conversion_rate"])}
    return lambda token, chain, dt: m.get((chain, token, dt), 1.0)
