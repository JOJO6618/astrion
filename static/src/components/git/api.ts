import { useConversationTabsStore } from '@/stores/conversationTabs';
export interface Workspace {
  workspace_id: string;
  label: string;
  path: string;
}
export interface Repository {
  id: string;
  name: string;
  path: string;
  workspaces: Array<{ workspace_id: string; label: string; relative_path: string }>;
}
export interface Commit {
  sha: string;
  parents: string[];
  author: string;
  date: string;
  subject: string;
  message?: string;
}
export interface Branch {
  ref: string;
  name: string;
}
export interface ChangedFile {
  path: string;
  old_path: string;
  status: string;
  additions: number;
  deletions: number;
  binary: boolean;
}
export interface Comparison {
  kind: 'commit' | 'branch';
  head_sha: string;
  base_sha: string;
  commit?: Commit;
  base_ref?: string;
  head_ref?: string;
  base_tip?: string;
  files: ChangedFile[];
  additions: number;
  deletions: number;
}
export interface DiffLine {
  kind: 'add' | 'delete' | 'context' | 'hunk' | 'meta';
  text: string;
  old_line: number | null;
  new_line: number | null;
}
export interface Diff {
  file: ChangedFile;
  lines: DiffLine[];
  total_lines: number;
  has_more: boolean;
  next_offset: number;
}
export interface Review {
  conversation_id: string;
  workspace_id: string;
  task_id: string;
  head_sha: string;
  base_sha: string;
  kind: string;
  status: string;
  created_at: string;
  title?: string;
  warning?: string;
}
export interface Discovery {
  repositories: Repository[];
  workspaces: Workspace[];
  warnings: Array<{ workspace_id: string; error: string }>;
}

export async function gitRequest<T>(
  path: string,
  query: Record<string, string> = {},
  body?: unknown
): Promise<T> {
  const params = new URLSearchParams(query);
  const response = await fetch(
    `/api/project-git/${path}${params.size ? `?${params}` : ''}`,
    body === undefined
      ? undefined
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        }
  );
  const result = await response.json();
  if (!response.ok || !result.success) throw new Error(result.error || `${response.status}`);
  return result.data as T;
}

export function comparisonQuery(compare: Comparison): Record<string, string> {
  if (compare.kind === 'branch')
    return { base: compare.base_tip || compare.base_ref || '', head: compare.head_sha };
  const query: Record<string, string> = { sha: compare.head_sha };
  if (compare.commit?.parents.length) query.parent = compare.base_sha;
  return query;
}

export function openReview(review: Pick<Review, 'conversation_id' | 'workspace_id'>) {
  const tabs = useConversationTabsStore();
  // 桌面多视图：Git 页面视图固定绑定其页面标签，打开对话 = 发 open 命令。
  if (tabs.enabled) {
    void tabs
      .openConversationTab({
        conversationId: review.conversation_id,
        workspaceId: review.workspace_id
      })
      .catch(() => {});
    return;
  }
  const id = review.conversation_id.replace(/^conv_/, '');
  window.location.assign(
    `/${encodeURIComponent(id)}?workspace_id=${encodeURIComponent(review.workspace_id)}`
  );
}

/** 列表与署名行用的短时间：今年省略年份，例如「11-17 04:38」 */
export function shortDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleString(undefined, {
    year: sameYear ? undefined : 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}
