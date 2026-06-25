import { Kysely, sql, SqlBool } from 'kysely';
import { AccessibilityRewardIncentiveRecord, BonusIncentive } from './schemas.ts';
import {
  BlockTimestampRecord,
  TokenPrice
} from '@/update-distribution-rewards-calculations/calculations/models/models.ts';
import {SupabaseDB} from "../db/schema.ts";

const knownPartnersCodes: Record<string, string> = {
  '1001': 'summerfi',
  '1002': 'defisaver',
  '1004': 'paraswap',
  '1007': 'yearn',
};

export async function getStarIdForRewardCode(
  db: Kysely<SupabaseDB>,
  accessibilityRewardCode: string
): Promise<number | null> {
  const trimmedCode = accessibilityRewardCode.trim();
  if (!/^[0-9]+$/.test(trimmedCode)) return null;

  // Use BigInt for arbitrary-precision comparison
  try {
    const result = await db
      .selectFrom('stars')
      .select('id')
      .where('rewards_codes_range_from', 'is not', null)
      .where('rewards_codes_range_to', 'is not', null)
      .where(
        sql<SqlBool>`rewards_codes_range_from::numeric <= ${trimmedCode}::numeric`
      )
      .where(
        sql<SqlBool>`rewards_codes_range_to::numeric >= ${trimmedCode}::numeric`
      )
      .limit(1)
      .executeTakeFirst();

    return result?.id ?? null;
  } catch {
    return null;
  }
}

/**
 * Get or create a network record
 */
export async function getOrCreateNetwork(
  db: Kysely<SupabaseDB>,
  networkName: string
): Promise<number> {
  let network = await db
    .selectFrom('networks')
    .select('id')
    .where('name', '=', networkName.toLowerCase())
    .executeTakeFirst();

  if (!network) {
    const [newNetwork] = await db
      .insertInto('networks')
      .values({ name: networkName.toLowerCase() })
      .returning('id')
      .execute();
    network = newNetwork;
  }

  return network.id;
}

/**
 * Get or create a partner record by accessibility reward code
 */
export async function getOrCreatePartner(
  db: Kysely<SupabaseDB>,
  accessibilityRewardCode: string,
  partnerName?: string
): Promise<number | null> {
  const normalizedCode = accessibilityRewardCode.toLowerCase().trim();
  const trimmedCode = accessibilityRewardCode.trim();

  const result = await db
    .selectFrom('partners as p')
    .select(['p.id', 'p.accessibility_reward_code', 'p.name'])
    .where(sql`LOWER(TRIM(p.accessibility_reward_code))`, '=', normalizedCode)
    .executeTakeFirst();

  if (!result && normalizedCode !== 'untagged') {
    try {
      const starId = await getStarIdForRewardCode(db, trimmedCode);

      const newPartner = await db
        .insertInto('partners')
        .values({
          name: partnerName ?? knownPartnersCodes[normalizedCode] ?? '-',
          accessibility_reward_code: trimmedCode,
          star_id: starId ?? null,
          is_active: true,
          track_ssr_incentives: true
        })
        .returning(['id', 'accessibility_reward_code', 'name'])
        .executeTakeFirst();

      if (!newPartner) {
        throw new Error(
          `Failed to create new partner for code: ${accessibilityRewardCode}`
        );
      }

      console.log(
        `Created new partner: ${newPartner.name} (${accessibilityRewardCode})`
      );
      return newPartner.id;
    } catch (error) {
      console.error(
        `Failed to create partner for code "${accessibilityRewardCode}":`,
        error
      );
      return null;
    }
  }

  return result?.id ?? null;
}

/**
 * Get partner active status
 */
export async function getPartnerActiveStatus(
  db: Kysely<SupabaseDB>,
  partnerId: number
): Promise<boolean> {
  const result = await db
    .selectFrom('partners')
    .select('is_active')
    .where('id', '=', partnerId)
    .executeTakeFirst();

  return result?.is_active ?? true;
}

/**
 * Get partner SSR incentives tracking status
 */
export async function getPartnerTrackSsrIncentivesStatus(
  db: Kysely<SupabaseDB>,
  partnerId: number
): Promise<boolean> {
  const result = await db
    .selectFrom('partners')
    .select('track_ssr_incentives')
    .where('id', '=', partnerId)
    .executeTakeFirst();

  return result?.track_ssr_incentives ?? true;
}

