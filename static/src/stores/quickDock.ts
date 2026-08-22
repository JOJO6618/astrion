import { defineStore } from 'pinia';
import { useFileStore } from './file';
import { useSubAgentStore } from './subAgent';
import { useBackgroundCommandStore } from './backgroundCommand';
import { useWorkflowStore } from './workflow';

/**
 * 快捷窗口（Quick Dock）状态
 * 对话区右侧占位列：工作流 / 待办 / 子智能体 / 后台指令 / 文件记录 五个窗口。
 * 本 store 管理：文件记录列表、详情面板目标、文件预览目标、全局 ⋯ 菜单。
 */

export interface EditedFileEntry {
  path: string;
  op?: string;
  ts?: string;
}

/**
 * 「上次页面离开时 dock 是否有内容」的本地缓存（与主题缓存同理）。
 * 进入页面时真实内容（文件记录/待办/子智能体/后台指令）全部异步到达，
 * 首帧只能按 hasContent=false 渲染 0 宽，数据到达后整列插入导致布局跳变。
 * 用缓存做首帧乐观展开，等首批数据到齐（settleInitialContent）后切回真实状态；
 * 缓存与真实不符时在初始无过渡窗口内瞬间纠正，用户无感知。
 */
const QUICK_DOCK_HAD_CONTENT_STORAGE_KEY = 'agents_quick_dock_had_content';

const loadCachedHadContent = (): boolean => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return false;
  }
  try {
    return window.localStorage.getItem(QUICK_DOCK_HAD_CONTENT_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
};

export const persistQuickDockHadContent = (hadContent: boolean) => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return;
  }
  try {
    window.localStorage.setItem(QUICK_DOCK_HAD_CONTENT_STORAGE_KEY, hadContent ? '1' : '0');
  } catch (error) {
    console.warn('写入快捷窗口内容状态缓存失败：', error);
  }
};

/**
 * 「每个对话各自」的 dock 内容状态缓存（LRU，上限 50 条）。
 * 切换对话时目标对话的回填是异步的，用该缓存同步假定目标对话的 dock 状态：
 * 有内容则切过去即展开（无动画），回填到齐后切回真实状态，不符时瞬间纠正。
 */
const QUICK_DOCK_CONV_CONTENT_STORAGE_KEY = 'agents_quick_dock_conv_content';
const CONV_CONTENT_CACHE_LIMIT = 50;

const loadConvContentMap = (): Record<string, boolean> => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(QUICK_DOCK_CONV_CONTENT_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, boolean>) : {};
  } catch {
    return {};
  }
};

export const loadQuickDockConvContent = (conversationId: string): boolean => {
  return loadConvContentMap()[conversationId] === true;
};

export const persistQuickDockConvContent = (conversationId: string, hadContent: boolean) => {
  if (typeof window === 'undefined' || !window.localStorage || !conversationId) {
    return;
  }
  try {
    const map = loadConvContentMap();
    // 删除重插：刷新插入序，让 Object.keys 顺序近似 LRU
    delete map[conversationId];
    map[conversationId] = hadContent;
    const keys = Object.keys(map);
    if (keys.length > CONV_CONTENT_CACHE_LIMIT) {
      for (const staleKey of keys.slice(0, keys.length - CONV_CONTENT_CACHE_LIMIT)) {
        delete map[staleKey];
      }
    }
    window.localStorage.setItem(QUICK_DOCK_CONV_CONTENT_STORAGE_KEY, JSON.stringify(map));
  } catch (error) {
    console.warn('写入快捷窗口对话内容缓存失败：', error);
  }
};

export interface QuickDockDetailTarget {
  kind: 'agent' | 'cmd';
  id: string;
}

export interface QuickDockMenuState {
  /** runner = 子智能体/后台指令条目菜单（强制关闭）；file = 文件条目菜单 */
  type: 'runner' | 'file';
  /** 仅 runner：区分子智能体 / 后台指令 */
  kind?: 'agent' | 'cmd';
  /** runner: task_id / command_id；file: 相对路径 */
  key: string;
  /** fixed 定位坐标 */
  left: number;
  top: number;
  /** 菜单与触发按钮右对齐（runner 的 ⋯ 在行右侧） */
  alignRight: boolean;
}

interface QuickDockState {
  editedFiles: EditedFileEntry[];
  /** true = 来自任务期实时事件（播动画）；false = 来自加载/bootstrap（静态呈现） */
  editedFilesLive: boolean;
  detail: QuickDockDetailTarget | null;
  previewPath: string | null;
  menu: QuickDockMenuState | null;
  /** 用户通过顶部悬浮按钮手动收起（与「无内容自动收起」相互独立） */
  userCollapsed: boolean;
  /** 乐观假定的内容状态（来源：上次离开的全局缓存 / 目标对话的按对话缓存） */
  assumedContent: boolean;
  /** 乐观掩码是否生效（三态）：生效时 hasContent 完全由 assumedContent 决定——
   *  包括「假定无内容」：切到缓存为空的对话时立即掩盖旧对话被刻意保留的真实内容，
   *  使 dock 与对话区在同一时刻切换（否则只能等 bootstrap 末尾回填才折叠，慢一拍）。
   *  真实数据到齐（settleInitialContent）后关闭，回退为纯真实状态 */
  assumedActive: boolean;
  /** 文件记录回填同步序号：每次 setEditedFiles +1（bootstrap 必经，空数组也调；
   *  /new 场景 app watcher 也会清空调用）。初始加载/切换对话的无过渡窗口
   *  通过比较序号识别「新一轮内容已回填」 */
  filesSyncSeq: number;
}

