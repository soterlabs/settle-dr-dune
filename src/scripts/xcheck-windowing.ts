/**
 * CORRECTNESS CROSS-CHECK for the windowing rewrite.
 *
 * The windowed Base query can't be validated directly (the un-windowed version
 * never completes). So we validate the *transform* on a chain that DOES run
 * un-windowed — Unichain — and trust Base by analogy (the chains differ only in
 * table prefixes / addresses / the blockchain literal; the TWA logic is identical).
 *
 *   ground truth = un-windowed Unichain query, full range, one execution
 *   candidate    = windowed Unichain (same transform as Base), run per quarter,
 *                  unioned client-side
 *
 * Both run on engine=large with the SAME execution date, so current_date-based
 * idle-fill is identical. We then diff per (month, ref_code) on dr_usd and
 * avg_twa_balance. A match proves the opening-balance seeding + idle-fill cap
 * reproduce the un-windowed result exactly.
 *
 *   DUNE_API_KEY=<large-capable key> npx tsx src/scripts/xcheck-windowing.ts
 */
import 'dotenv/config';
import * as fs from 'fs';
import * as path from 'path';

const API = 'https://api.dune.com/api/v1';
const KEY = process.env.DUNE_API_KEY;
if (!KEY) { console.error('Set DUNE_API_KEY (plan must allow performance=large).'); process.exit(1); }
const H = { 'x-dune-api-key': KEY, 'Content-Type': 'application/json' };

const GENESIS = '2024-09-01';
// Exclusive end = first-of-month AFTER the current month, computed at runtime so
// the validated range always extends through today (a hardcoded date would
// silently stop validating recent months on a later re-run). addMonths is a
// hoisted function declaration, so calling it here is fine.
const NOW = new Date();
const END = addMonths(`${NOW.getUTCFullYear()}-${String(NOW.getUTCMonth() + 1).padStart(2, '0')}-01`, 1);
const PERFORMANCE = 'large';
const POLL_MS = 15000;
const MAX_POLLS = 130;

// Swap the Base-specific tokens in the windowed Base SQL for Unichain's.
function baseToUnichain(sql: string): string {
  return sql
    .replace(/spark_protocol_base/g, 'spark_protocol_unichain')
    .replace(/erc20_base/g, 'erc20_unichain')
    .replace(/'base' as blockchain/g, "'unichain' as blockchain")
    .replace(/0x1601843c5E9bC251A3272907010AFa41Fa18347E/g, '0x7b42Ed932f26509465F7cE3FAF76FfCe1275312f')
    .replace(/0x5875eEE11Cf8398102FdAd704C9E96607675467a/g, '0xA06b10Db9F390990364A3984C04FaDf1c13691b5');
}

interface Row { month: string; ref_code: number; dr_usd: number; avg_twa_balance: number; }

