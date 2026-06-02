/**
 * Saves the DR query pipeline to Dune as public queries and wires the
 * placeholder IDs in dr_rewards_daily.sql / dr_rewards_rollup.sql to the real IDs.
 *
 * Order:
 *   1. Create the 9 component queries (5 foundational + 4 helpers).
 *   2. Substitute their IDs into dr_rewards_daily.sql (on disk), create it.
 *   3. Substitute the daily ID into dr_rewards_rollup.sql (on disk), create it.
 *
 * Idempotency: this creates NEW queries each run. Run once. The resulting IDs
 * are written back into the .sql files and printed at the end.
 *
 *   $env:DUNE_SAVE_KEY="..."; npx tsx src/scripts/save-dune-queries.ts
 */

import 'dotenv/config';
import * as fs from 'fs';
import * as path from 'path';

const API = 'https://api.dune.com/api/v1';
const KEY = process.env.DUNE_SAVE_KEY || process.env.DUNE_API_KEY;
if (!KEY) { console.error('Set DUNE_SAVE_KEY (or DUNE_API_KEY).'); process.exit(1); }
const H = { 'x-dune-api-key': KEY, 'Content-Type': 'application/json' };

const Q = (f: string) => path.resolve('queries', f);

// placeholder token -> component file
const COMPONENTS: { placeholder: string; file: string }[] = [
  { placeholder: 'query_AAAAAAA', file: 'twa_susds_susdc_erc4626.sql' },
  { placeholder: 'query_BBBBBBB', file: 'twa_susds_psm3_l2.sql' },
  { placeholder: 'query_CCCCCCC', file: 'twa_stusds.sql' },
  { placeholder: 'query_DDDDDDD', file: 'twa_usds_staking_farms.sql' },
  { placeholder: 'query_EEEEEEE', file: 'twa_sp_vaults.sql' },
  { placeholder: 'query_RRRRRRR', file: 'rates_dr.sql' },
  { placeholder: 'query_SSSSSSS', file: 'conversion_susds.sql' },
  { placeholder: 'query_TTTTTTT', file: 'conversion_stusds.sql' },
  { placeholder: 'query_PPPPPPP', file: 'conversion_sp_vaults.sql' },
];

async function createQuery(name: string, sql: string): Promise<number> {
  const res = await fetch(`${API}/query`, {
    method: 'POST', headers: H,
    body: JSON.stringify({ name, query_sql: sql, is_private: false }),
  });
  if (!res.ok) throw new Error(`create "${name}" failed: ${res.status} ${await res.text()}`);
  const { query_id } = await res.json() as { query_id: number };
  console.log(`  created ${name} -> ${query_id}`);
  return query_id;
}

async function main() {
  const idByFile: Record<string, number> = {};

  console.log('1) Creating component queries...');
  for (const c of COMPONENTS) {
    const sql = fs.readFileSync(Q(c.file), 'utf8');
    idByFile[c.file] = await createQuery(`DR pipeline | ${c.file.replace('.sql', '')}`, sql);
  }

  console.log('\n2) Wiring + creating dr_rewards_daily.sql...');
  let daily = fs.readFileSync(Q('dr_rewards_daily.sql'), 'utf8');
  for (const c of COMPONENTS) {
    daily = daily.split(c.placeholder).join(`query_${idByFile[c.file]}`);
  }
  fs.writeFileSync(Q('dr_rewards_daily.sql'), daily);
  const dailyId = await createQuery('DR pipeline | dr_rewards_daily', daily);

  console.log('\n3) Wiring + creating dr_rewards_rollup.sql...');
  let rollup = fs.readFileSync(Q('dr_rewards_rollup.sql'), 'utf8');
  rollup = rollup.split('query_FFFFFFF').join(`query_${dailyId}`);
  fs.writeFileSync(Q('dr_rewards_rollup.sql'), rollup);
  const rollupId = await createQuery('DR pipeline | dr_rewards_rollup', rollup);

  console.log('\n=== SAVED QUERY IDS ===');
  for (const c of COMPONENTS) {
    console.log(`${c.file.padEnd(34)} ${idByFile[c.file]}   https://dune.com/queries/${idByFile[c.file]}`);
  }
  console.log(`${'dr_rewards_daily.sql'.padEnd(34)} ${dailyId}   https://dune.com/queries/${dailyId}`);
  console.log(`${'dr_rewards_rollup.sql'.padEnd(34)} ${rollupId}   https://dune.com/queries/${rollupId}`);
  console.log('\nPlaceholders in the .sql files have been replaced with these IDs.');
}

main().catch(e => { console.error('\n' + e.message); process.exit(1); });
