// @ts-nocheck
import { usePersonalizationStore } from '../../../stores/personalization';
import { useTutorialStore, TUTORIAL_ENABLED } from '../../../stores/tutorial';

export const tutorialMethods = {
  async checkTutorialPrompt() {
    // 新手教程暂时关闭不开放（TUTORIAL_ENABLED=false），不再弹出新用户教程提示。
    // Host-only 主仓没有按普通账号记录教程完成度的后端，提示状态只保留本地行为。
    this.tutorialPromptVisible = false;
    this.tutorialPromptUsername = '';
    if (!TUTORIAL_ENABLED) {
      return;
    }
  },
  async updateTutorialPromptStatus(completed = true) {
    this.tutorialPromptLoading = true;
    try {
      const tutorialStore = useTutorialStore();
      if (completed) tutorialStore.markCompleted();
      this.tutorialPromptVisible = false;
      return true;
    } finally {
      this.tutorialPromptLoading = false;
    }
  },
  async handleNewUserTutorialStart() {
    const ok = await this.updateTutorialPromptStatus(true);
    if (!ok) {
      return;
    }
    const personalStore = usePersonalizationStore();
    personalStore.closeDrawer();
    const tutorialStore = useTutorialStore();
    window.setTimeout(() => {
      tutorialStore.startTutorial();
    }, 220);
  },
  async handleNewUserTutorialSkip() {
    await this.updateTutorialPromptStatus(true);
  }
};