function addMonths(ymd: string, n: number): string {
  const [y, m] = ymd.split('-').map(Number);
  const idx = y * 12 + (m - 1) + n;
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, '0')}-01`;
}
function quarters(start: string, end: string): { start: string; end: string }[] {
  const out: { start: string; end: string }[] = [];
  let s = start;
  while (s < end) { const e = addMonths(s, 3); out.push({ start: s, end: e < end ? e : end }); s = e; }
  return out;
}

async function run(label: string, sql: string): Promise<Row[]> {
  const body = sql.replace(/;\s*$/s, '').trim();
  const createRes = await fetch(`${API}/query`, {
    method: 'POST', headers: H,
    body: JSON.stringify({ name: `tmp xcheck: ${label}`, query_sql: body, is_private: true }),
  });
  if (!createRes.ok) throw new Error(`[${label}] create ${createRes.status}: ${await createRes.text()}`);
  const { query_id } = await createRes.json() as { query_id: number };
  try {
    const execRes = await fetch(`${API}/query/${query_id}/execute`, {
      method: 'POST', headers: H, body: JSON.stringify({ performance: PERFORMANCE }),
    });
    if (!execRes.ok) throw new Error(`[${label}] execute ${execRes.status}: ${await execRes.text()}`);
    const { execution_id } = await execRes.json() as { execution_id: string };
    for (let i = 0; i < MAX_POLLS; i++) {
      await new Promise(r => setTimeout(r, POLL_MS));
      const r = await fetch(`${API}/execution/${execution_id}/status`, { headers: H });
      if (!r.ok) { // transient 429/5xx: keep polling instead of crashing
        process.stdout.write(`\r    [${label}] status HTTP ${r.status}, retrying      `);
        if (i === MAX_POLLS - 1) throw new Error(`[${label}] status endpoint kept returning ${r.status}`);
        continue;
      }
      const j = await r.json() as { state: string; error?: unknown };
      if (j.state === 'QUERY_STATE_COMPLETED') break;
      if (['QUERY_STATE_FAILED', 'QUERY_STATE_CANCELLED', 'QUERY_STATE_EXPIRED'].includes(j.state))
        throw new Error(`[${label}] ${j.state}: ${JSON.stringify(j.error ?? j)}`);
      process.stdout.write(`\r    [${label}] ${j.state} (${Math.round((i + 1) * POLL_MS / 1000)}s)      `);
      if (i === MAX_POLLS - 1) throw new Error(`[${label}] poll ceiling hit`);
    }
    process.stdout.write('\r');
    const rows: Row[] = [];
    let offset = 0;
    for (;;) {
      const r = await fetch(`${API}/execution/${execution_id}/results?limit=1000&offset=${offset}`, { headers: H });
      if (!r.ok) throw new Error(`[${label}] results ${r.status}: ${await r.text()}`);
      const j = await r.json() as { result?: { rows: Row[] }; next_offset?: number | null };
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

const key = (r: Row) => `${String(r.month).slice(0, 10)}|${r.ref_code}`;
const index = (rows: Row[]) => { const m = new Map<string, Row>(); for (const r of rows) m.set(key(r), r); return m; };
const reldiff = (a: number, b: number) => { const d = Math.abs(a - b); const s = Math.max(Math.abs(a), Math.abs(b)); return s < 1e-9 ? 0 : d / s; };

async function main() {
  const windowedBase = fs.readFileSync(path.resolve('queries/dr_rewards_monthly_psm3_base.sql'), 'utf8');
  const unwindowedUni = fs.readFileSync(path.resolve('queries/dr_rewards_monthly_psm3_unichain.sql'), 'utf8');
  const windowedUni = baseToUnichain(windowedBase);

  console.log(`\nGround truth: un-windowed Unichain, full range -> ${END}, engine=${PERFORMANCE}`);
  const t0 = Date.now();
  const truth = await run('truth', unwindowedUni.replace(/\{\{end_date\}\}/g, END));
  console.log(`  ${truth.length} rows (${Math.round((Date.now() - t0) / 1000)}s)`);

  const wins = quarters(GENESIS, END);
  console.log(`\nCandidate: windowed Unichain, ${wins.length} quarters:`);
  const cand: Row[] = [];
  for (const w of wins) {
    process.stdout.write(`  ${w.start}..${w.end} ... `);
    const t = Date.now();
    const rows = await run(`win ${w.start}`, windowedUni.replace(/\{\{start_date\}\}/g, w.start).replace(/\{\{end_date\}\}/g, w.end));
    console.log(`${rows.length} rows (${Math.round((Date.now() - t) / 1000)}s)`);
    cand.push(...rows);
  }

  // Compare.
  const T = index(truth), C = index(cand);
  const allKeys = [...new Set([...T.keys(), ...C.keys()])].sort();
  const TOL = 1e-6;
  let maxUsdRel = 0, maxBalRel = 0, mismatches = 0, onlyTruth = 0, onlyCand = 0;
  const worst: string[] = [];
  for (const k of allKeys) {
    const a = T.get(k), b = C.get(k);
    if (a && !b) { onlyTruth++; worst.push(`  only in TRUTH:  ${k}  dr_usd=${a.dr_usd}`); continue; }
    if (b && !a) { onlyCand++;  worst.push(`  only in CAND:   ${k}  dr_usd=${b.dr_usd}`); continue; }
    if (!a || !b) continue;
    const ru = reldiff(a.dr_usd, b.dr_usd), rb = reldiff(a.avg_twa_balance, b.avg_twa_balance);
    maxUsdRel = Math.max(maxUsdRel, ru); maxBalRel = Math.max(maxBalRel, rb);
    if (ru > TOL || rb > TOL) { mismatches++; if (worst.length < 20) worst.push(`  DIFF ${k}  dr_usd ${a.dr_usd} vs ${b.dr_usd} (rel ${ru.toExponential(2)})  bal rel ${rb.toExponential(2)}`); }
  }
  const sum = (rows: Row[]) => rows.reduce((s, r) => s + (Number(r.dr_usd) || 0), 0);

  console.log(`\n================ CROSS-CHECK RESULT ================`);
  console.log(`keys: truth=${T.size} cand=${C.size} union=${allKeys.length}`);
  console.log(`total dr_usd: truth=${sum(truth).toFixed(4)}  cand=${sum(cand).toFixed(4)}  rel=${reldiff(sum(truth), sum(cand)).toExponential(3)}`);
  console.log(`max relative diff: dr_usd=${maxUsdRel.toExponential(3)}  avg_twa_balance=${maxBalRel.toExponential(3)}  (tol ${TOL})`);
  console.log(`rows only-in-truth=${onlyTruth}  only-in-cand=${onlyCand}  over-tol=${mismatches}`);
  if (worst.length) { console.log(`\nNotable rows:`); worst.forEach(w => console.log(w)); }
  const pass = mismatches === 0 && onlyTruth === 0 && onlyCand === 0;
  console.log(`\n${pass ? 'PASS — windowed union matches un-windowed within tolerance.' : 'FAIL — see diffs above.'}`);
  process.exit(pass ? 0 : 1);
}

main().catch(e => { console.error('\n' + (e?.stack ?? e)); process.exit(2); });
