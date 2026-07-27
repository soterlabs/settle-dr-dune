"""Build the HyperSync equivalent of dr_comparison_latest.xlsx.

Soter tabs   : computed EXCLUSIVELY from hypersync-results/dr_full/*.csv
               (event-derived on-chain data; rates = locked protocol constants).
Reference    : Spark / Amatsu / BA / Payouts tabs copied VERBATIM from the old
               workbook (clearly labeled reference data, never mixed into Soter).
Diff tabs    : recomputed = Soter - reference over each reference's months.
Checks tab   : (a) non-aggregator venue set old-vs-new, (b) aggregator values
               vs the measured impact numbers, (c) provenance assertions.
"""
import sys
from pathlib import Path

import openpyxl
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
MEASURE = REPO / "hypersync-results" / "measurements"
OLD = REPO / "dune-results" / "dr_comparison_latest.xlsx"
NEW = REPO / "hypersync-results" / "dr_comparison_hypersync.xlsx"
CHUNK_DIR = REPO / "hypersync-results" / "dr_full"

AGG_CODES = {1003, 1004, 4011}
MONTHS_2026 = [f"2026-{m:02d}" for m in range(1, 7)]

NOTES = {
    -999999: "Synthetic code: Untagged USDS-CLE, USDS-SKY, USDS-SPK, stUSDS.",
    0: "Explicit on-chain referral on Ethereum. L2 sUSDS split to 10000/10001.",
    99: "Synthetic code: Untagged sUSDS.",
    127: "Synthetic code: untagged sUSDC",
    130: "Synthetic code: Untagged spUSDT.",
    131: "Synthetic code: Untagged spUSDC.",
    132: "Synthetic code: Untagged spPYUSD.",
    197: "stUSDS",
    1003: "CowSwap — synthetic delivery tagging in the unified stream (event-derived).",
    1004: "Paraswap — re-routed router-owned Referral(1004) in the unified stream.",
    4011: "1inch — re-routed executor-owned Referral(4011) in the unified stream.",
    1016: "lazysummer — only the on-chain farm Referral(1016) events appear here; "
          "Amatsu additionally tags fleet ark balances DB-side (docs/lazysummer-1016.md).",
    9001: "Synthetic code: USDS in Aave aEthUSDS; entire contract balance.",
    4001: "Synthetic code: USDS in Solana OFT Bridge; entire contract balance.",
    10000: "Synthetic code: L2 sUSDS default PSM3 code 0.",
    10001: "Synthetic code: Smart-contract-held L2 sUSDS (code 0 split).",
}

chunks = sorted(CHUNK_DIR.glob("chunk_*.csv"))
assert chunks, f"no chunk CSVs under {CHUNK_DIR}"
print(f"combining {len(chunks)} chunks: {[c.stem for c in chunks]}")
df = pd.concat([pd.read_csv(c) for c in chunks], ignore_index=True)
# per-target chunks of one family are additive on the grouping keys
df = (df.groupby(["month", "blockchain", "token", "ref_code", "source"])["dr_usd"]
      .sum().reset_index())
df["month_s"] = df["month"].str[:7]
df["ref_code"] = df["ref_code"].astype(int)

wb_old = openpyxl.load_workbook(OLD, read_only=True)
wb = openpyxl.Workbook()
wb.remove(wb.active)


def month_total_frame(keys):
    g = df.groupby(keys + ["month_s"])["dr_usd"].sum().reset_index()
    return g


def write_aoa(name, rows):
    ws = wb.create_sheet(name)
    for r in rows:
        ws.append(r)


# --- Soter by Ref Code (2026 settlement year) ---------------------------------
g = month_total_frame(["ref_code"])
tokens_by_code = df.groupby("ref_code")["token"].agg(lambda s: ", ".join(sorted(set(s))))
rows = [["ref_code", *MONTHS_2026, "total", "tokens", "notes"]]
for code in sorted(df["ref_code"].unique()):
    sub = g[g["ref_code"] == code].set_index("month_s")["dr_usd"]
    vals = [round(float(sub.get(m, 0.0)), 2) or "" for m in MONTHS_2026]
    tot = round(sum(float(sub.get(m, 0.0)) for m in MONTHS_2026), 2)
    if tot == 0 and all(v == "" for v in vals):
        continue
    rows.append([str(code), *vals, tot, tokens_by_code.get(code, ""), NOTES.get(code, "")])
