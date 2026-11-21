/**
 * 设置页导航与搜索索引。
 *
 * - NAV_GROUPS：左侧导航分组（新增分区需同步 SettingsShell 的组件映射与 stores/settings.ts 的类型）。
 * - SEARCH_ENTRIES：可搜索的设置项。titleKey 必须与分区内该行标题使用的 i18n key 一致——
 *   跳转时按标题文本在已渲染的分区里定位目标行；找不到（行被条件隐藏）时只切到分区。
 *   新增设置行时顺手在此登记，否则搜索只能命中分区名。
 */
import { t } from '@/locales';
import type { SettingsSection } from '@/stores/settings';

export interface NavItem {
  id: SettingsSection;
  labelKey: string;
  icon: string;
}

export interface NavGroup {
  id: string;
  titleKey: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    id: 'account',
    titleKey: 'settings.groupAccount',
    items: [
      { id: 'usage', labelKey: 'settings.navUsage', icon: 'usage' },
      { id: 'storage', labelKey: 'settings.navStorage', icon: 'storage' }
    ]
  },
  {
    id: 'model',
    titleKey: 'settings.groupModel',
    items: [
      { id: 'providers', labelKey: 'settings.navProviders', icon: 'providers' },
      { id: 'models', labelKey: 'settings.navModels', icon: 'models' },
      { id: 'model-pref', labelKey: 'settings.navModelPref', icon: 'model-pref' },
      { id: 'codex', labelKey: 'settings.navCodex', icon: 'codex' }
    ]
  },
  {
    id: 'system',
    titleKey: 'settings.groupSystem',
    items: [
      { id: 'general', labelKey: 'settings.navGeneral', icon: 'general' },
      { id: 'chat', labelKey: 'chatMode.settings', icon: 'chat' },
      { id: 'quick-chat', labelKey: 'settings.navQuickChat', icon: 'quick-chat' },
      { id: 'workspace', labelKey: 'settings.navWorkspace', icon: 'workspace' },
      { id: 'search', labelKey: 'settings.navSearch', icon: 'search' },
      { id: 'tools', labelKey: 'settings.navTools', icon: 'tools' },
      { id: 'mcp', labelKey: 'settings.navMcp', icon: 'mcp' },
      { id: 'context', labelKey: 'settings.navContext', icon: 'context' },
      { id: 'files', labelKey: 'settings.navFiles', icon: 'files' },
      { id: 'browser', labelKey: 'settings.navBrowser', icon: 'browser' }
    ]
  },
  {
    id: 'agents',
    titleKey: 'settings.groupAgents',
    items: [
      { id: 'sub-agents', labelKey: 'settings.navSubAgents', icon: 'sub-agents' },
      { id: 'review-agents', labelKey: 'settings.navReviewAgents', icon: 'review-agents' }
    ]
  },
  {
    id: 'interface',
    titleKey: 'settings.groupInterface',
    items: [{ id: 'appearance', labelKey: 'settings.navAppearance', icon: 'appearance' }]
  },
  {
    id: 'admin',
    titleKey: 'settings.groupAdmin',
    items: [{ id: 'admin', labelKey: 'settings.navAdmin', icon: 'admin' }]
  }
];

/** 分区大标题 */
export const SECTION_TITLE_KEYS: Record<SettingsSection, string> = {
  usage: 'settings.usageTitle',
  storage: 'settings.storageTitle',
  providers: 'settings.providersTitle',
  models: 'settings.modelsTitle',
  'model-pref': 'settings.modelPrefTitle',
  codex: 'settings.codexTitle',
  general: 'settings.generalTitle',
  chat: 'chatMode.settings',
  'quick-chat': 'settings.quickChatTitle',
  workspace: 'settings.workspaceTitle',
  search: 'settings.searchTitle',
  tools: 'settings.toolsTitle',
  mcp: 'settings.navMcp',
  context: 'settings.contextTitle',
  files: 'settings.filesTitle',
  browser: 'settings.browserTitle',
  'sub-agents': 'settings.subAgentsTitle',
  'review-agents': 'settings.reviewAgentsTitle',
  appearance: 'settings.appearanceTitle',
  admin: 'settings.adminTitle'
};

interface SearchEntry {
  section: SettingsSection;
  titleKey: string;
  descKey?: string;
}

const entries = (section: SettingsSection, list: Array<string | [string, string]>): SearchEntry[] =>
  list.map((item) =>
    Array.isArray(item)
      ? { section, titleKey: item[0], descKey: item[1] }
      : { section, titleKey: item }
  );

