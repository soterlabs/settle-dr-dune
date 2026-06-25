/**
 * compare-dr.ts
 *
 * Produces a multi-tab Excel workbook comparing 2026 DR revenue across sources.
 *
 * "Summary" (first tab) — Soter ref_codes bucketed into partner groups
 *   (Skybase / Spark / Grove / Osero / Keel / Other) with per-month DR, a
 *   current-year cumulative total per code, and a per-group subtotal. No notes.
 * "Soter by Ref Code Token" — verbatim copy of the latest combined
 *   dr_monthly_by_refcode_token.csv.
 *
 * Data tabs (same column format — ref_code | YYYY-MM… | total | notes):
 *   "Soter by Ref Code" — latest dune-results/combined/<TS>/dr_monthly_by_refcode_token.csv
 *                  includes every ref_code found in Spark or Amatsu (blanks if absent)
 *   "Spark"      — spark-dr-data/query_5650519_full.csv, aggregated by (month, ref_code)
 *   "Amatsu"     — amatsu-dr-data/...csv, Jan–Mar 2026 only
 *
 * Diff tabs (source1 − source2, union of ref_codes, overlapping months):
 *   "Diff Soter-Spark"   — all 2026 months present in both
 *   "Diff Soter-Amatsu"  — Jan–Mar 2026; our −999999 + 99 merged → "untagged" to match Amatsu
 *   "Diff Spark-Amatsu"  — Jan–Mar 2026; Spark ref_code 99 renamed → "untagged" to match Amatsu
 *
 * Input:  by default the LATEST dune-results/combined/<TS>/ run (created by
 *         combine-dr-results.ts). Override with:
 *           --combined <TS>     pin a specific combined run-dir name
 *           --input <path.csv>  point directly at a by_refcode_token CSV
 * Output: a versioned dune-results/comparison/<TS>/dr_comparison_2026.xlsx
 *         (own fresh timestamp, never overwrites). Override with --out <path>.
 * Usage:  npm run compare
 *         npm run compare -- --combined 2026-06-11_035558
 *         npm run compare -- --input some/path.csv --out some/out.xlsx
 */
import * as fs from 'fs';
import * as path from 'path';
import { createRequire } from 'node:module';
const _require = createRequire(import.meta.url);
// eslint-disable-next-line @typescript-eslint/no-require-imports
const XLSX = _require('xlsx') as typeof import('xlsx');
const { utils, writeFile } = XLSX;

// ─── types ────────────────────────────────────────────────────────────────────

/** refCode → (YYYY-MM → value) */
type DataMap = Map<string, Map<string, number>>;

// ─── helpers ──────────────────────────────────────────────────────────────────

const MONTH_ABBR: Record<string, string> = {
  Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06',
  Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12',
};

/** "Jan 2026" → "2026-01", anything else → null. */
function parseMonthCol(col: string): string | null {
  const m = String(col).trim().match(/^(\w{3})\s+(\d{4})$/);
  if (!m) return null;
  const mm = MONTH_ABBR[m[1]];
  return mm ? `${m[2]}-${mm}` : null;
}

/**
 * Notes shown on every tab (data + diff) for specific ref_codes.
 * Plain numeric keys match both plain codes and the base of compound codes
 * (e.g. key '0' does NOT match '0 (sUSDS)' — compound codes get their own entry).
 */
const NOTES: Record<string, string> = {
  '-999999': 'Synthetic code: Untagged USDS-CLE, USDS-SKY, USDS-SPK, stUSDS.',
  '0':  'Methodology needs review. Results in agreement with other parties, but swaps using default ref code may be incorrectly applied.',
  '99':  'Synthetic code: Untagged sUSDS.',
  '126':  'Subproxy holdings, no DR applied. Handled in Supply Side MSC.',
  '127':  'Synthetic code: untagged sUSDC',
  '130':  'Synthetic code: Untagged spUSDT.',
  '131':  'Synthetic code: Untagged spUSDC. Combined into 128 by Spark on Dune.',
  '132':  'Synthetic code: Untagged spPYUSD. Combined into 128 by Spark on Dune.',
  '197':  'stUSDS',
  '1001': 'Included in aggregators, needs methodology update. From Feb. 2026, payments may have been applied to 1016.',
  '1002': 'Included in aggregators, needs methodology update.',
  '1003': 'Included in aggregators, needs methodology update.',
  '1004': 'Included in aggregators, needs methodology update.',
  '1007': 'Included in aggregators, needs methodology update.',
  '1015': 'Possibly included in aggregators, needs methodology update.',
  '1016': 'Included in aggregators, needs methodology update.',
  '4001': 'Synthetic code: USDS in Solana OFT Bridge (0x1e1D42781FC170EF9da004Fb735f56F0276d01B8). No on-chain Referral event; entire contract balance attributed. XR rate.',
  '4011': 'Included in aggregators, needs methodology update.',
  '9001': 'Synthetic code: USDS in Aave aEthUSDS (0x32a6268f9Ba3642Dda7892aDd74f1D34469A4259). No on-chain Referral event; entire contract balance attributed. XR rate.',
};

