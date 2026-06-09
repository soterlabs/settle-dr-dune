/**
 * Runs the WINDOWED Base PSM3 query (queries/dr_rewards_monthly_psm3_base.sql)
 * one calendar-quarter at a time on Dune's LARGEST engine, then unions the
 * per-window monthly rows client-side. Each window only materializes the
 * per-(user, day) TWA calendar for its own [start, end) slice, so it fits inside
 * Dune's 30-minute execution limit — unlike the un-windowed query which always
 * timed out (see the header of the .sql for why the split stays correct).
 *
 * Windows are aligned to month boundaries (quarters), so the monthly output
 * grain is never split across two windows. Genesis is 2024-09-01; the final
 * window's end_date is the first-of-month after today, so ongoing holders accrue
 * idle-day rewards up to current_date.
 *
 * REPRODUCIBILITY: only the FINAL window's idle-fill depends on Dune's
 * current_date (past windows collapse to end_date - 1 day regardless). Since each
 * window is a separate execution, a run that straddles UTC midnight could fill
 * the final window to a different day than a single-shot run would. Re-run the
 * final window if you need it pinned to a specific as-of date.
 *
 *   DUNE_API_KEY=<large-capable key> npx tsx src/scripts/run-psm3-base-windows.ts
 *   # test a single window (no CSV written, prints a sample):
 *   DUNE_API_KEY=<key> npx tsx src/scripts/run-psm3-base-windows.ts --window=2026-04-01:2026-07-01
 *
 * Writes dune-results/dr_rewards_monthly_psm3_base.csv on a full run. That CSV
 * is what combine-dr-results.ts should read for Base (the saved Dune query
 * 7684915 can only hold one window's result at a time).
 */
import 'dotenv/config';
import * as fs from 'fs';
import * as path from 'path';

const API = 'https://api.dune.com/api/v1';
const KEY = process.env.DUNE_API_KEY;
if (!KEY) { console.error('Set DUNE_API_KEY (must be on a plan that allows performance=large).'); process.exit(1); }
const H = { 'x-dune-api-key': KEY, 'Content-Type': 'application/json' };

const SQL_FILE = path.resolve('queries/dr_rewards_monthly_psm3_base.sql');
const GENESIS = '2024-09-01';
const PERFORMANCE = 'large';
const POLL_MS = 15000;
const MAX_POLLS = 130; // ~32 min ceiling, just past Dune's 30-min limit

interface MonthlyRow {
  month: string; blockchain: string; token: string;
  ref_code: number; dr_usd: number; avg_twa_balance: number;
}

// First-of-month string N months after a YYYY-MM-01 string.
function addMonths(ymd: string, n: number): string {
  const [y, m] = ymd.split('-').map(Number);
  const idx = (y * 12 + (m - 1)) + n;
  const ny = Math.floor(idx / 12);
  const nm = (idx % 12) + 1;
  return `${ny}-${String(nm).padStart(2, '0')}-01`;
}

// Quarterly windows [start, end) from GENESIS through the month after `today`.
function quarterWindows(today: Date): { start: string; end: string }[] {
  const endCap = `${today.getUTCFullYear()}-${String(today.getUTCMonth() + 1).padStart(2, '0')}-01`;
  const finalEnd = addMonths(endCap, 1); // first-of-month AFTER the current month
  const out: { start: string; end: string }[] = [];
  let s = GENESIS;
  while (s < finalEnd) {
    const e = addMonths(s, 3);
    out.push({ start: s, end: e < finalEnd ? e : finalEnd });
    s = e;
  }
  return out;
}

