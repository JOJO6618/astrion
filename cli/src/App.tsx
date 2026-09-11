// 主界面：时间线（真实事件流）+ 交互区（/ 菜单与面板）+ 输入栏 + 状态栏
// 与 demo 的差异：无演示播放（消息来自 Gateway 任务事件流）；审批事件自动弹出；
// 运行中 Enter=排队（本地队列，运行结束后自动发送，对齐 Web tryAutoSendRuntimeQueuedMessages）。
import { useKeyboard, useRenderer } from '@opentui/react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { TextareaRenderable } from '@opentui/core';
import {
  BlockView,
  Composer,
  HintBar,
  QueueList,
  StatusBar,
  adaptiveColors,
  resolveAdaptiveColors,
} from './components';
import { SlashMenu } from './menu';
import { useSlashMenu } from './menuState';
import { EFFORT_META } from './data';
import { t } from './i18n';
import { ChatRuntime } from './runtime';
import { CPS_INSTANT, THINK_COLLAPSE_DELAY, type Block, type TimelineApi } from './timeline';
import type { BootResult } from './boot';

const TICK_MS = 100;
/** 与 menuState 中一致的 / token 检测（提交拦截用） */
const SLASH_TOKEN_RE = /(^|[ \n])\/([^\s/]*)$/;

let idCounter = 0;

function makeApi(setBlocks: React.Dispatch<React.SetStateAction<Block[]>>, getElapsed: () => number): TimelineApi {
  let thinkingId: number | null = null;
  let assistantId: number | null = null;

  const patchById = (id: number, fn: (b: Block) => Block) => {
    setBlocks((prev) => prev.map((b) => (b.id === id ? fn(b) : b)));
  };

  const api: TimelineApi = {
    addUser(text) {
      setBlocks((prev) => [...prev, { kind: 'user', id: idCounter++, text }]);
    },
    addGuide(text) {
      setBlocks((prev) => [...prev, { kind: 'guide', id: idCounter++, text }]);
    },
    addSystem(text) {
      setBlocks((prev) => [...prev, { kind: 'system', id: idCounter++, text }]);
    },
    startThinking() {
      const id = idCounter++;
      thinkingId = id;
      setBlocks((prev) => [
        ...prev,
        { kind: 'thinking', id, full: '', revealStart: 0, cps: CPS_INSTANT, collapseAt: Number.POSITIVE_INFINITY },
      ]);
      return id;
    },
    appendThinking(chunk) {
      if (thinkingId == null) api.startThinking(); // 容错：无 start 直接来 chunk 时隐式建块
      const id = thinkingId!;
      patchById(id, (b) => (b.kind === 'thinking' ? { ...b, full: b.full + chunk } : b));
    },
    endThinking() {
      if (thinkingId == null) return;
      const id = thinkingId;
      thinkingId = null;
      const collapseAt = getElapsed() + THINK_COLLAPSE_DELAY;
      patchById(id, (b) => (b.kind === 'thinking' ? { ...b, collapseAt } : b));
    },
    startAssistant() {
      const id = idCounter++;
      assistantId = id;
      setBlocks((prev) => [...prev, { kind: 'assistant', id, full: '', revealStart: 0, cps: CPS_INSTANT }]);
      return id;
    },
    appendAssistant(chunk) {
      if (assistantId == null) api.startAssistant();
      const id = assistantId!;
      patchById(id, (b) => (b.kind === 'assistant' ? { ...b, full: b.full + chunk } : b));
    },
    startTool(init) {
      const id = idCounter++;
      setBlocks((prev) => [
        ...prev,
        { kind: 'tool', id, title: init.title, params: init.params ?? [], status: 'running', resultLines: [] },
      ]);
      return id;
    },
    finishTool(result, resultLines = []) {
      setBlocks((prev) => patchLastTool(prev, { status: 'done', result, resultLines }));
    },
    failTool(result, resultLines = []) {
      setBlocks((prev) => patchLastTool(prev, { status: 'error', result, resultLines }));
    },
  };
  return api;
}

/** 落定最近一个运行中的工具块（单线程模型顺序执行；按 tool_call_id 精确对应留待后续） */
function patchLastTool(blocks: Block[], patch: Partial<Extract<Block, { kind: 'tool' }>>): Block[] {
  for (let i = blocks.length - 1; i >= 0; i--) {
    const b = blocks[i]!;
    if (b.kind === 'tool' && b.status === 'running') {
      const next = blocks.slice();
      next[i] = { ...b, ...patch } as Block;
      return next;
    }
  }
  return blocks;
}

