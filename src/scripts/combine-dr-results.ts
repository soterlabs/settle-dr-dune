/**
 * Combines the per-source MONTHLY DR-revenue queries into the cross-asset
 * rollups that cannot be produced in a single Dune query (combining all five
 * sources re-inlines all five foundational queries and hits Dune's stage limit).
 *
 * All sources fetch the LATEST stored result (no re-execution, essentially
 * free). Run each monthly query on Dune first when you want fresh data, then
 * run this script to combine.
 *
 * Base PSM3 uses 8 windowed quarterly queries (one per calendar quarter) whose
 * union reproduces the original full-history query. Each window only
 * materialises one [start, end) slice and runs in 1–15 min on the large engine.
 *
 *   $env:DUNE_API_KEY="..."; npx tsx src/scripts/combine-dr-results.ts
 *
 * Output is versioned: each run writes into its OWN timestamped directory
 *   dune-results/combined/<YYYY-MM-DD_HHMMSS>/
 * so prior results are never overwritten (the Dune source results are cached
 * and not always reproducible, so we keep every run under version control).
 * Three CSVs are written there, pivoted WIDE (one column per YYYY-MM):
 *   - dr_monthly_by_refcode.csv
 *   - dr_monthly_by_refcode_token.csv
 *   - dr_monthly_combined.csv
 */
import 'dotenv/config';
import * as fs from 'fs';
import * as path from 'path';

const API = 'https://api.dune.com/api/v1';
const KEY = process.env.DUNE_API_KEY;
if (!KEY) { console.error('Set DUNE_API_KEY.'); process.exit(1); }
const H = { 'x-dune-api-key': KEY };

// All sources: fetch latest stored result — no re-execution, essentially free.
// Base PSM3 is covered by 8 non-overlapping quarterly windows whose union gives
// full-history coverage (original single query 7647196 always timed out).
// Re-run a window on Dune whenever its quarter needs refreshing.
const SOURCES: { source: string; id: number }[] = [
  { source: 'susds_susdc',   id: 7646377 },
  // psm3_base — 8 quarterly windows, union = full history.
  // Re-created under our own account (old openmsc-owned 7684981–88 were not
  // editable); see create-psm3-base-windows.ts for the old→new ID mapping.
  { source: 'psm3_base',     id: 7842602 }, // 2024-09-01 → 2024-12-01
  { source: 'psm3_base',     id: 7842603 }, // 2024-12-01 → 2025-03-01
  { source: 'psm3_base',     id: 7842604 }, // 2025-03-01 → 2025-06-01
  { source: 'psm3_base',     id: 7842605 }, // 2025-06-01 → 2025-09-01
  { source: 'psm3_base',     id: 7842606 }, // 2025-09-01 → 2025-12-01
  { source: 'psm3_base',     id: 7842607 }, // 2025-12-01 → 2026-03-01
  { source: 'psm3_base',     id: 7842608 }, // 2026-03-01 → 2026-06-01
  { source: 'psm3_base',     id: 7842609 }, // 2026-06-01 → 2026-07-01
  { source: 'psm3_arbitrum', id: 7647197 },
  { source: 'psm3_optimism', id: 7647198 },
  { source: 'psm3_unichain', id: 7647199 },
  { source: 'stusds',        id: 7646379 },
  { source: 'farms',         id: 7646380 },
  { source: 'sp',            id: 7683760 },
  // USDS held in Aave aEthUSDS (0x32a6268f9Ba3642Dda7892aDd74f1D34469A4259).
  // Synthetic ref_code 9001.
  { source: 'usds_aave',     id: 7812438 },
  // USDS held at in Solana OFT Bridge 0x1e1D42781FC170EF9da004Fb735f56F0276d01B8.
  // Synthetic ref_code 4001.
  { source: 'usds_ref4001',  id: 7809596 },
];

// Local timestamp YYYY-MM-DD_HHMMSS — Windows-safe (no colons) and lexically
// sortable, so the newest run dir is always the lexically-greatest name.
function runStamp(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_` +
         `${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

// Each run gets its own timestamped subdir under dune-results/combined/.
const OUT_DIR = path.resolve('dune-results', 'combined', runStamp());

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
    const rawBatch = (j.result?.rows ?? []) as unknown as Record<string, unknown>[];
    // Normalise: some saved queries use 'dt' instead of 'month' as the date column.
    const batch = rawBatch.map((r) => ({
      ...r,
      month: (r['month'] ?? r['dt']) as string,
    })) as unknown as MonthlyRow[];
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
  console.log(`Run dir: ${path.relative(process.cwd(), OUT_DIR)}\n`);

  const all: (MonthlyRow & { source: string })[] = [];

  // Fetch latest stored result for every source (free — no re-execution).
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

  // ref_codes whose DR is shown per-token rather than aggregated in the by_refcode rollup.
  const SPLIT_BY_TOKEN_CODES = new Set([0, 1]);

  // Sort that handles both plain numeric codes and compound labels like "0 (USDS-CLE)".
  const numericBase = (v: string | number) => {
    const m = String(v).match(/^(-?\d+)/);
    return m ? Number(m[1]) : NaN;
  };
  const byRefAsc = (a: (string | number)[], b: (string | number)[]) => {
    const na = numericBase(a[0]), nb = numericBase(b[0]);
    if (Number.isFinite(na) && Number.isFinite(nb)) {
      if (na !== nb) return na - nb;
      return String(a[0]).localeCompare(String(b[0]));
    }
    return String(a[0]).localeCompare(String(b[0]));
  };
  const byRefThenToken = (a: (string | number)[], b: (string | number)[]) =>
    Number(a[0]) - Number(b[0]) || String(a[1]).localeCompare(String(b[1]));
  const byRefTokenChainSource = (a: (string | number)[], b: (string | number)[]) =>
    Number(a[0]) - Number(b[0]) ||
    String(a[1]).localeCompare(String(b[1])) ||
    String(a[2]).localeCompare(String(b[2])) ||
    String(a[3]).localeCompare(String(b[3]));

  // 1. Per ref_code (one line each), months across, total.
  //    ref_codes 0 and 1 are split by token (compound label "0 (token)") so that
  //    Chronicle / USDS-CLE is visibly separated from sUSDS, USDS-SKY, etc.
  const p1 = pivot(
    ['ref_code'],
    (r) => [SPLIT_BY_TOKEN_CODES.has(r.ref_code) ? `${r.ref_code} (${r.token})` : r.ref_code],
    byRefAsc,
  );
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
