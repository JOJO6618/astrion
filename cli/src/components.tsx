// 渲染组件层：把 Block 列表渲染成 opentui 组件树
// 列规范：L0 块标记「• 」｜L2 参数/结果标记「└ 」｜L4 结果详情/流式输出/思考后续行
// 颜色规范：全部使用终端默认前景色；只有「└ 结果行 + 详情/输出内容」用 DIM 属性（微微淡，亮暗背景自适应）；
//           唯一例外是错误状态用红色。
import { useEffect, useMemo, type RefObject } from 'react';
import { RGBA, StyledText, TextAttributes, type CliRenderer, type TextareaRenderable } from '@opentui/core';
import { useRenderer } from '@opentui/react';
import { revealedText, type Block } from './timeline';

const DIM = TextAttributes.DIM;
const RED = '#e06c75';
// 终端默认前景色（ANSI 39）：白底自动黑字、黑底自动白字。opentui 不传 fg 时是写死的白色，必须显式传。
const FG = RGBA.defaultForeground();
// 输入栏内部第一个字符所在列 = 外层 paddingX(1) + 边框(1) + 边框内 paddingX(1) = 3。
// 输入栏下方的状态栏第一个字符与此列对齐（不顶格）。
const COMPOSER_TEXT_INDENT = 3;

// 亮暗自适应光标色，App 启动时经 resolveAdaptiveColors 检测后写入；渲染期读取。
// 拖选/复制走终端原生（useMouse=false），不再需要应用内 selection 配色。
export const adaptiveColors = {
  cursor: '#ffffff',
};

// 统一文本组件。
export function T({
  fg,
  attributes,
  children,
}: {
  fg?: RGBA | string;
  attributes?: number;
  children?: React.ReactNode;
}) {
  return (
    <text fg={fg ?? FG} attributes={attributes}>
      {children}
    </text>
  );
}
export function blinkDot(elapsed: number): string {
  return Math.floor(elapsed / 450) % 2 === 0 ? '•' : '◦';
}

function UserBlock({ block }: { block: Extract<Block, { kind: 'user' }> }) {
  return (
    <box flexDirection="row" marginTop={1}>
      <T fg={FG}>{'› '}</T>
      <T fg={FG}>{block.text}</T>
    </box>
  );
}

// 引导消息：运行中注入当前轮的用户消息（带 [引导] 标记，对应 Web 端 userHeaderGuide 徽标）
function GuideBlock({ block }: { block: Extract<Block, { kind: 'guide' }> }) {
  return (
    <box flexDirection="row" marginTop={1}>
      <T fg={FG}>{'› '}</T>
      <T fg={FG} attributes={DIM}>{'[引导] '}</T>
      <T fg={FG}>{block.text}</T>
    </box>
  );
}

// 系统行：命令执行结果（/model 切换、/compact 等），整行 DIM
function SystemBlock({ block }: { block: Extract<Block, { kind: 'system' }> }) {
  return (
    <box marginTop={1}>
      <T fg={FG} attributes={DIM}>{`• ${block.text}`}</T>
    </box>
  );
}

function ThinkingBlock({
  block,
  elapsed,
  expanded,
}: {
  block: Extract<Block, { kind: 'thinking' }>;
  elapsed: number;
  expanded: boolean;
}) {
  const revealed = revealedText(block.full, block.revealStart, block.cps, elapsed);
  const collapsed = elapsed >= block.collapseAt;

  if (!collapsed) {
    const streaming = revealed.length < block.full.length;
    const dot = streaming ? blinkDot(elapsed) : '•';
    const lines = revealed.split('\n').filter((l) => l.length > 0);
    return (
      <box flexDirection="column" marginTop={1}>
        <T fg={FG}>{`${dot} 思考中`}</T>
        {lines.map((l, i) => (
          <T key={i} fg={FG} attributes={DIM}>{`${i === 0 ? '  └ ' : '    '}${l}`}</T>
        ))}
      </box>
    );
  }

  const all = block.full.split('\n').filter((l) => l.length > 0);
  return (
    <box flexDirection="column" marginTop={1}>
      <T fg={FG}>{`• ${all[0] ?? ''}`}</T>
      {expanded
        ? all.slice(1).map((l, i) => (
            <T key={i} fg={FG} attributes={DIM}>{`${i === 0 ? '  └ ' : '    '}${l}`}</T>
          ))
        : null}
    </box>
  );
}

