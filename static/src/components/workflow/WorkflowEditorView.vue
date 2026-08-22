<template>
  <div class="wf-editor">
    <header class="wf-editor__topbar">
      <div class="wf-editor__topbar-left">
        <button type="button" class="wf-icon-btn" aria-label="返回工作流库" title="返回工作流库" @click="$emit('back')">
          <span class="icon" :style="iconSrc(ICONS.arrowLeft)" aria-hidden="true"></span>
        </button>
        <span class="icon wf-editor__logo" :style="iconSrc(ICONS.workflow)" aria-hidden="true"></span>
        <span class="wf-editor__name">{{ workflow.name }}</span>
        <span class="wf-editor__badge">{{ workflow.source === 'builtin' ? '内置' : '用户' }}</span>
        <span v-if="dirty" class="wf-editor__dirty">未保存</span>
      </div>
      <div class="wf-editor__topbar-right">
        <div class="wf-editor__issues-anchor" ref="issuesAnchorRef">
          <button
            type="button"
            class="wf-btn wf-btn--ghost"
            :class="{ 'wf-btn--issue': errorCount > 0 }"
            title="查看结构提醒与错误"
            @click.stop="issuesOpen = !issuesOpen"
          >
            <span class="icon" :style="iconSrc(errorCount > 0 ? ICONS.circleAlert : ICONS.check)" aria-hidden="true"></span>
            <span>{{ issueLabel }}</span>
          </button>
          <!-- 提醒/报错弹层：不占页面布局，固定高度 + 内部滚动，点外部关闭 -->
          <div v-if="issuesOpen && issues.length" class="wf-editor__issues-pop">
            <div v-for="(issue, i) in issues" :key="i" class="wf-issue" :class="`wf-issue--${issue.level}`">
              <span class="icon" :style="iconSrc(issue.level === 'error' ? ICONS.circleAlert : ICONS.triangleAlert)" aria-hidden="true"></span>
              <span>{{ issue.message }}</span>
            </div>
          </div>
        </div>
        <button type="button" class="wf-btn wf-btn--ghost" title="自动排版" @click="onAutoLayout">
          <span class="icon" :style="iconSrc(ICONS.layoutGrid)" aria-hidden="true"></span>
          <span>自动排版</span>
        </button>
        <div class="wf-select wf-select--toolbar" ref="addNodeSelectRef">
          <button type="button" class="wf-btn wf-btn--ghost" title="在画布中央添加节点（或双击画布空白处添加阶段）" @click.stop="addNodeMenuOpen = !addNodeMenuOpen">
            <span class="icon" :style="iconSrc(ICONS.plus)" aria-hidden="true"></span>
            <span>添加节点</span>
            <span class="icon wf-select__caret" :style="iconSrc(ICONS.chevronDown)" aria-hidden="true"></span>
          </button>
          <div v-if="addNodeMenuOpen" class="wf-select__menu">
            <button type="button" class="wf-select__option" @click="onAddNodeAtCenter('stage')">
              <span class="icon" :style="iconSrc(ICONS.plus)" aria-hidden="true"></span>
              <span class="wf-select__option-name">阶段</span>
              <span class="wf-select__option-desc">AI 执行，单入单出</span>
            </button>
            <button type="button" class="wf-select__option" @click="onAddNodeAtCenter('review')">
              <span class="icon" :style="iconSrc(ICONS.eye)" aria-hidden="true"></span>
              <span class="wf-select__option-name">审核</span>
              <span class="wf-select__option-desc">菱形，通过/驳回</span>
            </button>
            <button type="button" class="wf-select__option" @click="onAddNodeAtCenter('branch')">
              <span class="icon" :style="iconSrc(ICONS.gitBranch)" aria-hidden="true"></span>
              <span class="wf-select__option-name">分支</span>
              <span class="wf-select__option-desc">多入多出，条件路由</span>
            </button>
            <button type="button" class="wf-select__option" @click="onAddNodeAtCenter('start')">
              <span class="icon" :style="iconSrc(ICONS.play)" aria-hidden="true"></span>
              <span class="wf-select__option-name">开始</span>
              <span class="wf-select__option-desc">入口，只能有一个</span>
            </button>
            <button type="button" class="wf-select__option" @click="onAddNodeAtCenter('end')">
              <span class="icon" :style="iconSrc(ICONS.octagon)" aria-hidden="true"></span>
              <span class="wf-select__option-name">结束</span>
              <span class="wf-select__option-desc">终点，可多个</span>
            </button>
          </div>
        </div>
        <button type="button" class="wf-btn wf-btn--primary" :disabled="saving" @click="onSave">
          <span class="icon" :style="iconSrc(ICONS.save)" aria-hidden="true"></span>
          <span>{{ saving ? '保存中…' : '保存' }}</span>
        </button>
      </div>
    </header>

    <div v-if="flashMessage" class="wf-editor__flash">{{ flashMessage }}</div>

    <div class="wf-editor__body">
      <div class="wf-editor__canvas">
        <VueFlow
          v-model:nodes="nodes"
          v-model:edges="edges"
          class="wf-flow"
          :delete-key-code="['Backspace', 'Delete']"
          :min-zoom="0.2"
          :max-zoom="2"
          @connect="onConnect"
          @nodes-change="onNodesChange"
          @edges-change="onEdgesChange"
          @node-click="onNodeClick"
          @pane-click="onPaneClick"
          @dblclick="onPaneDblclick"
        >
          <Background :gap="24" :size="1.5" pattern-color="var(--border-strong)" />
          <Controls :show-interactive="false" position="bottom-left" />
          <MiniMap
            pannable
            zoomable
            position="bottom-right"
            node-color="var(--text-tertiary)"
            mask-color="color-mix(in srgb, var(--surface-base) 72%, transparent)"
          />
          <template #node-stage="nodeProps">
            <WorkflowStageNode v-bind="nodeProps" />
          </template>
          <template #node-review="nodeProps">
            <WorkflowReviewNode v-bind="nodeProps" />
          </template>
          <template #node-branch="nodeProps">
            <WorkflowBranchNode v-bind="nodeProps" />
          </template>
          <template #node-boundary="nodeProps">
            <WorkflowBoundaryNode v-bind="nodeProps" />
          </template>
        </VueFlow>
      </div>

      <aside class="wf-panel">
        <!-- 工作流属性（未选中节点） -->
        <template v-if="!selectedNode">
          <div class="wf-panel__section">
            <div class="wf-panel__heading">工作流属性</div>
            <label class="wf-field">
              <span class="wf-field__label">名称（唯一标识）</span>
              <input v-model="workflow.name" class="wf-input" type="text" spellcheck="false" />
            </label>
            <label class="wf-field">
              <span class="wf-field__label">描述</span>
              <input v-model="workflow.description" class="wf-input" type="text" placeholder="激活选择时靠它辨认" />
            </label>
            <div class="wf-field">
              <span class="wf-field__label">审核智能体取证能力</span>
              <div class="wf-segment">
                <button
                  type="button"
                  class="wf-segment__item"
                  :class="{ 'wf-segment__item--active': workflow.reviewMode === 'readonly' }"
                  @click="workflow.reviewMode = 'readonly'"
                >
                  只读审核
                </button>
                <button
                  type="button"
                  class="wf-segment__item"
                  :class="{ 'wf-segment__item--active': workflow.reviewMode === 'active' }"
                  @click="workflow.reviewMode = 'active'"
                >
                  可调命令取证
                </button>
              </div>
              <span class="wf-field__hint">active 模式允许审核智能体执行只读命令核实成果</span>
            </div>
            <label class="wf-field">
              <span class="wf-field__label">单阶段最大轮数</span>
              <input
                class="wf-input"
                type="text"
                inputmode="numeric"
                :value="workflow.maxStageRounds"
                @input="onMaxRoundsInput"
              />
            </label>
            <label class="wf-field">
              <span class="wf-field__label">整体结束方式</span>
              <input v-model="workflow.endConditions" class="wf-input" type="text" placeholder="例如：报告落盘且审核通过" />
            </label>
          </div>
          <div class="wf-panel__section">
            <div class="wf-panel__heading">全局说明</div>
            <label class="wf-field">
              <span class="wf-field__label">工作方式 / 验证方式 / 结束方式</span>
              <textarea v-model="workflow.body" class="wf-textarea" rows="8" spellcheck="false"></textarea>
            </label>
          </div>
        </template>

        <!-- 阶段属性（选中阶段节点） -->
        <template v-else-if="selectedStage">
          <div class="wf-panel__section">
            <div class="wf-panel__heading">
              <span>阶段属性</span>
              <span class="wf-panel__heading-id">{{ selectedStage.id }}</span>
            </div>
            <label class="wf-field">
              <span class="wf-field__label">阶段名称</span>
              <input v-model="selectedStage.name" class="wf-input" type="text" />
            </label>
            <label class="wf-field">
              <span class="wf-field__label">阶段目标（goal）</span>
              <textarea v-model="selectedStage.goal" class="wf-textarea" rows="2" placeholder="这一阶段要达成什么"></textarea>
            </label>
            <label class="wf-field">
              <span class="wf-field__label">工作方式说明</span>
              <textarea v-model="selectedStage.instructions" class="wf-textarea" rows="4" placeholder="本阶段的具体工作方式（自然语言）"></textarea>
            </label>
          </div>
          <div class="wf-panel__section">
            <div class="wf-panel__heading">前进路由</div>
            <div v-if="selectedStage.next" class="wf-route-list">
              <div class="wf-route-row">
                <span class="icon" :style="iconSrc(ICONS.gitBranch)" aria-hidden="true"></span>
                <span class="wf-route-row__name">{{ nodeNameOf(selectedStage.next) }}</span>
                <button
                  type="button"
                  class="wf-icon-btn wf-icon-btn--sm"
                  :aria-label="`移除到 ${nodeNameOf(selectedStage.next)} 的路由`"
                  @click="onRemoveSelectedRoute"
                >
                  <span class="icon" :style="iconSrc(ICONS.x)" aria-hidden="true"></span>
                </button>
              </div>
            </div>
            <div v-else class="wf-panel__empty">终点阶段（无后续路由）</div>
            <div class="wf-panel__hint">从节点右侧连桩拖线到目标节点；已有出线时再拉新线会替换旧线。想分叉请添加分支节点</div>
          </div>
          <div class="wf-panel__section">
            <button
              v-if="!confirmingStageDelete"
              type="button"
              class="wf-btn wf-btn--danger-outline"
              @click="confirmingStageDelete = true"
            >
              <span class="icon" :style="iconSrc(ICONS.trash)" aria-hidden="true"></span>
              <span>删除此节点</span>
            </button>
            <div v-else class="wf-confirm-row">
              <span class="wf-confirm-row__text">删除后指向它的路由也会移除</span>
              <button type="button" class="wf-btn wf-btn--danger-confirm" @click="onDeleteStage">确认删除</button>
            </div>
          </div>
        </template>

        <!-- 审核属性（选中菱形审核节点） -->
        <template v-else-if="selectedReview">
          <div class="wf-panel__section">
            <div class="wf-panel__heading">
              <span>审核属性</span>
              <span class="wf-panel__heading-id">{{ selectedReview.id }}</span>
            </div>
            <label class="wf-field">
              <span class="wf-field__label">审核名称</span>
              <input v-model="selectedReview.name" class="wf-input" type="text" />
            </label>
            <label class="wf-field">
              <span class="wf-field__label">审核关注点</span>
              <textarea v-model="selectedReview.prompt" class="wf-textarea" rows="3" placeholder="审核智能体检查什么（自然语言）"></textarea>
            </label>
            <label class="wf-field">
              <span class="wf-field__label">驳回上限（次）</span>
              <input
                class="wf-input"
                type="text"
                inputmode="numeric"
                :value="selectedReview.maxRejects"
                @input="onMaxRejectsInput"
              />
              <span class="wf-field__hint">超过上限后升级给用户处理（demo 仅记录数值）</span>
            </label>
          </div>
          <div class="wf-panel__section">
            <div class="wf-panel__heading">通过路由</div>
            <div v-if="selectedReview.next" class="wf-route-list">
              <div class="wf-route-row">
                <span class="icon wf-route-row__icon--pass" :style="iconSrc(ICONS.gitBranch)" aria-hidden="true"></span>
                <span class="wf-route-row__name">{{ nodeNameOf(selectedReview.next) }}</span>
                <button
                  type="button"
                  class="wf-icon-btn wf-icon-btn--sm"
                  :aria-label="`移除到 ${nodeNameOf(selectedReview.next)} 的通过路由`"
                  @click="onRemoveSelectedRoute"
                >
                  <span class="icon" :style="iconSrc(ICONS.x)" aria-hidden="true"></span>
                </button>
              </div>
            </div>
            <div v-else class="wf-panel__empty">通过即结束工作流</div>
            <div class="wf-panel__hint">审核通过后的去向，从菱形右侧连桩拖线（蓝线）</div>
          </div>
          <div class="wf-panel__section">
            <div class="wf-panel__heading">驳回路由</div>
            <div v-if="selectedReview.rejectTo" class="wf-route-list">
              <div class="wf-route-row">
                <span class="icon wf-route-row__icon--back" :style="iconSrc(ICONS.gitBranch)" aria-hidden="true"></span>
                <span class="wf-route-row__name">{{ nodeNameOf(selectedReview.rejectTo) }}</span>
                <button
                  type="button"
                  class="wf-icon-btn wf-icon-btn--sm"
                  :aria-label="`移除到 ${nodeNameOf(selectedReview.rejectTo)} 的驳回路由`"
                  @click="onRemoveRejectRoute"
                >
                  <span class="icon" :style="iconSrc(ICONS.x)" aria-hidden="true"></span>
                </button>
              </div>
            </div>
            <div v-else class="wf-panel__empty wf-panel__empty--error">必须连接驳回路由</div>
            <div class="wf-panel__hint">审核不通过时的回退目标，从菱形顶部或底部连桩拖线（红线；上下出口语义相同，按目标方位自动选向）</div>
          </div>
          <div class="wf-panel__section">
            <button
              v-if="!confirmingStageDelete"
              type="button"
              class="wf-btn wf-btn--danger-outline"
              @click="confirmingStageDelete = true"
            >
              <span class="icon" :style="iconSrc(ICONS.trash)" aria-hidden="true"></span>
              <span>删除此节点</span>
            </button>
            <div v-else class="wf-confirm-row">
              <span class="wf-confirm-row__text">删除后指向它的路由也会移除</span>
              <button type="button" class="wf-btn wf-btn--danger-confirm" @click="onDeleteStage">确认删除</button>
            </div>
          </div>
        </template>

        <!-- 分支属性（选中分支节点） -->
        <template v-else-if="selectedBranch">
          <div class="wf-panel__section">
            <div class="wf-panel__heading">
              <span>分支属性</span>
              <span class="wf-panel__heading-id">{{ selectedBranch.id }}</span>
            </div>
            <label class="wf-field">
              <span class="wf-field__label">分支名称</span>
              <input v-model="selectedBranch.name" class="wf-input" type="text" />
            </label>
          </div>
          <div class="wf-panel__section">
            <div class="wf-panel__heading">出线（{{ selectedBranch.next.length }}）</div>
            <div v-if="selectedBranch.next.length" class="wf-route-list">
              <div v-for="route in selectedBranch.next" :key="route.target" class="wf-route-item">
                <div class="wf-route-row">
                  <span class="icon" :style="iconSrc(ICONS.gitBranch)" aria-hidden="true"></span>
                  <span class="wf-route-row__name">{{ nodeNameOf(route.target) }}</span>
                  <button
                    type="button"
                    class="wf-icon-btn wf-icon-btn--sm"
                    :aria-label="`移除到 ${nodeNameOf(route.target)} 的出线`"
                    @click="onRemoveRoute(route.target)"
                  >
                    <span class="icon" :style="iconSrc(ICONS.x)" aria-hidden="true"></span>
                  </button>
                </div>
                <input
                  v-model="route.condition"
                  class="wf-input wf-input--sm"
                  type="text"
                  placeholder="条件：当……时走这条路"
                />
              </div>
            </div>
            <div v-else class="wf-panel__empty">无出线（死端）</div>
            <div class="wf-panel__hint">从节点右侧连桩逐条拖线；1 入 n 出 = 分线，n 入 1 出 = 并线。多条出线时每条都要写条件，AI 汇报时按条件选择去向</div>
          </div>
          <div class="wf-panel__section">
            <button
              v-if="!confirmingStageDelete"
              type="button"
              class="wf-btn wf-btn--danger-outline"
              @click="confirmingStageDelete = true"
            >
              <span class="icon" :style="iconSrc(ICONS.trash)" aria-hidden="true"></span>
              <span>删除此节点</span>
            </button>
            <div v-else class="wf-confirm-row">
              <span class="wf-confirm-row__text">删除后指向它的路由也会移除</span>
              <button type="button" class="wf-btn wf-btn--danger-confirm" @click="onDeleteStage">确认删除</button>
            </div>
          </div>
        </template>

        <!-- 开始/结束属性（选中边界节点） -->
        <template v-else-if="selectedBoundary">
          <div class="wf-panel__section">
            <div class="wf-panel__heading">
              <span>{{ selectedBoundary.kind === 'start' ? '开始节点' : '结束节点' }}</span>
              <span class="wf-panel__heading-id">{{ selectedBoundary.id }}</span>
            </div>
            <label class="wf-field">
              <span class="wf-field__label">名称</span>
              <input v-model="selectedBoundary.name" class="wf-input" type="text" />
            </label>
          </div>
          <div v-if="selectedBoundary.kind === 'start'" class="wf-panel__section">
            <div class="wf-panel__heading">入口路由</div>
            <div v-if="selectedBoundary.next" class="wf-route-list">
              <div class="wf-route-row">
                <span class="icon" :style="iconSrc(ICONS.gitBranch)" aria-hidden="true"></span>
                <span class="wf-route-row__name">{{ nodeNameOf(selectedBoundary.next) }}</span>
                <button
                  type="button"
                  class="wf-icon-btn wf-icon-btn--sm"
                  :aria-label="`断开到 ${nodeNameOf(selectedBoundary.next)} 的入口路由`"
                  @click="onRemoveSelectedRoute"
                >
                  <span class="icon" :style="iconSrc(ICONS.x)" aria-hidden="true"></span>
                </button>
              </div>
            </div>
            <div v-else class="wf-panel__empty wf-panel__empty--error">未连接（从开始节点右侧连桩拖线到首个节点）</div>
            <div class="wf-panel__hint">工作流从这里启动；开始节点只能有一个</div>
          </div>
          <div class="wf-panel__section">
            <button
              v-if="!confirmingStageDelete"
              type="button"
              class="wf-btn wf-btn--danger-outline"
              @click="confirmingStageDelete = true"
            >
              <span class="icon" :style="iconSrc(ICONS.trash)" aria-hidden="true"></span>
              <span>删除此节点</span>
            </button>
            <div v-else class="wf-confirm-row">
              <span class="wf-confirm-row__text">删除后指向它的路由也会移除</span>
              <button type="button" class="wf-btn wf-btn--danger-confirm" @click="onDeleteStage">确认删除</button>
            </div>
          </div>
        </template>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import {
  VueFlow,
  useVueFlow,
  applyNodeChanges,
  applyEdgeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import { MiniMap } from '@vue-flow/minimap';
import '@vue-flow/core/dist/style.css';
import '@vue-flow/core/dist/theme-default.css';
import '@vue-flow/controls/dist/style.css';
import '@vue-flow/minimap/dist/style.css';
import { ICONS } from '@/utils/icons';
import WorkflowStageNode from './WorkflowStageNode.vue';
import WorkflowBoundaryNode from './WorkflowBoundaryNode.vue';
import WorkflowReviewNode from './WorkflowReviewNode.vue';
import WorkflowBranchNode from './WorkflowBranchNode.vue';
import {
  addBranch,
  addReview,
  addStage,
  autoLayout,
  connectNext,
  connectRejectTo,
  disconnectNext,
  disconnectRejectTo,
  addEnd,
  addStart,
  removeNode,
  replaceBranchTarget,
  validateWorkflow,
  workflowToFlow,
  type WorkflowDef,
  type WorkflowNodeDef,
} from './workflowModel';
import { saveWorkflow } from './api';

const props = defineProps<{
  workflow: WorkflowDef;
}>();

const emit = defineEmits<{
  (event: 'back'): void;
  (event: 'save'): void;
}>();

// ---------------------------------------------------------------- 画布状态

const nodes = ref<Node[]>([]);
const edges = ref<Edge[]>([]);
const selectedStageId = ref('');
const issuesOpen = ref(false);
const flashMessage = ref('');
const dirty = ref(false);
const saving = ref(false);
const addNodeMenuOpen = ref(false);
const addNodeSelectRef = ref<HTMLElement | null>(null);
const issuesAnchorRef = ref<HTMLElement | null>(null);
const confirmingStageDelete = ref(false);
let flashTimer: ReturnType<typeof setTimeout> | null = null;

const { project, fitView, getViewport, updateNodeInternals } = useVueFlow();

const issues = computed(() => validateWorkflow(props.workflow));
const errorCount = computed(() => issues.value.filter((i) => i.level === 'error').length);
const issueLabel = computed(() => {
  if (errorCount.value > 0) return `${errorCount.value} 个错误`;
  if (issues.value.length > 0) return `${issues.value.length} 个提醒`;
  return '结构正常';
});
const issueStageIds = computed(() => new Set(issues.value.map((i) => i.nodeId).filter(Boolean) as string[]));
// 拖拽只改 position 也会触发校验重算产出新 Set 引用；用排序字符串 key 做值比较，避免拖拽中断
const issueStageKey = computed(() => Array.from(issueStageIds.value).sort().join(','));

const selectedNode = computed(() => props.workflow.nodes.find((n) => n.id === selectedStageId.value) ?? null);
const selectedStage = computed(() => (selectedNode.value?.kind === 'stage' ? selectedNode.value : null));
const selectedReview = computed(() => (selectedNode.value?.kind === 'review' ? selectedNode.value : null));
const selectedBranch = computed(() => (selectedNode.value?.kind === 'branch' ? selectedNode.value : null));
const selectedBoundary = computed(() =>
  selectedNode.value?.kind === 'start' || selectedNode.value?.kind === 'end' ? selectedNode.value : null
);

function nodeNameOf(id: string): string {
  return props.workflow.nodes.find((n) => n.id === id)?.name ?? id;
}

function rebuild() {
  // 有节点缺坐标时（初始加载/新建）先自动布局，保证回边等非线性结构可读
  if (props.workflow.nodes.length > 0 && props.workflow.nodes.some((n) => !n.position)) {
    autoLayout(props.workflow);
  }
  const flow = workflowToFlow(props.workflow, issueStageIds.value);
  nodes.value = flow.nodes;
  edges.value = flow.edges;
  // 桩位随连线数量动态增减/重排后，必须手动失效 Vue Flow 的 handle 位置缓存，
  // 否则边仍渲染在旧桩位上（线与连接点错位）
  nextTick(() => updateNodeInternals());
}

// 结构变更（连线/删除/校验状态变化）时重建画布；拖拽期间不重建避免闪断
watch(issueStageKey, rebuild);

watch(
  () => props.workflow,
  () => {
    dirty.value = true;
  },
  { deep: true }
);

// ---------------------------------------------------------------- 画布事件

function onConnect(connection: Connection) {
  const { source, target } = connection;
  if (!source || !target || source === target) return;
  const sourceHandle = connection.sourceHandle ?? '';
  const sourceNode = props.workflow.nodes.find((n) => n.id === source);
  if (!sourceNode || sourceNode.kind === 'end') return;
  if (sourceHandle.startsWith('reject-')) {
    // 菱形上/下驳回口拉出 = 驳回路由（红线，替换语义）
    const prev = sourceNode.kind === 'review' ? sourceNode.rejectTo : null;
    const err = connectRejectTo(props.workflow, source, target);
    if (err) flash(err);
    else if (prev && prev !== target) flash(`已替换原驳回路由（原驳回到「${nodeNameOf(prev)}」）`);
  } else if (sourceNode.kind === 'branch') {
    // 分支右桩：已占用桩拖线 = 修改该桩流向；空桩拖线 = 新增出线
    const slotMatch = /^out-(\d+)$/.exec(sourceHandle);
    const slotIdx = slotMatch ? parseInt(slotMatch[1], 10) : -1;
    if (slotIdx >= 0 && slotIdx < sourceNode.next.length) {
      const err = replaceBranchTarget(props.workflow, source, slotIdx, target);
      if (err) flash(err);
      else flash(`已将该出线的流向改到「${nodeNameOf(target)}」`);
    } else {
      const err = connectNext(props.workflow, source, target);
      if (err) flash(err);
    }
  } else {
    // 开始/阶段/审核右桩：单值替换语义
    const prev = sourceNode.next;
    const err = connectNext(props.workflow, source, target);
    if (err) flash(err);
    else if (prev && prev !== target) flash(`已替换原出线（原连到「${nodeNameOf(prev)}」）`);
  }
  rebuild();
}

function onNodesChange(changes: NodeChange[]) {
  let needRebuild = false;
  for (const change of changes) {
    if (change.type === 'position' && change.position) {
      const node = props.workflow.nodes.find((n) => n.id === change.id);
      if (node) node.position = { ...change.position };
    }
    if (change.type === 'remove') {
      removeNode(props.workflow, change.id);
      if (selectedStageId.value === change.id) selectedStageId.value = '';
      needRebuild = true;
    }
    if (change.type === 'select') {
      if (change.selected) {
        selectedStageId.value = change.id;
      } else if (selectedStageId.value === change.id) {
        selectedStageId.value = '';
      }
    }
  }
  nodes.value = applyNodeChanges(changes, nodes.value);
  if (needRebuild) rebuild();
}

function onEdgesChange(changes: EdgeChange[]) {
  let needRebuild = false;
  for (const change of changes) {
    if (change.type === 'remove') {
      // 驳回边 id 带 reject: 前缀；所有边都是显式连线，可直接删
      const isRejectEdge = change.id.startsWith('reject:');
      const [source, target] = (isRejectEdge ? change.id.slice(7) : change.id).split('->');
      if (!source || !target) continue;
      if (isRejectEdge) {
        disconnectRejectTo(props.workflow, source);
      } else {
        disconnectNext(props.workflow, source, target);
      }
      needRebuild = true;
    }
  }
  edges.value = applyEdgeChanges(changes, edges.value);
  if (needRebuild) rebuild();
}

function onNodeClick(event: { node: Node }) {
  selectedStageId.value = event.node.id;
  confirmingStageDelete.value = false;
}

function onPaneClick() {
  selectedStageId.value = '';
  confirmingStageDelete.value = false;
}

function onPaneDblclick(event: MouseEvent) {
  const target = event.target as HTMLElement;
  if (!target.classList.contains('vue-flow__pane')) return;
  const point = project({ x: event.clientX, y: event.clientY });
  const stage = addStage(props.workflow, { x: point.x - 105, y: point.y - 48 });
  rebuild();
  selectedStageId.value = stage.id;
}

function onAddNodeAtCenter(kind: WorkflowNodeDef['kind']) {
  addNodeMenuOpen.value = false;
  const viewport = getViewport();
  const wrapper = document.querySelector('.wf-editor__canvas');
  const rect = wrapper?.getBoundingClientRect();
  const cx = (rect?.width ?? 800) / 2;
  const cy = (rect?.height ?? 500) / 2;
  const point = {
    x: (cx - viewport.x) / viewport.zoom - 90,
    y: (cy - viewport.y) / viewport.zoom - 50,
  };
  const node =
    kind === 'review'
      ? addReview(props.workflow, point)
      : kind === 'branch'
        ? addBranch(props.workflow, point)
        : kind === 'start'
          ? addStart(props.workflow, point)
          : kind === 'end'
            ? addEnd(props.workflow, point)
            : addStage(props.workflow, point);
  rebuild();
  selectedStageId.value = node.id;
}

// ---------------------------------------------------------------- 面板操作

function onMaxRoundsInput(event: Event) {
  const raw = (event.target as HTMLInputElement).value.replace(/[^0-9]/g, '');
  (event.target as HTMLInputElement).value = raw;
  const value = parseInt(raw, 10);
  if (Number.isFinite(value) && value > 0) {
    props.workflow.maxStageRounds = value;
  }
}

function onMaxRejectsInput(event: Event) {
  const raw = (event.target as HTMLInputElement).value.replace(/[^0-9]/g, '');
  (event.target as HTMLInputElement).value = raw;
  const value = parseInt(raw, 10);
  if (Number.isFinite(value) && value > 0 && selectedReview.value) {
    selectedReview.value.maxRejects = value;
  }
}

/** 分支面板：按目标移除某条出线 */
function onRemoveRoute(target: string) {
  if (!selectedNode.value) return;
  disconnectNext(props.workflow, selectedNode.value.id, target);
  rebuild();
}

/** 阶段/审核面板：移除唯一前进/通过路由 */
function onRemoveSelectedRoute() {
  const n = selectedNode.value;
  if (!n || n.kind === 'branch' || !n.next) return;
  disconnectNext(props.workflow, n.id, n.next);
  rebuild();
}

/** 审核面板：移除唯一驳回路由 */
function onRemoveRejectRoute() {
  const n = selectedReview.value;
  if (!n?.rejectTo) return;
  disconnectRejectTo(props.workflow, n.id);
  rebuild();
}

function onDeleteStage() {
  if (!selectedNode.value) return;
  removeNode(props.workflow, selectedNode.value.id);
  selectedStageId.value = '';
  confirmingStageDelete.value = false;
  rebuild();
}

// ---------------------------------------------------------------- 顶栏操作

function onAutoLayout() {
  autoLayout(props.workflow);
  rebuild();
  nextTick(() => fitView({ padding: 0.2, maxZoom: 1 }));
}

function flash(text: string) {
  flashMessage.value = text;
  if (flashTimer) clearTimeout(flashTimer);
  flashTimer = setTimeout(() => {
    flashMessage.value = '';
  }, 2600);
}

async function onSave() {
  if (errorCount.value > 0) {
    issuesOpen.value = true;
    flash(`存在 ${errorCount.value} 个结构错误，请先修复`);
    return;
  }
  if (saving.value) return;
  saving.value = true;
  try {
    await saveWorkflow(props.workflow);
    dirty.value = false;
    emit('save');
    flash('已保存');
  } catch (err) {
    flash(err instanceof Error ? err.message : '保存失败');
  } finally {
    saving.value = false;
  }
}

function onDocumentClick(event: MouseEvent) {
  if (addNodeMenuOpen.value && addNodeSelectRef.value && !addNodeSelectRef.value.contains(event.target as HTMLElement)) {
    addNodeMenuOpen.value = false;
  }
  if (issuesOpen.value && issuesAnchorRef.value && !issuesAnchorRef.value.contains(event.target as HTMLElement)) {
    issuesOpen.value = false;
  }
}

function iconSrc(url: string) {
  return { '--icon-src': `url(${url})` } as Record<string, string>;
}

onMounted(() => {
  rebuild();
  document.addEventListener('click', onDocumentClick);
  nextTick(() => fitView({ padding: 0.2, maxZoom: 1 }));
});

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick);
  if (flashTimer) clearTimeout(flashTimer);
});
</script>

