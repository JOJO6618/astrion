declare module '*.vue' {
  import type { DefineComponent } from 'vue';
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, any>;
  export default component;
}

declare module 'prismjs';
declare module 'prismjs/components/*';
declare module 'katex/contrib/auto-render';
declare module '*.md' {
  const content: string;
  export default content;
}