/**
 * Get bonus integration boost incentive for a partner
 */
export async function getBonusIntegrationBoostIncentiveForPartner(
  db: Kysely<SupabaseDB>,
  partnerId: number
): Promise<BonusIncentive | null> {
  const result = await db
    .selectFrom('bonus_incentives')
    .select(['id', 'partner_id', 'star_id', 'amount', 'active'])
    .where('partner_id', '=', partnerId)
    .where('active', '=', true)
    .executeTakeFirst();

  if (!result || !result.partner_id) {
    return null;
  }

  return {
    id: result.id,
    partner_id: result.partner_id,
    star_id: result.star_id,
    amount: Number(result.amount),
    active: result.active ?? false
  };
}

/**
 * Update accessibility reward incentives in the database
 */
export async function updateAccessibilityRewardIncentives(
  db: Kysely<SupabaseDB>,
  records: AccessibilityRewardIncentiveRecord[],
  { updateAll = false }: { updateAll?: boolean }
) {
  if (records.length === 0) {
    return { success: true, message: 'No records to update', count: 0 };
  }

  const recordsByPartnerFarmNetwork = records.reduce(
    (acc, record) => {
      const key = `${record.partner_id}-${record.farm}-${record.network_id}`;
      if (!acc[key]) {
        acc[key] = {};
      }
      if (!acc[key][record.date]) {
        acc[key][record.date] = [];
      }
      acc[key][record.date].push(record);
      return acc;
    },
    {} as Record<string, Record<string, AccessibilityRewardIncentiveRecord[]>>
  );

  const lastTwoMonthsRecords = Object.values(
    recordsByPartnerFarmNetwork
  ).flatMap((partnerFarmNetworkRecords) => {
    // Get the last 2 months for this partner-farm-network combination
    const sortedDates = Object.keys(partnerFarmNetworkRecords)
      .sort((a, b) => new Date(b).getTime() - new Date(a).getTime())
      .slice(0, updateAll ? undefined : 2);

    return sortedDates.flatMap((date) => partnerFarmNetworkRecords[date]);
  });

  if (lastTwoMonthsRecords.length === 0) {
    return {
      success: true,
      message: 'No records to update after filtering',
      count: 0
    };
  }

  await db
    .insertInto('accessibility_reward_incentives')
    .values(
      lastTwoMonthsRecords.map((record) => ({
        partner_id: record.partner_id,
        network_id: record.network_id,
        farm: record.farm,
        date: record.date,
        eligible_tvl: record.eligible_tvl,
        incentive_amount: record.incentive_amount,
        incentive_amount_to_pay: record.incentive_amount_to_pay,
        bonus_incentive_amount: record.bonus_incentive_amount,
        is_active: record.is_active ?? true,
        created_at: sql`NOW()`,
        updated_at: sql`NOW()`
      }))
    )
    .onConflict((cb) =>
      cb.columns(['partner_id', 'network_id', 'farm', 'date']).doUpdateSet({
        eligible_tvl: sql`excluded.eligible_tvl`,
        incentive_amount: sql`excluded.incentive_amount`,
        incentive_amount_to_pay: sql`excluded.incentive_amount_to_pay`,
        bonus_incentive_amount: sql`excluded.bonus_incentive_amount`,
        is_active: sql`excluded.is_active`,
        updated_at: sql`NOW()`
      })
    )
    .execute();

  return {
    success: true,
    message: `Updated ${lastTwoMonthsRecords.length} accessibility reward incentive records (last 2 months per partner-farm-network)`,
    count: lastTwoMonthsRecords.length
  };
}
export async function upsertUserMonthlyHistories(
  db: Kysely<SupabaseDB>,
  rows: Array<{
    date: string;
    farm_name: string;
    network_id: number;
    referral_code: string;
    user_id: string;
    tvl: number;
  }>
): Promise<{ success: boolean; count: number }> {
  if (rows.length === 0) return { success: true, count: 0 };

  const BATCH_SIZE = 1000;
  let totalInserted = 0;

  for (let start = 0; start < rows.length; start += BATCH_SIZE) {
    const batch = rows.slice(start, start + BATCH_SIZE);
    await db
      .insertInto('user_monthly_histories_accessibility_rewards')
      .values(batch)
      .onConflict((cb) =>
        cb.columns(['date', 'farm_name', 'network_id', 'user_id']).doUpdateSet({
          referral_code: sql`excluded.referral_code`,
          tvl: sql`excluded.tvl`
        })
      )
      .execute();
    totalInserted += batch.length;
  }

  return { success: true, count: totalInserted };
}

