import React from 'react';
import { Text, useCursor, useInput, useStdout, type DOMElement } from 'ink';

type Props = {
  value: string;
  disabled?: boolean;
  contentRef: React.RefObject<DOMElement | null>;
  cursorYOffset?: number;
  refreshTick?: number;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void | Promise<void>;
};

export default function ImeTextInput({ value, disabled, contentRef, cursorYOffset = 0, refreshTick = 0, onChange, onSubmit }: Props) {
  const { stdout } = useStdout();
  const { setCursorPosition } = useCursor();
  const cursorOffsetRef = React.useRef<number>(Array.from(value || '').length);
  const [, forceRender] = React.useReducer((x: number) => x + 1, 0);
  const pendingYOffsetRef = React.useRef<number>(0);
  const seenRefreshTickRef = React.useRef<number>(refreshTick);

  React.useEffect(() => {
    const len = Array.from(value || '').length;
    if (cursorOffsetRef.current > len) cursorOffsetRef.current = len;
  }, [value]);

  React.useEffect(() => {
    if (refreshTick !== seenRefreshTickRef.current) {
      seenRefreshTickRef.current = refreshTick;
      pendingYOffsetRef.current = cursorYOffset;
      forceRender();
    }
  }, [refreshTick, cursorYOffset]);

  useInput((input, key) => {
    if (disabled) return;
    if (key.return) {
      void onSubmit(value);
      return;
    }
    if (key.leftArrow) {
      const next = Math.max(0, cursorOffsetRef.current - 1);
      if (next !== cursorOffsetRef.current) {
        cursorOffsetRef.current = next;
        forceRender();
      }
      return;
    }
    if (key.rightArrow) {
      const len = Array.from(value || '').length;
      const next = Math.min(len, cursorOffsetRef.current + 1);
      if (next !== cursorOffsetRef.current) {
        cursorOffsetRef.current = next;
        forceRender();
      }
      return;
    }
    if (key.backspace || key.delete) {
      if (cursorOffsetRef.current <= 0) return;
      const chars = Array.from(value || '');
      chars.splice(cursorOffsetRef.current - 1, 1);
      onChange(chars.join(''));
      cursorOffsetRef.current = Math.max(0, cursorOffsetRef.current - 1);
      return;
    }
    if (!input || key.ctrl || key.meta) return;
    const chars = Array.from(value || '');
    chars.splice(cursorOffsetRef.current, 0, input);
    onChange(chars.join(''));
    cursorOffsetRef.current += Array.from(input).length;
  }, { isActive: !disabled });

  const origin = getAbsoluteOrigin(contentRef.current);
  if (origin) {
    const head = Array.from(value || '').slice(0, cursorOffsetRef.current).join('');
    const terminalRows = Math.max(1, stdout.rows || 24);
    const bottomCompensation = origin.y >= terminalRows - 2 ? 1 : 0;
    const yOffsetThisFrame = pendingYOffsetRef.current;
    pendingYOffsetRef.current = 0;
    setCursorPosition({
      x: origin.x + 2 + displayWidth(head),
      y: origin.y + yOffsetThisFrame + bottomCompensation,
    });
  } else {
    setCursorPosition(undefined);
  }

  return <Text>{value}</Text>;
}

function getAbsoluteOrigin(node: DOMElement | null): { x: number; y: number } | null {
  if (!node?.yogaNode) return null;
  let x = 0;
  let y = 0;
  let current: DOMElement | undefined = node;
  while (current?.parentNode) {
    if (!current.yogaNode) return null;
    x += current.yogaNode.getComputedLeft();
    y += current.yogaNode.getComputedTop();
    current = current.parentNode;
  }
  return { x, y };
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