<style scoped lang="scss">
.wf-editor {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface-base);
  overflow: hidden;

  &__topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    height: 48px;
    padding: 0 12px;
    background: var(--surface-raised);
    border-bottom: 1px solid var(--border-default);
    flex-shrink: 0;
  }

  &__topbar-left,
  &__topbar-right {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }

  &__logo {
    --icon-size: 17px;
    color: var(--text-secondary);
    flex-shrink: 0;
  }

  &__name {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__badge {
    height: 20px;
    line-height: 20px;
    padding: 0 7px;
    border-radius: 5px;
    background: var(--badge-bg);
    color: var(--text-tertiary);
    font-size: 11px;
    flex-shrink: 0;
  }

  &__dirty {
    font-size: 11px;
    color: var(--state-warning);
    flex-shrink: 0;
  }

  /* 提醒/报错弹层：锚固在按钮下方，不占页面布局，固定高度 + 内部滚动 */
  &__issues-anchor {
    position: relative;
  }

  &__issues-pop {
    position: absolute;
    top: calc(100% + 6px);
    right: 0;
    width: 320px;
    max-width: calc(100vw - 32px);
    height: 240px;
    overflow-y: auto;
    padding: 8px 12px;
    border: 1px solid var(--border-default);
    border-radius: 10px;
    background: var(--surface-raised);
    box-shadow: var(--shadow-mid);
    z-index: 30;
    scrollbar-width: thin;
    scrollbar-color: color-mix(in srgb, var(--text-secondary) 55%, transparent) transparent;
  }

  &__flash {
    position: absolute;
    top: 56px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 20;
    height: 32px;
    line-height: 32px;
    padding: 0 14px;
    border-radius: 8px;
    background: var(--surface-raised);
    border: 1px solid var(--border-default);
    box-shadow: var(--shadow-mid);
    font-size: 12px;
    color: var(--text-secondary);
    pointer-events: none;
  }

  &__body {
    display: flex;
    flex: 1;
    min-height: 0;
  }

  &__canvas {
    flex: 1;
    min-width: 0;
    position: relative;
  }
}