function ToolBlock({ block, elapsed }: { block: Extract<Block, { kind: 'tool' }>; elapsed: number }) {
  const running = block.status === 'running';
  const failed = block.status === 'error';
  const dot = running ? blinkDot(elapsed) : '•';
  const streamOutput = running && block.stream
    ? block.stream.lines.slice(0, Math.max(0, Math.floor((elapsed - block.stream.startAt) / block.stream.interval)))
    : [];

  return (
    <box flexDirection="column" marginTop={1}>
      {failed ? <T fg={RED}>{`${dot} ${block.title}`}</T> : <T fg={FG}>{`${dot} ${block.title}`}</T>}
      {block.params.map((p) => (
        <T key={p} fg={FG}>{`  ${p}`}</T>
      ))}
      {streamOutput.map((l, i) => (
        <T key={i} fg={FG} attributes={DIM}>{`    ${l}`}</T>
      ))}
      {!running && block.result != null ? (
        <>
          <T fg={FG} attributes={DIM}>{`  └ ${block.result}`}</T>
          {block.resultLines.map((l, i) => (
            <T key={i} fg={FG} attributes={DIM}>{`    ${l}`}</T>
          ))}
        </>
      ) : null}
    </box>
  );
}

function AssistantBlock({ block, elapsed }: { block: Extract<Block, { kind: 'assistant' }>; elapsed: number }) {
  const revealed = revealedText(block.full, block.revealStart, block.cps, elapsed);
  return (
    <box flexDirection="column" marginTop={1}>
      {revealed.split('\n').map((l, i) => (
        <T key={i} fg={FG}>{l}</T>
      ))}
    </box>
  );
}

export function BlockView({
  block,
  elapsed,
  thinkingExpanded,
}: {
  block: Block;
  elapsed: number;
  thinkingExpanded: boolean;
}) {
  switch (block.kind) {
    case 'user':
      return <UserBlock block={block} />;
    case 'guide':
      return <GuideBlock block={block} />;
    case 'system':
      return <SystemBlock block={block} />;
    case 'thinking':
      return <ThinkingBlock block={block} elapsed={elapsed} expanded={thinkingExpanded} />;
    case 'tool':
      return <ToolBlock block={block} elapsed={elapsed} />;
    case 'assistant':
      return <AssistantBlock block={block} elapsed={elapsed} />;
  }
}

// 提前输入队列：运行中 Enter 发送的消息在此平铺，当前轮结束后自动按序发送；Ctrl+G 可将队首提升为引导
export function QueueList({ queue }: { queue: string[] }) {
  return (
    <box flexShrink={0} flexDirection="column" paddingLeft={COMPOSER_TEXT_INDENT} paddingRight={1}>
      <T fg={FG} attributes={DIM}>{`队列 ${queue.length} 条   结束后自动发送   Ctrl+G 引导队首`}</T>
      {queue.map((q, i) => (
        <T key={`${i}-${q}`} fg={FG} attributes={i === 0 ? 0 : DIM}>
          {`› ${q}${i === 0 ? '（下一个）' : ''}`}
        </T>
      ))}
    </box>
  );
}

export function HintBar() {
  return (
    <box flexShrink={0} paddingX={1}>
      <T fg={FG}>Astrion   [/] 命令菜单   [Ctrl+G] 引导   [Ctrl+E] 展开思考   [Esc] 退出</T>
    </box>
  );
}

interface AdaptiveColors {
  cursor: string;
}

