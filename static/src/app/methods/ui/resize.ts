// @ts-nocheck
import { debugLog } from '../common';
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import { usePersonalizationStore } from '../../../stores/personalization';
import { useTutorialStore } from '../../../stores/tutorial';
import { renderMarkdown as renderMarkdownHelper } from '../../../composables/useMarkdownRenderer';
import { scrollToBottom as scrollToBottomHelper, conditionalScrollToBottom as conditionalScrollToBottomHelper, scrollThinkingToBottom as scrollThinkingToBottomHelper } from '../../../composables/useScrollControl';
import { startResize as startPanelResize, handleResize as handlePanelResize, stopResize as stopPanelResize } from '../../../composables/usePanelResize';
import {
  SUB_AGENT_DONE_PREFIX_RE,
  BG_RUN_COMMAND_DONE_PREFIX_RE,
  userMDebug,
  UI_BOUNCE_TRACE_MAX,
  uiBounceTraceLastTsByKey,
  isUiBounceTraceEnabled,
  uiBounceTrace,
  isConnectionDiagEnabled,
  pushConnectionDiagRecord,
  connectionDiag,
  parseSubAgentDoneLabel,
  parseBackgroundRunCommandDoneLabel,
  parseSystemNoticeLabel,
} from './shared';

export const resizeMethods = {
  startResize(panel, event) {
    startPanelResize(this, panel, event);
  },
  handleResize(event) {
    handlePanelResize(this, event);
  },
  stopResize() {
    stopPanelResize(this);
  },
  startRightSplitResize(event) {
    event.preventDefault();
    const container = event.target.closest('.right-panels') as HTMLElement;
    if (!container) return;
    const startY = event.clientY;
    const startHeight = container.offsetHeight;
    const startRatio = this.rightSplitRatio ?? 0.5;

    // 拖拽期间禁用 CSS transition，确保跟手
    container.classList.add('right-panels--resizing');

    const onMove = (e: MouseEvent) => {
      const dy = e.clientY - startY;
      const newRatio = startRatio + dy / startHeight;
      this.rightSplitRatio = Math.max(0.15, Math.min(0.85, newRatio));
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      container.classList.remove('right-panels--resizing');
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
  }
};
