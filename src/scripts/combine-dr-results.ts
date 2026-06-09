/**
 * Combines the five per-source MONTHLY DR-revenue queries into the cross-asset
 * rollups that cannot be produced in a single Dune query (combining all five
 * sources re-inlines all five foundational queries and hits Dune's stage limit).
 *
 * It fetches the LATEST stored result of each monthly query (no re-execution,
 * so it is essentially free) and writes three CSVs to dune-results/. All are
 * pivoted WIDE: one column per YYYY-MM month + a total_dr_usd column, rows
 * sorted by ref_code ascending (empty cell = no activity that month):
 *   - dr_monthly_by_refcode.csv       ref_code,            <months...>, total
 *   - dr_monthly_by_refcode_token.csv ref_code, token,     <months...>, total
 *   - dr_monthly_combined.csv         ref_code, token, blockchain, source, <months...>, total
 *
 * Run the five monthly queries on Dune first (so they have a fresh result),
 * then:
 *   $env:DUNE_API_KEY="..."; npx tsx src/scripts/combine-dr-results.ts
 */
import 'dotenv/config';
import * as fs from 'fs';
import * as path from 'path';

const API = 'https://api.dune.com/api/v1';
const KEY = process.env.DUNE_API_KEY;
if (!KEY) { console.error('Set DUNE_API_KEY.'); process.exit(1); }
const H = { 'x-dune-api-key': KEY };

const SOURCES: { source: string; id: number }[] = [
  { source: 'susds_susdc',    id: 7646377 },
  // psm3 (7646378) retired — timed out on 4-chain combined scan; split by chain:
  // ###########################################################################
  // ## !! DISABLED !! psm3_base (7647196) ALWAYS HITS DUNE'S 30-MIN EXECUTION ##
  // ## LIMIT and never returns a result, so its rewards are MISSING from the  ##
  // ## combined output below. Base L2 sUSDS DR revenue is therefore UNDER-    ##
  // ## COUNTED until this query is made to run (e.g. split by year). Re-enable ##
  // ## the line once 7647196 produces a result.                               ##
  // ###########################################################################
  // { source: 'psm3_base',      id: 7647196 },
  { source: 'psm3_arbitrum',  id: 7647197 },
  { source: 'psm3_optimism',  id: 7647198 },
  { source: 'psm3_unichain',  id: 7647199 },
  { source: 'stusds',         id: 7646379 },
  { source: 'farms',          id: 7646380 },
  { source: 'sp',             id: 7683760 },
];

const OUT_DIR = path.resolve('dune-results');

interface MonthlyRow {
  month: string;
  blockchain: string;
  token: string;
  ref_code: number;
  dr_usd: number;
  avg_twa_balance: number;
}

async function fetchLatestRows(id: number): Promise<MonthlyRow[]> {
  const rows: MonthlyRow[] = [];
  let offset = 0;
  const limit = 1000;
  for (;;) {
    const res = await fetch(`${API}/query/${id}/results?limit=${limit}&offset=${offset}`, { headers: H });
    if (!res.ok) {
      throw new Error(`query ${id} results failed (${res.status}): ${await res.text()}`);
    }
    const j = await res.json() as {
      result?: { rows: MonthlyRow[] };
      is_execution_finished?: boolean;
      next_offset?: number | null;
    };
    const batch = j.result?.rows ?? [];
    rows.push(...batch);
    if (batch.length < limit || j.next_offset == null) break;
    offset = j.next_offset;
  }
  return rows;
}

function toCsvValue(v: unknown): string {
  const s = v === null || v === undefined ? '' : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function writeCsv(file: string, header: string[], rows: unknown[][]): void {
  const lines = [header.join(',')];
  for (const r of rows) lines.push(r.map(toCsvValue).join(','));
  fs.writeFileSync(file, lines.join('\n') + '\n');
  console.log(`  wrote ${path.basename(file)} (${rows.length} rows)`);
}

async function main() {
  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });

  const all: (MonthlyRow & { source: string })[] = [];
  for (const { source, id } of SOURCES) {
    process.stdout.write(`Fetching ${source} (query_${id})... `);
    const rows = await fetchLatestRows(id);
    console.log(`${rows.length} rows`);
    for (const r of rows) all.push({ ...r, source });
  }

  // Sorted list of YYYY-MM month columns present across all sources.
  const MONTHS = [...new Set(all.map((r) => r.month.slice(0, 7)))].sort();

  // Pivot helper: one row per distinct key, a column per month, plus a total.
  // Empty cell = no activity that month for that key.
  function pivot(
    keyHeaders: string[],
    keyOf: (r: MonthlyRow & { source: string }) => (string | number)[],
    sortRows: (a: (string | number)[], b: (string | number)[]) => number,
  ): { header: string[]; rows: (string | number)[][] } {
    const m = new Map<string, { key: (string | number)[]; months: Map<string, number>; total: number }>();
    for (const r of all) {
      const key = keyOf(r);
      const ks = key.join('\u0000');
      let e = m.get(ks);
      if (!e) { e = { key, months: new Map(), total: 0 }; m.set(ks, e); }
      const v = Number(r.dr_usd) || 0;
      const mo = r.month.slice(0, 7);
      e.months.set(mo, (e.months.get(mo) ?? 0) + v);
      e.total += v;
    }
    const rows = [...m.values()]
      .map((e) => [...e.key, ...MONTHS.map((mo) => (e.months.has(mo) ? e.months.get(mo)! : '')), e.total])
      .sort(sortRows);
    return { header: [...keyHeaders, ...MONTHS, 'total_dr_usd'], rows };
  }

  const byRefAsc = (a: (string | number)[], b: (string | number)[]) => Number(a[0]) - Number(b[0]);
  const byRefThenToken = (a: (string | number)[], b: (string | number)[]) =>
    Number(a[0]) - Number(b[0]) || String(a[1]).localeCompare(String(b[1]));
  const byRefTokenChainSource = (a: (string | number)[], b: (string | number)[]) =>
    Number(a[0]) - Number(b[0]) ||
    String(a[1]).localeCompare(String(b[1])) ||
    String(a[2]).localeCompare(String(b[2])) ||
    String(a[3]).localeCompare(String(b[3]));

  // 1. Per ref_code (one line each), months across, total.
  const p1 = pivot(['ref_code'], (r) => [r.ref_code], byRefAsc);
  writeCsv(path.join(OUT_DIR, 'dr_monthly_by_refcode.csv'), p1.header, p1.rows);

  // 2. Per (ref_code, token): one line per token, tokens grouped under each ref_code.
  const p2 = pivot(['ref_code', 'token'], (r) => [r.ref_code, r.token], byRefThenToken);
  writeCsv(path.join(OUT_DIR, 'dr_monthly_by_refcode_token.csv'), p2.header, p2.rows);

  // 3. Full detail (ref_code, token, blockchain, source), months across, total.
  const p3 = pivot(
    ['ref_code', 'token', 'blockchain', 'source'],
    (r) => [r.ref_code, r.token, r.blockchain, r.source],
    byRefTokenChainSource,
  );
  writeCsv(path.join(OUT_DIR, 'dr_monthly_combined.csv'), p3.header, p3.rows);

  console.log('\nDone.');
}

main().catch((e) => { console.error('\n' + (e as Error).message); process.exit(1); });
