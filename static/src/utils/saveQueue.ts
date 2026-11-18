/**
 * 自动保存合并队列（设置／个性化等「改完即存」场景共用）。
 *
 * - 停手 idleMs 后保存；持续修改时，距本轮首次修改最多 maxWaitMs 也会保存一次。
 * - 同一时刻最多一个请求在途；在途期间的新修改等它返回后再补发，绝不丢弃。
 * - flush() 立即发出积压修改（离开页面、切换分区、失焦、需要即时生效的开关）。
 *
 * run 每次都应读取「当前最新状态」组装请求体，因此多次 schedule 只需最后发一次。
 * run 自行处理错误并返回结果；若抛出异常，异常会传给本次 flush 的调用方。
 */
export interface SaveRunContext {
  /** 页面卸载场景：请求需 keepalive，保证关闭窗口后仍能送达 */
  keepalive: boolean;
}

export interface SaveQueueOptions<R> {
  run: (context: SaveRunContext) => Promise<R>;
  idleMs?: number;
  maxWaitMs?: number;
}

export interface SaveQueue<R> {
  /** 记一次修改，按「停手 idle / 最长 maxWait」合并保存 */
  schedule: () => void;
  /** 立即保存积压修改；无积压时等待在途请求（若有）后返回其结果 */
  flush: (context?: Partial<SaveRunContext>) => Promise<R | undefined>;
  hasPending: () => boolean;
  /** 丢弃尚未发出的积压修改（不影响在途请求） */
  cancel: () => void;
}

export const DEFAULT_SAVE_IDLE_MS = 1000;
export const DEFAULT_SAVE_MAX_WAIT_MS = 3000;

export function createSaveQueue<R>(options: SaveQueueOptions<R>): SaveQueue<R> {
  const idleMs = options.idleMs ?? DEFAULT_SAVE_IDLE_MS;
  const maxWaitMs = options.maxWaitMs ?? DEFAULT_SAVE_MAX_WAIT_MS;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let firstPendingAt: number | null = null;
  let pending = false;
  let keepaliveNext = false;
  let inflight: Promise<R> | null = null;

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const start = (): Promise<R> => {
    clearTimer();
    pending = false;
    firstPendingAt = null;
    const context: SaveRunContext = { keepalive: keepaliveNext };
    keepaliveNext = false;
    // 异步启动，保证 inflight 先赋值、finally 后执行
    const current: Promise<R> = Promise.resolve()
      .then(() => options.run(context))
      .finally(() => {
        if (inflight === current) {
          inflight = null;
        }
        // 在途期间又有修改且计时器已到点（或被 flush 合并）：立即补发
        if (pending && timer === null && inflight === null) {
          void start().catch(() => undefined);
        }
      });
    inflight = current;
    return current;
  };

  const fire = () => {
    timer = null;
    // 有在途请求时由其 finally 补发
    if (!inflight && pending) {
      void start().catch(() => undefined);
    }
  };

  const schedule = () => {
    pending = true;
    const now = Date.now();
    if (firstPendingAt === null) {
      firstPendingAt = now;
    }
    clearTimer();
    const delay = Math.max(0, Math.min(idleMs, maxWaitMs - (now - firstPendingAt)));
    timer = setTimeout(fire, delay);
  };

  const flush = async (context?: Partial<SaveRunContext>): Promise<R | undefined> => {
    clearTimer();
    if (context?.keepalive && pending) {
      keepaliveNext = true;
    }
    if (inflight) {
      const previous = inflight;
      if (!pending) {
        return previous;
      }
      // 在途请求结束时其 finally 会因 pending 立即补发，等待补发结果
      await previous.catch(() => undefined);
      if (inflight) {
        return inflight;
      }
      return pending ? start() : undefined;
    }
    return pending ? start() : undefined;
  };

  const cancel = () => {
    clearTimer();
    pending = false;
    firstPendingAt = null;
    keepaliveNext = false;
  };

  return {
    schedule,
    flush,
    hasPending: () => pending,
    cancel
  };
}