const P = 'personalization.';

const SEARCH_ENTRIES: SearchEntry[] = [
  ...entries('model-pref', [
    `${P}defaultModelTitle`,
    `${P}defaultThinkingModeTitle`,
    `${P}reasoningEffortTitle`,
    `${P}externalSessionHeaderTitle`
  ]),
  ...entries('codex', [
    `${P}codexAccountSelectorTitle`,
    `${P}codexStatusTitle`,
    `${P}codexModelsRowTitle`,
    `${P}codexUsageTitle`,
    `${P}codexResetsTitle`,
    [`${P}codexProxyRowTitle`, `${P}codexProxyDesc`]
  ]),
  ...entries('general', [
    'settings.hostPasswordTitle',
    ['settings.crosspetTitle', 'settings.crosspetDesc'],
    `${P}sendShortcutTitle`,
    [`${P}subscriptionQuotaTitle`, `${P}subscriptionQuotaDesc`],
    `${P}subscriptionQuotaSourceTitle`,
    `${P}autoTitleTitle`,
    `${P}titleModelTitle`,
    [`${P}thinkingSummaryTitle`, `${P}thinkingSummaryDesc`],
    `${P}thinkingSummaryModelTitle`,
    `${P}thinkingSummaryIntervalTitle`,
    ['commandBlocking.settingsTitle', 'commandBlocking.settingsDesc'],
    'commandBlocking.rulesLabel',
    ['settings.runDataTitle', 'settings.runDataDesc']
  ]),
  ...entries('chat', [
    'chatMode.defaultModel',
    'chatMode.effort',
    'chatMode.defaultNetwork',
    `${P}deepTriggerTokens`,
    'chatMode.instructions'
  ]),
  ...entries('quick-chat', [
    'quickEntry.enabled',
    'quickEntry.shortcut',
    'quickEntry.defaultWorkspace',
    'quickEntry.defaultModel'
  ]),
  ...entries('workspace', [
    `${P}defaultPermissionTitle`,
    `${P}defaultWorkModeTitle`,
    ['sandbox.sectionTitle', 'sandbox.sectionDesc'],
    `${P}agentsMdInjectTitle`,
    `${P}claudeMdInjectTitle`,
    [`${P}agentsSkillsScanTitle`, `${P}agentsSkillsScanDesc`],
    [`${P}modifyHistoryTitle`, `${P}modifyHistoryDesc`],
    `${P}versioningByDefaultTitle`,
    `${P}backupModeTitle`,
    [`${P}gitWorktreeTitle`, `${P}gitWorktreeDesc`]
  ]),
  ...entries('search', [
    [`${P}searchProviderTitle`, `${P}searchProviderDesc`],
    [`${P}extractProviderTitle`, `${P}extractProviderDesc`],
    `${P}jinaApiKeyTitle`,
    [`${P}webDirectExtractTitle`, `${P}webDirectExtractDesc`]
  ]),
  ...entries('tools', [
    [`${P}silentToolDisableTitle`, `${P}silentToolDisableDesc`],
    [`${P}toolIntentTitle`, `${P}toolIntentDesc`],
    [`${P}skillHintsTitle`, `${P}skillHintsDesc`],
    `${P}strictSkillTitle`,
    `${P}availableSkillsTitle`,
    `${P}disabledToolCategoriesTitle`,
    [`${P}toolLoadingTitle`, `${P}toolLoadingDesc`]
  ]),
  ...entries('context', [
    'settings.memoryMigration.hostEntry',
    [`${P}recentConversationsTitle`, `${P}recentConversationsDesc`],
    `${P}recentConversationsCountTitle`,
    `${P}maxMemoryInjectTitle`,
    `${P}compressionStrategyTitle`,
    `${P}autoShallowCompressTitle`,
    `${P}autoDeepCompressTitle`,
    [`${P}adaptiveCompressTitle`, `${P}adaptiveCompressDesc`]
  ]),
  ...entries('files', [`${P}imageCompressionTitle`]),
  ...entries('browser', [
    'browser.aiDefaultOn',
    'browser.aiAutoOpen',
    'browser.aiDownloadDir',
    'browser.aiSites'
  ]),
  ...entries('sub-agents', [
    `${P}subAgentModelTitle`,
    `${P}subAgentMaxInstancesTitle`,
    `${P}compressThresholdTitle`
  ]),
  ...entries('review-agents', [
    `${P}allowAutoFullAccessTitle`,
    `${P}maxRoundsTitle`,
    `${P}goalModeDivider`,
    [`${P}goalReviewActiveTitle`, `${P}goalReviewActiveDesc`]
  ]),
  ...entries('appearance', [
    `${P}themeTitle`,
    'common.settingsLanguage',
    [`${P}currencyTitle`, `${P}currencyDesc`],
    `${P}groupByWorkspaceTitle`,
    [`${P}enhancedToolDisplayTitle`, `${P}enhancedToolDisplayDesc`],
    [`${P}showStatusAvatarTitle`, `${P}showStatusAvatarDesc`],
    [`${P}showGitStatusBarTitle`, `${P}showGitStatusBarDesc`],
    `${P}autoOpenTerminalTitle`,
    [`${P}quickDockAutoExpandTitle`, `${P}quickDockAutoExpandDesc`],
    [`${P}previewAutoOpenTitle`, `${P}previewAutoOpenDesc`],
    [`${P}editSummaryLiveTitle`, `${P}editSummaryLiveDesc`],
    [`${P}filePreviewWrapTitle`, `${P}filePreviewWrapDesc`],
    `${P}blockDisplayModeTitle`,
    `${P}hideBlockBordersTitle`,
    `${P}minimalExpandHeightTitle`,
    `${P}compactMessageTitle`
  ]),
  ...entries('mcp', [
    ['settings.mcp.services', 'settings.mcp.servicesDesc'],
    'settings.mcp.defaults',
    ['settings.mcp.workspace', 'settings.mcp.workspaceDesc']
  ]),
  ...entries('admin', [`${P}customToolsAdminTitle`])
];

