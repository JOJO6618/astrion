// 定时任务页面的数据层：后端计划库是唯一权威；页面只保存每个计划的本地草稿。
export interface ScheduleDefinition {
  schema_version: number;
  id?: string;
  revision?: number;
  name: string;
  message: string;
  enabled: boolean;
  schedule: {
    type: string;
    timezone: string;
    times?: string[];
    date?: string;
    days?: number[];
    day?: number | 'last';
    every?: number;
    unit?: string;
    anchor?: string;
    expression?: string;
  };
  target: { workspace_id: string; conversation: { mode: string; id?: string } };
  configuration: {
    mode: string;
    model_key?: string;
    thinking?: boolean;
    reasoning_effort?: string | null;
    work_mode?: string;
    permission_mode?: string;
    execution_mode?: string;
    network_enabled?: boolean;
    ask_user?: boolean;
    worktree?: string | { branch_name: string };
  };
  goal: { mode: string; max_turns?: number; max_tokens?: number };
  policies: { busy: string; missed: string; approval_timeout_seconds?: number };
  notifications: { enabled: boolean };
}

export interface ScheduleRun {
  id: string;
  schedule_id: string;
  state: string;
  conversation_id: string | null;
  reason: string | null;
  summary: string | null;
  scheduled_for: string;
  created_at: string;
  finished_at: string | null;
  notification_status: string;
}

export interface SchedulePlan {
  definition: ScheduleDefinition;
  next_at: string | null;
  next_dates: string[];
  last_run: ScheduleRun | null;
  active_run: ScheduleRun | null;
}

export interface ScheduleOptions {
  workspaces: { id: string; label: string }[];
  models: { value: string; label: string; thinking: boolean; reasoning: boolean }[];
  timezones: string[];
  host_admin: boolean;
}

export interface ScheduleMenuItem {
  value: string;
  label: string;
  icon?: string;
  danger?: boolean;
  disabled?: boolean;
}

export interface ScheduleConversation {
  id: string;
  title: string;
}

export async function scheduleApi<T>(path = '', body?: unknown): Promise<T> {
  const response = await fetch(`/api/schedules${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  let result: { success?: boolean; error?: string; data?: T } = {};
  try {
    result = await response.json();
  } catch {
    // 非 JSON 响应按状态码报错
  }
  if (!response.ok || !result.success) throw new Error(result.error || String(response.status));
  return result.data as T;
}

/** 读取某工作区的对话列表（显式携带目标工作区，不依赖当前前台工作区）。 */
export async function fetchConversations(workspaceId: string): Promise<ScheduleConversation[]> {
  const query = new URLSearchParams({
    workspace_id: workspaceId,
    limit: '200',
    offset: '0',
    non_empty: '1'
  });
  const response = await fetch(`/api/conversations?${query}`, {
    headers: { 'X-Astrion-Workspace-Id': workspaceId }
  });
  const result = await response.json();
  if (!response.ok || !result.success) throw new Error(result.error || String(response.status));
  return (result.data?.conversations || []).map((item: { id: string; title?: string }) => ({
    id: String(item.id),
    title: String(item.title || item.id)
  }));
}

export function localTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

export function newDefinition(name: string, workspace: string): ScheduleDefinition {
  return {
    schema_version: 1,
    name,
    message: '',
    enabled: false,
    schedule: { type: 'daily', timezone: localTimezone(), times: ['09:00'] },
    target: { workspace_id: workspace, conversation: { mode: 'new' } },
    configuration: { mode: 'inherit' },
    goal: { mode: 'off' },
    policies: { busy: 'skip', missed: 'skip', approval_timeout_seconds: 3600 },
    notifications: { enabled: true }
  };
}

export const cloneDefinition = (value: ScheduleDefinition): ScheduleDefinition =>
  JSON.parse(JSON.stringify(value));

export const sameDefinition = (
  a: ScheduleDefinition | undefined,
  b: ScheduleDefinition | undefined
) => JSON.stringify(a) === JSON.stringify(b);

export function downloadText(text: string, filename: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/yaml;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const yamlFilename = (name: string) =>
  `${(name || 'schedule').replace(/[\\/:*?"<>|]/g, '-')}.yaml`;
