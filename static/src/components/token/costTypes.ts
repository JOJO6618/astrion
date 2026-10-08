export interface CostActor {
  id: string;
  name: string;
  kind: 'main' | 'sub_agent';
  cost_usd: string;
  requests: number;
  priced_requests: number;
  unpriced_requests: number;
  subscription_requests?: number;
  local_requests?: number;
  historical_unpriced: boolean;
}

export interface ConversationCosts {
  currency: 'USD';
  total_usd: string;
  partial: boolean;
  has_priced: boolean;
  non_metered: boolean;
  actors: CostActor[];
  exchange_rate?: {
    usd_cny?: number;
    rate_date?: string;
    fetched_at?: string;
    source?: string;
    stale?: boolean;
  };
}
