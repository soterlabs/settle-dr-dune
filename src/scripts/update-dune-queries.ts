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
  susds_susdc: { id: 7877542, file: 'twa_susds_susdc_erc4626.sql' },
  psm3:        { id: 7877543, file: 'twa_susds_psm3_l2.sql' },
  stusds:      { id: 7877544, file: 'twa_stusds.sql' },
  farms:       { id: 7877545, file: 'twa_usds_staking_farms.sql' },
  sp:          { id: 7877546, file: 'twa_sp_vaults.sql' },
  // Helper queries (reward rates + conversion rates + sp* deployment ratio).
  rates:          { id: 7877547, file: 'rates_dr.sql' },
  conv_susds:     { id: 7877548, file: 'conversion_susds.sql' },
  conv_stusds:    { id: 7877549, file: 'conversion_stusds.sql' },
  conv_sp_vaults: { id: 7877550, file: 'conversion_sp_vaults.sql' },
  deploy_ratio:   { id: 7877551, file: 'deployment_ratio_sp.sql' },
  // Monthly DR source queries.
  m_susds_susdc:  { id: 7877552, file: 'dr_rewards_monthly_susds_susdc.sql' },
  m_stusds:       { id: 7877553, file: 'dr_rewards_monthly_stusds.sql' },
  m_farms:        { id: 7877554, file: 'dr_rewards_monthly_farms.sql' },
  m_sp:           { id: 7877555, file: 'dr_rewards_monthly_sp.sql' },
  usds_aave:      { id: 7877569, file: 'dr_rewards_monthly_usds_aave.sql' },
  usds_ref4001:   { id: 7877570, file: 'dr_rewards_monthly_usds_ref4001.sql' },
  // Per-chain PSM3 monthly queries (split from retired 7646378). The Base
  // windowed set is NOT here — push it with update-psm3-base-windows.ts.
  psm3_arbitrum:  { id: 7877565, file: 'dr_rewards_monthly_psm3_arbitrum.sql' },
  psm3_optimism:  { id: 7877566, file: 'dr_rewards_monthly_psm3_optimism.sql' },
  psm3_unichain:  { id: 7877568, file: 'dr_rewards_monthly_psm3_unichain.sql' },
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
