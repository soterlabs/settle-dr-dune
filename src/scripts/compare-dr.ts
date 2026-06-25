/**
 * compare-dr.ts
 *
 * Produces a 6-tab Excel workbook comparing 2026 DR revenue across three sources.
 *
 * Data tabs (same column format — ref_code | YYYY-MM… | total | notes):
 *   "Soter Data" — latest dune-results/combined/<TS>/dr_monthly_by_refcode_token.csv
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
 * ref_codes that represent untagged / no-referral TVL.
 * These rows are separated to the bottom of every tab.
 */
const UNTAGGED_CODES = new Set([
  '-999999', '99', '123623963915635', '90000000000000000000', 'untagged',
]);

/**
 * Notes shown on every tab (data + diff) for specific ref_codes.
 * Plain numeric keys match both plain codes and the base of compound codes
 * (e.g. key '0' does NOT match '0 (sUSDS)' — compound codes get their own entry).
 */
const NOTES: Record<string, string> = {
  '0 (USDS-CLE)': 'Chronicle farm — not tracked by Spark/BA.',
  '1 (USDS-CLE)': 'Chronicle farm — not tracked by Spark/BA.',
  '126':  'Subproxy holdings, no DR applied. Handled in Supply Side MSC.',
  '127':  'Spark untagged Savings, had boosted DR applied by Amatsu.',
  '130':  'Found in Spark datasets only, no onchain events.',
  '188':  'Had boosted DR applied by Amatsu.',
  '190':  'Had boosted DR applied by Amatsu.',
  '191':  'Had boosted DR applied by Amatsu.',
  '192':  'Had boosted DR applied by Amatsu.',
  '195':  'Had boosted DR applied by Amatsu.',
  '197':  'stUSDS. Amatsu/BA applied DR in Jan./Feb. then switched to 0.1% after. s/b 0.1% always applied.',
  '1001': 'Feb. and Mar. had no DR by Amatsu.',
  '1016': 'Amatsu included DR for sUSDC, sUSDS, SKY, while onchain, only events for SKY can be found. unknown which balances or events to use for sUSDC and sUSDS.',
};

function getNote(code: string): string {
  return NOTES[code] ?? '';
}

/**
 * Ref_codes that are split by token in our output (compound keys like "0 (sUSDS)").
 * These are produced by combine-dr-results.ts and matched in Spark's data.
 */
const SPLIT_CODES = new Set(['0', '1']);

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
 * Build AOA for a data tab: [ref_code, month…, total, notes] per sorted ref_code.
 * Missing month values are left blank (empty string) so Excel displays no value.
 * Untagged ref_codes are separated to the bottom with a blank divider row.
 *
 * @param extraCodes Optional additional ref_codes to include even if absent from
 *   `data` (used for the Soter tab to show every code found in Spark or Amatsu).
 */
function buildAoa(
  data: DataMap,
  months: string[],
  extraCodes?: Iterable<string>,
): (string | number)[][] {
  const codeSet = new Set(data.keys());
  if (extraCodes) for (const c of extraCodes) codeSet.add(c);

  const allSorted    = sortedCodes([...codeSet]);
  const taggedCodes   = allSorted.filter(c => !UNTAGGED_CODES.has(c));
  const untaggedCodes = allSorted.filter(c =>  UNTAGGED_CODES.has(c));

  const header: (string | number)[] = ['ref_code', ...months, 'total', 'notes'];
  const blankRow: (string | number)[] = Array(header.length).fill('');

  const makeRow = (code: string): (string | number)[] => {
    const mm = data.get(code) ?? new Map<string, number>();
    const vals = months.map(m => {
      const v = mm.get(m);
      return v !== undefined ? round2(v) : ('' as string | number);
    });
    const total = round2(vals.reduce<number>((s, v) => s + (typeof v === 'number' ? v : 0), 0));
    return [refCell(code), ...vals, total, getNote(code)];
  };

  const untaggedHeader: (string | number)[] = ['── untagged ──', ...Array(header.length - 1).fill('')];

  return [
    header,
    ...taggedCodes.map(makeRow),
    ...(untaggedCodes.length > 0 ? [blankRow, untaggedHeader, ...untaggedCodes.map(makeRow)] : []),
  ];
}

/**
 * Build AOA for a diff tab: source1 − source2.
 * Shows the UNION of both datasets' ref_codes; missing side treated as 0.
 * Months are the provided overlapping slice.
 * Columns: ref_code | present_in | notes | month… | total_diff
 * A `present_in` column flags codes that exist in only one source.
 * A `notes` column carries ref_code-specific annotations (e.g. ref 126).
 * Untagged ref_codes are separated to the bottom with a blank divider row.
 */
