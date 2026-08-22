/**
 * 工作流 REST API 封装（对应 server/workflow_page.py 的 /api/workflows 端点）。
 */
import type { WorkflowDef } from './workflowModel';

/** 列表接口返回的轻量元信息（不含节点明细） */
export interface WorkflowListItem {
  name: string;
  description: string;
  source: 'builtin' | 'user';
  updatedAt: string;
  nodeCount: number;
}

async function parseError(resp: Response, fallback: string): Promise<Error> {
  try {
    const data = await resp.json();
    if (data && typeof data.error === 'string' && data.error) {
      return new Error(data.error);
    }
  } catch {
    // 响应体不是 JSON 时走兜底文案
  }
  return new Error(`${fallback}（HTTP ${resp.status}）`);
}

export async function listWorkflows(): Promise<WorkflowListItem[]> {
  const resp = await fetch('/api/workflows');
  if (!resp.ok) throw await parseError(resp, '加载工作流列表失败');
  const data = await resp.json();
  return Array.isArray(data.workflows) ? (data.workflows as WorkflowListItem[]) : [];
}

export async function loadWorkflow(name: string): Promise<WorkflowDef> {
  const resp = await fetch(`/api/workflows/${encodeURIComponent(name)}`);
  if (!resp.ok) throw await parseError(resp, '加载工作流失败');
  const data = await resp.json();
  return data.workflow as WorkflowDef;
}

export async function saveWorkflow(wf: WorkflowDef): Promise<void> {
  const resp = await fetch(`/api/workflows/${encodeURIComponent(wf.name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workflow: wf }),
  });
  if (!resp.ok) throw await parseError(resp, '保存工作流失败');
}

export async function deleteWorkflow(name: string): Promise<void> {
  const resp = await fetch(`/api/workflows/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
  if (!resp.ok) throw await parseError(resp, '删除工作流失败');
}