function getNote(code: string): string {
  return NOTES[code] ?? '';
}

/** All ref_codes are aggregated across tokens; no compound keys are used. */
function compoundKey(code: string, _token: string): string {
  return code;
}

/**
 * Extract the leading integer from a plain or compound code ("0 (sUSDS)" → 0).
 * Returns NaN for non-numeric strings like "untagged".
 */
function numericBase(code: string): number {
  const m = code.match(/^(-?\d+)/);
  return m ? Number(m[1]) : NaN;
}

/** Numeric ref_codes ascending (compound codes sort under their base), then non-numeric alphabetically. */
function sortedCodes(codes: Iterable<string>): string[] {
  return [...codes].sort((a, b) => {
    const na = numericBase(a), nb = numericBase(b);
    const aNum = Number.isFinite(na), bNum = Number.isFinite(nb);
    if (aNum && bNum) {
      if (na !== nb) return na - nb;
      return a.localeCompare(b); // same base → "0 (sUSDS)" before "0 (USDS-SKY)"
    }
    if (aNum) return -1;
    if (bNum) return 1;
    return a.localeCompare(b);
  });
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

/** Excel cell value: numeric ref_codes become numbers, strings stay strings. */
function refCell(code: string): string | number {
  const n = Number(code);
  return Number.isFinite(n) ? n : code;
}

// ─── sheet builders ───────────────────────────────────────────────────────────

/**
 * Build AOA for a data tab: [ref_code, month…, total, tokens?, notes] per sorted ref_code.
 * Missing month values are left blank (empty string) so Excel displays no value.
 *
 * @param extraCodes   Optional additional ref_codes to include even if absent from
 *   `data` (used for the Soter tab to show every code found in other sources).
 * @param tokensByCode Optional map of ref_code → sorted token list. When provided
 *   a "tokens" column is inserted between "total" and "notes".
 */
function buildAoa(
  data: DataMap,
  months: string[],
  extraCodes?: Iterable<string>,
  tokensByCode?: Map<string, string[]>,
): (string | number)[][] {
  const codeSet = new Set(data.keys());
  if (extraCodes) for (const c of extraCodes) codeSet.add(c);

  const allSorted = sortedCodes([...codeSet]);

  const header: (string | number)[] = tokensByCode
    ? ['ref_code', ...months, 'total', 'tokens', 'notes']
    : ['ref_code', ...months, 'total', 'notes'];

  const makeRow = (code: string): (string | number)[] => {
    const mm = data.get(code) ?? new Map<string, number>();
    const vals = months.map(m => {
      const v = mm.get(m);
      return v !== undefined ? round2(v) : ('' as string | number);
    });
    const total = round2(vals.reduce<number>((s, v) => s + (typeof v === 'number' ? v : 0), 0));
    if (tokensByCode) {
      const tokens = tokensByCode.get(code)?.join(', ') ?? '';
      return [refCell(code), ...vals, total, tokens, getNote(code)];
    }
    return [refCell(code), ...vals, total, getNote(code)];
  };

  return [header, ...allSorted.map(makeRow)];
}

/**
 * Build AOA for the "Soter Rates" tab.
 * One row per unique token found in Soter data; columns show the APY and the
 * computed reward_per (annualised daily rate) for each 2026 month.
 * Rate values and token→rate mappings are sourced from rates_dr.sql.
 * spETH earns 0 DR and is flagged in the notes column.
 */
function buildRatesAoa(tokens: string[], months: string[]): (string | number)[][] {
  // Token → rate type + 2026 APY (from rates_dr.sql schedule).
  // XR family:       sUSDS / USDS-* → 0.5 % APY from 2026
  // XR* family:      sUSDC / sp*    → 0.2 % APY from 2026
  // XR-stUSDS:       stUSDS         → 0.1 % APY from 2026
  const RATE_MAP: Record<string, { rateType: string; apy: number; note?: string }> = {
    'sUSDS':    { rateType: 'XR',        apy: 0.005 },
    'USDS':     { rateType: 'XR',        apy: 0.005 },
    'USDS-SKY': { rateType: 'XR',        apy: 0.005 },
    'USDS-SPK': { rateType: 'XR',        apy: 0.005 },
    'USDS-CLE': { rateType: 'XR',        apy: 0.005 },
    'sUSDC':    { rateType: 'XR*',       apy: 0.002 },
    'spUSDC':   { rateType: 'XR*',       apy: 0.002 },
    'spUSDT':   { rateType: 'XR*',       apy: 0.002 },
    'spPYUSD':  { rateType: 'XR*',       apy: 0.002 },
    'spETH':    { rateType: 'XR*',       apy: 0.002, note: 'Earns 0 DR (zeroed in dr_rewards_monthly_sp.sql)' },
    'stUSDS':   { rateType: 'XR-stUSDS', apy: 0.001 },
  };

  // reward_per = 365 × (exp(ln(1 + apy) / 365) − 1)  [from rates_dr.sql]
  const rewardPer = (apy: number): number =>
    apy === 0 ? 0 : 365 * (Math.exp(Math.log(1 + apy) / 365) - 1);

  const header: (string | number)[] = ['token', 'rate_type', 'apy', 'reward_per', ...months, 'notes'];
  const rows: (string | number)[][] = [header];

  for (const token of [...tokens].sort()) {
    const r = RATE_MAP[token];
    if (!r) {
      rows.push([token, 'unknown', '', '', ...months.map(() => ''), '']);
      continue;
    }
    const rp = rewardPer(r.apy);
    // round reward_per to 10 significant figures for readability
    const rpFmt = Math.round(rp * 1e10) / 1e10;
    rows.push([token, r.rateType, r.apy, rpFmt, ...months.map(() => rpFmt), r.note ?? '']);
  }

  return rows;
}

/**
 * Build AOA for a diff tab: source1 − source2.
 * Shows the UNION of both datasets' ref_codes; missing side treated as 0.
 * Months are the provided overlapping slice.
 * Columns: ref_code | present_in | notes | month… | total_diff
 * A `present_in` column flags codes that exist in only one source.
 * A `notes` column carries ref_code-specific annotations (e.g. ref 126).
 */
function buildDiffAoa(
  d1: DataMap,
  d2: DataMap,
  months: string[],
  label1: string,
  label2: string,
): (string | number)[][] {
  const allCodes = sortedCodes([...new Set([...d1.keys(), ...d2.keys()])]);

  const header: (string | number)[] = ['ref_code', 'present_in', ...months, 'total_diff', 'notes'];

  const makeRow = (code: string): (string | number)[] => {
    const has1 = d1.has(code), has2 = d2.has(code);
    const present = has1 && has2 ? 'both' : has1 ? `${label1} only` : `${label2} only`;
    const notes = getNote(code);
    const m1 = d1.get(code) ?? new Map<string, number>();
    const m2 = d2.get(code) ?? new Map<string, number>();
    const diffs = months.map(m => round2((m1.get(m) ?? 0) - (m2.get(m) ?? 0)));
    const totalDiff = round2(diffs.reduce<number>((s, v) => s + v, 0));
    return [refCell(code), present, ...diffs, totalDiff, notes];
  };

  return [header, ...allCodes.map(makeRow)];
}

// ─── summary tab ────────────────────────────────────────────────────────────

/**
 * Partner groupings for the Summary tab, keyed by ref_code range:
 *   Skybase — 0, 1, 1000-1999
 *   Spark   — 2-999, EXCEPT 99 / 126 / 127 / 130-139 (untagged & house codes)
 *   Grove   — 2000-2999
 *   Osero   — 3000-3999
 *   Keel    — 4000-4999
 *   Other   — everything else (untagged -999999, the Spark exceptions above,
 *             out-of-range synthetics like 9001, etc.) so nothing is dropped.
 */
function classifyGroup(code: string): string {
  const n = numericBase(code);
  if (!Number.isFinite(n)) return 'Other';
  if (n === 0 || n === 1 || (n >= 1000 && n <= 1999)) return 'Skybase';
  if (n >= 2 && n <= 999) {
    if (n === 99 || n === 126 || n === 127 || (n >= 130 && n <= 139)) return 'Other';
    return 'Spark';
  }
  if (n >= 2000 && n <= 2999) return 'Grove';
  if (n >= 3000 && n <= 3999) return 'Osero';
  if (n >= 4000 && n <= 4999) return 'Keel';
  if (n === 9001) return 'Spark';
  return 'Other';
}

const SUMMARY_GROUP_ORDER = ['Skybase', 'Spark', 'Grove', 'Osero', 'Keel', 'Other'];

/**
 * Build AOA for the Summary tab: ref_codes bucketed into partner groups, each
 * with per-month DR, a current-year cumulative total per code, a notes column,
 * and a per-group subtotal row. Codes with no current-year DR are omitted.
 * Blank groups are skipped.
 * Columns: group | ref_code | month… | total | notes
 */
function buildSummaryAoa(data: DataMap, months: string[]): (string | number)[][] {
  const header: (string | number)[] = ['group', 'ref_code', ...months, 'total', 'notes'];
  const out: (string | number)[][] = [header];
  const blankRow: (string | number)[] = Array(header.length).fill('');

  const byGroup = new Map<string, string[]>();
  for (const [code, mm] of data) {
    const total = months.reduce((s, m) => s + (mm.get(m) ?? 0), 0);
    if (total === 0) continue; // skip codes with no current-year DR
    const g = classifyGroup(code);
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g)!.push(code);
  }

  let firstGroup = true;
  for (const group of SUMMARY_GROUP_ORDER) {
    const codes = sortedCodes(byGroup.get(group) ?? []);
    if (codes.length === 0) continue;
    if (!firstGroup) out.push([...blankRow]);
    firstGroup = false;

    const monthSum = new Map<string, number>();
    let groupTotal = 0;

    codes.forEach((code, idx) => {
      const mm = data.get(code)!;
      const cells = months.map(m => {
        const v = mm.get(m);
        if (v !== undefined) monthSum.set(m, (monthSum.get(m) ?? 0) + v);
        return v === undefined || v === 0 ? '' : round2(v);
      });
      const total = months.reduce((s, m) => s + (mm.get(m) ?? 0), 0);
      groupTotal += total;
      out.push([idx === 0 ? group : '', refCell(code), ...cells, round2(total), getNote(code)]);
    });

    out.push([
      '', 'Total',
      ...months.map(m => { const v = monthSum.get(m) ?? 0; return v === 0 ? '' : round2(v); }),
      round2(groupTotal), '',
    ]);
  }

  return out;
}

