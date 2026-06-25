import { Kysely } from 'kysely';
import BigNumber from 'bignumber.js';

import {
  getOrCreateNetwork,
  getOrCreatePartner,
  getPartnerActiveStatus,
  getPartnerTrackSsrIncentivesStatus,
  getBonusIntegrationBoostIncentiveForPartner,
  updateAccessibilityRewardIncentives,
  upsertUserMonthlyHistories,
  syncAdditionalPercentagesFromPartners
} from './queries.ts';
import {
  FARM_CONFIGS,
  AmatsuFarmResponse,
  AccessibilityRewardIncentiveRecord
} from './schemas.ts';
import { processMonthlyRewards } from '@/update-distribution-rewards-calculations/calculations/helpers.ts';
import { UserState } from '@/update-distribution-rewards-calculations/calculations/models/models.ts';
import {SupabaseDB} from "../db/schema.ts";

// ---------------------------------------------------------------------------
// Partner data cache — resolved once per farm, used for all months/codes
// ---------------------------------------------------------------------------

interface PartnerInfo {
  partnerId: number;
  isActive: boolean;
  trackSsr: boolean;
  bonusIncentive: { active: boolean; amount: number } | null;
}

/**
 * Batch-load all partner metadata for a set of referral codes.
 *
 * Resolves partner IDs (creating if needed), then fetches active status,
 * SSR tracking flag, and bonus incentive data in bulk — replacing the
 * per-referral-code × per-month N+1 pattern.
 *
 * NOTE: The individual query helpers are reused here. For further
 * optimization, replace the loops below with proper batch SQL queries
 * (e.g. `WHERE partner_id = ANY($1)`). The key improvement is that
 * each referral code is only queried ONCE regardless of how many months
 * exist, rather than once per month × per code.
 */
async function batchLoadPartnerInfo(
  db: Kysely<SupabaseDB>,
  referralCodes: string[]
): Promise<Map<string, PartnerInfo>> {
  const cache = new Map<string, PartnerInfo>();

  // Step 1: Resolve all partner IDs (creates missing ones)
  const partnerIds = new Map<string, number>();
  for (const code of referralCodes) {
    const partnerId = await getOrCreatePartner(db, code);
    if (partnerId) {
      partnerIds.set(code, partnerId);
    }
  }

  // Step 2: Batch-fetch metadata for all resolved partners
  const allPartnerIds = [...partnerIds.values()];
  const [activeStatuses, ssrStatuses, bonusIncentives] = await Promise.all([
    Promise.all(
      allPartnerIds.map(async (id) => ({
        id,
        isActive: await getPartnerActiveStatus(db, id)
      }))
    ),
    Promise.all(
      allPartnerIds.map(async (id) => ({
        id,
        trackSsr: await getPartnerTrackSsrIncentivesStatus(db, id)
      }))
    ),
    Promise.all(
      allPartnerIds.map(async (id) => ({
        id,
        bonus: await getBonusIntegrationBoostIncentiveForPartner(db, id)
      }))
    )
  ]);

  // Build lookup maps from results
  const activeMap = new Map(activeStatuses.map((r) => [r.id, r.isActive]));
  const ssrMap = new Map(ssrStatuses.map((r) => [r.id, r.trackSsr]));
  const bonusMap = new Map(bonusIncentives.map((r) => [r.id, r.bonus]));

  // Assemble final cache
  for (const [code, partnerId] of partnerIds) {
    cache.set(code, {
      partnerId,
      isActive: activeMap.get(partnerId) ?? false,
      trackSsr: ssrMap.get(partnerId) ?? false,
      bonusIncentive: bonusMap.get(partnerId) ?? null
    });
  }

  return cache;
}

// ---------------------------------------------------------------------------
// Config parsing
// ---------------------------------------------------------------------------

/**
 * Parse farm configuration string to extract farm name and network
 */
export function parseFarmConfig(farmConfig: string): {
  farm: string;
  network: string;
} {
  const parts = farmConfig.split(' - ');
  if (parts.length !== 2) {
    throw new Error(`Invalid farm config format: ${farmConfig}`);
  }
  return { farm: parts[0], network: parts[1] };
}

// ---------------------------------------------------------------------------
// Farm data processing (N+1 fixed)
// ---------------------------------------------------------------------------

/**
 * Collect all unique referral codes across all months in a farm's results.
 */
function collectReferralCodes(farmData: AmatsuFarmResponse): string[] {
  const codes = new Set<string>();
  for (const monthData of farmData.processedMonths) {
    for (const referralCode of Object.keys(monthData.rewards)) {
      codes.add(referralCode);
    }
  }
  return [...codes];
}

/**
 * Process a single farm's data and convert to AccessibilityRewardIncentiveRecord[].
 *
 * Partner metadata is loaded once for all unique referral codes, then reused
 * across all months — eliminating the previous N+1 query pattern.
 */