export function App({ boot }: { boot: BootResult }) {
  const renderer = useRenderer();
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [queue, setQueue] = useState<string[]>([]);
  const [running, setRunning] = useState(false);

  const elapsedRef = useRef(0);
  elapsedRef.current = elapsed;
  const apiRef = useRef<TimelineApi | null>(null);
  apiRef.current = useMemo(() => makeApi(setBlocks, () => elapsedRef.current), []);
  const textareaRef = useRef<TextareaRenderable>(null);
  const width = (renderer as unknown as { width?: number }).width ?? 78;

  // 审批裁决 → Gateway（runtime 在下方创建，经 ref 互指解开循环依赖）
  const runtimeRef = useRef<ChatRuntime | null>(null);
  const menu = useSlashMenu({
    textareaRef,
    apiRef,
    destroy: () => {
      runtimeRef.current?.destroy();
      renderer.destroy();
      process.exit(0);
    },
    width,
    elapsed,
    onApprovalAction: (action, approval) => {
      if (action === 'reject') {
        void runtimeRef.current?.decideApproval(approval.id, 'rejected');
        apiRef.current?.addSystem(`${t('approval.rejected')}${approval.toolLabel}`);
      } else {
        void runtimeRef.current?.decideApproval(approval.id, 'approved');
        apiRef.current?.addSystem(
          action === 'unrestricted'
            ? `${t('approval.switched')}${approval.toolLabel}`
            : `${t('approval.approved')}${approval.toolLabel}`,
        );
      }
    },
  });
  const menuRef = useRef(menu);
  menuRef.current = menu;

  // 会话运行时：发消息/事件轮询/审批自动弹出（只创建一次）
  if (!runtimeRef.current) {
    const rt = new ChatRuntime(boot.gateway, {
      api: apiRef.current,
      onRunningChange: setRunning,
      onApprovalRequired: (a) => menuRef.current.openApproval(a),
      onSystemMessage: (text) => apiRef.current?.addSystem(text),
      tr: t,
    });
    rt.conversationId = boot.conversationId;
    runtimeRef.current = rt;
  }

  const runningRef = useRef(running);
  runningRef.current = running;
  const queueRef = useRef(queue);
  queueRef.current = queue;

  // 启动时亮暗检测：结果写入 adaptiveColors（module 级），setState 触发一次重渲染
  const [, setColorsTick] = useState(0);
  useEffect(() => {
    void resolveAdaptiveColors(renderer).then((c) => {
      adaptiveColors.cursor = c.cursor;
      setColorsTick((v) => v + 1);
    });
  }, [renderer]);

  // 光标常驻输入栏（点击/拖选其他区域会切走焦点，主循环强制恢复）
  useEffect(() => {
    const timer = setInterval(() => {
      const ta = textareaRef.current;
      if (ta && !ta.focused) ta.focus();
    }, TICK_MS);
    return () => clearInterval(timer);
  }, []);

  // app 时钟：驱动思考折叠/闪烁点/revealedText（无演示播放）
  useEffect(() => {
    const startedAt = Date.now();
    const timer = setInterval(() => setElapsed(Date.now() - startedAt), TICK_MS);
    return () => clearInterval(timer);
  }, []);

  // 运行结束后，提前输入队列逐条自动发送
  useEffect(() => {
    if (running || queue.length === 0) return;
    const timer = setTimeout(() => {
      const [head, ...rest] = queue;
      if (head) void runtimeRef.current?.send(head);
      setQueue(rest);
    }, 300);
    return () => clearTimeout(timer);
  }, [running, queue]);

  // 引导：输入框有字=直接引导注入当前轮；无字=提升队首为引导
  // TODO(gateway)：接 POST /api/tasks/<id>/runtime_guidance（当前仅本地上屏，服务端注入后续接）
  const handleGuide = () => {
    if (!runningRef.current) return;
    const text = (textareaRef.current?.plainText ?? '').trim();
    if (text) {
      textareaRef.current?.setText('');
      apiRef.current?.addGuide(text);
      return;
    }
    const [head, ...rest] = queueRef.current;
    if (head) {
      setQueue(rest);
      apiRef.current?.addGuide(head);
    }
  };

  useKeyboard((key) => {
    // 菜单打开时 ↑↓/←→/Enter/Esc/删除键 优先给交互区面板
    if (menu.handleKey(key)) return;
    if (key.name === 'escape' || (key.ctrl && key.name === 'c')) {
      runtimeRef.current?.destroy();
      renderer.destroy();
      process.exit(0);
    }
    if (key.ctrl && key.name === 'e') {
      key.preventDefault();
      setExpanded((v) => !v);
    }
    if (key.ctrl && key.name === 'g') {
      key.preventDefault();
      handleGuide();
    }
  });

  // Enter（菜单关闭时）：运行中=提前输入排队；空闲=直接发送
  const handleSubmit = () => {
    const text = (textareaRef.current?.plainText ?? '').trim();
    if (!text || SLASH_TOKEN_RE.test(text)) return;
    textareaRef.current?.setText('');
    if (runningRef.current) {
      setQueue((q) => [...q, text]);
    } else {
      void runtimeRef.current?.send(text);
    }
  };

  const sf = menu.statusFields;

  return (
    <box flexDirection="column" width="100%" height="100%">
      <HintBar />
      <scrollbox flexGrow={1} stickyScroll stickyStart="bottom" verticalScrollbarOptions={{ visible: false }}>
        <box flexDirection="column" paddingX={1}>
          {blocks.map((block) => (
            <BlockView key={block.id} block={block} elapsed={elapsed} thinkingExpanded={expanded} />
          ))}
        </box>
      </scrollbox>
      {queue.length > 0 ? <QueueList queue={queue} /> : null}
      {menu.slashMenuProps ? <SlashMenu {...menu.slashMenuProps} /> : null}
      <Composer
        finished={!running}
        textareaRef={textareaRef}
        onSubmitMessage={handleSubmit}
        onContentChange={menu.handleContentChange}
        placeholderOverride={menu.inputPlaceholder}
      />
      <StatusBar
        model={sf.model}
        thinking={sf.thinking}
        effortLabel={EFFORT_META[sf.effort].label}
        workMode={sf.workMode}
        permMode={sf.permMode}
        execEnv={sf.execEnv}
        contextUsage={sf.contextUsage}
      />
    </box>
  );
}
