// @ts-nocheck
import { usePersonalizationStore } from '../../stores/personalization';

const SEARCH_FLAT_LIMIT = 50;
const SEARCH_GROUP_LIMIT = 20;

export const searchMethods = {
  handleSidebarSearchInput(value) {
    this.searchQuery = value;
  },

  handleSidebarSearchSubmit(value) {
    this.searchQuery = value;
    const trimmed = String(value || '').trim();
    if (!trimmed) {
      this.exitConversationSearch();
      return;
    }
    this.startConversationSearch(trimmed);
  },

  exitConversationSearch() {
    this.searchActive = false;
    this.searchInProgress = false;
    this.searchMoreAvailable = false;
    this.searchOffset = 0;
    this.searchTotal = 0;
    this.searchResults = [];
    this.searchGroups = [];
    this.searchActiveQuery = '';
    this.conversationsOffset = 0;
    this.loadConversationsList();
  },

  /** 是否处于「按工作区分组」的侧边栏模式（决定搜索是否跨工作区） */
  isGroupedSidebarSearch() {
    const personalizationStore = usePersonalizationStore();
    return (
      !!personalizationStore?.form?.group_sidebar_by_workspace &&
      !!(this.versioningHostMode || this.dockerProjectMode)
    );
  },

  async startConversationSearch(query) {
    const trimmed = String(query || '').trim();
    if (!trimmed) {
      return;
    }
    const requestSeq = ++this.searchRequestSeq;
    this.searchActiveQuery = trimmed;
    this.searchActive = true;
    this.searchInProgress = true;
    // 后端一次性返回全部匹配（上限 limit），不再需要分页加载更多
    this.searchMoreAvailable = false;
    this.searchOffset = 0;
    this.searchTotal = 0;
    this.searchResults = [];
    this.searchGroups = [];

    // 搜索范围跟随侧边栏类型过滤器（普通/多智能体）
    const { useConversationStore } = await import('../../stores/conversation');
    const maParam = useConversationStore().sidebarConversationType === 'multi_agent' ? '&multi_agent_mode=1' : '&multi_agent_mode=0';
    const grouped = this.isGroupedSidebarSearch();
    const url = grouped
      ? `/api/conversations/search?q=${encodeURIComponent(trimmed)}&limit=${SEARCH_GROUP_LIMIT}&all_workspaces=1${maParam}`
      : `/api/conversations/search?q=${encodeURIComponent(trimmed)}&limit=${SEARCH_FLAT_LIMIT}${maParam}`;

    try {
      const response = await fetch(url);
      const payload = await response.json();
      if (requestSeq !== this.searchRequestSeq) {
        return;
      }
      if (!payload.success) {
        console.error('搜索对话失败:', payload.error || payload.message);
        return;
      }
      const data = payload.data || {};
      if (grouped) {
        this.searchGroups = Array.isArray(data.groups) ? data.groups : [];
      } else {
        this.searchResults = Array.isArray(data.results) ? data.results : [];
        this.searchTotal = data.count || this.searchResults.length;
      }
    } catch (error) {
      console.error('搜索对话异常:', error);
    } finally {
      if (requestSeq === this.searchRequestSeq) {
        this.searchInProgress = false;
      }
    }
  },

  /** 后端搜索一次性返回结果，保留此方法仅为兼容模板绑定 */
  async loadMoreSearchResults() {
    // no-op：搜索结果不再分页
  }
};
