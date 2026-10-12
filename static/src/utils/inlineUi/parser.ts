/** Extract inline UI before Markdown sees its HTML, CSS or script source. */
export interface InlineUiPart {
  type: 'text' | 'ui';
  content: string;
  start: number;
  closed: boolean;
  js: 'off' | 'on';
}

function tagEnd(source: string, start: number): number {
  let quote = '';
  for (let i = start; i < source.length; i++) {
    const ch = source[i];
    if (quote) {
      if (ch === quote) quote = '';
    } else if (ch === '"' || ch === "'") {
      quote = ch;
    } else if (ch === '>') {
      return i;
    }
  }
  return -1;
}

export function splitInlineUi(source: string): InlineUiPart[] {
  const parts: InlineUiPart[] = [];
  let cursor = 0;
  let i = 0;
  let fence = '';
  let inlineTicks = 0;
  const pushText = (end: number) => {
    if (end > cursor) {
      parts.push({ type: 'text', content: source.slice(cursor, end), start: cursor, closed: true, js: 'off' });
    }
  };
  while (i < source.length) {
    // Fences are line-based; recognize both CommonMark fence characters.
    if (i === 0 || source[i - 1] === '\n') {
      const line = /^( {0,3})(`{3,}|~{3,})([^\n]*)/.exec(source.slice(i));
      if (line && !inlineTicks) {
        const marker = line[2];
        if (!fence) fence = marker;
        else if (marker[0] === fence[0] && marker.length >= fence.length && !line[3].trim()) fence = '';
        i += line[0].length;
        continue;
      }
    }
    if (fence) { i++; continue; }
    if (source[i] === '`' && source[i - 1] !== '\\') {
      const ticks = /^`+/.exec(source.slice(i))![0].length;
      if (!inlineTicks) inlineTicks = ticks;
      else if (inlineTicks === ticks) inlineTicks = 0;
      i += ticks;
      continue;
    }
    if (inlineTicks || source[i] !== '<') { i++; continue; }
    // Existing HTML cards are opaque too: do not interpret examples inside them.
    const html = /^<show[_-]html\b/i.exec(source.slice(i));
    if (html) {
      const closing = /<\/show[_-]html\s*>/i.exec(source.slice(i + html[0].length));
      if (!closing) break;
      i += html[0].length + closing.index + closing[0].length;
      continue;
    }
    const opening = /^<show_ui(?=[\s>]|$)/i.exec(source.slice(i));
    if (!opening || source[i - 1] === '\\') { i++; continue; }
    pushText(i);
    const end = tagEnd(source, i);
    if (end < 0) {
      parts.push({ type: 'ui', content: '', start: i, closed: false, js: 'off' });
      cursor = source.length;
      break;
    }
    const header = source.slice(i, end + 1);
    const js = /\bjs\s*=\s*(?:"on"|'on'|on(?=[\s>]))/i.test(header) ? 'on' : 'off';
    const closing = /<\/show_ui\s*>/i.exec(source.slice(end + 1));
    const contentEnd = closing ? end + 1 + closing.index : source.length;
    let content = source.slice(end + 1, contentEnd);
    if (!closing) content = content.replace(/<\/(?:s(?:h(?:o(?:w(?:_(?:u(?:i)?)?)?)?)?)?)?$/i, '');
    parts.push({ type: 'ui', content, start: i, closed: !!closing, js });
    cursor = closing ? contentEnd + closing[0].length : source.length;
    i = cursor;
  }
  pushText(source.length);
  return parts;
}