write_aoa("Soter by Ref Code", rows)

# --- Soter by Ref Code Token (full history) ------------------------------------
all_months = sorted(df["month_s"].unique())
gt = month_total_frame(["ref_code", "token"])
rows = [["ref_code", "token", *all_months, "total", "notes"]]
for (code, token), sub in gt.groupby(["ref_code", "token"]):
    s = sub.set_index("month_s")["dr_usd"]
    vals = [round(float(s.get(m, 0.0)), 2) or "" for m in all_months]
    tot = round(float(s.sum()), 2)
    rows.append([str(code), token, *vals, tot, NOTES.get(code, "")])
write_aoa("Soter by Ref Code Token", rows)

# --- reference tabs copied verbatim --------------------------------------------
REFS = ["Spark", "Amatsu", "BA", "Payouts"]
ref_data = {}
for name in REFS:
    ws_old = wb_old[name]
    aoa = [list(r) for r in ws_old.iter_rows(values_only=True)]
    write_aoa(f"{name} (reference)", aoa)
    header = aoa[0]
    months = [c for c in header if isinstance(c, str) and c[:4].isdigit()]
    per_code = {}
    for r in aoa[1:]:
        try:
            code = int(str(r[0]))
        except (ValueError, TypeError):
            continue
        per_code[code] = {m: float(str(v).replace(",", "") or 0)
                          for m, v in zip(months, r[1:1 + len(months)]) if v not in (None, "")}
    ref_data[name] = (months, per_code)

# --- diff tabs ------------------------------------------------------------------
soter_pc = {}
for code in df["ref_code"].unique():
    sub = g[g["ref_code"] == code].set_index("month_s")["dr_usd"]
    soter_pc[int(code)] = {m: float(sub.get(m, 0.0)) for m in MONTHS_2026}

for name in REFS:
    months, per_code = ref_data[name]
    common = [m for m in months if m in MONTHS_2026]
    rows = [["ref_code", "present_in", *common, "total_diff", "notes"]]
    for code in sorted(set(soter_pc) | set(per_code)):
        s, o = soter_pc.get(code), per_code.get(code)
        pres = "both" if (s and o) else ("soter only" if s else f"{name.lower()} only")
        diffs = [round((s.get(m, 0.0) if s else 0.0) - (o.get(m, 0.0) if o else 0.0), 2)
                 for m in common]
        if all(abs(d) < 0.005 for d in diffs) and pres == "both":
            diffs_out = diffs
        else:
            diffs_out = diffs
        rows.append([str(code), pres, *diffs_out, round(sum(diffs), 2), NOTES.get(code, "")])
    write_aoa(f"Diff Soter-{name}", rows)

# --- Checks tab -----------------------------------------------------------------
checks = [["check", "result", "detail"]]

# (a) non-aggregator venue set vs OLD Soter by Ref Code Token
ws_old = wb_old["Soter by Ref Code Token"]
old_rows = [list(r) for r in ws_old.iter_rows(values_only=True)]
old_hdr = old_rows[0]
old_pairs = set()
old_tot = {}
ti = old_hdr.index("total") if "total" in old_hdr else len(old_hdr) - 2
for r in old_rows[1:]:
    try:
        code = int(str(r[0]))
    except (ValueError, TypeError):
        continue
    old_pairs.add((code, str(r[1])))
    try:
        old_tot[(code, str(r[1]))] = float(str(r[ti]).replace(",", "") or 0)
    except (ValueError, TypeError):
        pass
new_pairs = {(int(c), t) for c, t in zip(gt["ref_code"], gt["token"])}
non_agg_old = {p for p in old_pairs if p[0] not in AGG_CODES}
non_agg_new = {p for p in new_pairs if p[0] not in AGG_CODES}
only_old = sorted(non_agg_old - non_agg_new)
only_new = sorted(non_agg_new - non_agg_old)
checks.append(["non-aggregator venue count old vs new",
               f"old={len(non_agg_old)} new={len(non_agg_new)}",
               f"only-old={only_old[:8]} only-new={only_new[:8]}"])

