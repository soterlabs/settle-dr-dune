/**
 * Runs a foundational TWA .sql file directly against Dune (create -> execute ->
 * poll) to confirm it compiles and executes. Rotates through several API keys
 * until one works. By default it narrows every `date 'YYYY-MM-DD'` literal in the
 * token_targets block to a recent window so the test is cheap; pass --full to run
 * the real date range.
 *
 *   npx tsx src/scripts/run-dune-query.ts queries/twa_sp_vaults.sql
 *   npx tsx src/scripts/run-dune-query.ts queries/twa_susds_susdc_erc4626.sql --since=2026-05-25
 *   npx tsx src/scripts/run-dune-query.ts queries/twa_stusds.sql --full
 */

import 'dotenv/config';
import * as fs from 'fs';
import * as path from 'path';

const API = 'https://api.dune.com/api/v1';

// Keys to try, in order. Env override wins; otherwise the keys provided for testing.
const KEYS = (process.env.DUNE_API_KEYS?.split(',').map(s => s.trim()).filter(Boolean)) ?? [
  'Sl1u0gS7KbSu31c0qYZPoVLzCxXwy7zC',
  'IgTyRTwGk9VmW0LIiP1jE02gZUB5D5Ck',
  'z9bztqa6gK0noRbz0fpnaTATOLBuvdxz',
];

function parseArgs() {
  const args = process.argv.slice(2);
  const file = args.find(a => !a.startsWith('--'));
  if (!file) { console.error('usage: run-dune-query.ts <file.sql> [--since=YYYY-MM-DD] [--full] [--limit=N]'); process.exit(1); }
  const full = args.includes('--full');
  const since = args.find(a => a.startsWith('--since='))?.split('=')[1] ?? '2026-05-25';
  const limit = Number(args.find(a => a.startsWith('--limit='))?.split('=')[1] ?? 200);
  return { file, full, since, limit };
}

function prepareSql(raw: string, full: boolean, since: string, limit: number): string {
  let sql = raw.replace(/;\s*$/s, '').trim();
  if (!full) {
    // Narrow scan window: rewrite every `date 'YYYY-MM-DD'` literal to `since`.
    sql = sql.replace(/date\s+'\d{4}-\d{2}-\d{2}'/gi, `date '${since}'`);
  }
  // Cap result payload (valid after a trailing ORDER BY).
  if (!/limit\s+\d+\s*$/i.test(sql)) sql += `\nlimit ${limit}`;
  return sql;
}

type RunResult =
  | { ok: true; rows: any[]; rowCount: number; columns: string[] }
  | { ok: false; reason: 'auth' | 'sql' | 'timeout'; detail: string };

async function runWithKey(key: string, sql: string): Promise<RunResult> {
  const H = { 'x-dune-api-key': key, 'Content-Type': 'application/json' };

  const createRes = await fetch(`${API}/query`, {
    method: 'POST', headers: H,
    body: JSON.stringify({ name: 'tmp: twa query test', query_sql: sql, is_private: false }),
  });
  if (createRes.status === 401 || createRes.status === 403 || createRes.status === 402 || createRes.status === 429) {
    return { ok: false, reason: 'auth', detail: `create ${createRes.status}: ${await createRes.text()}` };
  }
  if (!createRes.ok) return { ok: false, reason: 'sql', detail: `create ${createRes.status}: ${await createRes.text()}` };
  const { query_id } = await createRes.json() as { query_id: number };

  try {
    const execRes = await fetch(`${API}/query/${query_id}/execute`, {
      method: 'POST', headers: H, body: JSON.stringify({ performance: 'medium' }),
    });
    if ([401, 402, 403, 429].includes(execRes.status)) {
      return { ok: false, reason: 'auth', detail: `execute ${execRes.status}: ${await execRes.text()}` };
    }
    if (!execRes.ok) return { ok: false, reason: 'sql', detail: `execute ${execRes.status}: ${await execRes.text()}` };
    const { execution_id } = await execRes.json() as { execution_id: string };

    for (let i = 0; i < 100; i++) {
      await new Promise(r => setTimeout(r, 3000));
      const r = await fetch(`${API}/execution/${execution_id}/results?limit=${50}`, { headers: H });
      const j = await r.json() as { state: string; error?: unknown; result?: { rows: unknown[]; metadata?: { total_row_count?: number; column_names?: string[] } } };
      if (j.state === 'QUERY_STATE_COMPLETED') {
        const rows = j.result?.rows ?? [];
        return { ok: true, rows, rowCount: j.result?.metadata?.total_row_count ?? rows.length, columns: j.result?.metadata?.column_names ?? [] };
      }
      if (j.state === 'QUERY_STATE_FAILED') {
        return { ok: false, reason: 'sql', detail: j.error ? JSON.stringify(j.error) : JSON.stringify(j) };
      }
      process.stdout.write(`\r  ${j.state} (${i * 3}s)   `);
    }
    return { ok: false, reason: 'timeout', detail: 'no result after ~300s' };
  } finally {
    await fetch(`${API}/query/${query_id}`, { method: 'DELETE', headers: H }).catch(() => {});
  }
}

async function main() {
  const { file, full, since, limit } = parseArgs();
  const abs = path.isAbsolute(file) ? file : path.resolve(process.cwd(), file);
  const raw = fs.readFileSync(abs, 'utf8');
  const sql = prepareSql(raw, full, since, limit);

  console.log(`\n=== ${path.basename(file)} ===`);
  console.log(full ? '(full date range)' : `(test window from ${since})`);

  for (let k = 0; k < KEYS.length; k++) {
    const masked = KEYS[k].slice(0, 4) + '…' + KEYS[k].slice(-4);
    console.log(`\nkey #${k + 1} (${masked})`);
    const res = await runWithKey(KEYS[k], sql);
    if (res.ok) {
      console.log(`\nOK — ${res.rowCount} rows (showing ${Math.min(res.rows.length, 5)})`);
      console.log('columns:', res.columns.join(', '));
      for (const row of res.rows.slice(0, 5)) console.log('  ', JSON.stringify(row));
      return;
    }
    if (res.reason === 'auth') {
      console.log(`  key rejected (${res.detail.slice(0, 120)}) — trying next key`);
      continue;
    }
    // SQL or timeout error: report and stop (rotating keys won't help).
    console.error(`\nFAILED (${res.reason}):\n${res.detail}`);
    process.exit(3);
  }
  console.error('\nAll keys exhausted / rejected.');
  process.exit(2);
}

main().catch(e => { console.error(e); process.exit(1); });