.wf-flow {
  width: 100%;
  height: 100%;
}

.wf-issue {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 28px;
  font-size: 12px;

  .icon {
    --icon-size: 13px;
    flex-shrink: 0;
  }

  &--error {
    color: var(--state-danger);
  }

  &--warning {
    color: var(--state-warning);
  }
}

/* ---------------------------------------------------------------- 右侧面板 */

.wf-panel {
  width: 300px;
  flex-shrink: 0;
  background: var(--surface-raised);
  border-left: 1px solid var(--border-default);
  overflow-y: auto;
  padding: 16px 14px 32px;
  display: flex;
  flex-direction: column;
  gap: 20px;

  &__heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 24px;
    font-size: 12px;
    font-weight: 650;
    color: var(--text-tertiary);
    letter-spacing: 0.04em;
    margin-bottom: 10px;
  }

  &__heading-id {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-weight: 400;
  }

  &__empty {
    font-size: 12px;
    color: var(--text-tertiary);
    padding: 4px 0;

    &--error {
      color: var(--state-danger);
    }
  }

  &__hint {
    font-size: 11px;
    color: var(--text-muted);
    margin-top: 6px;
  }
}

.wf-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-bottom: 12px;

  &__label {
    font-size: 12px;
    color: var(--text-secondary);

    &--inline {
      cursor: default;
    }
  }

  &__hint {
    font-size: 11px;
    color: var(--text-muted);
  }
}

