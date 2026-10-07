// 纯协议函数：不读写 store、不触发 IO；请求所有权只由 task.ts 管理。
export type EventHandler = (event: any) => void;
export type TaskStatus =
  | 'idle'
  | 'pending'
  | 'running'
  | 'cancel_requested'
  | 'succeeded'
  | 'failed'
  | 'canceled'
  | 'stopped';

export interface CreateTaskOptions {
  model_key?: string | null;
  run_mode?: 'fast' | 'thinking' | null;
  thinking_mode?: boolean | null;
  message_source?: string | null;
  queued_message_id?: string | null;
  goal_mode?: boolean | null;
  skill_refs?: Array<{ name?: string; path: string }> | null;
  files?: string[] | null;
  eventHandler?: EventHandler;
}

export function createTaskPayload(
  message: string,
  images: any[],
  videos: any[],
  conversationId: string | null,
  options: CreateTaskOptions
) {
  return {
    message,
    images,
    videos,
    conversation_id: conversationId,
    model_key: options.model_key ?? undefined,
    run_mode: options.run_mode ?? undefined,
    thinking_mode: typeof options.thinking_mode === 'boolean' ? options.thinking_mode : undefined,
    message_source: options.message_source ?? undefined,
    queued_message_id: options.queued_message_id ?? undefined,
    goal_mode: options.goal_mode === true ? true : undefined,
    skill_refs: Array.isArray(options.skill_refs) ? options.skill_refs : undefined,
    files: Array.isArray(options.files) && options.files.length ? options.files : undefined
  };
}

export function snapshotTaskState(task: any, nextOffset: number) {
  if (!task?.task_id) throw new TypeError('Snapshot task_id is required');
  if (!Number.isSafeInteger(nextOffset) || nextOffset < 0) {
    throw new RangeError('Snapshot nextOffset must be a non-negative absolute cursor');
  }
  return {
    currentTaskId: task.task_id,
    cursorTaskId: task.task_id,
    taskStatus: task.status || 'running',
    taskCreatedAt: task.created_at ?? null,
    taskUpdatedAt: task.updated_at ?? null,
    lastEventIndex: nextOffset,
    pollingError: null,
    pollingErrorCount: 0,
    pollingWarned: false
  };
}

export function runtimeQueueSnapshot(data: any, taskId: string) {
  const messages = Array.isArray(data.runtime_queued_messages) ? data.runtime_queued_messages : [];
  const key = JSON.stringify(
    messages
      .map((item: any) => [String(item?.id || ''), String(item?.text || '')])
      .concat([[String(!!data.runtime_queue_paused)]])
  );
  return {
    key,
    messages,
    event: {
      type: 'runtime_queue_sync',
      data: {
        task_id: data.task_id || taskId,
        conversation_id: data.conversation_id || null,
        paused: !!data.runtime_queue_paused,
        messages
      }
    }
  };
}

export function terminalFallbackEvent(
  data: any,
  taskId: string,
  messages: any[],
  sawComplete: boolean,
  sawStopped: boolean
) {
  const identity = {
    task_id: data.task_id || taskId,
    conversation_id: data.conversation_id || null,
    synthetic: true
  };
  if (data.status === 'canceled' && !sawStopped) {
    return { type: 'task_stopped', data: identity };
  }
  if (data.status === 'succeeded' && !sawComplete) {
    return {
      type: 'task_complete',
      data: {
        ...identity,
        task_type: data.task_type,
        preserve_pending_messages: !!data.runtime_queue_paused,
        runtime_queued_messages: messages,
        has_running_sub_agents: false,
        has_running_background_commands: false
      }
    };
  }
  return null;
}