/**
 * Build AOA that faithfully mirrors a wide CSV (header + rows), coercing numeric
 * cells to numbers so Excel treats them as values. Empty cells stay blank.
 * Used for the "Soter by Ref Code Token" tab — a direct copy of the latest
 * combined dr_monthly_by_refcode_token.csv.
 */
function buildRawCsvAoa(file: string): (string | number)[][] {
  const rows = parseCsv(fs.readFileSync(file, 'utf8'));
  return rows.map((row, ri) =>
    row.map(cell => {
      if (ri === 0 || cell === '') return cell;          // header / blank
      const n = Number(cell);
      return Number.isFinite(n) ? n : cell;              // numeric → number
    }),
  );
}

// ─── CSV parser ───────────────────────────────────────────────────────────────

/**
 * RFC-4180-compliant CSV parser. Handles quoted fields (including embedded
 * commas and doubled-quote escapes). Returns rows as string arrays.
 */
function parseCsv(content: string): string[][] {
  const rows: string[][] = [];
  const src = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  let i = 0;
  while (i <= src.length) {
    const row: string[] = [];
    while (i <= src.length) {
      if (i === src.length || src[i] === '\n') { i++; break; }
      if (src[i] === '"') {
        let field = '';
        i++; // skip opening quote
        while (i < src.length) {
          if (src[i] === '"') {
            if (src[i + 1] === '"') { field += '"'; i += 2; } // escaped quote
            else { i++; break; } // closing quote
          } else {
            field += src[i++];
          }
        }
        row.push(field);
        if (src[i] === ',') i++;
      } else {
        let field = '';
        while (i < src.length && src[i] !== ',' && src[i] !== '\n') field += src[i++];
        row.push(field.trim());
        if (src[i] === ',') i++;
      }
    }
    if (row.length > 1 || (row.length === 1 && row[0] !== '')) rows.push(row);
  }
  return rows;
}

