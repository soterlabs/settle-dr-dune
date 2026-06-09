/**
 * One-off maintenance: archive the throwaway "tmp ..." Dune queries that the
 * run/xcheck scripts created. The Dune API has NO delete endpoint, so the old
 * `DELETE /query/{id}` calls silently failed (405) and temp queries piled up
 * until the private-query cap ("Max number of private queries reached") was hit.
 * The correct removal is POST /query/{id}/archive (archived queries can't be run
 * or edited and don't count against the cap); we also POST /unprivate so they
 * definitely stop counting as private.
 *
 * SAFETY: only queries whose name matches /^tmp[ :]/ are touched. Real project
 * queries (dr_*, twa_*, conversion_*, rates_*) are never archived.
 *
 *   DUNE_API_KEY=<key> npx tsx src/scripts/cleanup-temp-queries.ts          # dry run
 *   DUNE_API_KEY=<key> npx tsx src/scripts/cleanup-temp-queries.ts --apply  # archive
 */
import 'dotenv/config';

const API = 'https://api.dune.com/api/v1';
const KEY = process.env.DUNE_API_KEY;
if (!KEY) { console.error('Set DUNE_API_KEY.'); process.exit(1); }
const H = { 'x-dune-api-key': KEY, 'Content-Type': 'application/json' };
const APPLY = process.argv.includes('--apply');

const isTemp = (name: string) => /^tmp[ :]/.test(name ?? '');

interface Q { id: number; name: string; owner: string; is_archived?: boolean; is_private?: boolean; }

async function listAll(): Promise<Q[]> {
  const out: Q[] = [];
  const limit = 100;
  let offset = 0;
  for (;;) {
    const r = await fetch(`${API}/queries?limit=${limit}&offset=${offset}`, { headers: H });
    if (!r.ok) throw new Error(`list ${r.status}: ${await r.text()}`);
    const j = await r.json() as { queries?: Q[]; next_offset?: number | null };
    const batch = j.queries ?? [];
    out.push(...batch);
    if (batch.length < limit || j.next_offset == null) {
      // Some Dune deployments omit next_offset; fall back to length-based stop.
      if (batch.length < limit) break;
      if (j.next_offset == null) { offset += limit; continue; }
    }
    offset = j.next_offset ?? offset + limit;
  }
  return out;
}

async function main() {
  const all = await listAll();
  const temp = all.filter(q => isTemp(q.name));
  const keep = all.filter(q => !isTemp(q.name));
  console.log(`Total queries: ${all.length}  |  temp (will archive): ${temp.length}  |  keep: ${keep.length}`);
  console.log(`\nKEEP (not touched):`);
  for (const q of keep) console.log(`  ${q.id}  ${q.name}`);
  console.log(`\nTEMP (target):`);
  for (const q of temp) console.log(`  ${q.id}  ${q.name}`);

  if (!APPLY) { console.log(`\n(dry run — re-run with --apply to archive the ${temp.length} temp queries)`); return; }

  let ok = 0, fail = 0;
  for (const q of temp) {
    const a = await fetch(`${API}/query/${q.id}/archive`, { method: 'POST', headers: H });
    const p = await fetch(`${API}/query/${q.id}/unprivate`, { method: 'POST', headers: H });
    if (a.ok) ok++; else { fail++; console.error(`  archive ${q.id} -> ${a.status}: ${(await a.text()).slice(0, 120)}`); }
    void p; // best-effort; archive is what matters
  }
  console.log(`\nArchived ${ok}/${temp.length} temp queries (${fail} failed).`);
}

main().catch(e => { console.error(e?.stack ?? e); process.exit(1); });
