// 时间线：消息流 Block 类型与 TimelineApi（正式版：真实事件流驱动，流式追加）
// 渲染层（components.tsx）只读 Block 列表；revealedText 保留供思考/助手块的渐进显示
// （真实流式下 cps 传 CPS_INSTANT 即时全显，动画节奏由事件到达速度决定）。

export type ToolStatus = 'running' | 'done' | 'error';

/** 即时全显的 cps（真实流式：文本已到即显示，不做演示式逐字播放） */
export const CPS_INSTANT = 1e9;

export type Block =
  | { kind: 'user'; id: number; text: string }
  | { kind: 'guide'; id: number; text: string } // 引导消息：运行中注入当前轮的用户消息
  | { kind: 'system'; id: number; text: string } // 系统行：命令执行结果等（/model 切换、审批操作回执）
  | { kind: 'thinking'; id: number; full: string; revealStart: number; cps: number; collapseAt: number }
  | {
      kind: 'tool';
      id: number;
      title: string;
      params: string[];
      status: ToolStatus;
      stream?: { lines: string[]; startAt: number; interval: number };
      result?: string;
      resultLines: string[];
    }
  | { kind: 'assistant'; id: number; full: string; revealStart: number; cps: number };

export interface TimelineApi {
  /** 清空全部块（/session 切换对话时用） */
  reset(): void;
  addUser(text: string): void;
  addGuide(text: string): void;
  addSystem(text: string): void;
  /** 思考块：start 建块（cps=CPS_INSTANT），append 追加 chunk，end 标记折叠计时 */
  startThinking(): number;
  appendThinking(chunk: string): void;
  endThinking(): void;
  /** 助手块：start 建块，append 追加 chunk（end 由 task_complete 隐含） */
  startAssistant(): number;
  appendAssistant(chunk: string): void;
  /** 工具块：start 建块（running），finish/fail 落定结果 */
  startTool(init: { title: string; params?: string[] }): number;
  finishTool(result: string, resultLines?: string[]): void;
  failTool(result: string, resultLines?: string[]): void;
}

export const THINK_COLLAPSE_DELAY = 1200;

/** 由 elapsed 派生流式可见文本（纯函数；真实流式 cps=CPS_INSTANT 时等价全文） */
export function revealedText(full: string, revealStart: number, cps: number, elapsed: number): string {
  const count = Math.floor(((elapsed - revealStart) / 1000) * cps);
  return full.slice(0, Math.max(0, count));
}