// ─── loaders ──────────────────────────────────────────────────────────────────

/**
 * Our pipeline output: wide CSV with header ref_code,token,YYYY-MM,…,total_dr_usd.
 * Source: dr_monthly_by_refcode_token.csv (ref_code × token granularity).
 * All ref_codes are aggregated across tokens (compoundKey is identity).
 *
 * Also returns:
 *   tokensByCode — sorted list of tokens that had a non-zero 2026 value per ref_code
 *   allTokens    — every unique token seen across all rows (used for Soter Rates tab)
 */
function loadOurData(file: string): {
  data: DataMap;
  months2026: string[];
  tokensByCode: Map<string, string[]>;
  allTokens: string[];
} {
  const rows = parseCsv(fs.readFileSync(file, 'utf8'));
  const headers = rows[0];
  const months2026 = headers.filter(h => /^2026-\d{2}$/.test(h) && h <= '2026-05').sort();

  const data: DataMap = new Map();
  const tokenSets = new Map<string, Set<string>>();
  const allTokenSet = new Set<string>();

  for (let i = 1; i < rows.length; i++) {
    const cols = rows[i];
    const rawCode = cols[0]?.trim();
    const token   = cols[1]?.trim() ?? '';
    if (!rawCode) continue;
    if (token) allTokenSet.add(token);
    const mapKey = compoundKey(rawCode, token);
    if (!data.has(mapKey)) data.set(mapKey, new Map());
    if (!tokenSets.has(mapKey)) tokenSets.set(mapKey, new Set());
    const mm = data.get(mapKey)!;
    let hasValue2026 = false;
    for (const m of months2026) {
      const v = parseFloat(cols[headers.indexOf(m)] ?? '');
      if (!isNaN(v) && v !== 0) { mm.set(m, (mm.get(m) ?? 0) + v); hasValue2026 = true; }
    }
    if (hasValue2026 && token) tokenSets.get(mapKey)!.add(token);
  }

  const tokensByCode = new Map(
    [...tokenSets.entries()].map(([k, s]) => [k, [...s].sort()]),
  );
  return { data, months2026, tokensByCode, allTokens: [...allTokenSet].sort() };
}

