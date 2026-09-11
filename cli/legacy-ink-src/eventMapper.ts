import type { TimelineItem } from './types.js';
import { lastLines } from './format.js';

type ToolState = {
  timelineId: string;
  name: string;
  args: any;
  title: string;
  summary: string;
};

export type EventRenderState = {
  thinkingId?: string;
  thinkingStartedAt?: number;
  thinkingText: string;
  thinkingLines: string[];
  assistantId?: string;
  assistantText: string;
  tools: Map<string, ToolState>;
};

export function createEventRenderState(): EventRenderState {
  return { thinkingText: '', thinkingLines: [], assistantText: '', tools: new Map() };
}

export function reduceTaskEvent(items: TimelineItem[], state: EventRenderState, event: { type: string; data: any }): TimelineItem[] {
  const data = event.data || {};
  switch (event.type) {
    case 'thinking_start': {
      const timelineId = newId();
      state.thinkingId = timelineId;
      state.thinkingStartedAt = Date.now();
      state.thinkingText = '';
      state.thinkingLines = [];
      return [...items, { id: timelineId, kind: 'thinking', title: '思考中', status: 'running', lines: [] }];
    }
    case 'thinking_chunk': {
      const content = String(data.content || '');
      state.thinkingText += content;
      const lines = state.thinkingText.split(/\r?\n/).filter((line) => line.length > 0);
      state.thinkingLines = lines;
      if (!state.thinkingId) {
        state.thinkingId = newId();
        state.thinkingStartedAt = Date.now();
        state.thinkingText = content;
        items = [...items, { id: state.thinkingId, kind: 'thinking', title: '思考中', status: 'running', lines: [] }];
      }
      return patchItem(items, state.thinkingId, { lines: lastLines(state.thinkingLines, 5) });
    }
    case 'thinking_end': {
      if (!state.thinkingId) return items;
      const elapsed = state.thinkingStartedAt ? `${((Date.now() - state.thinkingStartedAt) / 1000).toFixed(1)}s` : '';
      const id = state.thinkingId;
      state.thinkingId = undefined;
      state.thinkingStartedAt = undefined;
      state.thinkingText = '';
      state.thinkingLines = [];
      return patchItem(items, id, {
        title: `思考完成 ${elapsed}`.trim(),
        status: 'success',
        body: undefined,
        summary: undefined,
        lines: [],
      });
    }
    case 'text_start': {
      const timelineId = newId();
      state.assistantId = timelineId;
      state.assistantText = '';
      return [...items, { id: timelineId, kind: 'assistant', status: 'running', body: '' }];
    }
    case 'text_chunk': {
      const content = String(data.content || '');
      if (!state.assistantId) {
        state.assistantId = newId();
        state.assistantText = '';
        items = [...items, { id: state.assistantId, kind: 'assistant', status: 'running', body: '' }];
      }
      state.assistantText += content;
      return patchItem(items, state.assistantId, { body: state.assistantText });
    }
    case 'text_end': {
      const full = String(data.full_content || state.assistantText || '');
      if (!state.assistantId) return full ? [...items, { id: newId(), kind: 'assistant', title: '完成', status: 'success', body: full }] : items;
      const id = state.assistantId;
      state.assistantId = undefined;
      state.assistantText = '';
      return patchItem(items, id, { status: 'success', body: full });
    }
    case 'tool_preparing':
    case 'tool_start': {
      const toolId = String(data.id || data.execution_id || data.preparing_id || newId());
      const name = String(data.name || 'tool');
      const args = data.arguments || {};
      const title = toolTitle(name, args, 'running');
      const summary = toolSummaryPreview(data, name, args, 'running');
      const existingKey = data.preparing_id ? String(data.preparing_id) : toolId;
      const existing = state.tools.get(existingKey);
      if (existing) {
        state.tools.delete(existingKey);
        state.tools.set(toolId, { ...existing, name, args, title, summary });
        return patchItem(items, existing.timelineId, {
          title,
          summary,
          status: 'running',
          body: toolInitialBody(name, args),
          lines: undefined,
        });
      }
      const timelineId = newId();
      state.tools.set(toolId, { timelineId, name, args, title, summary });
      return [...items, { id: timelineId, kind: 'tool', title, summary, status: 'running', body: toolInitialBody(name, args) }];
    }
    case 'tool_update_action':
    case 'update_action': {
      const toolId = String(data.id || data.execution_id || data.preparing_id || '');
      const tool = state.tools.get(toolId);
      if (!tool) return items;
      if (data.result !== undefined || data.status) {
        const status = String(data.status || '').toLowerCase() === 'completed' ? 'success' : String(data.status || '').toLowerCase() === 'failed' ? 'failed' : 'running';
        return patchItem(items, tool.timelineId, {
          title: toolTitle(tool.name, tool.args, status),
          summary: toolSummaryPreview(data, tool.name, tool.args, status),
          status,
          body: toolResultBody(tool.name, tool.args, data.result),
          lines: toolResultLines(tool.name, tool.args, data.result),
        });
      }
      return items;
    }
    case 'append_payload':
    case 'modify_payload': {
      return [...items, renderPayloadEvent(data)];
    }
    case 'tool_approval_required': {
      const approval = data.approval || data;
      return [...items, { id: newId(), kind: 'tool', title: `需要审批：${approval.tool_name || '工具调用'}`, status: 'running', body: approval.preview?.summary || '' }];
    }
    case 'tool_approval_resolved': {
      return [...items, { id: newId(), kind: 'tool', title: `审批${data.decision === 'approved' ? '通过' : '拒绝'}`, status: data.decision === 'approved' ? 'success' : 'failed', body: data.reason || '' }];
    }
    case 'task_complete': {
      return items;
    }
    case 'task_stopped': {
      return [...items, { id: newId(), kind: 'tool', title: '任务已停止', status: 'cancelled', body: data.message || '' }];
    }
    case 'error': {
      return [...items, { id: newId(), kind: 'tool', title: '任务失败', status: 'failed', body: data.message || data.error || '未知错误' }];
    }
    case 'token_update': {
      return items;
    }
    default:
      return items;
  }
}

