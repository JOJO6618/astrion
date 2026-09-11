import React from 'react';
import { Box, Text, useStdout, type DOMElement } from 'ink';
import ImeTextInput from './ImeTextInput.js';
import type { CliStatus, SlashCommand, SkillDefinition, TimelineItem, Workspace } from './types.js';
import { formatContext, shortPath } from './format.js';

const SHOW_THINKING_DETAILS = false;

export function StatusLine({ status }: { status: CliStatus }) {
  const skill = status.activeSkill ? `  skill:${status.activeSkill.label}` : '';
  const background = [
    status.backgroundAgents > 0 ? `${status.backgroundAgents}后台智能体` : '',
    status.backgroundCommands > 0 ? `${status.backgroundCommands}后台指令` : '',
  ].filter(Boolean).join(' · ');
  const left = `${status.model} · ${status.workspace.label} · ${status.permissionMode} · ${status.executionMode}${background ? ` · ${background}` : ''} · 版本控制:${status.versioningEnabled ? '开' : '关'}${skill}`;
  const right = formatContext(status.contextUsed, status.contextLimit);
  return (
    <Box justifyContent="space-between" width="100%">
      <Text color="gray">{left}</Text>
      <Text color="gray">{right}</Text>
    </Box>
  );
}

export function Composer(
  {
    value,
    disabled,
    marginTop = 1,
    cursorYOffset = 0,
    refreshTick = 0,
    onChange,
    onSubmit,
  }: {
    value: string;
    disabled?: boolean;
    marginTop?: number;
    cursorYOffset?: number;
    refreshTick?: number;
    onChange: (value: string) => void;
    onSubmit: (value: string) => void | Promise<void>;
  },
) {
  const contentRef = React.useRef<DOMElement>(null);

  return (
    <Box flexDirection="column" marginTop={marginTop}>
      <Box borderStyle="single" borderColor={disabled ? 'gray' : 'cyan'} paddingX={1} flexDirection="column">
        <Box ref={contentRef}>
          <Text color="cyan">› </Text>
          <ImeTextInput
            value={value}
            disabled={disabled}
            contentRef={contentRef}
            cursorYOffset={cursorYOffset}
            refreshTick={refreshTick}
            onChange={onChange}
            onSubmit={onSubmit}
          />
        </Box>
      </Box>
    </Box>
  );
}

function displayWidth(text: string): number {
  let width = 0;
  for (const char of Array.from(text)) {
    const code = char.codePointAt(0) || 0;
    if (code === 0) continue;
    if (code < 32 || (code >= 0x7f && code < 0xa0)) continue;
    width += isWideCodePoint(code) ? 2 : 1;
  }
  return width;
}

function isWideCodePoint(code: number): boolean {
  return (
    code >= 0x1100 && (
      code <= 0x115f ||
      code === 0x2329 ||
      code === 0x232a ||
      (code >= 0x2e80 && code <= 0xa4cf && code !== 0x303f) ||
      (code >= 0xac00 && code <= 0xd7a3) ||
      (code >= 0xf900 && code <= 0xfaff) ||
      (code >= 0xfe10 && code <= 0xfe19) ||
      (code >= 0xfe30 && code <= 0xfe6f) ||
      (code >= 0xff00 && code <= 0xff60) ||
      (code >= 0xffe0 && code <= 0xffe6) ||
      (code >= 0x1f300 && code <= 0x1f64f) ||
      (code >= 0x1f900 && code <= 0x1f9ff) ||
      (code >= 0x20000 && code <= 0x3fffd)
    )
  );
}

export function WelcomePanel({ status }: { status: CliStatus }) {
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text color="gray">Astrion</Text>
      <Box borderStyle="round" borderColor="gray" paddingX={2} paddingY={1} flexDirection="column">
        <Text bold>Welcome back!</Text>
        <Text color="gray">{status.model} · {status.runMode}</Text>
        <Text color="gray">{shortPath(status.directory)}</Text>
      </Box>
    </Box>
  );
}

export function Timeline({ items, pulseTick = 0, width = 78 }: { items: TimelineItem[]; pulseTick?: number; width?: number }) {
  return (
    <Box flexDirection="column">
      {items.map((item) => (
        <TimelineRow key={item.id} item={item} pulseTick={pulseTick} />
      ))}
    </Box>
  );
}