/**
 * Spark reference: long CSV with columns dt,blockchain,…,ref_code,…,tw_reward_usd.
 * All fields are quoted. Aggregates tw_reward_usd by (YYYY-MM, ref_code).
 *
 * Mirrors the compoundKey() logic: codes 0/1 USDS-CLE get a compound key;
 * all other tokens for those codes are folded into the plain "0" / "1" row.
 */
function loadSparkData(file: string): { data: DataMap; months2026: string[] } {
  const rows = parseCsv(fs.readFileSync(file, 'utf8'));
  const headers = rows[0];
  const dtIdx   = headers.indexOf('dt');
  const codeIdx = headers.indexOf('ref_code');
  const valIdx  = headers.indexOf('tw_reward_usd');
  const tokenIdx = headers.indexOf('token');

  const data: DataMap = new Map();
  const monthSet = new Set<string>();

  for (let i = 1; i < rows.length; i++) {
    const cols = rows[i];
    const dt = cols[dtIdx]?.trim() ?? '';
    if (!dt.startsWith('2026')) continue;
    const month = dt.slice(0, 7);
    if (month > '2026-05') continue;
    monthSet.add(month);
    const code  = cols[codeIdx]?.trim() ?? '';
    const token = tokenIdx >= 0 ? (cols[tokenIdx]?.trim() ?? '') : '';
    const mapKey = compoundKey(code, token);
    const val = parseFloat(cols[valIdx] ?? '0');
    if (!mapKey || isNaN(val)) continue;
    if (!data.has(mapKey)) data.set(mapKey, new Map());
    const mm = data.get(mapKey)!;
    mm.set(month, (mm.get(month) ?? 0) + val);
  }

  return { data, months2026: [...monthSet].sort() };
}

/**
 * Amatsu: wide CSV with header [Partner Name, Referral Code, …, Mon YYYY, …, Total].
 * Keeps only Jan/Feb/Mar 2026. Values use thousands-separator commas ("1,160.2").
 */