async function runWindow(rawSql: string, start: string, end: string): Promise<MonthlyRow[]> {
  const sql = rawSql.replace(/\{\{start_date\}\}/g, start).replace(/\{\{end_date\}\}/g, end).replace(/;\s*$/s, '').trim();

  const createRes = await fetch(`${API}/query`, {
    method: 'POST', headers: H,
    body: JSON.stringify({ name: `tmp: psm3_base window ${start}..${end}`, query_sql: sql, is_private: true }),
  });
  if (!createRes.ok) throw new Error(`create ${createRes.status}: ${await createRes.text()}`);
  const { query_id } = await createRes.json() as { query_id: number };

  try {
    const execRes = await fetch(`${API}/query/${query_id}/execute`, {
      method: 'POST', headers: H, body: JSON.stringify({ performance: PERFORMANCE }),
    });
    if (!execRes.ok) throw new Error(`execute ${execRes.status}: ${await execRes.text()}`);
    const { execution_id } = await execRes.json() as { execution_id: string };

    for (let i = 0; i < MAX_POLLS; i++) {
      await new Promise(r => setTimeout(r, POLL_MS));
      const r = await fetch(`${API}/execution/${execution_id}/status`, { headers: H });
      if (!r.ok) { // transient 429/5xx: keep polling rather than crashing a multi-hour run
        process.stdout.write(`\r    status HTTP ${r.status}, retrying (${Math.round((i + 1) * POLL_MS / 1000)}s)      `);
        if (i === MAX_POLLS - 1) throw new Error(`status endpoint kept returning ${r.status}`);
        continue;
      }
      const j = await r.json() as { state: string; error?: unknown };
      if (j.state === 'QUERY_STATE_COMPLETED') break;
      if (['QUERY_STATE_FAILED', 'QUERY_STATE_CANCELLED', 'QUERY_STATE_EXPIRED'].includes(j.state)) {
        throw new Error(`execution ${j.state}: ${JSON.stringify(j.error ?? j)}`);
      }
      process.stdout.write(`\r    ${j.state} (${Math.round((i + 1) * POLL_MS / 1000)}s)      `);
      if (i === MAX_POLLS - 1) throw new Error('window did not finish within poll ceiling');
    }
    process.stdout.write('\r');

    // Page through the completed result.
    const rows: MonthlyRow[] = [];
    let offset = 0;
    for (;;) {
      const r = await fetch(`${API}/execution/${execution_id}/results?limit=1000&offset=${offset}`, { headers: H });
      if (!r.ok) throw new Error(`results ${r.status}: ${await r.text()}`);
      const j = await r.json() as { result?: { rows: MonthlyRow[] }; next_offset?: number | null };
      const batch = j.result?.rows ?? [];
      rows.push(...batch);
      if (batch.length < 1000 || j.next_offset == null) break;
      offset = j.next_offset;
    }
    return rows;
  } finally {
    // Dune has NO delete endpoint; archive (POST) is the cleanup, and frees the
    // private-query quota. A swallowed DELETE here used to silently leak queries.
    await fetch(`${API}/query/${query_id}/archive`, { method: 'POST', headers: H }).catch(() => {});
  }
}

function csvCell(v: unknown): string {
  const s = v === null || v === undefined ? '' : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function toCsv(rows: MonthlyRow[]): string {
  const header = ['month', 'blockchain', 'token', 'ref_code', 'dr_usd', 'avg_twa_balance'];
  const lines = [header.join(',')];
  for (const r of rows) lines.push([r.month, r.blockchain, r.token, r.ref_code, r.dr_usd, r.avg_twa_balance].map(csvCell).join(','));
  return lines.join('\n') + '\n';
}

async function main() {
  const rawSql = fs.readFileSync(SQL_FILE, 'utf8');
  const single = process.argv.find(a => a.startsWith('--window='))?.split('=')[1];

  if (single) {
    const [start, end] = single.split(':');
    if (!start || !end) { console.error('--window=START:END (YYYY-MM-DD:YYYY-MM-DD)'); process.exit(1); }
    // Windows MUST start/end on a month boundary, else a boundary month's grain
    // is split and would be wrong if later unioned with another window.
    if (!/^\d{4}-\d{2}-01$/.test(start) || !/^\d{4}-\d{2}-01$/.test(end)) {
      console.error(`window bounds must be first-of-month (YYYY-MM-01); got ${start}..${end}`); process.exit(1);
    }
    console.log(`\nSingle window ${start}..${end} on engine=${PERFORMANCE}`);
    const t0 = Date.now();
    const rows = await runWindow(rawSql, start, end);
    console.log(`OK in ${Math.round((Date.now() - t0) / 1000)}s — ${rows.length} monthly rows`);
    for (const r of rows.slice(0, 8)) console.log('  ', JSON.stringify(r));
    return;
  }

  const windows = quarterWindows(new Date());
  console.log(`\nRunning ${windows.length} quarterly windows on engine=${PERFORMANCE}:`);
  const all: MonthlyRow[] = [];
  for (const w of windows) {
    process.stdout.write(`  ${w.start}..${w.end} ... `);
    const t0 = Date.now();
    const rows = await runWindow(rawSql, w.start, w.end);
    console.log(`${rows.length} rows (${Math.round((Date.now() - t0) / 1000)}s)`);
    all.push(...rows);
  }

  const outDir = path.resolve('dune-results');
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
  const outFile = path.join(outDir, 'dr_rewards_monthly_psm3_base.csv');
  fs.writeFileSync(outFile, toCsv(all));
  console.log(`\nWrote ${outFile} (${all.length} rows across ${windows.length} windows)`);
}

main().catch(e => { console.error('\n' + (e?.stack ?? e)); process.exit(1); });