// 终端亮暗检测 → 光标色（OSC 12 只认具体色）。
// 白底：深色光标；黑底：白光标。
// 检测链：1. renderer.themeMode（DEC 997）；2. 兜底 getPalette() 查 OSC 11 背景亮度；3. 都失败按黑底惯例。
export async function resolveAdaptiveColors(renderer: CliRenderer): Promise<AdaptiveColors> {
  const light: AdaptiveColors = { cursor: '#111111' };
  const dark: AdaptiveColors = { cursor: '#ffffff' };
  if (renderer.themeMode === 'light') return light;
  if (renderer.themeMode === 'dark') return dark;
  try {
    const palette = await renderer.getPalette({ timeout: 500 });
    const hex = palette.defaultBackground;
    if (!hex) return dark;
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.5 ? light : dark;
  } catch {
    return dark;
  }
}

export function Composer({
  finished,
  textareaRef,
  onSubmitMessage,
  onContentChange,
  placeholderOverride,
}: {
  finished: boolean;
  textareaRef: RefObject<TextareaRenderable | null>;
  onSubmitMessage: () => void;
  onContentChange: () => void;
  /** 面板占用输入栏时的提示覆盖（如 /path 输入授权路径） */
  placeholderOverride?: string;
}) {
  const renderer = useRenderer();
  useEffect(() => {
    // 显式聚焦：opentui 不做自动焦点，必须手动 focus() 才能输入
    textareaRef.current?.focus();
  }, [renderer, textareaRef]);
  // 提示文字变淡：StyledText 形式的 placeholder 直接采用 chunk 自带样式（placeholderColor 仅对字符串形式生效），
  // 用默认前景色 + DIM，与「└ 内容微微淡」同一变淡语义。
  const placeholder = useMemo(
    () =>
      new StyledText([
        {
          __isChunk: true as const,
          text:
            placeholderOverride ??
            (finished ? '输入消息…   / 打开命令菜单' : '运行中   Enter 排队   Ctrl+G 引导   / 打开菜单'),
          fg: FG,
          attributes: DIM,
        },
      ]),
    [finished, placeholderOverride],
  );
  return (
    <box flexShrink={0} flexDirection="column" paddingX={1} marginTop={1}>
      <box borderStyle="single" borderColor={FG} paddingX={1}>
        <textarea
          ref={textareaRef}
          width="100%"
          minHeight={1}
          maxHeight={6}
          placeholder={placeholder}
          textColor={FG}
          focusedTextColor={FG}
          cursorColor={adaptiveColors.cursor}
          cursorStyle={{ style: 'line', blinking: true }}
          onSubmit={onSubmitMessage}
          onContentChange={onContentChange}
          keyBindings={[
            // Enter=发送，Shift+Enter=换行（对齐 web 端；opentui textarea 默认相反：return=换行、meta+return=提交）
            { name: 'return', action: 'submit' },
            { name: 'return', shift: true, action: 'newline' },
          ]}
        />
      </box>
    </box>
  );
}

export function StatusBar({
  model,
  thinking,
  effortLabel,
  workMode,
  permMode,
  execEnv,
  contextUsage,
}: {
  model: string;
  thinking: boolean;
  /** 推理强度档位标签（思考模式时拼在模型后） */
  effortLabel: string;
  workMode: string;
  permMode: string;
  execEnv: string;
  contextUsage: string;
}) {
  // 第一个字符与输入栏内部第一个字符对齐（见 COMPOSER_TEXT_INDENT）
  // 布局：左组 = 模型+模式/强度、工作模式、权限模式、执行环境（空格分栏，不用 ·）；上下文用量固定最右
  const modelPart = thinking ? `${model} 思考 ${effortLabel}` : `${model} 快速`;
  return (
    <box flexShrink={0} width="100%" flexDirection="row" justifyContent="space-between" paddingLeft={COMPOSER_TEXT_INDENT} paddingRight={1}>
      <T fg={FG}>{`${modelPart}   ${workMode}   ${permMode}   ${execEnv}`}</T>
      <T fg={FG}>{contextUsage}</T>
    </box>
  );
}
