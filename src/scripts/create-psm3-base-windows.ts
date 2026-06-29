/**
 * Creates the 8 windowed Base PSM3 queries as NEW public Dune queries under the
 * caller's own account (the original 7684981–7684988 live under a team the
 * caller can't edit). Each window's {{start_date}}/{{end_date}} is substituted
 * from the single template, then POSTed as a new public query.
 *
 *   $env:DUNE_API_KEY="..."; npx tsx src/scripts/create-psm3-base-windows.ts
 *
 * Prints an old->new ID mapping. After running, repoint:
 *   - src/scripts/combine-dr-results.ts  (SOURCES psm3_base IDs)
 *   - src/scripts/update-psm3-base-windows.ts  (WINDOWS ids, for future edits)
 * then execute the new queries (e.g. update-psm3-base-windows.ts --execute-only).
 */
import 'dotenv/config';
import * as fs from 'fs';
import * as path from 'path';

const API = 'https://api.dune.com/api/v1';
const KEY = process.env.DUNE_API_KEY;
if (!KEY) { console.error('Set DUNE_API_KEY.'); process.exit(1); }
const H = { 'x-dune-api-key': KEY, 'content-type': 'application/json' };

// old id (openmsc-owned) + window bounds. New copies are created under the caller.
const WINDOWS: { oldId: number; start: string; end: string }[] = [
  { oldId: 7684981, start: '2024-09-01', end: '2024-12-01' },
  { oldId: 7684982, start: '2024-12-01', end: '2025-03-01' },
  { oldId: 7684983, start: '2025-03-01', end: '2025-06-01' },
  { oldId: 7684984, start: '2025-06-01', end: '2025-09-01' },
  { oldId: 7684985, start: '2025-09-01', end: '2025-12-01' },
  { oldId: 7684986, start: '2025-12-01', end: '2026-03-01' },
  { oldId: 7684987, start: '2026-03-01', end: '2026-06-01' },
  { oldId: 7684988, start: '2026-06-01', end: '2026-07-01' },
];

const TEMPLATE = path.resolve('queries', 'dr_rewards_monthly_psm3_base.sql');

function buildSql(start: string, end: string): string {
  return fs.readFileSync(TEMPLATE, 'utf8')
    .replace(/\{\{start_date\}\}/g, start)
    .replace(/\{\{end_date\}\}/g, end)
    .replace(/;\s*$/, '');
}

async function createQuery(name: string, sql: string): Promise<number> {
  const res = await fetch(`${API}/query`, {
    method: 'POST', headers: H,
    body: JSON.stringify({ name, query_sql: sql, is_private: false }),
  });
  if (!res.ok) throw new Error(`create "${name}" failed (${res.status}): ${await res.text()}`);
  const { query_id } = await res.json() as { query_id: number };
  return query_id;
}

async function main() {
  const mapping: { oldId: number; newId: number; start: string; end: string }[] = [];
  for (const { oldId, start, end } of WINDOWS) {
    const name = `DR monthly PSM3 sUSDS Base [${start}..${end})`;
    process.stdout.write(`Creating ${name} ... `);
    const newId = await createQuery(name, buildSql(start, end));
    console.log(`new query_${newId}  (was ${oldId})`);
    mapping.push({ oldId, newId, start, end });
  }

  console.log('\n=== OLD -> NEW ID MAPPING ===');
  for (const m of mapping) console.log(`${m.oldId} -> ${m.newId}   https://dune.com/queries/${m.newId}`);
  console.log('\nNext: repoint combine-dr-results.ts + update-psm3-base-windows.ts, then execute the new queries.');
}

main().catch((e) => { console.error('\n' + (e as Error).message); process.exit(1); });