export interface SearchResult {
  key: string;
  section: SettingsSection;
  /** 结果标题（设置项标题或分区名） */
  title: string;
  /** 右侧次文字：所属分区；分区本身为空 */
  meta: string;
  /** 跳转后要定位的行标题；分区结果为空 */
  anchor: string;
}

const normalize = (value: string) => value.toLocaleLowerCase().replace(/\s+/g, '');

/**
 * 搜索设置项与分区名。排序：标题前缀命中 > 标题包含 > 仅说明命中；分区名命中排在同档最前。
 * labelOf 由外壳提供（工作区/项目模式下分区名不同）；canShow 过滤管理员与平台不可用分区。
 */
export function searchSettings(
  query: string,
  labelOf: (section: SettingsSection) => string,
  canShow: (section: SettingsSection) => boolean,
  canShowEntry: (titleKey: string) => boolean = () => true
): SearchResult[] {
  const q = normalize(query);
  if (!q) return [];
  const scored: Array<{ score: number; order: number; result: SearchResult }> = [];
  let order = 0;

  for (const group of NAV_GROUPS) {
    for (const item of group.items) {
      if (!canShow(item.id)) continue;
      const label = labelOf(item.id);
      const name = normalize(label);
      if (!name.includes(q)) continue;
      scored.push({
        score: name.startsWith(q) ? 0 : 2,
        order: order++,
        result: { key: `section:${item.id}`, section: item.id, title: label, meta: '', anchor: '' }
      });
    }
  }

  for (const entry of SEARCH_ENTRIES) {
    if (!canShow(entry.section) || !canShowEntry(entry.titleKey)) continue;
    const title = t(entry.titleKey);
    const name = normalize(title);
    let score = -1;
    if (name.startsWith(q)) score = 1;
    else if (name.includes(q)) score = 3;
    else if (entry.descKey && normalize(t(entry.descKey)).includes(q)) score = 4;
    if (score < 0) continue;
    scored.push({
      score,
      order: order++,
      result: {
        key: `${entry.section}:${entry.titleKey}`,
        section: entry.section,
        title,
        meta: labelOf(entry.section),
        anchor: title
      }
    });
  }

  return scored
    .sort((a, b) => a.score - b.score || a.order - b.order)
    .slice(0, 40)
    .map((item) => item.result);
}

/** 在已渲染的分区内按标题文本找到对应行（行容器优先，退回标题元素本身） */
export function findSettingRow(root: HTMLElement, anchor: string): HTMLElement | null {
  const target = anchor.trim();
  const titles = root.querySelectorAll<HTMLElement>(
    '.stg-row-title, .stg-section-divider__label, .stg-section-title'
  );
  for (const el of titles) {
    if ((el.textContent || '').trim() !== target) continue;
    const row = el.closest<HTMLElement>(
      '.stg-select-row, .stg-input-row, .stg-list-row, .stg-action-row, .stg-toggle-row, .stg-textarea-row, .stg-group-title, .stg-section-divider, .stg-section-header'
    );
    return row || el;
  }
  return null;
}
