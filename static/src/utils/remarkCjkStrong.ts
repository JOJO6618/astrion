import { attention } from 'micromark-core-commonmark';
import type { Code, Construct, Token, Tokenizer } from 'micromark-util-types';
import type { Processor } from 'unified';

const CJK_TEXT = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;
const CJK_PUNCTUATION = /[\u3000-\u303f\uff01-\uff65\u2018-\u201f\u2026]/u;

function isCjkText(code: Code): boolean {
  return code !== null && code > 0 && CJK_TEXT.test(String.fromCodePoint(code));
}

function isCjkPunctuation(code: Code): boolean {
  if (code === null || code <= 0) return false;
  const character = String.fromCodePoint(code);
  return CJK_PUNCTUATION.test(character) && /\p{P}/u.test(character);
}

// Reuse CommonMark's tokenizer and resolver; only relax strong delimiter
// boundaries between CJK text and punctuation, e.g. **重点：**内容.
const tokenizeCjkStrong: Tokenizer = function (effects, ok, nok) {
  const previous = this.previous;
  let sequence: Token | undefined;
  const wrappedEffects = {
    ...effects,
    exit: (type: Parameters<typeof effects.exit>[0]) => {
      const token = effects.exit(type);
      if (type === 'attentionSequence') sequence = token;
      return token;
    }
  };

  return attention.tokenize.call(
    this,
    wrappedEffects,
    (code) => {
      if (sequence && sequence.end.offset - sequence.start.offset >= 2) {
        if (isCjkText(previous) && isCjkPunctuation(code)) sequence._open = true;
        if (isCjkPunctuation(previous) && isCjkText(code)) sequence._close = true;
      }
      return ok(code);
    },
    nok
  );
};

const cjkStrong: Construct = { ...attention, tokenize: tokenizeCjkStrong };

export default function remarkCjkStrong(this: Processor): void {
  const data = this.data();
  const extensions = data.micromarkExtensions || (data.micromarkExtensions = []);
  extensions.push({ text: { 42: cjkStrong } });
}
