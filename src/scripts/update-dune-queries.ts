/**
 * Pushes local foundational/helper .sql files to their already-saved Dune query
 * IDs (PATCH /api/v1/query/{id}). Use after editing a referenced query locally,
 * so the monthly queries (which inline the SAVED version) pick up the change.
 *
 *   $env:DUNE_API_KEY="..."; npx tsx src/scripts/update-dune-queries.ts
 *
 * Pass a subset of file keys to push only those, e.g.:
 *   npx tsx src/scripts/update-dune-queries.ts psm3 susds_susdc
 */
import 'dotenv/config';
import * as fs from 'fs';
import * as path from 'path';

const API = 'https://api.dune.com/api/v1';
const KEY = process.env.DUNE_API_KEY;
if (!KEY) { console.error('Set DUNE_API_KEY.'); process.exit(1); }

const Q = path.resolve('queries');
const MAP: Record<string, { id: number; file: string }> = {
  // Foundational TWA queries.
  susds_susdc: { id: 7640317, file: 'twa_susds_susdc_erc4626.sql' },
  psm3:        { id: 7640318, file: 'twa_susds_psm3_l2.sql' },
  stusds:      { id: 7640319, file: 'twa_stusds.sql' },
  farms:       { id: 7640320, file: 'twa_usds_staking_farms.sql' },
  sp:          { id: 7640321, file: 'twa_sp_vaults.sql' },
  // Monthly DR source queries.
  usds_aave:      { id: 7812438, file: 'dr_rewards_monthly_usds_aave.sql' },
  usds_ref4001:   { id: 7809596, file: 'dr_rewards_monthly_usds_ref4001.sql' },
  // Per-chain PSM3 monthly queries (split from retired 7646378).
  psm3_base:      { id: 7647196, file: 'dr_rewards_monthly_psm3_base.sql' },
  psm3_arbitrum:  { id: 7647197, file: 'dr_rewards_monthly_psm3_arbitrum.sql' },
  psm3_optimism:  { id: 7647198, file: 'dr_rewards_monthly_psm3_optimism.sql' },
  psm3_unichain:  { id: 7647199, file: 'dr_rewards_monthly_psm3_unichain.sql' },
};

async function main() {
  const keys = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(MAP);
  for (const k of keys) {
    const entry = MAP[k];
    if (!entry) { console.error(`unknown key "${k}" (valid: ${Object.keys(MAP).join(', ')})`); continue; }
    const sql = fs.readFileSync(path.join(Q, entry.file), 'utf8').replace(/;\s*$/, '');
    process.stdout.write(`Pushing ${entry.file} -> query_${entry.id}... `);
    const res = await fetch(`${API}/query/${entry.id}`, {
      method: 'PATCH',
      headers: { 'x-dune-api-key': KEY!, 'content-type': 'application/json' },
      body: JSON.stringify({ query_sql: sql }),
    });
    if (!res.ok) { console.log(`FAILED (${res.status})`); console.error(await res.text()); process.exit(1); }
    console.log('ok');
  }
  console.log('\nDone.');
}

main().catch((e) => { console.error((e as Error).message); process.exit(1); });
