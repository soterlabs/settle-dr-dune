/**
 * Combines the per-source DAILY-USDS diagnostic queries into one wide CSV so the
 * USDS base each source contributes can be eyeballed side-by-side for
 * double-counting. The per-source split exists because a single query that
 * references every foundational TWA query at once inlines them all and fails
 * Dune's "too many stages" limit (same reason combine-dr-results.ts exists).
 *
 * All sources fetch the LATEST stored result (no re-execution, essentially
 * free). Run each diag_daily_usds_*.sql on Dune first when you want fresh data,
 * then run this script to combine.
 *
 *   $env:DUNE_API_KEY="..."; npx tsx src/scripts/combine-daily-usds.ts
 *
 * Writes dune-results/daily_usds_by_source.csv, pivoted WIDE (one column per
 * source) with a total_usds column. Base chain is excluded by the queries.
 *
 * SETUP: save each query below to Dune, run it, and paste its query ID here.
 * Sources with id 0 are skipped (so you can wire them up incrementally).
 */
import 'dotenv/config';
import * as fs from 'fs';
import * as path from 'path';

const API = 'https://api.dune.com/api/v1';
const KEY = process.env.DUNE_API_KEY;
if (!KEY) { console.error('Set DUNE_API_KEY.'); process.exit(1); }
const H = { 'x-dune-api-key': KEY };

// Replace each 0 with the saved query ID (PATCH via update-dune-queries.ts or
// save manually on Dune). `source` MUST match the label emitted by the .sql.
const SOURCES: { source: string; id: number; file: string }[] = [
  { source: 'susds_susdc', id: 0, file: 'diag_daily_usds_susds_susdc.sql' },
  { source: 'psm3',        id: 0, file: 'diag_daily_usds_psm3.sql' },
  { source: 'stusds',      id: 0, file: 'diag_daily_usds_stusds.sql' },
  { source: 'farms',       id: 0, file: 'diag_daily_usds_farms.sql' },
  { source: 'sp',          id: 0, file: 'diag_daily_usds_sp.sql' },
  { source: 'usds_aave',   id: 0, file: 'diag_daily_usds_aave.sql' },
];

const OUT_DIR = path.resolve('dune-results');

interface DailyRow {
  dt: string;
  source: string;
  usds_base: number;
}

async function fetchLatestRows(id: number): Promise<DailyRow[]> {
  const rows: DailyRow[] = [];
  let offset = 0;
  const limit = 1000;
  for (;;) {
    const res = await fetch(`${API}/query/${id}/results?limit=${limit}&offset=${offset}`, { headers: H });
    if (!res.ok) {
      throw new Error(`query ${id} results failed (${res.status}): ${await res.text()}`);
    }
    const j = await res.json() as { result?: { rows: DailyRow[] }; next_offset?: number | null };
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

async function main() {
  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });

  const active = SOURCES.filter((s) => s.id !== 0);
  const skipped = SOURCES.filter((s) => s.id === 0);
  if (skipped.length) {
    console.log(`Skipping (no query ID yet): ${skipped.map((s) => s.source).join(', ')}`);
  }
  if (!active.length) {
    console.error('No query IDs set. Edit SOURCES in this script with the saved Dune query IDs.');
    process.exit(1);
  }

  // day -> (source -> usds_base)
  const byDay = new Map<string, Map<string, number>>();
  for (const { source, id } of active) {
    process.stdout.write(`Fetching ${source} (query_${id})... `);
    const rows = await fetchLatestRows(id);
    console.log(`${rows.length} rows`);
    for (const r of rows) {
      const day = String(r.dt).slice(0, 10);
      let e = byDay.get(day);
      if (!e) { e = new Map(); byDay.set(day, e); }
      // The query emits its own source label, but key off the configured source
      // so a mislabeled row can't create a phantom column.
      e.set(source, (e.get(source) ?? 0) + (Number(r.usds_base) || 0));
    }
  }

  const sources = active.map((s) => s.source);
  const header = ['dt', ...sources.map((s) => `${s}_usds`), 'total_usds'];
  const days = [...byDay.keys()].sort();
  const lines = [header.join(',')];
  for (const day of days) {
    const e = byDay.get(day)!;
    let total = 0;
    const cells = sources.map((s) => {
      const v = e.get(s);
      if (v === undefined) return '';
      total += v;
      return v;
    });
    lines.push([day, ...cells, total].map(toCsvValue).join(','));
  }

  const file = path.join(OUT_DIR, 'daily_usds_by_source.csv');
  fs.writeFileSync(file, lines.join('\n') + '\n');
  console.log(`\n  wrote ${path.basename(file)} (${days.length} days)`);
  console.log('Done.');
}

main().catch((e) => { console.error('\n' + (e as Error).message); process.exit(1); });