function loadAmatsuData(file: string): { data: DataMap; months: string[] } {
  const TARGET = new Set(['2026-01', '2026-02', '2026-03']);
  const rows = parseCsv(fs.readFileSync(file, 'utf8'));
  const headerRow = rows[0];
  const refCodeIdx = headerRow.findIndex(h => h.trim() === 'Referral Code');

  const colMap: Array<[number, string]> = [];
  for (let i = 0; i < headerRow.length; i++) {
    const m = parseMonthCol(headerRow[i]);
    if (m && TARGET.has(m)) colMap.push([i, m]);
  }
  const months = colMap.map(([, m]) => m).sort();

  const data: DataMap = new Map();
  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    const code = row[refCodeIdx]?.trim() ?? '';
    if (!code) continue;
    const mm = new Map<string, number>();
    for (const [colIdx, month] of colMap) {
      const v = parseFloat((row[colIdx] ?? '').replace(/,/g, ''));
      if (!isNaN(v) && v !== 0) mm.set(month, v);
    }
    data.set(code, mm);
  }
  return { data, months };
}

/**
 * Payouts: wide CSV with header [Referral Code, Partner/Prime, Mon YYYY, …, Total].
 * Keeps only months from 2026-01 onwards. Values use thousands-separator commas.
 * Format is identical in structure to the Amatsu loader.
 */
function loadPayoutsData(file: string): { data: DataMap; months: string[] } {
  const rows = parseCsv(fs.readFileSync(file, 'utf8'));
  const headerRow = rows[0];
  const refCodeIdx = headerRow.findIndex(h => h.trim() === 'Referral Code');

  const colMap: Array<[number, string]> = [];
  for (let i = 0; i < headerRow.length; i++) {
    const m = parseMonthCol(headerRow[i]);
    if (m && m >= '2026-01') colMap.push([i, m]);
  }
  const months = colMap.map(([, m]) => m).sort();

  const data: DataMap = new Map();
  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    const code = row[refCodeIdx]?.trim() ?? '';
    if (!code) continue;
    const mm = new Map<string, number>();
    for (const [colIdx, month] of colMap) {
      const v = parseFloat((row[colIdx] ?? '').replace(/,/g, ''));
      if (!isNaN(v) && v !== 0) mm.set(month, v);
    }
    if (mm.size > 0) data.set(code, mm);
  }
  return { data, months };
}

/**
 * BA reference: columnar CSV where each group of 3 columns is (token, code, dr)
 * for one month. Header: "Jan,code,dr,Feb,code,dr,Mar,code,dr,…"
 * Aggregates dr by (YYYY-MM, ref_code), summing across all tokens.
 * Skips blank codes, non-numeric labels (Treasury rows), and zero dr values.
 * Normalises scientific-notation codes ("9E+19" → "90000000000000000000").
 */
function loadBaData(file: string): { data: DataMap; months: string[] } {
  const MONTH_KEY: Record<string, string> = {
    Jan: '2026-01', Feb: '2026-02', Mar: '2026-03',
    Apr: '2026-04', May: '2026-05', Jun: '2026-06',
  };

  const rows = parseCsv(fs.readFileSync(file, 'utf8'));
  const headerRow = rows[0];

  // Every 3 columns = (token_label, code, dr) for one month.
  const groups: Array<{ month: string; codeCol: number; drCol: number }> = [];
  for (let i = 0; i < headerRow.length - 1; i += 3) {
    const monthKey = MONTH_KEY[headerRow[i]?.trim()];
    if (monthKey) groups.push({ month: monthKey, codeCol: i + 1, drCol: i + 2 });
  }

  const months = groups.map(g => g.month);
  const data: DataMap = new Map();

  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    for (const { month, codeCol, drCol } of groups) {
      const codeRaw = row[codeCol]?.trim() ?? '';
      if (!codeRaw || !/^-?\d/.test(codeRaw)) continue; // skip blank / Treasury labels
      // Normalise scientific notation → full integer string ("9E+19" → "90000000000000000000")
      const code = /[eE]/.test(codeRaw) ? parseFloat(codeRaw).toFixed(0) : codeRaw;
      const dr = parseFloat(row[drCol]?.trim() ?? '');
      if (isNaN(dr) || dr === 0) continue;
      if (!data.has(code)) data.set(code, new Map());
      const mm = data.get(code)!;
      mm.set(month, (mm.get(month) ?? 0) + dr);
    }
  }

  return { data, months };
}

// ─── untagged normalisation ───────────────────────────────────────────────────

/**
 * For ours-vs-amatsu diff: merge our ref_codes −999999 and 99 into a single
 * "untagged" entry to match Amatsu's "untagged" row. The individual codes are
 * dropped from the returned map.
 */