/**
 * 初始乐观掩码的内容假定值。落在空对话路由（/ 或 /new）时全局缓存不适用——
 * 空对话真实内容必为空，按缓存乐观展开只会呈现「展开空白几秒后收回」；
 * 落在具体对话路径时保持「首帧按上次离开状态展开」的防跳变优化。
 */
const loadInitialAssumedContent = (): boolean => {
  if (typeof window !== 'undefined') {
    const path = window.location.pathname.replace(/\/+$/, '') || '/';
    if (path === '/' || path === '/new') {
      return false;
    }
  }
  return loadCachedHadContent();
};

export const useQuickDockStore = defineStore('quickDock', {
  state: (): QuickDockState => ({
    editedFiles: [],
    editedFilesLive: false,
    detail: null,
    previewPath: null,
    menu: null,
    userCollapsed: false,
    assumedContent: loadInitialAssumedContent(),
    assumedActive: loadInitialAssumedContent(),
    filesSyncSeq: 0
  }),
  getters: {
    /** 五个窗口（工作流/待办/子智能体/后台指令/文件记录）任一有内容 */
    hasContent(state): boolean {
      const fileStore = useFileStore();
      const subAgentStore = useSubAgentStore();
      const bgStore = useBackgroundCommandStore();
      const workflowStore = useWorkflowStore();
      const todoCount = fileStore.todoList?.tasks?.length || 0;
      // 工作流是「实时状态」（激活/完成/停用即刻变化），不是回填内容，不参与乐观掩码——
      // 否则切换对话时 assumedContent=false 会把刚激活的工作流掩盖掉（/new 激活闪烁根因）。
      // 切对话时旧工作流残留走「保留至回填覆盖」策略，与其余窗口一致。
      const restReal =
        todoCount > 0 ||
        subAgentStore.subAgents.length > 0 ||
        bgStore.commands.length > 0 ||
        state.editedFiles.length > 0;
      // 乐观掩码生效期间（初始加载/切换对话的内容未到齐窗口）以假定状态为准；
      // 掩码关闭后纯真实状态。
      // workflowStore.exiting：退出动画播放期间保持容器展开，否则容器 300ms
      // 收起会吞掉窗口自身的 240ms 退出动画（动画播完 finishExit 后才真正清空）
      return (
        workflowStore.isActive ||
        workflowStore.exiting ||
        (state.assumedActive ? state.assumedContent : restReal)
      );
    },
    /** 实际处于展开态：有内容且未被用户手动收起 */
    expanded(state): boolean {
      return this.hasContent && !state.userCollapsed;
    }
  },
  actions: {
    setEditedFiles(list: EditedFileEntry[] | null | undefined, live = false) {
      this.filesSyncSeq += 1;
      this.editedFilesLive = live;
      if (!Array.isArray(list)) {
        this.editedFiles = [];
        return;
      }
      const seen = new Set<string>();
      const normalized: EditedFileEntry[] = [];
      for (const item of list) {
        if (!item || typeof item.path !== 'string' || !item.path) {
          continue;
        }
        if (seen.has(item.path)) {
          continue;
        }
        seen.add(item.path);
        normalized.push({ path: item.path, op: item.op, ts: item.ts });
      }
      this.editedFiles = normalized;
      // 列表移除正在预览的文件时，同步关闭预览
      if (this.previewPath && !seen.has(this.previewPath)) {
        this.previewPath = null;
      }
    },
    openDetail(kind: 'agent' | 'cmd', id: string) {
      if (!id) {
        return;
      }
      // 再点同一条目 = 收起
      if (this.detail && this.detail.kind === kind && this.detail.id === id) {
        this.detail = null;
        return;
      }
      this.detail = { kind, id };
    },
    closeDetail() {
      this.detail = null;
    },
    openPreview(path: string) {
      if (!path) {
        return;
      }
      // 再点同一文件 = 收起
      if (this.previewPath === path) {
        this.previewPath = null;
        return;
      }
      this.previewPath = path;
    },
    closePreview() {
      this.previewPath = null;
    },
    openMenu(menu: QuickDockMenuState) {
      // 同一按钮再点 = 收起
      if (this.menu && this.menu.type === menu.type && this.menu.key === menu.key) {
        this.menu = null;
        return;
      }
      this.menu = menu;
    },
    closeMenu() {
      this.menu = null;
    },
    /** 切换对话 / 隐藏快捷窗口时重置全部瞬态 */
    resetTransient() {
      this.detail = null;
      this.previewPath = null;
      this.menu = null;
    },
    /** 真实内容数据到齐（无过渡窗口关闭时调用，幂等）：关闭乐观掩码，此后 hasContent 只看真实状态 */
    settleInitialContent() {
      this.assumedActive = false;
      this.assumedContent = false;
    },
    /** 切换对话时按缓存乐观假定目标对话的 dock 内容状态（无缓存/无 id → 假定无内容）
     *  并开启掩码：与对话区同刻切换 dock 的收起/展开，不等待异步回填 */
    assumeContentForConversation(conversationId: string | null) {
      this.assumedContent = conversationId ? loadQuickDockConvContent(conversationId) : false;
      this.assumedActive = true;
    }
  }
});
