/**
 * compare-dr.ts
 *
 * Produces a 6-tab Excel workbook comparing 2026 DR revenue across three sources.
 *
 * Data tabs (same column format — ref_code | YYYY-MM… | total):
 *   "Our Data"  — dune-results/dr_monthly_by_refcode.csv, all 2026 months
 *   "Spark"     — spark-dr-data/query_5650519_full.csv, aggregated by (month, ref_code)
 *   "Amatsu"    — amatsu-dr-data/...csv, Jan–Mar 2026 only
 *
 * Diff tabs (source1 − source2, union of ref_codes, overlapping months):
 *   "Diff Ours-Spark"    — all 2026 months present in both
 *   "Diff Ours-Amatsu"   — Jan–Mar 2026; our −999999 + 99 merged → "untagged" to match Amatsu
 *   "Diff Spark-Amatsu"  — Jan–Mar 2026; Spark ref_code 99 renamed → "untagged" to match Amatsu
 *
 * Output: dune-results/dr_comparison_2026.xlsx
 * Usage:  npm run compare
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

/** Numeric ref_codes ascending, then non-numeric alphabetically. */
function sortedCodes(codes: Iterable<string>): string[] {
  return [...codes].sort((a, b) => {
    const na = Number(a), nb = Number(b);
    const aNum = Number.isFinite(na), bNum = Number.isFinite(nb);
    if (aNum && bNum) return na - nb;
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
 * Build AOA for a data tab: [ref_code, month…, total] per sorted ref_code.
 * Missing month values are left blank (empty string) so Excel displays no value.
 */
function buildAoa(data: DataMap, months: string[]): (string | number)[][] {
  const codes = sortedCodes([...data.keys()]);
  const header: (string | number)[] = ['ref_code', ...months, 'total'];
  const rows: (string | number)[][] = codes.map(code => {
    const mm = data.get(code) ?? new Map<string, number>();
    const vals = months.map(m => {
      const v = mm.get(m);
      return v !== undefined ? round2(v) : ('' as string | number);
    });
    const total = round2(vals.reduce<number>((s, v) => s + (typeof v === 'number' ? v : 0), 0));
    return [refCell(code), ...vals, total];
  });
  return [header, ...rows];
}

/**
 * Build AOA for a diff tab: source1 − source2.
 * Shows the UNION of both datasets' ref_codes; missing side treated as 0.
 * Months are the provided overlapping slice.
 * A `present_in` column flags codes that exist in only one source.
 */
function buildDiffAoa(
  d1: DataMap,
  d2: DataMap,
  months: string[],
  label1: string,
  label2: string,
): (string | number)[][] {
  const allCodes = sortedCodes([...new Set([...d1.keys(), ...d2.keys()])]);
  const header: (string | number)[] = ['ref_code', 'present_in', ...months, 'total_diff'];
  const rows: (string | number)[][] = allCodes.map(code => {
    const has1 = d1.has(code), has2 = d2.has(code);
    const present = has1 && has2 ? 'both' : has1 ? `${label1} only` : `${label2} only`;
    const m1 = d1.get(code) ?? new Map<string, number>();
    const m2 = d2.get(code) ?? new Map<string, number>();
    const diffs = months.map(m => round2((m1.get(m) ?? 0) - (m2.get(m) ?? 0)));
    const totalDiff = round2(diffs.reduce<number>((s, v) => s + v, 0));
    return [refCell(code), present, ...diffs, totalDiff];
  });
  return [header, ...rows];
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
 * Our pipeline output: wide CSV with header ref_code,YYYY-MM,…,total_dr_usd.
 */
function loadOurData(file: string): { data: DataMap; months2026: string[] } {
  const rows = parseCsv(fs.readFileSync(file, 'utf8'));
  const headers = rows[0];
  const months2026 = headers.filter(h => /^2026-\d{2}$/.test(h) && h <= '2026-05').sort();

  const data: DataMap = new Map();
  for (let i = 1; i < rows.length; i++) {
    const cols = rows[i];
    const code = cols[0]?.trim();
    if (!code) continue;
    const mm = new Map<string, number>();
    for (const m of months2026) {
      const v = parseFloat(cols[headers.indexOf(m)] ?? '');
      if (!isNaN(v) && v !== 0) mm.set(m, v);
    }
    data.set(code, mm);
  }
  return { data, months2026 };
}

/**
 * Spark reference: long CSV with columns dt,blockchain,…,ref_code,…,tw_reward_usd.
 * All fields are quoted. Aggregates tw_reward_usd by (YYYY-MM, ref_code).
 */
function loadSparkData(file: string): { data: DataMap; months2026: string[] } {
  const rows = parseCsv(fs.readFileSync(file, 'utf8'));
  const headers = rows[0];
  const dtIdx  = headers.indexOf('dt');
  const codeIdx = headers.indexOf('ref_code');
  const valIdx  = headers.indexOf('tw_reward_usd');

  const data: DataMap = new Map();
  const monthSet = new Set<string>();

  for (let i = 1; i < rows.length; i++) {
    const cols = rows[i];
    const dt = cols[dtIdx]?.trim() ?? '';
    if (!dt.startsWith('2026')) continue;
    const month = dt.slice(0, 7);
    if (month > '2026-05') continue;
    monthSet.add(month);
    const code = cols[codeIdx]?.trim() ?? '';
    const val = parseFloat(cols[valIdx] ?? '0');
    if (!code || isNaN(val)) continue;
    if (!data.has(code)) data.set(code, new Map());
    const mm = data.get(code)!;
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
  ws['!cols'] = Array.from({ length: numCols }, (_, i) => ({ wch: i === 0 ? 14 : i === 1 && String(aoa[0]?.[1]) === 'present_in' ? 14 : 13 }));
  utils.book_append_sheet(wb, ws, name);
}

function main(): void {
  const root = path.resolve('.');
  const ourFile    = path.join(root, 'dune-results',   'dr_monthly_by_refcode.csv');
  const sparkFile  = path.join(root, 'spark-dr-data',  'query_5650519_full.csv');
  const amatsuFile = path.join(root, 'amatsu-dr-data',
    'distribution-rewards-payouts-referralCode-monthly-2026-04-21_total.csv');
  const outFile    = path.join(root, 'dune-results',   'dr_comparison_2026.xlsx');

  for (const f of [ourFile, sparkFile, amatsuFile]) {
    if (!fs.existsSync(f)) { console.error(`Missing: ${f}`); process.exit(1); }
  }

  console.log('Loading data sources...');
  const { data: ourData,   months2026: ourMonths   } = loadOurData(ourFile);
  const { data: sparkData, months2026: sparkMonths } = loadSparkData(sparkFile);
  const { data: amatsuData, months: amatsuMonths   } = loadAmatsuData(amatsuFile);

  // Derived maps for "untagged" matching
  const ourForAmatsu   = mergeOurUntagged(ourData);
  const sparkForAmatsu = renameSparkUntagged(sparkData);

  // Overlapping months for ours-vs-spark (both have 2026-01..2026-06 currently)
  const ourSparkMonths = ourMonths.filter(m => sparkMonths.includes(m));

  console.log(`  Our Data : ${ourMonths.length} months, ${ourData.size} ref_codes`);
  console.log(`  Spark    : ${sparkMonths.length} months, ${sparkData.size} ref_codes`);
  console.log(`  Amatsu   : ${amatsuMonths.length} months, ${amatsuData.size} ref_codes`);

  const wb = utils.book_new();

  addSheet(wb, buildAoa(ourData,   ourMonths),       'Our Data');
  addSheet(wb, buildAoa(sparkData, sparkMonths),     'Spark');
  addSheet(wb, buildAoa(amatsuData, amatsuMonths),   'Amatsu');
  addSheet(wb, buildDiffAoa(ourData,        sparkData,  ourSparkMonths, 'ours',  'spark'),  'Diff Ours-Spark');
  addSheet(wb, buildDiffAoa(ourForAmatsu,   amatsuData, amatsuMonths,  'ours',  'amatsu'), 'Diff Ours-Amatsu');
  addSheet(wb, buildDiffAoa(sparkForAmatsu, amatsuData, amatsuMonths,  'spark', 'amatsu'), 'Diff Spark-Amatsu');

  if (!fs.existsSync(path.dirname(outFile))) fs.mkdirSync(path.dirname(outFile), { recursive: true });
  writeFile(wb, outFile);
  console.log(`\nWritten: ${path.relative(root, outFile)}`);
  console.log('Tabs: Our Data | Spark | Amatsu | Diff Ours-Spark | Diff Ours-Amatsu | Diff Spark-Amatsu');
}

main();