.wf-input,
.wf-textarea {
  width: 100%;
  padding: 0 10px;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--surface-base);
  color: var(--text-primary);
  font-size: 13px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.12s ease;

  &:focus {
    border-color: var(--accent);
  }

  &::placeholder {
    color: var(--text-muted);
  }
}

.wf-input {
  height: 32px;

  &--sm {
    height: 26px;
    font-size: 11px;
  }
}

.wf-textarea {
  padding: 8px 10px;
  line-height: 1.55;
  resize: vertical;
  min-height: 56px;
}

/* 自定义开关（禁原生 checkbox） */
.wf-toggle-row {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 24px;
}

.wf-toggle {
  position: relative;
  width: 34px;
  height: 20px;
  border: none;
  border-radius: 10px;
  background: var(--switch-track);
  cursor: pointer;
  transition: background-color 0.15s ease;
  flex-shrink: 0;

  &__knob {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--surface-raised);
    box-shadow: var(--shadow-soft);
    transition: transform 0.15s ease;
  }

  &--on {
    background: var(--accent);

    .wf-toggle__knob {
      transform: translateX(14px);
    }
  }
}

/* 分段控件（审核模式） */
.wf-segment {
  display: flex;
  height: 32px;
  padding: 2px;
  border-radius: 8px;
  background: var(--surface-soft);
  border: 1px solid var(--border-default);

  &__item {
    flex: 1;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--text-tertiary);
    font-size: 12px;
    font-family: inherit;
    cursor: pointer;
    transition: background-color 0.12s ease, color 0.12s ease;

    &--active {
      background: var(--surface-raised);
      color: var(--text-primary);
      box-shadow: var(--shadow-soft);
    }
  }
}

