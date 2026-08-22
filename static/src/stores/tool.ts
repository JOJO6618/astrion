import { defineStore } from 'pinia';

export interface ToolCategory {
  id: string;
  label: string;
  enabled: boolean;
  locked?: boolean;
  locked_state?: boolean | null;
  tools?: string[];
}

interface ToolState {
  preparingTools: Map<string, any>;
  activeTools: Map<string, any>;
  toolActionIndex: Map<string, any>;
  toolStacks: Map<string, any[]>;
  toolSettings: ToolCategory[];
  toolSettingsLoading: boolean;
}

export const useToolStore = defineStore('tool', {
  state: (): ToolState => ({
    preparingTools: new Map<string, any>(),
    activeTools: new Map<string, any>(),
    toolActionIndex: new Map<string, any>(),
    toolStacks: new Map<string, any[]>(),
    toolSettings: [],
    toolSettingsLoading: false
  }),
  actions: {
    setToolSettings(categories: ToolCategory[]) {
      this.toolSettings = categories;
    },
    setToolSettingsLoading(value: boolean) {
      this.toolSettingsLoading = value;
    },
    registerToolAction(action: any, executionId: string | number | null = null) {
      if (!action || action.type !== 'tool') {
        return;
      }
      const keys = new Set<string | number>();
      if (action.id) {
        keys.add(action.id);
      }
      if (action.tool && action.tool.id) {
        keys.add(action.tool.id);
      }
      if (executionId) {
        keys.add(executionId);
      }
      if (action.tool && action.tool.executionId) {
        keys.add(action.tool.executionId);
      }
      keys.forEach((key) => {
        if (key !== undefined && key !== null) {
          this.toolActionIndex.set(String(key), action);
        }
      });
    },
    unregisterToolAction(action: any) {
      if (!action || action.type !== 'tool') {
        return;
      }
      const keysToRemove: string[] = [];
      for (const [key, stored] of this.toolActionIndex.entries()) {
        if (stored === action) {
          keysToRemove.push(key);
        }
      }
      keysToRemove.forEach((key) => this.toolActionIndex.delete(key));
      if (action.tool && action.tool.name) {
        this.releaseToolAction(action.tool.name, action);
      }
    },
    findToolAction(
      id?: string | number | null,
      preparingId?: string | number | null,
      executionId?: string | number | null
    ) {
      const candidates = [executionId, id, preparingId];
      for (const candidate of candidates) {
        if (candidate === undefined || candidate === null) {
          continue;
        }
        const key = String(candidate);
        if (this.toolActionIndex.has(key)) {
          return this.toolActionIndex.get(key);
        }
      }
      return null;
    },
    trackToolAction(toolName: string, action: any) {
      if (!toolName || !action) {
        return;
      }
      if (!this.toolStacks.has(toolName)) {
        this.toolStacks.set(toolName, []);
      }
      const stack = this.toolStacks.get(toolName)!;
      if (!stack.includes(action)) {
        stack.push(action);
      }
    },
    releaseToolAction(toolName: string, action: any) {
      if (!toolName || !this.toolStacks.has(toolName)) {
        return;
      }
      const stack = this.toolStacks.get(toolName)!;
      const index = stack.indexOf(action);
      if (index !== -1) {
        stack.splice(index, 1);
      }
      if (stack.length === 0) {
        this.toolStacks.delete(toolName);
      }
    },
    getLatestActiveToolAction(toolName: string) {
      if (!toolName || !this.toolStacks.has(toolName)) {
        return null;
      }
      const stack = this.toolStacks.get(toolName)!;
      for (let i = stack.length - 1; i >= 0; i--) {
        const action = stack[i];
        if (!action || action.type !== 'tool' || !action.tool) {
          continue;
        }
        if (['preparing', 'running', 'stale'].includes(action.tool.status)) {
          return action;
        }
      }
      return stack[stack.length - 1] || null;
    },
    resetToolTracking() {
      this.preparingTools.clear();
      this.activeTools.clear();
      this.toolActionIndex.clear();
      this.toolStacks.clear();
    }
  }
});