function toolTitle(name: string, args: any, status: string): string {
  if (name === 'run_command') return `运行 ${args.command || ''}`.trim();
  if (name === 'write_file') return `Write ${args.file_path || args.path || ''} ${countWrite(args.content || '')}`.trim();
  if (name === 'edit_file') return `Edited ${args.file_path || args.path || ''} ${countEdit(args)}`.trim();
  if (name === 'read_file' || name === 'read_skill') return `阅读 ${args.path || args.file_path || ''}`.trim();
  if (name === 'terminal_session') return `${status === 'running' ? '创建' : '终端'} ${args.session_name || args.name || ''}`.trim();
  if (name === 'terminal_input') return `终端输入 ${args.command || args.input || ''}`.trim();
  return name;
}

function toolSummaryPreview(data: any, name: string, args: any, status: string): string {
  const intent = data?.intent_rendered || data?.intent_full || data?.intent || args?.intent_rendered || args?.intent_full || args?.intent || '';
  if (intent) return truncateSummaryPreview(firstLine(String(intent)));
  if (status === 'success') return '工具执行完成';
  if (status === 'failed') return `${name || '工具'} 执行失败`;
  if (data?.status === 'preparing') return `准备调用 ${name || '工具'}...`;
  return `正在调用 ${name || '工具'}...`;
}

function firstLine(text: string): string {
  return text.split(/\r?\n/)[0] || '';
}

function truncateSummaryPreview(text: string): string {
  const value = String(text || '');
  return value.length > 25 ? `${value.slice(0, 25)}...` : value;
}

function toolInitialBody(name: string, args: any): string {
  if (name === 'write_file' || name === 'edit_file') return '';
  return '';
}

