import { defineAsyncComponent } from 'vue';
import ChatArea from '../components/chat/ChatArea.vue';
import ConversationSidebar from '../components/sidebar/ConversationSidebar.vue';
import ToolApprovalPanel from '../components/panels/ToolApprovalPanel.vue';
import GitChangesPanel from '../components/panels/GitChangesPanel.vue';
import TerminalPanel from '../components/panels/TerminalPanel.vue';
import TokenDrawer from '../components/token/TokenDrawer.vue';
import QuickMenu from '../components/input/QuickMenu.vue';
import InputComposer from '../components/input/InputComposer.vue';
import EffortSlider from '../components/input/EffortSlider.vue';
import AppShell from '../components/shell/AppShell.vue';
import StatusAvatar from '../components/avatar/StatusAvatar.vue';

const PersonalizationDrawer = defineAsyncComponent(
  () => import('../components/personalization/PersonalizationDrawer.vue')
);
const ImagePicker = defineAsyncComponent(() => import('../components/overlay/ImagePicker.vue'));
const ConversationReviewDialog = defineAsyncComponent(
  () => import('../components/overlay/ConversationReviewDialog.vue')
);
const SubAgentActivityDialog = defineAsyncComponent(
  () => import('../components/overlay/SubAgentActivityDialog.vue')
);
const BackgroundCommandDialog = defineAsyncComponent(
  () => import('../components/overlay/BackgroundCommandDialog.vue')
);
const VersioningDialog = defineAsyncComponent(
  () => import('../components/overlay/VersioningDialog.vue')
);
const UserQuestionDialog = defineAsyncComponent(
  () => import('../components/overlay/UserQuestionDialog.vue')
);
const PlanApprovalDialog = defineAsyncComponent(
  () => import('../components/overlay/PlanApprovalDialog.vue')
);
const SandboxSetupDialog = defineAsyncComponent(
  () => import('../components/overlay/SandboxSetupDialog.vue')
);
const TutorialOverlay = defineAsyncComponent(
  () => import('../components/overlay/TutorialOverlay.vue')
);
const NewUserTutorialPrompt = defineAsyncComponent(
  () => import('../components/overlay/NewUserTutorialPrompt.vue')
);
const GoalProgressDialog = defineAsyncComponent(
  () => import('../components/overlay/GoalProgressDialog.vue')
);
const WorkflowDemoShell = defineAsyncComponent(
  () => import('../components/workflow/WorkflowDemoShell.vue')
);

export const appComponents = {
  ChatArea,
  ConversationSidebar,
  ToolApprovalPanel,
  GitChangesPanel,
  TerminalPanel,
  TokenDrawer,
  PersonalizationDrawer,
  QuickMenu,
  InputComposer,
  EffortSlider,
  AppShell,
  StatusAvatar,
  ImagePicker,
  ConversationReviewDialog,
  SubAgentActivityDialog,
  BackgroundCommandDialog,
  VersioningDialog,
  UserQuestionDialog,
  PlanApprovalDialog,
  SandboxSetupDialog,
  TutorialOverlay,
  NewUserTutorialPrompt,
  GoalProgressDialog,
  WorkflowDemoShell
};
