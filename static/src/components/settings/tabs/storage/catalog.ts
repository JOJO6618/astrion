/** 类别 id → 文案 key 与颜色（顺序与后端 CATEGORY_IDS 一致）。 */
export interface CategoryMeta {
  id: string;
  key: string;
  color: string;
}

export const CATEGORY_META: CategoryMeta[] = [
  { id: 'backups', key: 'storageStats.catBackups', color: 'var(--storage-1)' },
  { id: 'logs', key: 'storageStats.catLogs', color: 'var(--storage-2)' },
  { id: 'mcp_profiles', key: 'storageStats.catMcpProfiles', color: 'var(--storage-3)' },
  {
    id: 'agent_conversations',
    key: 'storageStats.catAgentConversations',
    color: 'var(--storage-4)'
  },
  { id: 'media', key: 'storageStats.catMedia', color: 'var(--storage-5)' },
  { id: 'sub_agents', key: 'storageStats.catSubAgents', color: 'var(--storage-6)' },
  { id: 'chat', key: 'storageStats.catChat', color: 'var(--storage-7)' },
  { id: 'other', key: 'storageStats.catOther', color: 'var(--storage-8)' }
];

const FALLBACK = CATEGORY_META[CATEGORY_META.length - 1];

export function categoryMeta(id: string): CategoryMeta {
  return CATEGORY_META.find((item) => item.id === id) || { ...FALLBACK, id };
}
