/**
 * Recreates the ENTIRE DR pipeline under the account owning DUNE_API_KEY.
 * (2026-07 migration off the yessssssssssssssssssss account, whose API key is
 * no longer available — its queries stay readable/executable by anyone but are
 * editable only by the owner.)
 *
 * Creates, in dependency order:
 *   1. the 5 foundational TWA queries ({{end_date}} param, default 2030-01-01)
 *   2. rates_dr, the 3 conversion helpers, deployment_ratio_sp
 *   3. the 9 monthly DR source queries
 *   4. the 8 windowed Base PSM3 quarterly queries (from the template)
 * rewriting every cross-query reference (query_<old-id>) to the new IDs in the
 * uploaded SQL, then substituting old->new IDs across the local .sql files,
 * combine-dr-results.ts, update-dune-queries.ts, the psm3-base-windows
 * scripts and both READMEs, so the repo points at the new queries.
 *
 * Idempotency: creates NEW queries on every run — run ONCE per migration.
 *
 *   DUNE_API_KEY=... npx tsx src/scripts/recreate-dune-pipeline.ts
 */
import 'dotenv/config';
import * as fs from 'fs';
import * as path from 'path';

const API = 'https://api.dune.com/api/v1';
const KEY = process.env.DUNE_API_KEY;
if (!KEY) { console.error('Set DUNE_API_KEY.'); process.exit(1); }

const Q = (f: string) => path.resolve('queries', f);

interface Spec { oldId: number; file: string; endDateParam?: boolean }

// Dependency order matters: deployment_ratio_sp references twa_sp_vaults,
// every monthly references components created before it.
const COMPONENTS: Spec[] = [
  { oldId: 7640317, file: 'twa_susds_susdc_erc4626.sql', endDateParam: true },
  { oldId: 7640318, file: 'twa_susds_psm3_l2.sql',       endDateParam: true },
  { oldId: 7640319, file: 'twa_stusds.sql',              endDateParam: true },
  { oldId: 7640320, file: 'twa_usds_staking_farms.sql',  endDateParam: true },
  { oldId: 7640321, file: 'twa_sp_vaults.sql',           endDateParam: true },
  { oldId: 7640322, file: 'rates_dr.sql' },
  { oldId: 7640323, file: 'conversion_susds.sql' },
  { oldId: 7640324, file: 'conversion_stusds.sql' },
  { oldId: 7640325, file: 'conversion_sp_vaults.sql' },
  { oldId: 7683727, file: 'deployment_ratio_sp.sql' },
];

const MONTHLIES: Spec[] = [
  { oldId: 7646377, file: 'dr_rewards_monthly_susds_susdc.sql' },
  { oldId: 7646379, file: 'dr_rewards_monthly_stusds.sql' },
  { oldId: 7646380, file: 'dr_rewards_monthly_farms.sql' },
  { oldId: 7683760, file: 'dr_rewards_monthly_sp.sql' },
  // The per-chain PSM3 monthlies inline their TWA, so they carry the
  // {{end_date}} parameter themselves.
  { oldId: 7647197, file: 'dr_rewards_monthly_psm3_arbitrum.sql', endDateParam: true },
  { oldId: 7647198, file: 'dr_rewards_monthly_psm3_optimism.sql', endDateParam: true },
  { oldId: 7647199, file: 'dr_rewards_monthly_psm3_unichain.sql', endDateParam: true },
  { oldId: 7812438, file: 'dr_rewards_monthly_usds_aave.sql' },
  { oldId: 7809596, file: 'dr_rewards_monthly_usds_ref4001.sql' },
];

// Base PSM3 quarterly windows; template = dr_rewards_monthly_psm3_base.sql.
const WINDOWS: { oldId: number; start: string; end: string }[] = [
  { oldId: 7842602, start: '2024-09-01', end: '2024-12-01' },
  { oldId: 7842603, start: '2024-12-01', end: '2025-03-01' },
  { oldId: 7842604, start: '2025-03-01', end: '2025-06-01' },
  { oldId: 7842605, start: '2025-06-01', end: '2025-09-01' },
  { oldId: 7842606, start: '2025-09-01', end: '2025-12-01' },
  { oldId: 7842607, start: '2025-12-01', end: '2026-03-01' },
  { oldId: 7842608, start: '2026-03-01', end: '2026-06-01' },
  { oldId: 7842609, start: '2026-06-01', end: '2026-07-01' },
];

// Repo files whose old query IDs must be rewritten to the new ones.
const LOCAL_FILES = [
  ...fs.readdirSync(path.resolve('queries')).filter((f) => f.endsWith('.sql')).map(Q),
  ...fs.readdirSync(path.resolve('diagnostic-queries')).filter((f) => f.endsWith('.sql'))
    .map((f) => path.resolve('diagnostic-queries', f)),
  ...fs.readdirSync(path.resolve('docs')).filter((f) => f.endsWith('.md'))
    .map((f) => path.resolve('docs', f)),
  path.resolve('src/scripts/combine-dr-results.ts'),
  path.resolve('src/scripts/update-dune-queries.ts'),
  path.resolve('src/scripts/update-psm3-base-windows.ts'),
  path.resolve('src/scripts/create-psm3-base-windows.ts'),
  path.resolve('src/scripts/regenerate-dr-comparison.ts'),
  path.resolve('README.md'),
  path.resolve('queries', 'README.md'),
];