async function processFarmData(
  db: Kysely<SupabaseDB>,
  farmConfig: string,
  farmData: AmatsuFarmResponse,
  networkId: number
): Promise<AccessibilityRewardIncentiveRecord[]> {
  const { farm } = parseFarmConfig(farmConfig);

  // Batch-load all partner info upfront
  const allCodes = collectReferralCodes(farmData);
  const partnerCache = await batchLoadPartnerInfo(db, allCodes);

  const records: AccessibilityRewardIncentiveRecord[] = [];
  let skippedPartners = 0;
  let skippedSsr = 0;

  for (const monthData of farmData.processedMonths) {
    const startDate = `${monthData.month}-01`;

    for (const [referralCode, rewardData] of Object.entries(
      monthData.rewards
    )) {
      const partner = partnerCache.get(referralCode);
      if (!partner) {
        skippedPartners++;
        continue;
      }

      // TVL is always tracked regardless of SSR flag
      // If SSR tracking is disabled, set only SSR incentive amounts to 0
      const ssrIncentiveAmount = partner.trackSsr
        ? new BigNumber(String(rewardData.rewards)).toNumber()
        : 0;
      const ssrIncentiveAmountToPay = partner.trackSsr
        ? new BigNumber(String(rewardData.rewardToPay)).toNumber()
        : 0;

      const incentiveRecord: AccessibilityRewardIncentiveRecord = {
        partner_id: partner.partnerId,
        network_id: networkId,
        farm: farm,
        date: startDate,
        eligible_tvl: new BigNumber(
          String(rewardData.monthEndEligibleTVL)
        ).toNumber(),
        incentive_amount: ssrIncentiveAmount,
        incentive_amount_to_pay: ssrIncentiveAmountToPay,
        is_active: partner.isActive
      };

      if (partner.bonusIncentive?.active) {
        incentiveRecord.bonus_incentive_amount = partner.bonusIncentive.amount;
      }

      // Only skip if both SSR tracking is disabled AND there's no bonus incentive
      if (!partner.trackSsr && !incentiveRecord.bonus_incentive_amount) {
        skippedSsr++;
        continue;
      }

      records.push(incentiveRecord);
    }
  }

  if (skippedPartners > 0 || skippedSsr > 0) {
    console.log(
      `${farm} (${parseFarmConfig(farmConfig).network}): Processed ${records.length} records, skipped ${skippedPartners} unknown partners, ${skippedSsr} SSR-disabled`
    );
  }

  return records;
}

// ---------------------------------------------------------------------------
// User history snapshots
// ---------------------------------------------------------------------------

export async function saveMonthlyUserHistories(
  db: Kysely<SupabaseDB>,
  farmName: string,
  networkId: number,
  userHistories: Record<string, UserState>,
  month: string // e.g. "2025-09"
) {
  const date = `${month}-01`;

  const rows = Object.entries(userHistories).map(([userId, userState]) => ({
    date,
    farm_name: farmName,
    network_id: networkId,
    referral_code: userState.referral,
    user_id: userId,
    tvl: userState.tvl
  }));

  if (rows.length === 0) {
    return { success: true, count: 0 };
  }

  const result = await upsertUserMonthlyHistories(db, rows);
  console.log(`Saved ${result.count} userHistories for ${farmName} (${month})`);
  return result;
}

// ---------------------------------------------------------------------------
// Main calculation loop
// ---------------------------------------------------------------------------

/**
 * Fetch accessibility rewards incentives data from all Amatsu farms
 */
export async function calculateAccessibilityRewardsIncentives(
  db: Kysely<SupabaseDB>
): Promise<AccessibilityRewardIncentiveRecord[]> {
  const records: AccessibilityRewardIncentiveRecord[] = [];
  let failedFarms = 0;

  for (const farmConfig of FARM_CONFIGS) {
    try {
      const { farm, network } = parseFarmConfig(farmConfig);

      // Resolve network once per farm — reused by both processFarmData
      // and saveMonthlyUserHistories (was previously resolved twice)
      const networkId = await getOrCreateNetwork(db, network);

      const farmData = await processMonthlyRewards(farmConfig, true, true);

      // Only save snapshots for months that have userHistories
      for (const monthData of farmData.processedMonths) {
        const histories = monthData.userHistories;
        if (histories && Object.keys(histories).length > 0) {
          await saveMonthlyUserHistories(
            db,
            farm,
            networkId,
            histories,
            monthData.month
          );
        }
      }

      const farmRecords = await processFarmData(
        db,
        farmConfig,
        farmData,
        networkId
      );
      records.push(...farmRecords);
    } catch (error) {
      failedFarms++;
      console.error(`Error processing farm ${farmConfig}:`, error);
    }
  }

  const successfulFarms = FARM_CONFIGS.length - failedFarms;
  console.log(
    `Processed ${records.length} records from ${successfulFarms}/${FARM_CONFIGS.length} farms${failedFarms > 0 ? ` (${failedFarms} failed)` : ''}`
  );

  return records;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/**
 * Main function to update accessibility rewards incentives
 */
export async function updateAccessibilityRewardsIncentivesData(
  db: Kysely<SupabaseDB>,
  { updateAll = false }: { updateAll?: boolean } = {}
) {
  try {
    await syncAdditionalPercentagesFromPartners(db);
    const allRecords = await calculateAccessibilityRewardsIncentives(db);

    if (allRecords.length === 0) {
      return {
        success: false,
        message: 'Failed to collect accessibility rewards incentives data',
        results: {
          amatsuFarms: { success: false, error: 'No data collected' }
        }
      };
    }

    const updateResult = await updateAccessibilityRewardIncentives(
      db,
      allRecords,
      { updateAll }
    );

    return {
      success: true,
      message: updateResult.message,
      results: {
        amatsuFarms: {
          count: allRecords.length,
          success: true
        }
      },
      totalUpdated: updateResult.count
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : String(error);
    return {
      success: false,
      message: 'Failed to update accessibility rewards incentives',
      results: { amatsuFarms: { success: false, error: errorMessage } }
    };
  }
}