function TimelineRow({ item, pulseTick }: { item: TimelineItem; pulseTick: number }) {
  const separator = useFullWidthSeparator();
  if (item.kind === 'user') {
    const lines = String(item.body || '').split(/\r?\n/);
    return (
      <Box flexDirection="column" marginTop={1}>
        <Box borderStyle="single" borderColor="gray" paddingX={1} flexDirection="column">
          {lines.map((line, index) => (
            <Text key={`${item.id}-user-${index}`}>
              {index === 0 ? <Text color="cyan">› </Text> : <Text>  </Text>}
              <Text>{line}</Text>
            </Text>
          ))}
        </Box>
      </Box>
    );
  }
  if (item.kind === 'assistant') {
    return (
      <Box flexDirection="column" marginTop={1}>
        <Text color="gray">{separator}</Text>
        <Text> </Text>
        {wrapIndentedText(String(item.body || ''), 2, separator.length).map((line, index) => (
          <Text key={`${item.id}-assistant-${index}`}>{line}</Text>
        ))}
        {item.status === 'success' ? <><Text> </Text><Text color="gray">{separator}</Text></> : null}
      </Box>
    );
  }
  if (item.kind === 'system') {
    return <Text color="gray">• {item.body}</Text>;
  }
  const running = item.status === 'running';
  const failed = item.status === 'failed';
  const color = running ? (pulseTick % 2 === 0 ? 'gray' : undefined) : failed ? 'red' : item.kind === 'thinking' ? undefined : 'green';
  const dot = running ? (pulseTick % 2 === 0 ? '◦' : '•') : '•';
  const detailColor = item.kind === 'thinking' || item.kind === 'tool' || item.kind === 'sub_agent' ? 'gray' : undefined;
  const detailWidth = separator.length;
  const showDetails = item.kind === 'thinking' ? SHOW_THINKING_DETAILS : true;
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text><Text color={color}>{dot}</Text> {item.title || item.kind}</Text>
      {showDetails && !running && item.body ? wrapIndentedText(`└ ${item.body}`, 2, detailWidth).map((line, index) => (
        <Text key={`${item.id}-body-${index}`} color={detailColor}>{line}</Text>
      )) : null}
      {showDetails && (item.kind === 'thinking' || !running) && (item.lines || []).map((line, index) => (
        wrapIndentedText(line, index === 0 && !item.body ? 2 : 4, detailWidth).map((wrapped, wrappedIndex) => (
          <Text key={`${item.id}-${index}-${wrappedIndex}`} color={detailColor}>
            {index === 0 && !item.body && wrappedIndex === 0 ? wrapped.replace(/^  /, '  └ ') : wrapped}
          </Text>
        ))
      ))}
    </Box>
  );
}

function useFullWidthSeparator(): string {
  const { stdout } = useStdout();
  const width = Math.max(2, (stdout.columns || 80) - 2);
  return '─'.repeat(width);
}

function wrapIndentedText(text: string, indent: number, width: number): string[] {
  const prefix = ' '.repeat(indent);
  const contentWidth = Math.max(10, width - indent);
  const result: string[] = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine || '';
    if (displayWidth(line) <= contentWidth) {
      result.push(`${prefix}${line}`);
      continue;
    }
    let current = '';
    let currentWidth = 0;
    for (const char of Array.from(line)) {
      const charWidth = displayWidth(char);
      if (current && currentWidth + charWidth > contentWidth) {
        result.push(`${prefix}${current}`);
        current = char;
        currentWidth = charWidth;
      } else {
        current += char;
        currentWidth += charWidth;
      }
    }
    result.push(`${prefix}${current}`);
  }
  return result.length ? result : [prefix];
}