function mergeOurUntagged(data: DataMap): DataMap {
  const out: DataMap = new Map();
  const merged = new Map<string, number>();
  for (const [code, mm] of data) {
    if (code === '-999999' || code === '99') {
      for (const [m, v] of mm) merged.set(m, (merged.get(m) ?? 0) + v);
    } else {
      out.set(code, mm);
    }
  }
  out.set('untagged', merged);
  return out;
}

/**
 * For spark-vs-amatsu diff: rename Spark's ref_code 99 → "untagged" to match
 * Amatsu's "untagged" row. Other codes are unchanged.
 */
function renameSparkUntagged(data: DataMap): DataMap {
  const out: DataMap = new Map();
  for (const [code, mm] of data) {
    out.set(code === '99' ? 'untagged' : code, mm);
  }
  return out;
}

// ─── workbook assembly ────────────────────────────────────────────────────────


function addSheet(wb: import('xlsx').WorkBook, aoa: (string | number)[][], name: string): void {
  const ws = utils.aoa_to_sheet(aoa);
  const numCols = aoa[0]?.length ?? 0;
  const headers = (aoa[0] ?? []).map(String);
  ws['!cols'] = Array.from({ length: numCols }, (_, i) => {
    if (headers[i] === 'group')      return { wch: 12 };
    if (headers[i] === 'ref_code')   return { wch: 20 };
    if (headers[i] === 'present_in') return { wch: 14 };
    if (headers[i] === 'tokens')     return { wch: 45 };
    if (headers[i] === 'notes')      return { wch: 55 };
    if (headers[i] === 'token')      return { wch: 12 };
    if (headers[i] === 'rate_type')  return { wch: 14 };
    return { wch: 13 };
  });
  utils.book_append_sheet(wb, ws, name);
}