export async function getLatestSnapshotDate(
  db: Kysely<SupabaseDB>,
  farmName: string,
  networkId: number
) {
  return await db
    .selectFrom('user_monthly_histories_accessibility_rewards')
    .select(sql<string>`date::text`.as('date'))
    .where('farm_name', '=', farmName)
    .where('network_id', '=', networkId)
    .orderBy('date', 'desc')
    .limit(1)
    .executeTakeFirst();
}

export async function getUserMonthlySnapshotRows(
  db: Kysely<SupabaseDB>,
  farmName: string,
  networkId: number,
  date: string
) {
  return await db
    .selectFrom('user_monthly_histories_accessibility_rewards')
    .select(['user_id', 'referral_code', 'tvl'])
    .where('farm_name', '=', farmName)
    .where('network_id', '=', networkId)
    .where(sql`date::text`, '=', date)
    .execute();
}

export async function getGenesisBlockTimestamp(
  db: Kysely<SupabaseDB>,
  chainNetwork: string
): Promise<BlockTimestampRecord | null> {
  const row = await db
    .selectFrom('block_timestamp_accessibility_rewards')
    .selectAll()
    .where('network', '=', chainNetwork)
    .orderBy('block_number', 'asc')
    .limit(1)
    .executeTakeFirst();

  if (!row) return null;

  return {
    blockNumber: parseInt(row.block_number, 10),
    timestamp: parseInt(row.timestamp, 10),
    network: row.network
  };
}

export async function getMonthBlocks(
  db: Kysely<SupabaseDB>,
  monthStartTimestamp: number,
  monthEndTimestamp: number,
  network: string
): Promise<BlockTimestampRecord[]> {
  const rows = await db
    .selectFrom('block_timestamp_accessibility_rewards')
    .selectAll()
    .where('timestamp', '>=', monthStartTimestamp.toString())
    .where('timestamp', '<', monthEndTimestamp.toString())
    .where('network', '=', network)
    .orderBy('block_number', 'asc')
    .execute();

  return rows.map((row) => ({
    blockNumber: parseInt(row.block_number, 10),
    timestamp: parseInt(row.timestamp, 10),
    network: row.network
  }));
}

export async function getProcessedMonthRewardsBeforeDate(
  db: Kysely<SupabaseDB>,
  farm: string,
  networkId: number,
  endDate: string
): Promise<
  Array<{
    date: string;
    referral_code: string;
    eligible_tvl: number;
    incentive_amount: number;
    incentive_amount_to_pay: number;
  }>
> {
  const rawRows = await db
    .selectFrom('accessibility_reward_incentives as ari')
    .innerJoin('partners as p', 'ari.partner_id', 'p.id')
    .select([
      sql<string>`ari.date::text`.as('date'),
      sql<string>`p.accessibility_reward_code`.as('referral_code'),
      'ari.eligible_tvl',
      'ari.incentive_amount',
      'ari.incentive_amount_to_pay'
    ])
    .where('ari.farm', '=', farm)
    .where('ari.network_id', '=', networkId)
    .where('ari.date', '<', new Date(endDate))
    .orderBy('ari.date', 'asc')
    .execute();

  return rawRows.map((row) => ({
    date: row.date,
    referral_code: row.referral_code,
    eligible_tvl: Number(row.eligible_tvl),
    incentive_amount: Number(row.incentive_amount),
    incentive_amount_to_pay: Number(row.incentive_amount_to_pay ?? 0)
  }));
}

