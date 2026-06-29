/**
 * Pushes the windowed Base PSM3 template (queries/dr_rewards_monthly_psm3_base.sql)
 * to each of the 8 deployed quarterly window queries, substituting that window's
 * {{start_date}} / {{end_date}} with literal dates, then (optionally) executes it.
 *
 * The template is parameterized with {{start_date}}/{{end_date}}; each deployed
 * window bakes those in as literals. This script keeps all 8 in sync with the
 * single source-of-truth template (e.g. after editing the balances CTE).
 *
 *   $env:DUNE_API_KEY="..."; npx tsx src/scripts/update-psm3-base-windows.ts
 *
 * Flags:
 *   --no-execute   only PATCH the SQL, do not trigger a run
 *   --execute-only only run the queries, do not PATCH (re-run as-is)
 *   --performance <free|medium|large>   engine tier (default: large)
 */
import 'dotenv/config';
import * as fs from 'fs';
import * as path from 'path';

const API = 'https://api.dune.com/api/v1';
const KEY = process.env.DUNE_API_KEY;
if (!KEY) { console.error('Set DUNE_API_KEY.'); process.exit(1); }

// Window boundaries are baked into each deployed query's name + SQL. Keep this
// table in sync with the window→ID list in combine-dr-results.ts (SOURCES).
const WINDOWS: { id: number; start: string; end: string }[] = [
  { id: 7842602, start: '2024-09-01', end: '2024-12-01' },
  { id: 7842603, start: '2024-12-01', end: '2025-03-01' },
  { id: 7842604, start: '2025-03-01', end: '2025-06-01' },
  { id: 7842605, start: '2025-06-01', end: '2025-09-01' },
  { id: 7842606, start: '2025-09-01', end: '2025-12-01' },
  { id: 7842607, start: '2025-12-01', end: '2026-03-01' },
  { id: 7842608, start: '2026-03-01', end: '2026-06-01' },
  { id: 7842609, start: '2026-06-01', end: '2026-07-01' },
];

const TEMPLATE = path.resolve('queries', 'dr_rewards_monthly_psm3_base.sql');

const argv = process.argv.slice(2);
const noExecute   = argv.includes('--no-execute');
const executeOnly = argv.includes('--execute-only');
const perfIdx = argv.indexOf('--performance');
const performance = perfIdx >= 0 ? argv[perfIdx + 1] : 'large';

function buildSql(start: string, end: string): string {
  const tpl = fs.readFileSync(TEMPLATE, 'utf8');
  return tpl
    .replace(/\{\{start_date\}\}/g, start)
    .replace(/\{\{end_date\}\}/g, end)
    .replace(/;\s*$/, '');
}

async function patch(id: number, sql: string): Promise<void> {
  const res = await fetch(`${API}/query/${id}`, {
    method: 'PATCH',
    headers: { 'x-dune-api-key': KEY!, 'content-type': 'application/json' },
    body: JSON.stringify({ query_sql: sql }),
  });
  if (!res.ok) throw new Error(`PATCH query ${id} failed (${res.status}): ${await res.text()}`);
}

async function execute(id: number): Promise<string> {
  const res = await fetch(`${API}/query/${id}/execute`, {
    method: 'POST',
    headers: { 'x-dune-api-key': KEY!, 'content-type': 'application/json' },
    body: JSON.stringify({ performance }),
  });
  if (!res.ok) throw new Error(`EXECUTE query ${id} failed (${res.status}): ${await res.text()}`);
  const j = await res.json() as { execution_id: string; state: string };
  return j.execution_id;
}

async function main() {
  console.log(`Engine: ${performance}${noExecute ? '  (PATCH only)' : ''}${executeOnly ? '  (EXECUTE only)' : ''}\n`);
  for (const { id, start, end } of WINDOWS) {
    process.stdout.write(`query_${id}  [${start}..${end})  `);
    if (!executeOnly) {
      await patch(id, buildSql(start, end));
      process.stdout.write('patched ');
    }
    if (!noExecute) {
      const exec = await execute(id);
      process.stdout.write(`executing (${exec})`);
    }
    console.log();
  }
  console.log('\nDone. Poll progress at https://dune.com/queries/<id> or via getExecutionResults.');
}

main().catch((e) => { console.error('\n' + (e as Error).message); process.exit(1); });
