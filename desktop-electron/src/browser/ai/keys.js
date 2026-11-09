// Playwright 风格按键名 → CDP Input.dispatchKeyEvent 参数（覆盖常用键，macOS 编辑命令）。

const NAMED = {
  Enter: { code: 'Enter', keyCode: 13, text: '\r' }, Tab: { code: 'Tab', keyCode: 9 }, Escape: { code: 'Escape', keyCode: 27 },
  Backspace: { code: 'Backspace', keyCode: 8 }, Delete: { code: 'Delete', keyCode: 46 }, Space: { key: ' ', code: 'Space', keyCode: 32, text: ' ' },
  ArrowUp: { code: 'ArrowUp', keyCode: 38 }, ArrowDown: { code: 'ArrowDown', keyCode: 40 }, ArrowLeft: { code: 'ArrowLeft', keyCode: 37 }, ArrowRight: { code: 'ArrowRight', keyCode: 39 },
  Home: { code: 'Home', keyCode: 36 }, End: { code: 'End', keyCode: 35 }, PageUp: { code: 'PageUp', keyCode: 33 }, PageDown: { code: 'PageDown', keyCode: 34 },
  Insert: { code: 'Insert', keyCode: 45 }, Shift: { code: 'ShiftLeft', keyCode: 16 }, Control: { code: 'ControlLeft', keyCode: 17 },
  Alt: { code: 'AltLeft', keyCode: 18 }, Meta: { code: 'MetaLeft', keyCode: 91 }
};
for (let i = 1; i <= 12; i++) NAMED[`F${i}`] = { code: `F${i}`, keyCode: 111 + i };

const MODIFIER_BITS = { Alt: 1, Control: 2, Meta: 4, Shift: 8 };
const ALIASES = { Ctrl: 'Control', Cmd: 'Meta', Command: 'Meta', Option: 'Alt', Esc: 'Escape', Return: 'Enter', ControlOrMeta: 'Meta', Up: 'ArrowUp', Down: 'ArrowDown', Left: 'ArrowLeft', Right: 'ArrowRight' };

// macOS 下 Chromium 需要显式编辑命令，否则 Cmd+A 等快捷键不生效。
const MAC_COMMANDS = {
  'Meta+a': ['selectAll'], 'Meta+c': ['copy'], 'Meta+x': ['cut'], 'Meta+v': ['paste'], 'Meta+z': ['undo'], 'Meta+Shift+z': ['redo'],
  'Meta+Backspace': ['deleteToBeginningOfLine'], 'Alt+Backspace': ['deleteWordBackward'],
  'Meta+ArrowLeft': ['moveToBeginningOfLine'], 'Meta+ArrowRight': ['moveToEndOfLine'],
  'Meta+ArrowUp': ['moveToBeginningOfDocument'], 'Meta+ArrowDown': ['moveToEndOfDocument'],
  'Alt+ArrowLeft': ['moveWordLeft'], 'Alt+ArrowRight': ['moveWordRight']
};

function single(key) {
  if (NAMED[key]) return { key: NAMED[key].key || key, ...NAMED[key] };
  if ([...key].length !== 1) return null;
  const upper = key.toUpperCase();
  const letter = /^[a-z]$/i.test(key), digit = /^\d$/.test(key);
  return { key, code: letter ? `Key${upper}` : digit ? `Digit${key}` : '', keyCode: letter || digit ? upper.charCodeAt(0) : 0, text: key };
}

/** 解析 "Control+Shift+K" 形式；返回按下/松开的事件序列。 */
export function keyEvents(combo) {
  if (typeof combo !== 'string' || !combo || combo.length > 64) throw new Error('invalid_key');
  const parts = combo === '+' ? ['+'] : combo.split('+').map(part => part || '+');
  const names = parts.map(part => ALIASES[part] || part);
  const main = names.at(-1), modifiers = names.slice(0, -1);
  if (modifiers.some(name => !(name in MODIFIER_BITS))) throw new Error('invalid_key');
  const def = single(main);
  if (!def) throw new Error('invalid_key');
  let bits = 0;
  const events = [];
  for (const name of modifiers) {
    bits |= MODIFIER_BITS[name];
    events.push({ type: 'rawKeyDown', key: name, code: NAMED[name].code, windowsVirtualKeyCode: NAMED[name].keyCode, modifiers: bits });
  }
  const printable = def.text && !(bits & (MODIFIER_BITS.Control | MODIFIER_BITS.Meta | MODIFIER_BITS.Alt));
  const commandKey = [...modifiers.filter(n => n !== 'Shift'), ...(modifiers.includes('Shift') ? ['Shift'] : []), main.length === 1 ? main.toLowerCase() : main].join('+');
  const down = { type: printable ? 'keyDown' : 'rawKeyDown', key: bits & MODIFIER_BITS.Shift && def.text && main.length === 1 ? main.toUpperCase() : def.key, code: def.code, windowsVirtualKeyCode: def.keyCode, modifiers: bits };
  if (printable) { down.text = bits & MODIFIER_BITS.Shift && main.length === 1 ? main.toUpperCase() : def.text; down.unmodifiedText = def.text; }
  if (process.platform === 'darwin' && MAC_COMMANDS[commandKey]) down.commands = MAC_COMMANDS[commandKey];
  events.push(down, { type: 'keyUp', key: down.key, code: def.code, windowsVirtualKeyCode: def.keyCode, modifiers: bits });
  for (const name of [...modifiers].reverse()) {
    bits &= ~MODIFIER_BITS[name];
    events.push({ type: 'keyUp', key: name, code: NAMED[name].code, windowsVirtualKeyCode: NAMED[name].keyCode, modifiers: bits });
  }
  return events;
}

export function modifierBits(list) {
  if (list === undefined) return 0;
  if (!Array.isArray(list)) throw new Error('invalid_modifiers');
  return list.reduce((bits, raw) => {
    const name = ALIASES[raw] || raw;
    if (!(name in MODIFIER_BITS)) throw new Error('invalid_modifiers');
    return bits | MODIFIER_BITS[name];
  }, 0);
}

/** 逐字输入（slowly=true）：每个字符一组 keyDown/keyUp；非 ASCII 字符走 insertText。 */
export function charEvents(char) {
  const def = single(char);
  if (!def || !def.code) return null;
  return [
    { type: 'keyDown', key: char, code: def.code, windowsVirtualKeyCode: def.keyCode, text: char, unmodifiedText: char },
    { type: 'keyUp', key: char, code: def.code, windowsVirtualKeyCode: def.keyCode }
  ];
}
