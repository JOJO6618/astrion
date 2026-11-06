export interface CostActor {
  id: string;
  name: string;
  /** review_agent：三个审核智能体，name 为 agent key（auto_approval / goal_review / workflow_review） */
  kind: 'main' | 'sub_agent' | 'thinking_summary' | 'review_agent';
  cost_usd: string;
  requests: number;
  priced_requests: number;
  unpriced_requests: number;
  /** 旧账本键位：历史订阅／本地请求未计价，按未计价显示 */
  subscription_requests?: number;
  local_requests?: number;
  historical_unpriced: boolean;
}

export interface ConversationCosts {
  currency: 'USD';
  total_usd: string;
  partial: boolean;
  has_priced: boolean;
  actors: CostActor[];
  exchange_rate?: {
    usd_cny?: number;
    rate_date?: string;
    fetched_at?: string;
    source?: string;
    stale?: boolean;
  };
}
