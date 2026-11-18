import type { PhrasingContent, Root } from 'mdast';
import { visit } from 'unist-util-visit';
import type { Plugin } from 'unified';

const CJK_BOUNDARY = /[、。〈〉《》「」『』【】〔〕（）［］｛｝，！？；：“”‘’…—–]/u;
const WEB_PREFIX = /^(?:https?:\/\/|www\.)/i;
const WWW_PREFIX = /^www\./i;
const NEXT_ADDRESS = new RegExp(
  `(?<=${CJK_BOUNDARY.source})(?=${WEB_PREFIX.source.slice(1)})`,
  'iu'
);

function isValidAddress(href: string): boolean {
  try {
    return Boolean(new URL(href).hostname);
  } catch {
    return false;
  }
}

const remarkConversationAutolink: Plugin<[], Root> = () => (tree, file) => {
  const markdown = String(file.value);
  visit(tree, 'link', (node, index, parent) => {
    if (!parent || typeof index !== 'number' || node.children.length !== 1) return;
    const [label] = node.children;
    if (label.type !== 'text' || !WEB_PREFIX.test(label.value) || !CJK_BOUNDARY.test(label.value)) {
      return;
    }
    if (node.url !== (WWW_PREFIX.test(label.value) ? `http://${label.value}` : label.value)) return;

    const start = node.position?.start.offset;
    const end = node.position?.end.offset;
    // Explicit Markdown links have their opening delimiter in this span. GFM's
    // fallback pass can also create literal links without a source position.
    if (start !== undefined && end !== undefined && !WEB_PREFIX.test(markdown.slice(start, end))) {
      return;
    }
    const followsBoldMarker =
      start !== undefined && markdown.slice(Math.max(0, start - 2), start) === '**';
    const replacements: PhrasingContent[] = [];

    // GFM may combine two URLs separated only by CJK punctuation in a single node.
    for (const [partIndex, part] of label.value.split(NEXT_ADDRESS).entries()) {
      const boundary = part.search(CJK_BOUNDARY);
      let address = boundary === -1 ? part : part.slice(0, boundary).replace(/[.,;:!?]+$/, '');
      if (partIndex === 0 && followsBoldMarker && address.endsWith('**')) {
        address = address.slice(0, -2);
      }
      const href = WWW_PREFIX.test(address) ? `http://${address}` : address;
      if (!isValidAddress(href)) {
        replacements.push({ type: 'text', value: part });
        continue;
      }
      replacements.push({ type: 'link', url: href, children: [{ type: 'text', value: address }] });
      if (part.length > address.length) {
        replacements.push({ type: 'text', value: part.slice(address.length) });
      }
    }
    parent.children.splice(index, 1, ...replacements);
    return index + replacements.length;
  });
};

export default remarkConversationAutolink;