/** Simple `--flag value` lookup over process.argv (returns undefined if absent). */
function getArg(flag: string): string | undefined {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

/** Local timestamp YYYY-MM-DD_HHMMSS — Windows-safe and lexically sortable. */
function runStamp(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_` +
         `${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

/** Lexically-greatest (i.e. newest) run-dir name under dune-results/combined/. */
function latestCombinedRun(combinedRoot: string): string {
  if (!fs.existsSync(combinedRoot)) {
    throw new Error(`No combined/ directory found at ${combinedRoot} — run \`npm run combine\` first.`);
  }
  const runs = fs.readdirSync(combinedRoot, { withFileTypes: true })
    .filter(e => e.isDirectory())
    .map(e => e.name)
    .sort();
  if (runs.length === 0) throw new Error(`No combined runs found in ${combinedRoot} — run \`npm run combine\` first.`);
  return runs[runs.length - 1];
}

function main(): void {
  const root = path.resolve('.');

  // Resolve the "Soter" input: explicit --input wins, else a --combined run dir,
  // else the latest combined/<TS>/ run.
  const combinedRoot = path.join(root, 'dune-results', 'combined');
  const inputArg = getArg('--input');
  let ourFile: string;
  if (inputArg) {
    ourFile = path.resolve(inputArg);
  } else {
    const run = getArg('--combined') ?? latestCombinedRun(combinedRoot);
    ourFile = path.join(combinedRoot, run, 'dr_monthly_by_refcode_token.csv');
  }

  const sparkFile   = path.join(root, 'spark-dr-data',  'query_5650519_full.csv');
  const amatsuFile  = path.join(root, 'amatsu-dr-data',
    'distribution-rewards-payouts-referralCode-monthly-2026-04-21_total.csv');
  const baFile      = path.join(root, 'ba-dr-data',     'ba-all-codes.csv');
  const payoutsFile = path.join(root, 'payouts-dr-data', 'payouts.csv');

  // Output is versioned into its own fresh-timestamp comparison run dir.
  const outFile = getArg('--out')
    ? path.resolve(getArg('--out')!)
    : path.join(root, 'dune-results', 'comparison', runStamp(), 'dr_comparison_2026.xlsx');

  console.log(`Input (Soter): ${path.relative(root, ourFile)}`);

  for (const f of [ourFile, sparkFile, amatsuFile, baFile, payoutsFile]) {
    if (!fs.existsSync(f)) { console.error(`Missing: ${f}`); process.exit(1); }
  }

  console.log('Loading data sources...');
  const { data: ourData, months2026: ourMonths, tokensByCode, allTokens } = loadOurData(ourFile);
  const { data: sparkData,    months2026: sparkMonths    } = loadSparkData(sparkFile);
  const { data: amatsuData,   months:     amatsuMonths   } = loadAmatsuData(amatsuFile);
  const { data: baData,       months:     baMonths       } = loadBaData(baFile);
  const { data: payoutsData,  months:     payoutsMonths  } = loadPayoutsData(payoutsFile);

  // Merged-untagged view: our -999999 + 99 → "untagged", used for diffs against
  // sources that carry a single "untagged" row (Amatsu, Payouts).
  const ourMergedUntagged = mergeOurUntagged(ourData);
  const sparkForAmatsu    = renameSparkUntagged(sparkData);

  // Overlapping months per comparison
  const ourSparkMonths   = ourMonths.filter(m => sparkMonths.includes(m));
  const ourBaMonths      = ourMonths.filter(m => baMonths.includes(m));
  const ourPayoutsMonths = ourMonths.filter(m => payoutsMonths.includes(m));

  console.log(`  Soter    : ${ourMonths.length} months, ${ourData.size} ref_codes`);
  console.log(`  Spark    : ${sparkMonths.length} months, ${sparkData.size} ref_codes`);
  console.log(`  Amatsu   : ${amatsuMonths.length} months, ${amatsuData.size} ref_codes`);
  console.log(`  BA       : ${baMonths.length} months, ${baData.size} ref_codes`);
  console.log(`  Payouts  : ${payoutsMonths.length} months, ${payoutsData.size} ref_codes`);

  // All codes from any source — used to include rows in the Soter tab even if our
  // pipeline returned no data for that code (blanks shown for missing values).
  const allRefCodes = new Set([
    ...ourData.keys(), ...sparkData.keys(), ...amatsuData.keys(),
    ...baData.keys(),  ...payoutsData.keys(),
  ]);

  const tabs: Array<{ name: string; aoa: (string | number)[][] }> = [
    { name: 'Summary',             aoa: buildSummaryAoa(ourData,    ourMonths)                                             },
    { name: 'Soter by Ref Code',   aoa: buildAoa(ourData,           ourMonths,      allRefCodes, tokensByCode)             },
    { name: 'Soter by Ref Code Token', aoa: buildRawCsvAoa(ourFile)                                                        },
    { name: 'Soter Rates',         aoa: buildRatesAoa(allTokens,    ourMonths)                                             },
    { name: 'Spark',               aoa: buildAoa(sparkData,         sparkMonths)                                          },
    { name: 'Amatsu',              aoa: buildAoa(amatsuData,        amatsuMonths)                                         },
    { name: 'BA',                  aoa: buildAoa(baData,            baMonths)                                             },
    { name: 'Payouts',             aoa: buildAoa(payoutsData,       payoutsMonths)                                        },
    { name: 'Diff Soter-Spark',    aoa: buildDiffAoa(ourData,            sparkData,    ourSparkMonths,   'soter', 'spark')   },
    { name: 'Diff Soter-Amatsu',   aoa: buildDiffAoa(ourMergedUntagged,  amatsuData,   amatsuMonths,     'soter', 'amatsu')  },
    { name: 'Diff Soter-BA',       aoa: buildDiffAoa(ourData,            baData,       ourBaMonths,      'soter', 'ba')      },
    { name: 'Diff Soter-Payouts',  aoa: buildDiffAoa(ourMergedUntagged,  payoutsData,  ourPayoutsMonths, 'soter', 'payouts') },
    { name: 'Diff Spark-Amatsu',   aoa: buildDiffAoa(sparkForAmatsu,     amatsuData,   amatsuMonths,     'spark', 'amatsu')  },
    { name: 'Diff Amatsu-Payouts', aoa: buildDiffAoa(amatsuData,         payoutsData,  amatsuMonths,     'amatsu', 'payouts') },
  ];

  if (!fs.existsSync(path.dirname(outFile))) fs.mkdirSync(path.dirname(outFile), { recursive: true });
  const wb = utils.book_new();
  for (const { name, aoa } of tabs) addSheet(wb, aoa, name);
  writeFile(wb, outFile);

  // Also write to a fixed well-known path so consumers always find the latest
  // without knowing the timestamp. The timestamped copy above is the archive.
  const latestFile = path.join(root, 'dune-results', 'dr_comparison_latest.xlsx');
  fs.copyFileSync(outFile, latestFile);

  console.log(`\nWritten: ${path.relative(root, outFile)}`);
  console.log(`Latest:  ${path.relative(root, latestFile)}`);
  console.log('Tabs: ' + tabs.map(t => t.name).join(' | '));
}

main();

