import { defineStore } from 'pinia';
import { useConversationStore } from './conversation';

const FILE_STORE_DEBUG_LOGS = false;
function fileDebugLog(...args: unknown[]) {
  if (!FILE_STORE_DEBUG_LOGS) {
    return;
  }
  console.log(...args);
}

interface FileNode {
  type: 'folder' | 'file';
  name: string;
  path: string;
  annotation?: string;
  children?: FileNode[];
}

interface TodoTask {
  index: number;
  title: string;
  status: string;
}

interface TodoList {
  instruction?: string;
  tasks?: TodoTask[];
}

interface ContextMenuState {
  visible: boolean;
  x: number;
  y: number;
  node: FileNode | null;
}

interface FileState {
  fileTree: FileNode[];
  expandedFolders: Record<string, boolean>;
  todoList: TodoList | null;
  /** true = 来自任务期实时事件（播动画）；false = 来自加载/fetch（静态呈现） */
  todoListLive: boolean;
  /** 待办同步序号：fetchTodoList 成功 / setTodoList 调用时 +1。
   *  快捷窗口无过渡窗口通过比较序号识别「新一轮待办已回填」 */
  todoSyncSeq: number;
  fileTreeUnavailable: boolean;
  fileTreeMessage: string;
  contextMenu: ContextMenuState;
}

function buildNodes(treeMap: Record<string, any> | undefined): FileNode[] {
  if (!treeMap) {
    return [];
  }
  const entries = Object.keys(treeMap).map((name) => {
    const node = treeMap[name] || {};
    if (node.type === 'folder') {
      return {
        type: 'folder' as const,
        name,
        path: node.path || name,
        children: buildNodes(node.children)
      };
    }
    return {
      type: 'file' as const,
      name,
      path: node.path || name,
      annotation: node.annotation || ''
    };
  });
  entries.sort((a, b) => {
    if (a.type !== b.type) {
      return a.type === 'folder' ? -1 : 1;
    }
    return a.name.localeCompare(b.name, 'zh-CN');
  });
  return entries;
}

export const useFileStore = defineStore('file', {
  state: (): FileState => ({
    fileTree: [],
    expandedFolders: {},
    todoList: null,
    todoListLive: false,
    todoSyncSeq: 0,
    fileTreeUnavailable: false,
    fileTreeMessage: '',
    contextMenu: {
      visible: false,
      x: 0,
      y: 0,
      node: null
    }
  }),
  actions: {
    async fetchFileTree() {
      try {
        const response = await fetch('/api/files');
        const data = await response.json();
        fileDebugLog('[FileTree] fetch result', data);
        this.setFileTreeFromResponse(data);
      } catch (error) {
        console.error('获取文件树失败:', error);
      }
    },
    markFileTreeUnavailable(message: string) {
      this.fileTreeUnavailable = true;
      this.fileTreeMessage = message || '宿主机模式下文件树不可用';
      this.fileTree = [];
      this.expandedFolders = {};
    },
    setFileTreeFromResponse(payload: any) {
      if (!payload) {
        console.warn('[FileTree] 空 payload');
        return;
      }
      if (payload.unavailable) {
        this.markFileTreeUnavailable(payload.message || payload.error);
        return;
      }
      const structure = payload.structure || payload;
      if (structure && structure.unavailable) {
        this.markFileTreeUnavailable(structure.message || structure.error);
        return;
      }
      if (!structure || !structure.tree) {
        console.warn('[FileTree] 缺少 structure.tree', structure);
        return;
      }
      this.fileTreeUnavailable = false;
      this.fileTreeMessage = '';
      const nodes = buildNodes(structure.tree);
      const expanded = { ...this.expandedFolders };
      const validFolderPaths = new Set<string>();

      const ensureExpansion = (list: FileNode[]) => {
        list.forEach((item) => {
          if (item.type === 'folder') {
            validFolderPaths.add(item.path);
            if (expanded[item.path] === undefined) {
              expanded[item.path] = false;
            }
            ensureExpansion(item.children || []);
          }
        });
      };

      ensureExpansion(nodes);
      Object.keys(expanded).forEach((path) => {
        if (!validFolderPaths.has(path)) {
          delete expanded[path];
        }
      });

      this.expandedFolders = expanded;
      this.fileTree = nodes;
    },
    toggleFolder(path: string) {
      if (!path) {
        return;
      }
      const current = !!this.expandedFolders[path];
      fileDebugLog('[FileTree] toggle folder', path, '=>', !current);
      this.expandedFolders = {
        ...this.expandedFolders,
        [path]: !current
      };
    },
    async fetchTodoList() {
      // /new 等无对话场景：待办是对话级数据，直接清空不发请求。
      // 不带 id 时后端返回工作区级服务 terminal——它可能被 /new 首条消息的
      // 任务污染而持有最近对话的待办，会导致 /new 页面残留老待办。
      const convId = useConversationStore().currentConversationId;
      if (!convId) {
        this.setTodoList(null);
        return;
      }
      try {
        const url = `/api/todo-list?conversation_id=${encodeURIComponent(convId)}`;
        const response = await fetch(url);
        const data = await response.json();
        if (data && data.success) {
          // 顺序同 setTodoList：先写 live 标记（REST 拉取一律静态），再写数据
          this.todoListLive = false;
          this.todoList = data.data || null;
          this.todoSyncSeq += 1;
        }
      } catch (error) {
        console.error('获取待办列表失败:', error);
      }
    },
    setTodoList(payload: TodoList | null, live = false) {
      // 必须先写 live 标记再写数据：TodoWindow 的 watch 是 flush:'sync'，
      // todoList 一赋值就同步触发并读取 todoListLive；顺序颠倒会让 watch
      // 读到上一次调用残留的旧值，动画路径（划线/圆点弹跳）永远走不到。
      this.todoListLive = live;
      this.todoList = payload;
      this.todoSyncSeq += 1;
    },
    showContextMenu(payload: { node: FileNode; event: MouseEvent }) {
      if (!payload || !payload.node) {
        return;
      }
      const { node, event } = payload;
      if (!node.path && node.path !== '') {
        this.hideContextMenu();
        return;
      }
      if (node.type !== 'file' && node.type !== 'folder') {
        this.hideContextMenu();
        return;
      }

      if (event && typeof event.preventDefault === 'function') {
        event.preventDefault();
      }
      if (event && typeof event.stopPropagation === 'function') {
        event.stopPropagation();
      }

      const menuWidth = 200;
      const menuHeight = 50;
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      let x = (event && event.clientX) || 0;
      let y = (event && event.clientY) || 0;

      if (x + menuWidth > viewportWidth) {
        x = viewportWidth - menuWidth - 8;
      }
      if (y + menuHeight > viewportHeight) {
        y = viewportHeight - menuHeight - 8;
      }

      this.contextMenu = {
        visible: true,
        x: Math.max(8, x),
        y: Math.max(8, y),
        node
      };
    },
    hideContextMenu() {
      if (!this.contextMenu.visible) {
        return;
      }
      this.contextMenu = {
        visible: false,
        x: 0,
        y: 0,
        node: null
      };
    }
  }
});
