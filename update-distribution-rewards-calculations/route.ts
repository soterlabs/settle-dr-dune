import { DateTime } from 'luxon';
import type { Kysely } from 'kysely';
import { updateAccessibilityRewardsIncentivesData } from './helpers.ts';
import database from "../db/db.ts";

function getDb() {
  // deno-lint-ignore no-explicit-any
  return database.pools.fullAccess as unknown as Kysely<any>;
}

/**
 * GET /api/update-distribution-rewards
 *
 * Query params:
 *   - updateAll=true   reprocess historical data as well as the current window
 *
 */
export async function GET(request: Request): Promise<Response> {

  const url = new URL(request.url);
  const updateAll = url.searchParams.get('updateAll') === 'true';

  const startTime = DateTime.now().toISO();
  console.log(`[distribution-rewards] starting run at ${startTime} (updateAll=${updateAll})`);

  try {
    const db = getDb();
    const result = await updateAccessibilityRewardsIncentivesData(db, { updateAll });
    console.log(`[distribution-rewards] run complete`);
    return Response.json(result);
  } catch (error) {
    console.error('[distribution-rewards] error:', error);
    const details = error instanceof Error ? error.message : String(error);
    return Response.json(
        { error: 'Failed to update distribution rewards', details },
        { status: 500 },
    );
  }
}