/* 自定义下拉（入口阶段，禁原生 select） */
.wf-select {
  position: relative;

  /* 顶栏变体：菜单右对齐触发器、固定宽度（触发器是窄按钮，默认 left:0/right:0 会把菜单压成按钮同宽） */
  &--toolbar {
    .wf-select__menu {
      left: auto;
      right: 0;
      width: 216px;
    }
  }

  &__caret {
    --icon-size: 13px;
    color: var(--text-tertiary);
  }

  &__menu {
    position: absolute;
    top: 36px;
    left: 0;
    right: 0;
    z-index: 30;
    max-height: 220px;
    overflow-y: auto;
    padding: 4px;
    border: 1px solid var(--border-default);
    border-radius: 9px;
    background: var(--surface-raised);
    box-shadow: var(--shadow-mid);
  }

  &__option {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    height: 30px;
    padding: 0 8px;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--text-secondary);
    font-size: 12px;
    font-family: inherit;
    cursor: pointer;
    text-align: left;

    &:hover {
      background: var(--hover-bg);
    }

    /* 条目前置类型图标（非选中勾）：固定尺寸、不挤压文字 */
    .icon {
      --icon-size: 13px;
      flex-shrink: 0;
      color: var(--text-tertiary);
    }
  }

  &__option-name {
    flex-shrink: 0;
    white-space: nowrap;
    color: var(--text-primary);
  }

  /* 条目说明文字：右对齐弱化显示，过长省略号（固定行高不换装行） */
  &__option-desc {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-align: right;
    color: var(--text-muted);
    font-size: 11px;
  }
}

