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
  const id = review.conversation_id.replace(/^conv_/, '');
  window.location.assign(
    `/${encodeURIComponent(id)}?workspace_id=${encodeURIComponent(review.workspace_id)}`
  );
}
