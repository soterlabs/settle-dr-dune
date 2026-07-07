/**
 * One-shot regeneration of dune-results/dr_comparison_latest.xlsx and all its
 * dependencies:
 *
 *   1. (unless --no-execute) re-EXECUTE the monthly DR queries + the Base PSM3
 *      windows whose period overlaps the settlement month, and poll until done
 *   2. (unless --no-spark-refresh) refresh spark-dr-data/query_5650519_full.csv
 *      from Spark's public query's latest stored result
 *   3. combine  — fetch all monthly results, write dune-results/combined/<TS>/
 *   4. compare  — build dune-results/comparison/<TS>/dr_comparison_2026.xlsx
 *                 and copy it to dune-results/dr_comparison_latest.xlsx
 *
 *   DUNE_API_KEY=... npm run regenerate                # full run
 *   DUNE_API_KEY=... npm run regenerate -- --no-execute       # reuse cached Dune results
 *   DUNE_API_KEY=... npm run regenerate -- --no-spark-refresh # keep local Spark CSV
 *
 * The settlement cutoff lives in settlement.ts (move it with
 * set-settlement-cutoff.ts, then push SQL via update-dune-queries.ts +
 * update-psm3-base-windows.ts before regenerating).
 */
import 'dotenv/config';
import { execFileSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { SETTLE_MONTH, settleEndExclusive } from './settlement.js';

const API = 'https://api.dune.com/api/v1';
const KEY = process.env.DUNE_API_KEY;
if (!KEY) { console.error('Set DUNE_API_KEY.'); process.exit(1); }
const H = { 'x-dune-api-key': KEY, 'content-type': 'application/json' };

const argv = process.argv.slice(2);
const noExecute = argv.includes('--no-execute');
const noSparkRefresh = argv.includes('--no-spark-refresh');

// The 9 monthly DR source queries (full-history — always re-executed).
const MONTHLY_IDS = [7877552, 7877553, 7877554, 7877555, 7877565, 7877566, 7877568, 7877569, 7877570];

// Base PSM3 windows: only windows overlapping months <= SETTLE_MONTH that are
// not yet frozen need re-running. A window is FROZEN once its whole period is
// before the settlement month (its stored result can never change).
const WINDOWS: { id: number; start: string; end: string }[] = [
  { id: 7877571, start: '2024-09-01', end: '2024-12-01' },
  { id: 7877572, start: '2024-12-01', end: '2025-03-01' },
  { id: 7877573, start: '2025-03-01', end: '2025-06-01' },
  { id: 7877574, start: '2025-06-01', end: '2025-09-01' },
  { id: 7877576, start: '2025-09-01', end: '2025-12-01' },
  { id: 7877577, start: '2025-12-01', end: '2026-03-01' },
  { id: 7877578, start: '2026-03-01', end: '2026-06-01' },
  { id: 7877579, start: '2026-06-01', end: '2026-07-01' },
];

// Spark's own public reference query (owner sparkdotfi) backing the "Spark"
// tabs; monthly grain, dt >= 2025-07-01, spETH excluded.
const SPARK_QUERY_ID = 5650519;
const SPARK_CSV = path.resolve('spark-dr-data', 'query_5650519_full.csv');

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function execute(id: number): Promise<string> {
  const res = await fetch(`${API}/query/${id}/execute`, {
    method: 'POST', headers: H, body: JSON.stringify({ performance: 'large' }),
  });
  if (!res.ok) throw new Error(`execute ${id} failed (${res.status}): ${await res.text()}`);
  return ((await res.json()) as { execution_id: string }).execution_id;
}

async function pollAll(executions: Map<number, string>): Promise<void> {
  for (;;) {
    await sleep(60_000);
    let pending = 0;
    const failed: number[] = [];
    for (const [qid, eid] of executions) {
      const res = await fetch(`${API}/execution/${eid}/status`, { headers: H });
      const { state } = (await res.json()) as { state: string };
      if (state === 'QUERY_STATE_FAILED' || state === 'QUERY_STATE_CANCELLED') failed.push(qid);
      else if (state !== 'QUERY_STATE_COMPLETED') pending++;
    }
    console.log(`  ${executions.size - pending} / ${executions.size} done`);
    if (failed.length) throw new Error(`executions failed for queries: ${failed.join(', ')}`);
    if (pending === 0) return;
  }
}

async function refreshSparkCsv(): Promise<void> {
  const header = ['dt', 'blockchain', 'token_symbol', 'token', 'referral_type', 'ref_code', 'tw_reward', 'price_usd', 'tw_reward_usd'];
  const rows: Record<string, unknown>[] = [];
  for (let offset = 0; ; ) {
    const res = await fetch(`${API}/query/${SPARK_QUERY_ID}/results?limit=1000&offset=${offset}`, { headers: H });
    if (!res.ok) throw new Error(`spark results failed (${res.status}): ${await res.text()}`);
    const j = (await res.json()) as { result?: { rows: Record<string, unknown>[] }; next_offset?: number | null };
    rows.push(...(j.result?.rows ?? []));
    if (!j.result?.rows?.length || j.next_offset == null) break;
    offset = j.next_offset;
  }
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  // dt arrives as '2026-06-01 00:00:00.000 UTC' — keep the date part only,
  // matching the committed CSV format.
  const lines = [header.map(esc).join(',')];
  for (const r of rows) {
    lines.push(header.map((h) => esc(h === 'dt' ? String(r[h]).slice(0, 10) : r[h])).join(','));
  }
  fs.writeFileSync(SPARK_CSV, lines.join('\n') + '\n');
  console.log(`  refreshed ${path.relative(process.cwd(), SPARK_CSV)} (${rows.length} rows)`);
}

async function main() {
  console.log(`Settlement month: ${SETTLE_MONTH}\n`);

  if (!noExecute) {
    // Only the window(s) overlapping the settlement month need a fresh run;
    // earlier windows are frozen (their period is over, results immutable) and
    // later ones are outside the cutoff.
    const monthStart = `${SETTLE_MONTH}-01`;
    const monthEnd = settleEndExclusive();
    const windowsEnd = WINDOWS.reduce((max, w) => (w.end > max ? w.end : max), '');
    if (windowsEnd < monthEnd) {
      throw new Error(
        `No Base PSM3 window covers the settlement month ${SETTLE_MONTH} (windows end at ${windowsEnd}). ` +
        `Append a new window here (WINDOWS), in update-psm3-base-windows.ts and combine-dr-results.ts ` +
        `SOURCES, create it on Dune, then re-run.`);
    }
    const windows = WINDOWS.filter((w) => w.start < monthEnd && w.end > monthStart);
    const ids = [...MONTHLY_IDS, ...windows.map((w) => w.id)];
    console.log(`1) Executing ${ids.length} queries on the large engine (9 monthlies + ${windows.length} window(s))...`);
    const executions = new Map<number, string>();
    for (const id of ids) {
      executions.set(id, await execute(id));
      await sleep(500);
    }
    console.log('   polling every 60s...');
    await pollAll(executions);
  } else {
    console.log('1) --no-execute: reusing latest stored Dune results.');
  }

  if (!noSparkRefresh) {
    console.log('\n2) Refreshing Spark reference CSV...');
    await refreshSparkCsv();
  } else {
    console.log('\n2) --no-spark-refresh: keeping local Spark CSV.');
  }

  const tsx = path.resolve('node_modules', '.bin', 'tsx');
  console.log('\n3) combine...');
  execFileSync(tsx, ['src/scripts/combine-dr-results.ts'], { stdio: 'inherit' });
  console.log('\n4) compare...');
  execFileSync(tsx, ['src/scripts/compare-dr.ts'], { stdio: 'inherit' });

  console.log('\nDone: dune-results/dr_comparison_latest.xlsx');
}

main().catch((e) => { console.error((e as Error).message); process.exit(1); });