/* 路由列表 */
.wf-route-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* 分支出线项：目标行 + 条件输入框 */
.wf-route-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;

  &:last-child {
    margin-bottom: 0;
  }
}

.wf-route-row {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 30px;
  padding: 0 6px;
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-secondary);

  &:hover {
    background: var(--hover-bg);
  }

  > .icon {
    --icon-size: 13px;
    color: var(--text-tertiary);
    flex-shrink: 0;
  }

  > .icon.wf-route-row__icon--back {
    color: var(--state-danger);
  }

  > .icon.wf-route-row__icon--pass {
    color: var(--state-info);
  }

  &__name {
    flex: 1;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}

.wf-file-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.wf-file-row {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 30px;
  padding: 0 6px;
  font-size: 12px;
  color: var(--text-secondary);

  > .icon {
    --icon-size: 13px;
    color: var(--text-tertiary);
    flex-shrink: 0;
  }

  &__name {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}

.wf-confirm-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;

  &__text {
    font-size: 11px;
    color: var(--text-tertiary);
  }
}

/* demo 内共享按钮体系 */
.wf-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--surface-raised);
  color: var(--text-secondary);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  transition: background-color 0.12s ease, color 0.12s ease;

  .icon {
    --icon-size: 14px;
  }

  &:hover {
    background: var(--hover-bg);
    color: var(--text-primary);
  }

  &:disabled {
    cursor: default;
    opacity: 0.55;
  }

  &--ghost {
    border-color: transparent;
    background: transparent;

    &:hover {
      background: var(--hover-bg);
    }
  }

  &--issue {
    color: var(--state-danger);

    &:hover {
      color: var(--state-danger);
    }
  }

  &--primary {
    border-color: var(--accent);
    background: var(--accent);
    color: var(--on-accent);

    &:hover {
      background: var(--accent-hover);
      color: var(--on-accent);
    }
  }

  &--danger-outline {
    width: 100%;
    justify-content: center;
    border-color: var(--border-default);
    color: var(--state-danger);

    &:hover {
      background: var(--hover-bg);
      color: var(--state-danger);
    }
  }

  &--danger-confirm {
    border-color: var(--state-danger);
    background: var(--state-danger);
    color: var(--on-accent);
    flex-shrink: 0;

    &:hover {
      background: var(--state-danger-strong);
      color: var(--on-accent);
    }
  }
}