export function SlashMenu({ query, commands, selectedIndex }: { query: string; commands: SlashCommand[]; selectedIndex: number }) {
  const visibleCount = 5;
  const leftIndent = '  ';
  const start = Math.max(0, selectedIndex - 2);
  const end = Math.min(commands.length, start + visibleCount);
  const adjustedStart = Math.max(0, end - visibleCount);
  const visibleCommands = commands.slice(adjustedStart, end);
  return (
    <Box flexDirection="column" marginTop={1}>
      <Box flexDirection="column">
        {visibleCommands.map((command, visibleIndex) => {
          const index = adjustedStart + visibleIndex;
          return (
          <Text key={command.name} color={index === selectedIndex ? 'cyan' : undefined}>
            {leftIndent}{index === selectedIndex ? '› ' : '  '}{command.name.padEnd(13)} {command.description}
          </Text>
          );
        })}
        {!commands.length ? <Text color="gray">{leftIndent}  无匹配命令</Text> : null}
      </Box>
    </Box>
  );
}

export function Picker({ title, items, selectedIndex }: { title: string; items: Array<{ label: string; description?: string; disabled?: boolean }>; selectedIndex: number }) {
  const visibleCount = 5;
  const start = Math.max(0, selectedIndex - 2);
  const end = Math.min(items.length, start + visibleCount);
  const adjustedStart = Math.max(0, end - visibleCount);
  const visibleItems = items.slice(adjustedStart, end);
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text bold>{title}</Text>
      <Box flexDirection="column" marginTop={1}>
        {visibleItems.map((item, visibleIndex) => {
          const index = adjustedStart + visibleIndex;
          return (
            <Text key={`${item.label}-${index}`} color={item.disabled ? 'gray' : index === selectedIndex ? 'cyan' : undefined}>
              {index === selectedIndex ? '› ' : '  '}{item.label.padEnd(20)} {item.description || ''}
            </Text>
          );
        })}
        {!items.length ? <Text color="gray">  暂无选项</Text> : null}
      </Box>
    </Box>
  );
}

export function WorkspacePrompt({ cwd, selectedIndex }: { cwd: string; selectedIndex: number }) {
  const options = ['确认，添加并继续', '拒绝，退出'];
  return (
    <Box flexDirection="column">
      <Text color="yellow">当前目录尚未添加为工作区：</Text>
      <Text>  {shortPath(cwd)}</Text>
      <Box marginTop={1} flexDirection="column">
        <Text>Agent 将在此目录中读取文件、执行命令和修改内容。</Text>
        <Text>是否将此目录添加为工作区？</Text>
      </Box>
      <Box marginTop={1} flexDirection="column">
        {options.map((label, index) => (
          <Text key={label} color={index === selectedIndex ? 'cyan' : undefined}>
            {index === selectedIndex ? '› ' : '  '}{index + 1}. {label}
          </Text>
        ))}
      </Box>
    </Box>
  );
}

export function StatusPanel({ status }: { status: CliStatus }) {
  return (
    <Box borderStyle="round" borderColor="gray" paddingX={1} flexDirection="column" marginTop={1}>
      <Text>{'>_ Astrion CLI'}</Text>
      <Text> </Text>
      <StatusField label="Model" value={status.model} />
      <StatusField label="Directory" value={shortPath(status.directory)} />
      <StatusField label="Workspace" value={status.workspace.label} />
      <StatusField label="Permissions" value={status.permissionMode} />
      <StatusField label="Execution" value={status.executionMode} />
      <StatusField label="Session" value={status.sessionId} />
      <StatusField label="Context window" value={formatContext(status.contextUsed, status.contextLimit)} />
    </Box>
  );
}

function StatusField({ label, value }: { label: string; value: string }) {
  return <Text>  {label.padEnd(15)} {value}</Text>;
}

export function HelpPanel({ commands }: { commands: SlashCommand[] }) {
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text bold>Commands</Text>
      <Box flexDirection="column" marginTop={1}>
        {commands.map((command) => (
          <Text key={command.name}>  {command.name.padEnd(13)} {command.description}</Text>
        ))}
      </Box>
    </Box>
  );
}

export function SkillHint({ skill }: { skill?: SkillDefinition }) {
  if (!skill) return null;
  return <Text color="gray">  + 先阅读 {skill.label} skill</Text>;
}

export function WorkspaceList({ workspaces, selectedIndex }: { workspaces: Workspace[]; selectedIndex: number }) {
  return (
    <Picker
      title="Workspace"
      selectedIndex={selectedIndex}
      items={workspaces.map((workspace) => ({ label: workspace.label, description: shortPath(workspace.path) }))}
    />
  );
}