// Old -> new IDs already created by a previous (partial) run; those specs are
// skipped instead of creating duplicates. Clear this when migrating again.
const ALREADY_CREATED: [number, number][] = [
  [7640317, 7877542], [7640318, 7877543], [7640319, 7877544],
  [7640320, 7877545], [7640321, 7877546], [7640322, 7877547],
  [7640323, 7877548], [7640324, 7877549], [7640325, 7877550],
  [7683727, 7877551], [7646377, 7877552], [7646379, 7877553],
  [7646380, 7877554], [7683760, 7877555],
];

const idMap = new Map<number, number>(ALREADY_CREATED);

/** Rewrite query_<oldId> references to the new IDs (for upload). */
function sub(sql: string): string {
  return sql.replace(/query_(\d{7})/g, (m, id) => {
    const n = idMap.get(Number(id));
    return n ? `query_${n}` : m;
  });
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function createQuery(name: string, sql: string, endDateParam = false): Promise<number> {
  const body: Record<string, unknown> = {
    name,
    query_sql: sql.replace(/;\s*$/, ''),
    is_private: false,
  };
  if (endDateParam) {
    body.parameters = [{ key: 'end_date', value: '2030-01-01 00:00:00', type: 'datetime' }];
  }
  for (let attempt = 1; ; attempt++) {
    const res = await fetch(`${API}/query`, {
      method: 'POST',
      headers: { 'x-dune-api-key': KEY!, 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res.status === 429 && attempt < 5) { await sleep(2000 * attempt); continue; }
    if (!res.ok) throw new Error(`create "${name}" failed (${res.status}): ${await res.text()}`);
    const { query_id } = await res.json() as { query_id: number };
    return query_id;
  }
}

async function main() {
  console.log('1) Components (TWA + helpers)...');
  for (const c of COMPONENTS) {
    if (idMap.has(c.oldId)) { console.log(`  ${c.file.padEnd(34)} already created -> ${idMap.get(c.oldId)}`); continue; }
    const sql = sub(fs.readFileSync(Q(c.file), 'utf8'));
    const id = await createQuery(`DR pipeline | ${c.file.replace('.sql', '')}`, sql, c.endDateParam);
    idMap.set(c.oldId, id);
    console.log(`  ${c.file.padEnd(34)} ${c.oldId} -> ${id}`);
    await sleep(400);
  }

  console.log('\n2) Monthly DR source queries...');
  for (const m of MONTHLIES) {
    if (idMap.has(m.oldId)) { console.log(`  ${m.file.padEnd(34)} already created -> ${idMap.get(m.oldId)}`); continue; }
    const sql = sub(fs.readFileSync(Q(m.file), 'utf8'));
    const id = await createQuery(`DR pipeline | ${m.file.replace('.sql', '')}`, sql, m.endDateParam);
    idMap.set(m.oldId, id);
    console.log(`  ${m.file.padEnd(34)} ${m.oldId} -> ${id}`);
    await sleep(400);
  }

  console.log('\n3) Base PSM3 quarterly windows...');
  const tpl = sub(fs.readFileSync(Q('dr_rewards_monthly_psm3_base.sql'), 'utf8'));
  for (const w of WINDOWS) {
    if (idMap.has(w.oldId)) { console.log(`  [${w.start}..${w.end})  already created -> ${idMap.get(w.oldId)}`); continue; }
    const sql = tpl
      .replace(/\{\{start_date\}\}/g, w.start)
      .replace(/\{\{end_date\}\}/g, w.end);
    const id = await createQuery(`DR monthly PSM3 sUSDS Base [${w.start}..${w.end})`, sql);
    idMap.set(w.oldId, id);
    console.log(`  [${w.start}..${w.end})  ${w.oldId} -> ${id}`);
    await sleep(400);
  }

  console.log('\n4) Rewriting old IDs in local files...');
  for (const f of LOCAL_FILES) {
    const before = fs.readFileSync(f, 'utf8');
    let hits = 0;
    // Two forms: `query_1234567` references in SQL (underscore prevents a \b
    // match before the digits) and bare IDs in scripts/README tables/URLs.
    const after = before
      .replace(/query_(\d{7})/g, (m, id) => {
        const n = idMap.get(Number(id));
        if (!n) return m;
        hits++;
        return `query_${n}`;
      })
      .replace(/\b(7\d{6})\b/g, (m, id) => {
        const n = idMap.get(Number(id));
        if (!n) return m;
        hits++;
        return String(n);
      });
    if (hits > 0) {
      fs.writeFileSync(f, after);
      console.log(`  ${path.relative(process.cwd(), f)}: ${hits} ID(s) rewritten`);
    }
  }

  console.log('\n=== OLD -> NEW ID MAP ===');
  for (const [oldId, newId] of idMap) {
    console.log(`${oldId} -> ${newId}   https://dune.com/queries/${newId}`);
  }
  console.log('\nNext: execute the monthly queries + windows, then npm run combine / compare.');
}

main().catch((e) => { console.error((e as Error).message); process.exit(1); });