.wf-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  flex-shrink: 0;
  transition: background-color 0.12s ease, color 0.12s ease;

  .icon {
    --icon-size: 15px;
  }

  &:hover {
    background: var(--hover-bg);
    color: var(--text-primary);
  }

  &--sm {
    width: 24px;
    height: 24px;

    .icon {
      --icon-size: 12px;
    }
  }
}
</style>

<!-- Vue Flow 默认样式 token 化（三主题随 token 自动适配；与组件基础定义同文件） -->
<style lang="scss">
.wf-editor__canvas {
  /* 边语义三色：白=下一步，蓝=审核通过，红=审核不通过回退 */
  .vue-flow__edge-path {
    stroke: var(--text-secondary);
    stroke-width: 1.5;
  }

  .vue-flow__edge.wf-edge-approved .vue-flow__edge-path {
    stroke: var(--state-info);
  }

  .vue-flow__edge.wf-edge-back .vue-flow__edge-path {
    stroke: var(--state-danger);
  }

  .vue-flow__edge:not(.wf-edge-approved):not(.wf-edge-back):hover .vue-flow__edge-path {
    stroke: var(--text-primary);
  }

  /* 选中 = 高光（提亮 + 加粗），不是变灰；语义色边选中保持本色仅加粗 */
  .vue-flow__edge .vue-flow__edge-path {
    transition: stroke-width 0.12s ease;
  }

  .vue-flow__edge.selected .vue-flow__edge-path {
    stroke: var(--text-primary);
    stroke-width: 2.5;
  }

  .vue-flow__edge.wf-edge-approved.selected .vue-flow__edge-path {
    stroke: var(--state-info);
    stroke-width: 2.5;
  }

  .vue-flow__edge.wf-edge-back.selected .vue-flow__edge-path {
    stroke: var(--state-danger);
    stroke-width: 2.5;
  }

  .vue-flow__connection-path {
    stroke: var(--accent);
    stroke-width: 1.5;
  }

  .vue-flow__handle {
    width: 8px;
    height: 8px;
    background: var(--surface-raised);
    border: 1.5px solid var(--text-tertiary);
  }

  .vue-flow__handle:hover {
    border-color: var(--accent);
  }

  .vue-flow__node.selectable:focus {
    outline: none;
  }

  .vue-flow__controls {
    box-shadow: var(--shadow-soft);
    border-radius: 8px;
    overflow: hidden;
  }

  .vue-flow__controls-button {
    background: var(--surface-raised);
    border-bottom: 1px solid var(--border-default);
    color: var(--text-secondary);
    width: 26px;
    height: 26px;

    &:hover {
      background: var(--hover-bg);
      color: var(--text-primary);
    }

    svg {
      fill: currentColor;
    }
  }

  .vue-flow__minimap {
    background: var(--surface-soft);
    border: 1px solid var(--border-default);
    border-radius: 8px;
  }

  .vue-flow__background {
    background: var(--surface-base);
  }

  .vue-flow__edge-text {
    fill: var(--text-tertiary);
    font-size: 10px;
  }

  .vue-flow__edge-textbg {
    fill: var(--surface-base);
  }
}
</style>