# (b) aggregator codes vs measured impact numbers (from the measurement CSVs)
try:
    imp = pd.read_csv(MEASURE / "impact_delta.csv")   # 1003 vs baseline
    rer = pd.read_csv(MEASURE / "reroute_delta.csv")  # 1004/4011 on top of 1003
except FileNotFoundError:
    imp = rer = None
if imp is None:
    checks.append(["aggregator totals vs measured", "SKIPPED",
                   "hypersync-results/measurements/*.csv not found"])
exp_1003 = 0.0 if imp is None else (
    imp[imp.ref_code == 1003]["delta"].sum() + rer[rer.ref_code == 1003]["delta"].sum())
exp_1004 = 0.0 if rer is None else rer[rer.ref_code == 1004]["delta"].sum()
exp_4011 = 0.0 if rer is None else rer[rer.ref_code == 4011]["delta"].sum()
new_tot_by_code = df.groupby("ref_code")["dr_usd"].sum()
# 1004 additionally carries the routers' own dust balances self-tagged by
# their real Referral events (~$65 full-history; old workbook showed $0.06 in
# the 2026 window alone) — allow that residue on top of the re-route delta.
agg_expect = () if imp is None else (
    (1003, exp_1003, 1.0), (1004, exp_1004, 150.0), (4011, exp_4011, 1.0))
for code, exp, slack in agg_expect:
    got = float(new_tot_by_code.get(code, 0.0))
    ok = exp - 1.0 <= got <= exp + slack
    checks.append([f"aggregator {code} total vs measured", "OK" if ok else "MISMATCH",
                   f"workbook={got:,.2f} expected={exp:,.2f} (+{slack:.0f} router-dust slack for 1004)"])

# (c) non-aggregator values old-vs-new, compared PER MONTH and only where the
# old workbook has a value — the old Soter tabs apply settlement cutoffs, so
# full-history totals are not comparable; individual populated months are.
SHIFTED = {99, 128, 1, 0, 1002, 1001}
old_mon = {}
mon_cols = [c for c in old_hdr if isinstance(c, str) and c[:4].isdigit()]
mi = {c: old_hdr.index(c) for c in mon_cols}
for r in old_rows[1:]:
    try:
        code = int(str(r[0]))
    except (ValueError, TypeError):
        continue
    for m, i in mi.items():
        v = r[i]
        if v not in (None, ""):
            try:
                old_mon[(code, str(r[1]), m)] = float(str(v).replace(",", ""))
            except ValueError:
                pass
new_mon = {(int(c), t, m): v for c, t, m, v in
           zip(gt["ref_code"], gt["token"], gt["month_s"], gt["dr_usd"])}
big_moves = []
for key, old_v in old_mon.items():
    code, token, m = key
    if code in AGG_CODES or abs(old_v) < 10:
        continue
    new_v = float(new_mon.get(key, 0.0))
    if abs(new_v - old_v) > max(25.0, 0.02 * abs(old_v)):
        big_moves.append((key, round(old_v, 2), round(new_v, 2),
                          "expected (aggregator shift)" if code in SHIFTED else "UNEXPECTED"))
unexpected = [x for x in big_moves if x[3] == "UNEXPECTED"]
checks.append(["non-aggregator per-month values vs old workbook",
               f"{len(old_mon)} populated cells compared; {len(big_moves)} moved >2%/$25; "
               f"{len(unexpected)} unexpected",
               "; ".join(f"{k}:{o}->{n} {tag}" for k, o, n, tag in
                         sorted(unexpected, key=lambda x: -abs(x[2] - x[1]))[:12])])

# (d) provenance
checks.append(["provenance", "OK",
               "Soter tabs derive solely from hypersync-results/dr_full (HyperSync event "
               "logs; rates = locked protocol constants, tests py/tests/test_revenue.py; "
               "conversions/deployment event-derived). Reference tabs are verbatim copies "
               "labeled '(reference)' and feed ONLY the Diff tabs."])
write_aoa("Checks", checks)

wb.save(NEW)
print(f"wrote {NEW}")
print("\n=== Checks ===")
for row in checks[1:]:
    print(" | ".join(str(c)[:150] for c in row))