export async function getDepositRatesTimeSeries(
  db: Kysely<SupabaseDB>,
  network: string,
  contractAddress: string
): Promise<TokenPrice[]> {
  const normalizedAddress = contractAddress.toLowerCase();

  // Method 1: Using raw SQL query
  const result = await sql<TokenPrice>`
    WITH all_days AS (
      SELECT
        generate_series(
          (CURRENT_DATE - INTERVAL '364 days')::date,
          CURRENT_DATE,
          INTERVAL '1 day'
        )::date AS event_day
    ),
    latest_deposits AS (
      SELECT
        DATE(to_timestamp(b.timestamp)) AS event_day,
        CASE 
          WHEN (e.return_values->>'shares')::numeric = 0 OR (e.return_values->>'shares') IS NULL THEN 1
          ELSE ((e.return_values->>'assets')::numeric / 1e18) / ((e.return_values->>'shares')::numeric / 1e18)
        END AS rate,
        ROW_NUMBER() OVER (
          PARTITION BY DATE(to_timestamp(b.timestamp))
          ORDER BY e.block_number DESC, e.log_index DESC
        ) AS rn
      FROM events_accessibility_rewards e
      INNER JOIN block_timestamp_accessibility_rewards b
        ON e.block_number = b.block_number
        AND e.network = b.network
      WHERE e.event = 'Deposit'
        AND LOWER(e.contract_address) = ${normalizedAddress}
        AND e.network = ${network}
        AND DATE(to_timestamp(b.timestamp)) >= (CURRENT_DATE - INTERVAL '364 days')::date
        AND DATE(to_timestamp(b.timestamp)) <= CURRENT_DATE
    )
    SELECT
      EXTRACT(EPOCH FROM d.event_day)::BIGINT AS timestamp,
      COALESCE(ld.rate::text, '1') AS rate
    FROM all_days d
    LEFT JOIN latest_deposits ld
      ON d.event_day = ld.event_day
      AND ld.rn = 1
    ORDER BY d.event_day
  `.execute(db);

  return result.rows;
}

export async function syncAdditionalPercentagesFromPartners(
  db: Kysely<SupabaseDB>
): Promise<{ success: boolean; inserted: number }> {
  await db
    .insertInto('distribution_rewards_additional_percentage')
    .columns(['ref_code', 'since_date', 'additional_percentage'])
    .expression((eb) =>
      eb
        .selectFrom('partners')
        .select([
          'accessibility_reward_code as ref_code',
          sql`'2000-01-01'::timestamp without time zone`.as('since_date'),
          sql`
            CASE 
              WHEN accessibility_reward_code = 'untagged' THEN 0.002
              WHEN accessibility_reward_code ~ '^[0-9]+$' 
                AND accessibility_reward_code::numeric >= 100 
                AND accessibility_reward_code::numeric < 1000 
              THEN 0.004
              ELSE 0.002
            END
          `.as('additional_percentage')
        ])
        .where('accessibility_reward_code', 'is not', null)
        .where('accessibility_reward_code', '!=', '')
        .groupBy('accessibility_reward_code')
    )
    .onConflict((oc) => oc.columns(['ref_code', 'since_date']).doNothing())
    .execute();

  const result = await db
    .insertInto('distribution_rewards_additional_percentage')
    .columns(['ref_code', 'since_date', 'additional_percentage'])
    .expression((eb) =>
      eb
        .selectFrom('partners')
        .select([
          'accessibility_reward_code as ref_code',
          sql`'2026-01-01'::timestamp without time zone`.as('since_date'),
          sql`0.003`.as('additional_percentage')
        ])
        .where('accessibility_reward_code', 'is not', null)
        .where('accessibility_reward_code', '!=', '')
        .groupBy('accessibility_reward_code')
    )
    .onConflict((oc) => oc.columns(['ref_code', 'since_date']).doNothing())
    .execute();

  return { success: true, inserted: result.length };
}

export async function getAdditionalPercentagesForDate(
  db: Kysely<SupabaseDB>,
  date: string
): Promise<Map<string, number>> {
  const rows = await db
    .selectFrom('distribution_rewards_additional_percentage as drap')
    .select(['drap.ref_code', 'drap.additional_percentage'])
    .where('drap.since_date', '<=', new Date(date))
    .distinctOn('drap.ref_code')
    .orderBy('drap.ref_code')
    .orderBy('drap.since_date', 'desc')
    .execute();

  const percentageMap = new Map<string, number>();

  for (const row of rows) {
    percentageMap.set(row.ref_code, Number(row.additional_percentage));
  }

  return percentageMap;
}
