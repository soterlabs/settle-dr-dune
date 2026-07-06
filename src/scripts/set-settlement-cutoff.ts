/**
 * Moves the pipeline's settlement cutoff to a new month, rewriting every place
 * the cutoff is hardcoded:
 *   - queries/*.sql   `least(timestamp '{{end_date}}', timestamp '<end>')`
 *                     `least(current_date, date '<last-day>')`
 *   - src/scripts/settlement.ts  SETTLE_MONTH constant (used by combine/compare)
 *
 *   npx tsx src/scripts/set-settlement-cutoff.ts 2026-06
 *
 * After running: push the SQL to Dune (update-dune-queries.ts or
 * recreate-dune-pipeline.ts) and re-execute the monthly queries.
 */
import * as fs from 'fs';
import * as path from 'path';
import { settleLastDay, settleEndExclusive } from './settlement.js';

const month = process.argv[2];
if (!/^\d{4}-\d{2}$/.test(month ?? '')) {
  console.error('usage: set-settlement-cutoff.ts <YYYY-MM>');
  process.exit(1);
}
const lastDay = settleLastDay(month);
const endExclusive = settleEndExclusive(month);

const QUERIES = path.resolve('queries');
let sqlFiles = 0, sqlHits = 0;
for (const f of fs.readdirSync(QUERIES).filter((f) => f.endsWith('.sql'))) {
  const p = path.join(QUERIES, f);
  const before = fs.readFileSync(p, 'utf8');
  let hits = 0;
  const after = before
    .replace(/least\(timestamp '\{\{end_date\}\}', timestamp '\d{4}-\d{2}-\d{2}'\)/g,
      () => (hits++, `least(timestamp '{{end_date}}', timestamp '${endExclusive}')`))
    .replace(/least\(current_date, date '\d{4}-\d{2}-\d{2}'\)/g,
      () => (hits++, `least(current_date, date '${lastDay}')`));
  if (hits > 0) {
    fs.writeFileSync(p, after);
    sqlFiles++; sqlHits += hits;
    console.log(`  ${f}: ${hits} literal(s)`);
  }
}

const SETTLEMENT = path.resolve('src', 'scripts', 'settlement.ts');
const src = fs.readFileSync(SETTLEMENT, 'utf8');
fs.writeFileSync(SETTLEMENT, src.replace(/SETTLE_MONTH = '\d{4}-\d{2}'/, `SETTLE_MONTH = '${month}'`));

console.log(`\nCutoff set to ${month} (fills capped at ${lastDay}, scans at < ${endExclusive}).`);
console.log(`${sqlHits} SQL literals across ${sqlFiles} files + settlement.ts updated.`);
console.log('Next: push SQL to Dune and re-execute the monthly queries.');
