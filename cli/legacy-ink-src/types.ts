export type PermissionMode = 'readonly' | 'approval' | 'auto_approval' | 'unrestricted';
export type ExecutionMode = 'sandbox' | 'direct';
export type RunMode = 'fast' | 'thinking' | 'deep';

export type Workspace = {
  workspace_id: string;
  label: string;
  path: string;
};

export type WorkspaceCatalog = {
  default_workspace_id: string;
  workspaces: Workspace[];
};

export type CliStatus = {
  model: string;
  runMode: RunMode;
  directory: string;
  workspace: Workspace;
  permissionMode: PermissionMode;
  executionMode: ExecutionMode;
  sessionId: string;
  contextUsed: number;
  contextLimit: number;
  versioningEnabled: boolean;
  backgroundAgents: number;
  backgroundCommands: number;
  activeSkill?: SkillDefinition;
};

export type SkillDefinition = {
  id: string;
  label: string;
  description: string;
};

export type ModelDefinition = {
  model_key: string;
  name: string;
  description?: string;
  supports_thinking?: boolean;
  fast_only?: boolean;
  deep_only?: boolean;
  context_window?: number;
};

export type TimelineItem = {
  id: string;
  kind: 'system' | 'user' | 'assistant' | 'tool' | 'thinking' | 'sub_agent';
  title?: string;
  summary?: string;
  body?: string;
  status?: 'running' | 'success' | 'failed' | 'cancelled';
  lines?: string[];
};

export type SlashCommandKind = 'immediate' | 'picker' | 'panel' | 'form';

export type SlashCommand = {
  name: string;
  description: string;
  aliases?: string[];
  kind: SlashCommandKind;
};
