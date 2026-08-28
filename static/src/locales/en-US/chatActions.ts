// chatActions message pack (wave-2 migration namespace, group 6: chat action components)
// Spec: doc/frontend/i18n_spec.md. Key structure must match zh-CN/chatActions.ts exactly (tsc enforced).
// Note: keys identical to chat.ts (targetFile/appendDone/appendSuccess/appendFailed/appendWarning/
//       linesCount/bytesCount/thinking/thinkingRunning) reuse chat.*; not re-defined here.
//
// TODO(common): candidate common words (dedupe by main task):
//   - apply / run / downloadFile / targetFile
export default {
  // —— Action summary (ActionSummary) ——
  applyModify: 'Apply modifications',
  appendContent: 'Append content',
  apply: 'Apply',
  run: 'Run',
  downloadFile: 'Download file',

  // —— Append payload (AppendPayloadAction; main copy reuses chat.*) ——
  appendFailedRetry: 'Failed to write to {path}. Please retry as prompted.',

  // —— Modify details / modify summary ——
  modifyRecord: 'Modification record',
  entryTitle: 'Modification {n}',
  modifyTotal: '· {n} total',
  modifyCompleted: '· {n} completed',
  modifyDone: '· {n} done',
  modifyRemaining: '· {n} remaining',
  modifyProcessed: 'Processed {path}',
  modifyWarning: 'No closing marker detected; handled automatically.',

  // —— Loader pool (loaders/index.ts) ——
  loaderPoolEmpty: 'loaderPool cannot be empty',
} as const;