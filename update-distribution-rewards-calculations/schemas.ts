import { z } from 'zod';

// Farm configurations for the Amatsu API
export const FARM_CONFIGS = [
  'Sky Farm - ethereum',
  'Spk Farm - ethereum',
  'sUSDS Farm - ethereum',
  'sUSDS Farm - arbitrum',
  'sUSDS Farm - base',
  'sUSDS Farm - unichain',
  'sUSDS Farm - optimism',
  'sUSDC Farm - ethereum',
  'sUSDC Farm - base',
  'sUSDC Farm - optimism',
  'sUSDC Farm - unichain',
  'sUSDC Farm - arbitrum',
  'Chronicle - ethereum',
  'stUSDS Farm - ethereum',
  'spUSDC Farm - ethereum',
  'spUSDT Farm - ethereum',
  'spPYUSD Farm - ethereum',
  'spUSDC Farm - avalanche'
] as const;

// Amatsu API response schema
export const AmatsuFarmResponseSchema = z.object({
  processedMonths: z.array(
    z.object({
      month: z.string(),
      rewards: z.record(
        z.string(),
        z.object({
          referralCode: z.string(),
          monthEndEligibleTVL: z.number(),
          rewards: z.number(),
          rewardToPay: z.number()
        })
      )
    })
  )
});

export type AmatsuFarmResponse = z.infer<typeof AmatsuFarmResponseSchema>;

// Internal data structures
export interface AccessibilityRewardIncentiveRecord {
  partner_id: number;
  network_id: number;
  farm: string;
  date: string;
  eligible_tvl: number;
  incentive_amount: number;
  incentive_amount_to_pay: number;
  bonus_incentive_amount?: number | null;
  is_active?: boolean;
}

export interface BonusIncentive {
  id: number;
  partner_id: number;
  star_id: number | null;
  amount: number;
  active: boolean;
}