function toolResultBody(name: string, args: any, result: any): string {
  if (typeof result === 'string') return summarizeText(result);
  if (!result) return '';
  if (name === 'run_command' || name === 'terminal_input') {
    const exitCode = result.exit_code ?? result.returncode;
    const text = String(result?.output || result?.stdout || result?.stderr || '').trim();
    if (exitCode !== undefined && exitCode !== 0) return text ? `exit code ${exitCode}` : `exit code ${exitCode}\n(no output)`;
    return text ? '' : '(no output)';
  }
  if (name === 'read_file' || name === 'read_skill') {
    const content = result.content || result.text || '';
    const count = String(content).split(/\r?\n/).filter(Boolean).length;
    const type = result.type || 'read';
    if (type === 'extract') return `提取了 ${count} 行`;
    return `读取了 ${count} 行`;
  }
  if (name === 'write_file') return '';
  if (name === 'edit_file') return '';
  if (result.error) return String(result.error);
  return result.success === false ? '失败' : '成功';
}

function toolResultLines(name: string, args: any, result: any): string[] | undefined {
  if (name === 'write_file') {
    return String(args.content || '').split('\n').map((line, idx) => `${idx + 1} +${line}`);
  }
  if (name === 'edit_file') {
    return editLines(args);
  }
  const text = typeof result === 'string' ? result : String(result?.output || result?.stdout || result?.content || result?.text || '');
  if (!text || name === 'read_file' || name === 'read_skill') return undefined;
  return summarizeLines(text);
}

function renderPayloadEvent(data: any): TimelineItem {
  const path = data.path || data.file_path || '';
  const lines: string[] = [];
  if (Array.isArray(data.old_lines)) lines.push(...data.old_lines.map((line: string, idx: number) => `${idx + 1} -${line}`));
  if (Array.isArray(data.new_lines)) lines.push(...data.new_lines.map((line: string, idx: number) => `${idx + 1} +${line}`));
  return { id: newId(), kind: 'tool', title: `Edited ${path}`, status: 'success', lines };
}

function countWrite(content: string): string {
  const added = content ? content.split('\n').length : 0;
  return `(+${added} -0)`;
}

function countEdit(args: any): string {
  const lines = editLines(args);
  const removed = lines.filter((line) => line.includes(' -')).length;
  const added = lines.filter((line) => line.includes(' +')).length;
  return `(+${added} -${removed})`;
}

function editLines(args: any): string[] {
  const replacements = Array.isArray(args.replacements) ? args.replacements : [];
  const result: string[] = [];
  const emitPair = (oldText: string, newText: string) => {
    const oldLines = oldText.replace(/\\n/g, '\n').split('\n');
    const newLines = newText.replace(/\\n/g, '\n').split('\n');
    oldLines.filter((line) => line.length > 0).forEach((line, idx) => result.push(`${idx + 1} -${line}`));
    newLines.filter((line) => line.length > 0).forEach((line, idx) => result.push(`${idx + 1} +${line}`));
  };
  if (replacements.length) {
    for (const rep of replacements) emitPair(String(rep.old_string || ''), String(rep.new_string || ''));
  }
  return result;
}

function summarizeText(text: string): string {
  const lines = text.split(/\r?\n/).filter((line) => line.trim().length > 0);
  return summarizeLines(lines.join('\n')).join('\n');
}

function summarizeLines(text: string): string[] {
  const lines = text.split(/\r?\n/).filter((line) => line.length > 0);
  if (lines.length <= 5) return lines;
  return [lines[0]!, lines[1]!, `… +${lines.length - 4} lines`, lines[lines.length - 2]!, lines[lines.length - 1]!];
}

function patchItem(items: TimelineItem[], id: string, patch: Partial<TimelineItem>): TimelineItem[] {
  return items.map((item) => item.id === id ? { ...item, ...patch } : item);
}

function newId(): string {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