function buildDiffAoa(
  d1: DataMap,
  d2: DataMap,
  months: string[],
  label1: string,
  label2: string,
): (string | number)[][] {
  const allCodes    = sortedCodes([...new Set([...d1.keys(), ...d2.keys()])]);
  const taggedCodes   = allCodes.filter(c => !UNTAGGED_CODES.has(c));
  const untaggedCodes = allCodes.filter(c =>  UNTAGGED_CODES.has(c));

  const header: (string | number)[] = ['ref_code', 'present_in', ...months, 'total_diff', 'notes'];
  const blankRow: (string | number)[] = Array(header.length).fill('');

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

  const untaggedHeader: (string | number)[] = ['── untagged ──', ...Array(header.length - 1).fill('')];

  return [
    header,
    ...taggedCodes.map(makeRow),
    ...(untaggedCodes.length > 0 ? [blankRow, untaggedHeader, ...untaggedCodes.map(makeRow)] : []),
  ];
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
 *
 * For ref_codes in SPLIT_CODES (0, 1) each token gets its own DataMap entry
 * with a compound key "0 (sUSDS)", "1 (USDS-CLE)", etc.
 * All other ref_codes are aggregated across tokens (same as the old by_refcode rollup).
 */
function loadOurData(file: string): { data: DataMap; months2026: string[] } {
  const rows = parseCsv(fs.readFileSync(file, 'utf8'));
  const headers = rows[0];
  const months2026 = headers.filter(h => /^2026-\d{2}$/.test(h) && h <= '2026-05').sort();

  const data: DataMap = new Map();
  for (let i = 1; i < rows.length; i++) {
    const cols = rows[i];
    const rawCode = cols[0]?.trim();
    const token   = cols[1]?.trim() ?? '';
    if (!rawCode) continue;
    const mapKey = SPLIT_CODES.has(rawCode) ? `${rawCode} (${token})` : rawCode;
    if (!data.has(mapKey)) data.set(mapKey, new Map());
    const mm = data.get(mapKey)!;
    for (const m of months2026) {
      const v = parseFloat(cols[headers.indexOf(m)] ?? '');
      if (!isNaN(v) && v !== 0) mm.set(m, (mm.get(m) ?? 0) + v);
    }
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
    const code = cols[codeIdx]?.trim() ?? '';
    const token = tokenIdx >= 0 ? (cols[tokenIdx]?.trim() ?? '') : '';
    // Mirror the compound-key split used in our pipeline for codes 0 and 1.
    const mapKey = SPLIT_CODES.has(code) ? `${code} (${token})` : code;
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
    if (headers[i] === 'ref_code')   return { wch: 20 };
    if (headers[i] === 'present_in') return { wch: 14 };
    if (headers[i] === 'notes')      return { wch: 55 };
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

  const sparkFile  = path.join(root, 'spark-dr-data',  'query_5650519_full.csv');
  const amatsuFile = path.join(root, 'amatsu-dr-data',
    'distribution-rewards-payouts-referralCode-monthly-2026-04-21_total.csv');

  // Output is versioned into its own fresh-timestamp comparison run dir.
  const outFile = getArg('--out')
    ? path.resolve(getArg('--out')!)
    : path.join(root, 'dune-results', 'comparison', runStamp(), 'dr_comparison_2026.xlsx');

  console.log(`Input (Soter): ${path.relative(root, ourFile)}`);

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

  // Overlapping months for ours-vs-spark
  const ourSparkMonths = ourMonths.filter(m => sparkMonths.includes(m));

  console.log(`  Soter    : ${ourMonths.length} months, ${ourData.size} ref_codes`);
  console.log(`  Spark    : ${sparkMonths.length} months, ${sparkData.size} ref_codes`);
  console.log(`  Amatsu   : ${amatsuMonths.length} months, ${amatsuData.size} ref_codes`);

  // All codes from any source — used to include rows in the Soter tab even if our
  // pipeline returned no data for that code (blanks shown for missing values).
  const allRefCodes = new Set([...ourData.keys(), ...sparkData.keys(), ...amatsuData.keys()]);

  const tabs: Array<{ name: string; aoa: (string | number)[][] }> = [
    { name: 'Soter Data',         aoa: buildAoa(ourData,        ourMonths,      allRefCodes)                       },
    { name: 'Spark',              aoa: buildAoa(sparkData,      sparkMonths)                                       },
    { name: 'Amatsu',             aoa: buildAoa(amatsuData,     amatsuMonths)                                      },
    { name: 'Diff Soter-Spark',   aoa: buildDiffAoa(ourData,        sparkData,  ourSparkMonths, 'soter', 'spark')  },
    { name: 'Diff Soter-Amatsu',  aoa: buildDiffAoa(ourForAmatsu,   amatsuData, amatsuMonths,   'soter', 'amatsu') },
    { name: 'Diff Spark-Amatsu',  aoa: buildDiffAoa(sparkForAmatsu, amatsuData, amatsuMonths,   'spark', 'amatsu') },
  ];

  if (!fs.existsSync(path.dirname(outFile))) fs.mkdirSync(path.dirname(outFile), { recursive: true });
  const wb = utils.book_new();
  for (const { name, aoa } of tabs) addSheet(wb, aoa, name);
  writeFile(wb, outFile);

  console.log(`\nWritten: ${path.relative(root, outFile)}`);
  console.log('Tabs: ' + tabs.map(t => t.name).join(' | '));
}

main();

