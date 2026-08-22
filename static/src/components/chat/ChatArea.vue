<template>
  <div class="chat-area-shell" ref="shellRef">
    <div
      class="messages-area messages-area--stick"
      :class="{ 'messages-area--render-pending': renderPending }"
      ref="scrollRef"
    >
    <div class="messages-flow" ref="contentRef">
      <!-- 窗口化渲染：virtua 只挂载可视区+buffer 的消息块，未挂载项按实测高度缓存占位 -->
      <Virtualizer
        ref="virtualizerRef"
        v-if="stickScrollElement"
        :data="filteredMessages || []"
        :scroll-ref="stickScrollElement"
        :buffer-size="400"
        :keep-mounted="keepMountedIndexes"
      >
        <template #default="{ item: msg, index }">
      <div
        :key="getMessageKey(msg, index)"
        class="message-block"
        :class="{
          'message-block--compact-user': getMessageVisibility(msg) === 'compact',
          'message-block--sub-agent-notice': isSubAgentNoticeOnlyMessage(msg),
          'message-block--before-sub-agent-notice': isFollowedBySubAgentNotice(index),
          'message-block--multi-agent': isMultiAgentMessage(msg),
          'message-block--first': index === 0,
          'message-block--last': index === (filteredMessages || []).length - 1
        }"
      >
        <div
          v-if="msg.role === 'user'"
          class="user-message"
          :class="{
            'user-message--multi-agent': isMultiAgentMessage(msg),
            'user-message--compact': getMessageVisibility(msg) === 'compact',
            'user-message--brief': isBriefCompactMessage(msg)
          }"
        >
          <div v-if="isBriefCompactMessage(msg)" class="compact-brief-line" role="separator">
            <span class="compact-brief-text">{{ compactBriefLabel(msg) }}</span>
          </div>
          <template v-else>
            <div
              class="message-header icon-label"
              :class="{
                'message-header--multi-agent': isMultiAgentMessage(msg),
                'message-header--hidden':
                  isMultiAgentMessage(msg) && isContinuousMultiAgentMessage(msg, index)
              }"
            >
              <template v-if="isMultiAgentMessage(msg)">
                <span>{{ multiAgentHeaderLabel(msg) }}</span>
              </template>
              <template v-else>
                <span
                  class="icon icon-sm"
                  :style="iconStyleSafe(userHeaderIconKey(msg))"
                  aria-hidden="true"
                ></span>
                <span>{{ userHeaderLabel(msg) }}</span>
              </template>
            </div>
            <div
              class="message-text user-bubble-text"
              :class="{
                'is-collapsed': isUserBubbleCollapsed(msg, index),
                'is-expandable': isUserBubbleExpandable(msg, index),
                'is-history-loading': props.historyLoading || userBubbleTransitionSuppressed
              }"
            >
              <div
                v-if="msg.content"
                class="bubble-text"
                :class="{ 'is-expanded': isUserBubbleExpanded(msg, index) }"
                :ref="(el) => registerUserBubbleRef(msg, index, el)"
              >
                <template v-if="isMultiAgentMessage(msg)">
                  <MarkdownRenderer
                    :content="multiAgentBubbleContent(msg)"
                    :is-streaming="false"
                  />
                </template>
                <template v-else>
                  <span v-html="renderUserMessageContent(msg.content)"></span>
                </template>
              </div>
              <div
                v-if="(msg.images && msg.images.length) || messageFiles(msg).length"
                class="image-inline-row"
              >
                <div
                  class="image-thumbnail-wrapper"
                  v-for="(img, imgIndex) in msg.images"
                  :key="mediaPreviewKey(msg, img, imgIndex)"
                  @click.stop="previewMessageImage(msg, img)"
                >
                  <img
                    :src="getPreviewUrl(msg, img, 'image')"
                    :alt="formatImageName(img)"
                    class="image-thumbnail"
                  />
                </div>
                <FileChips v-if="messageFiles(msg).length" :files="messageFiles(msg)" />
              </div>
              <div v-if="msg.videos && msg.videos.length" class="image-inline-row video-inline-row">
                <div
                  class="image-thumbnail-wrapper"
                  v-for="(video, videoIndex) in msg.videos"
                  :key="mediaPreviewKey(msg, video, videoIndex)"
                >
                  <img
                    :src="getPreviewUrl(msg, video, 'video')"
                    :alt="formatImageName(video)"
                    class="image-thumbnail"
                  />
                </div>
              </div>
              <div
                v-if="isUserBubbleExpandable(msg, index)"
                class="user-bubble-expand-row"
              >
                <button
                  class="user-bubble-action-btn user-bubble-expand-btn"
                  :class="{ expanded: isUserBubbleExpanded(msg, index) }"
                  :title="isUserBubbleExpanded(msg, index) ? '收起' : '展开'"
                  :aria-label="isUserBubbleExpanded(msg, index) ? '收起' : '展开'"
                  @click.stop="toggleUserBubble(msg, index)"
                ></button>
              </div>
            </div>
            <div v-if="!isMultiAgentMessage(msg)" class="user-bubble-actions">
              <button
                class="user-bubble-action-btn copy"
                :class="{ copied: isUserBubbleCopied(msg, index) }"
                :title="isUserBubbleCopied(msg, index) ? '已复制' : '复制'"
                :aria-label="isUserBubbleCopied(msg, index) ? '已复制' : '复制'"
                @click="copyUserMessage(msg, index)"
              ></button>
              <button
                class="user-bubble-action-btn branch"
                title="分支"
                aria-label="分支"
                @click="branchUserMessage(msg)"
              ></button>
              <span class="user-bubble-time">{{ formatUserMessageTime(msg) }}</span>
            </div>
          </template>
        </div>
        <div v-else-if="msg.role === 'assistant'" class="assistant-message">
          <!-- 在 assistant 消息前显示 Astrion 头部 -->
          <!-- 只有当前一条消息是 user 且当前消息有内容时才显示 -->
          <div
            v-if="
              shouldShowAssistantHeader(index) &&
              (hasRenderableAssistantActions(msg.actions || []) || msg.awaitingFirstContent)
            "
            class="message-header icon-label"
          >
            <span>{{ aiAssistantName }}</span>
            <span v-if="assistantWorkLabel(index)" class="assistant-work-status">{{
              assistantWorkLabel(index)
            }}</span>
          </div>
          <div
            v-if="msg.awaitingFirstContent"
            class="action-item streaming-content immediate-show assistant-generating-block"
          >
            <div class="text-output">
              <div
                class="text-content assistant-generating-placeholder"
                role="status"
                aria-live="polite"
              >
                <span
                  v-for="(letter, letterIndex) in getGeneratingLetters(msg)"
                  :key="letterIndex"
                  class="assistant-generating-letter"
                  :style="{ animationDelay: `${letterIndex * 0.08}s` }"
                >
                  {{ letter }}
                </span>
              </div>
            </div>
          </div>
          <template v-if="blockDisplayMode === 'minimal'">
            <MinimalBlocks
              v-if="hasRenderableAssistantActions(msg.actions || [])"
              :actions="msg.actions || []"
              :conversation-running="streamingMessage"
              :is-latest-message="index === latestMessageIndex"
              :icon-style="iconStyleSafe"
              :get-tool-icon="getToolIcon"
              :get-tool-status-text="getToolStatusText"
              :get-tool-description="getToolDescription"
              :format-search-topic="formatSearchTopic"
              :format-search-time="formatSearchTime"
              :format-search-domains="formatSearchDomains"
              :register-thinking-ref="registerThinkingRef"
              :handle-thinking-scroll="props.handleThinkingScroll"
              @group-toggle="handleMinimalGroupToggle"
            />
          </template>
          <template v-else-if="stackedBlocksEnabled">
            <template v-for="group in splitActionGroups(msg.actions || [], index)" :key="group.key">
              <StackedBlocks
                v-if="group.kind === 'stack'"
                class="stacked-blocks-wrapper"
                :actions="group.actions"
                :expanded-blocks="expandedBlocks"
                :conversation-running="streamingMessage"
                :is-latest-message="index === latestMessageIndex"
                :icon-style="iconStyleSafe"
                :toggle-block="toggleBlock"
                :register-thinking-ref="registerThinkingRef"
                :handle-thinking-scroll="handleThinkingScroll"
                :get-tool-animation-class="getToolAnimationClass"
                :get-tool-icon="getToolIcon"
                :get-tool-status-text="getToolStatusText"
                :get-tool-description="getToolDescription"
                :format-search-topic="formatSearchTopic"
                :format-search-time="formatSearchTime"
                :format-search-domains="formatSearchDomains"
                @more-toggle="handleStackedMoreToggle"
              />
              <div
                v-else-if="isActionVisible(group.action)"
                class="action-item"
                :key="group.action?.id || `${index}-${group.actionIndex}`"
                :class="{
                  'streaming-content': group.action?.streaming,
                  'completed-tool': group.action?.type === 'tool' && !group.action?.streaming,
                  'immediate-show':
                    group.action?.streaming ||
                    group.action?.type === 'text' ||
                    group.action?.type === 'thinking',
                  'thinking-finished': group.action?.type === 'thinking' && !group.action?.streaming,
                  'no-entry-animation': !streamingMessage || index !== latestMessageIndex
                }"
              >
                <div
                  v-if="group.action?.type === 'thinking'"
                  class="collapsible-block thinking-block"
                  :class="{
                    expanded: expandedBlocks?.has(
                      group.action.blockId || `${index}-thinking-${group.actionIndex}`
                    )
                  }"
                  :data-block-id="group.action.blockId || `${index}-thinking-${group.actionIndex}`"
                >
                  <div
                    class="collapsible-header"
                    @click="
                      toggleBlock(group.action.blockId || `${index}-thinking-${group.actionIndex}`)
                    "
                  >
                    <div class="arrow"></div>
                    <div class="status-icon">
                      <span
                        class="thinking-icon"
                        :class="{ 'thinking-animation': group.action.streaming }"
                      >
                        <span
                          class="icon icon-sm"
                          :style="iconStyleSafe('brain')"
                          aria-hidden="true"
                        ></span>
                      </span>
                    </div>
                    <span class="status-text">{{
                      group.action.streaming ? '正在思考...' : '思考过程'
                    }}</span>
                  </div>
                  <div
                    class="collapsible-content"
                    :ref="
                      (el) =>
                        registerCollapseContent(
                          group.action.blockId || `${index}-thinking-${group.actionIndex}`,
                          el
                        )
                    "
                  >
                    <div
                      class="content-inner thinking-content"
                      :ref="
                        (el) =>
                          registerThinkingRef(
                            group.action.blockId || `${index}-thinking-${group.actionIndex}`,
                            el
                          )
                      "
                      @scroll="
                        handleThinkingScroll(
                          group.action.blockId || `${index}-thinking-${group.actionIndex}`,
                          $event
                        )
                      "
                      style="max-height: 240px; overflow-y: auto"
                    >
                      {{ group.action.content }}
                    </div>
                  </div>
                </div>

                <div v-else-if="group.action?.type === 'text'" class="text-output">
                  <div class="text-content" :class="{ 'streaming-text': group.action.streaming }">
                    <MarkdownRenderer
                      :content="group.action.content || ''"
                      :is-streaming="group.action.streaming"
                    />
                  </div>
                </div>

                <div
                  v-else-if="
                    group.action?.type === 'system' && getSubAgentSystemNoticeLabel(group.action)
                  "
                  class="sub-agent-system-summary-line"
                >
                  {{ getSubAgentSystemNoticeLabel(group.action) }}
                </div>

                <div
                  v-else-if="group.action?.type === 'append_payload'"
                  class="append-placeholder"
                  :class="{ 'append-error': group.action.append?.success === false }"
                >
                  <div class="append-placeholder-content">
                    <template v-if="group.action.append?.success !== false">
                      <div class="icon-label append-status">
                        <span
                          class="icon icon-sm"
                          :style="iconStyleSafe('pencil')"
                          aria-hidden="true"
                        ></span>
                        <span
                          >已写入
                          {{ group.action.append?.path || '目标文件' }}
                          的追加内容（内容已保存至文件）</span
                        >
                      </div>
                    </template>
                    <template v-else>
                      <div class="icon-label append-status append-error-text">
                        <span
                          class="icon icon-sm"
                          :style="iconStyleSafe('x')"
                          aria-hidden="true"
                        ></span>
                        <span
                          >向
                          {{ group.action.append?.path || '目标文件' }}
                          写入失败，内容已截获供后续修复。</span
                        >
                      </div>
                    </template>
                    <div class="append-meta" v-if="group.action.append">
                      <span
                        v-if="
                          group.action.append.lines !== null &&
                          group.action.append.lines !== undefined
                        "
                      >
                        · 行数 {{ group.action.append.lines }}
                      </span>
                      <span
                        v-if="
                          group.action.append.bytes !== null &&
                          group.action.append.bytes !== undefined
                        "
                      >
                        · 字节 {{ group.action.append.bytes }}
                      </span>
                    </div>
                    <div class="append-warning icon-label" v-if="group.action.append?.forced">
                      <span
                        class="icon icon-sm"
                        :style="iconStyleSafe('triangleAlert')"
                        aria-hidden="true"
                      ></span>
                      <span>未检测到结束标记，请根据提示继续补充。</span>
                    </div>
                  </div>
                </div>

                <div
                  v-else-if="group.action?.type === 'append'"
                  class="append-placeholder"
                  :class="{ 'append-error': group.action.append?.success === false }"
                >
                  <div class="append-placeholder-content">
                    <div class="icon-label append-status">
                      <span
                        class="icon icon-sm"
                        :style="iconStyleSafe('pencil')"
                        aria-hidden="true"
                      ></span>
                      <span>{{ group.action.append?.summary || '文件追加完成' }}</span>
                    </div>
                    <div class="append-meta" v-if="group.action.append">
                      <span>{{ group.action.append.path || '目标文件' }}</span>
                      <span v-if="group.action.append.lines"
                        >· 行数 {{ group.action.append.lines }}</span
                      >
                      <span v-if="group.action.append.bytes"
                        >· 字节 {{ group.action.append.bytes }}</span
                      >
                    </div>
                    <div class="append-warning icon-label" v-if="group.action.append?.forced">
                      <span
                        class="icon icon-sm"
                        :style="iconStyleSafe('triangleAlert')"
                        aria-hidden="true"
                      ></span>
                      <span>未检测到结束标记，请按提示继续补充。</span>
                    </div>
                  </div>
                </div>

                <ToolAction
                  v-else-if="group.action?.type === 'tool'"
                  :action="group.action"
                  :expanded="
                    expandedBlocks?.has(
                      group.action.blockId || `${index}-tool-${group.actionIndex}`
                    )
                  "
                  :icon-style="iconStyleSafe"
                  :get-tool-animation-class="getToolAnimationClass"
                  :get-tool-icon="getToolIcon"
                  :get-tool-status-text="getToolStatusText"
                  :get-tool-description="getToolDescription"
                  :format-search-topic="formatSearchTopic"
                  :format-search-time="formatSearchTime"
                  :format-search-domains="formatSearchDomains"
                  :streaming-message="streamingMessage"
                  :register-collapse-content="registerCollapseContent"
                  :collapse-key="group.action.blockId || `${index}-tool-${group.actionIndex}`"
                  :block-id="group.action.blockId || `${index}-tool-${group.actionIndex}`"
                  @toggle="
                    toggleBlock(group.action.blockId || `${index}-tool-${group.actionIndex}`)
                  "
                />
              </div>
            </template>
          </template>

          <template v-else>
            <template
              v-for="(action, actionIndex) in msg.actions || []"
              :key="action.id || `${index}-${actionIndex}`"
            >
              <div
                v-if="isActionVisible(action)"
                class="action-item"
                :class="{
                  'streaming-content': action.streaming,
                  'completed-tool': action.type === 'tool' && !action.streaming,
                  'immediate-show':
                    action.streaming || action.type === 'text' || action.type === 'thinking',
                  'thinking-finished': action.type === 'thinking' && !action.streaming,
                  'no-entry-animation': !streamingMessage || index !== latestMessageIndex
                }"
              >
                <div
                  v-if="action.type === 'thinking'"
                  class="collapsible-block thinking-block"
                  :class="{
                    expanded: expandedBlocks?.has(
                      action.blockId || `${index}-thinking-${actionIndex}`
                    )
                  }"
                  :data-block-id="action.blockId || `${index}-thinking-${actionIndex}`"
                >
                  <div
                    class="collapsible-header"
                    @click="toggleBlock(action.blockId || `${index}-thinking-${actionIndex}`)"
                  >
                    <div class="arrow"></div>
                    <div class="status-icon">
                      <span
                        class="thinking-icon"
                        :class="{ 'thinking-animation': action.streaming }"
                      >
                        <span
                          class="icon icon-sm"
                          :style="iconStyleSafe('brain')"
                          aria-hidden="true"
                        ></span>
                      </span>
                    </div>
                    <span class="status-text">{{
                      action.streaming ? '正在思考...' : '思考过程'
                    }}</span>
                  </div>
                  <div
                    class="collapsible-content"
                    :ref="
                      (el) =>
                        registerCollapseContent(
                          action.blockId || `${index}-thinking-${actionIndex}`,
                          el
                        )
                    "
                  >
                    <div
                      class="content-inner thinking-content"
                      :ref="
                        (el) =>
                          registerThinkingRef(
                            action.blockId || `${index}-thinking-${actionIndex}`,
                            el
                          )
                      "
                      @scroll="
                        handleThinkingScroll(
                          action.blockId || `${index}-thinking-${actionIndex}`,
                          $event
                        )
                      "
                      style="max-height: 240px; overflow-y: auto"
                    >
                      {{ action.content }}
                    </div>
                  </div>
                </div>

                <div v-else-if="action.type === 'text'" class="text-output">
                  <div class="text-content" :class="{ 'streaming-text': action.streaming }">
                    <MarkdownRenderer
                      :content="action.content || ''"
                      :is-streaming="action.streaming"
                    />
                  </div>
                </div>

                <div
                  v-else-if="action.type === 'system' && getSubAgentSystemNoticeLabel(action)"
                  class="sub-agent-system-summary-line"
                >
                  {{ getSubAgentSystemNoticeLabel(action) }}
                </div>

                <div
                  v-else-if="action.type === 'append_payload'"
                  class="append-placeholder"
                  :class="{ 'append-error': action.append?.success === false }"
                >
                  <div class="append-placeholder-content">
                    <template v-if="action.append?.success !== false">
                      <div class="icon-label append-status">
                        <span
                          class="icon icon-sm"
                          :style="iconStyleSafe('pencil')"
                          aria-hidden="true"
                        ></span>
                        <span
                          >已写入
                          {{ action.append?.path || '目标文件' }}
                          的追加内容（内容已保存至文件）</span
                        >
                      </div>
                    </template>
                    <template v-else>
                      <div class="icon-label append-status append-error-text">
                        <span
                          class="icon icon-sm"
                          :style="iconStyleSafe('x')"
                          aria-hidden="true"
                        ></span>
                        <span
                          >向
                          {{ action.append?.path || '目标文件' }}
                          写入失败，内容已截获供后续修复。</span
                        >
                      </div>
                    </template>
                    <div class="append-meta" v-if="action.append">
                      <span
                        v-if="action.append.lines !== null && action.append.lines !== undefined"
                      >
                        · 行数 {{ action.append.lines }}
                      </span>
                      <span
                        v-if="action.append.bytes !== null && action.append.bytes !== undefined"
                      >
                        · 字节 {{ action.append.bytes }}
                      </span>
                    </div>
                    <div class="append-warning icon-label" v-if="action.append?.forced">
                      <span
                        class="icon icon-sm"
                        :style="iconStyleSafe('triangleAlert')"
                        aria-hidden="true"
                      ></span>
                      <span>未检测到结束标记，请根据提示继续补充。</span>
                    </div>
                  </div>
                </div>

                <div
                  v-else-if="action.type === 'append'"
                  class="append-placeholder"
                  :class="{ 'append-error': action.append?.success === false }"
                >
                  <div class="append-placeholder-content">
                    <div class="icon-label append-status">
                      <span
                        class="icon icon-sm"
                        :style="iconStyleSafe('pencil')"
                        aria-hidden="true"
                      ></span>
                      <span>{{ action.append?.summary || '文件追加完成' }}</span>
                    </div>
                    <div class="append-meta" v-if="action.append">
                      <span>{{ action.append.path || '目标文件' }}</span>
                      <span v-if="action.append.lines">· 行数 {{ action.append.lines }}</span>
                      <span v-if="action.append.bytes">· 字节 {{ action.append.bytes }}</span>
                    </div>
                    <div class="append-warning icon-label" v-if="action.append?.forced">
                      <span
                        class="icon icon-sm"
                        :style="iconStyleSafe('triangleAlert')"
                        aria-hidden="true"
                      ></span>
                      <span>未检测到结束标记，请按提示继续补充。</span>
                    </div>
                  </div>
                </div>

                <ToolAction
                  v-else-if="action.type === 'tool'"
                  :action="action"
                  :expanded="expandedBlocks?.has(action.blockId || `${index}-tool-${actionIndex}`)"
                  :icon-style="iconStyleSafe"
                  :get-tool-animation-class="getToolAnimationClass"
                  :get-tool-icon="getToolIcon"
                  :get-tool-status-text="getToolStatusText"
                  :get-tool-description="getToolDescription"
                  :format-search-topic="formatSearchTopic"
                  :format-search-time="formatSearchTime"
                  :format-search-domains="formatSearchDomains"
                  :streaming-message="streamingMessage"
                  :register-collapse-content="registerCollapseContent"
                  :collapse-key="action.blockId || `${index}-tool-${actionIndex}`"
                  :block-id="action.blockId || `${index}-tool-${actionIndex}`"
                  @toggle="toggleBlock(action.blockId || `${index}-tool-${actionIndex}`)"
                />
              </div>
            </template>
          </template>

          <!-- 本次工作编辑摘要：仅在该工作段最后一条 assistant 末尾渲染 -->
          <EditSummaryCard
            v-if="editSummarySegmentEnds.has(index)"
            :summary="editSummarySegmentEnds.get(index)"
            :icon-style="iconStyleSafe"
          />
        </div>
        <div v-else class="system-message">
          <div
            class="collapsible-block system-block"
            :class="{ expanded: expandedBlocks?.has(`system-${index}`) }"
            :data-block-id="`system-${index}`"
          >
            <div class="collapsible-header" @click="toggleBlock(`system-${index}`)">
              <div class="arrow"></div>
              <div class="status-icon">
                <span
                  class="tool-icon icon icon-md"
                  :style="iconStyleSafe('info')"
                  aria-hidden="true"
                ></span>
              </div>
              <span class="status-text">系统消息 (role: {{ msg.role }})</span>
            </div>
            <div
              class="collapsible-content"
              :ref="(el) => registerCollapseContent(`system-${index}`, el)"
            >
              <div class="content-inner">
                {{ msg.content }}
              </div>
            </div>
          </div>
        </div>
      </div>
        </template>
      </Virtualizer>
    </div>
    <div class="messages-bottom-spacer" aria-hidden="true"></div>
    </div>
    <!-- 左侧用户输入快捷跳转导航：每个真实用户输入一根横线，hover 波浪 + 预览，点击跳转 -->
    <nav
      v-if="quickNavItems.length > 0 && quickNavMode !== 'hidden'"
      class="chat-quick-nav"
      :class="{ 'chat-quick-nav--center': quickNavMode === 'center' }"
      :style="quickNavNavStyle"
      aria-label="用户输入快捷跳转"
      @mousemove="onQuickNavMouseMove"
      @mouseleave="onQuickNavMouseLeave"
    >
      <div
        v-for="(item, i) in quickNavItems"
        :key="item.msgIndex"
        class="qn-line"
        :class="{ 'is-hot': quickNavActiveIndex === i, 'is-current': quickNavCurrentIndex === i }"
        :style="{ width: quickNavLineWidth(i) + 'px' }"
        :ref="(el) => registerQuickNavLine(i, el)"
        @click="onQuickNavLineClick(item)"
      ></div>
    </nav>
    <div
      class="quick-nav-preview"
      :class="{ 'is-visible': quickNavActiveIndex >= 0 }"
      :style="quickNavPreviewStyle"
    >
      <div class="quick-nav-preview-user">{{ quickNavPreviewUser }}</div>
      <div class="quick-nav-preview-ai">
        <MarkdownRenderer :content="quickNavPreviewAi" :is-streaming="false" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue';
import { useStickToBottom } from 'vue-stick-to-bottom';
import { Virtualizer } from 'virtua/vue';
import { useBlockExpansionAnchor } from '@/composables/useBlockExpansionAnchor';
import ToolAction from '@/components/chat/actions/ToolAction.vue';
import StackedBlocks from './StackedBlocks.vue';
import MinimalBlocks from './MinimalBlocks.vue';
import MarkdownRenderer from './MarkdownRenderer.vue';
import EditSummaryCard from './EditSummaryCard.vue';
import { usePersonalizationStore } from '@/stores/personalization';
import { useUiStore } from '@/stores/ui';
import FileChips from '@/components/chat/FileChips.vue';
import { getMessageVisibility, messageStartsWork } from '@/utils/messageVisibility';

const props = defineProps<{
  messages: Array<any>;
  iconStyle: (key: string, size?: string) => Record<string, string>;
  expandedBlocks: Set<string>;
  streamingMessage: boolean;
  toggleBlock: (blockId: string) => void;
  handleThinkingScroll: (blockId: string, event: Event) => void;
  renderMarkdown: (content: string, isStreaming: boolean) => string;
  getToolIcon: (tool: any) => string;
  getToolStatusText: (tool: any) => string;
  getToolAnimationClass: (tool: any) => string | Record<string, unknown>;
  getToolDescription: (tool: any) => string;
  formatSearchTopic: (filters: Record<string, any>) => string;
  formatSearchTime: (filters: Record<string, any>) => string;
  formatSearchDomains: (filters: Record<string, any>) => string;
  historyLoading?: boolean;
}>();
const emit = defineEmits<{
  (
    event: 'stick-state-change',
    payload: { isAtBottom: boolean; isNearBottom: boolean; escapedFromLock: boolean }
  ): void;
  (event: 'user-scroll-intent', payload: { ts: number; delta: number; top: number }): void;
}>();

const personalization = usePersonalizationStore();
const personalizationReady = computed(() => {
  return !!personalization.loaded || !!personalization.error;
});
const renderPending = computed(() => {
  return !!props.historyLoading || !personalizationReady.value;
});
const blockDisplayMode = computed(() => {
  return personalization.experiments.blockDisplayMode || 'stacked';
});
const compactMessageDisplay = computed(() => {
  return (
    personalization.form.compact_message_display ||
    personalization.experiments.compactMessageDisplay ||
    'full'
  );
});
const stackedBlocksEnabled = computed(() => {
  // 堆叠模式和极简模式都使用堆叠布局
  return blockDisplayMode.value === 'stacked' || blockDisplayMode.value === 'minimal';
});
const aiAssistantName = computed(() => {
  if (!personalization.form.use_custom_names) {
    return 'Astrion';
  }
  return personalization.form.self_identify || 'Astrion';
});
const userName = computed(() => {
  if (!personalization.form.use_custom_names) {
    return '用户';
  }
  return personalization.form.user_name || '用户';
});

// 用户输入气泡折叠相关状态与逻辑
interface UserBubbleFoldState {
  needsFold: boolean;
  expanded: boolean;
  foldHeight: number;
  fullHeight: number;
}

const USER_BUBBLE_FOLD_LINES = 10;
const DEFAULT_GENERATING_TEXT = '生成中…';
const userBubbleRefs = new Map<string, HTMLElement>();
const userBubbleFoldStates = ref<Record<string, UserBubbleFoldState>>({});
const copiedBubbleKeys = ref<Set<string>>(new Set());
const userBubbleTransitionSuppressed = ref(false);
let userBubbleResizeObserver: ResizeObserver | null = null;
let copiedBubbleTimeouts = new Map<string, number>();
let userBubbleTransitionSeq = 0;

const getUserBubbleKey = (msg: any, index: number) => msg?.id || `user-bubble-${index}`;

const registerUserBubbleRef = (msg: any, index: number, el: Element | null) => {
  const key = getUserBubbleKey(msg, index);
  if (el instanceof HTMLElement) {
    userBubbleRefs.set(key, el);
    // 虚拟列表下气泡随滚动挂载/卸载：挂载时补做观察与高度测量，保证折叠状态正确恢复
    if (userBubbleResizeObserver) {
      userBubbleResizeObserver.observe(el);
    }
    const state = userBubbleFoldStates.value[key];
    if (state && state.foldHeight === 0) {
      requestAnimationFrame(() => {
        if (el.isConnected) measureUserBubbles();
      });
    }
  } else {
    userBubbleRefs.delete(key);
  }
};

const estimateUserBubbleNeedsFold = (msg: any): boolean => {
  const content = typeof msg?.content === 'string' ? msg.content : '';
  if (!content) return false;
  // 按常见桌面端气泡宽度和字号估算：每行约 48 个字符。
  // 预留余量，避免短气泡被误判为需要折叠而出现“先折叠后展开”的跳变。
  const APPROX_CHARS_PER_LINE = 48;
  const foldChars = APPROX_CHARS_PER_LINE * (USER_BUBBLE_FOLD_LINES + 2);
  const newlineCount = (content.match(/\n/g) || []).length;
  return content.length > foldChars || newlineCount >= USER_BUBBLE_FOLD_LINES + 2;
};

const preMeasureUserBubbles = () => {
  // 在 DOM 测量前，先根据文本内容同步估算是否需要折叠，
  // 避免长气泡先完整渲染再被收起。
  // 只给尚未有状态记录的气泡做首次估算，避免切换对话时旧消息反复重算导致动画。
  (Array.isArray(filteredMessages.value) ? filteredMessages.value : []).forEach((msg, index) => {
    if (!msg || msg.role !== 'user') return;
    const key = getUserBubbleKey(msg, index);
    if (userBubbleFoldStates.value[key]) return;
    const needsFold = estimateUserBubbleNeedsFold(msg);
    userBubbleFoldStates.value[key] = {
      needsFold,
      expanded: false,
      foldHeight: 0,
      fullHeight: 0
    };
  });
};

const measureUserBubbles = () => {
  userBubbleRefs.forEach((el, key) => {
    if (!el.isConnected) {
      userBubbleRefs.delete(key);
      return;
    }
    const computed = window.getComputedStyle(el);
    const rawLineHeight = parseFloat(computed.lineHeight);
    const lineHeight = Number.isFinite(rawLineHeight) ? rawLineHeight : 24;
    const foldHeight = Math.round(lineHeight * USER_BUBBLE_FOLD_LINES);
    const fullHeight = Math.ceil(el.scrollHeight);
    const needsFold = fullHeight > foldHeight + 1;
    const existing = userBubbleFoldStates.value[key];
    userBubbleFoldStates.value[key] = {
      needsFold,
      expanded: existing?.expanded ?? false,
      foldHeight,
      fullHeight
    };
    el.style.setProperty('--bubble-fold-height', `${foldHeight}px`);
    el.style.setProperty('--bubble-full-height', `${fullHeight}px`);
  });
};

const isUserBubbleExpandable = (msg: any, index: number) => {
  const key = getUserBubbleKey(msg, index);
  return !!userBubbleFoldStates.value[key]?.needsFold;
};

const isUserBubbleExpanded = (msg: any, index: number) => {
  const key = getUserBubbleKey(msg, index);
  return !!userBubbleFoldStates.value[key]?.expanded;
};

const isUserBubbleCollapsed = (msg: any, index: number) => {
  const key = getUserBubbleKey(msg, index);
  const state = userBubbleFoldStates.value[key];
  return !!state?.needsFold && !state?.expanded;
};

const toggleUserBubble = (msg: any, index: number) => {
  const key = getUserBubbleKey(msg, index);
  const state = userBubbleFoldStates.value[key];
  if (!state || !state.needsFold) return;
  userBubbleFoldStates.value[key] = { ...state, expanded: !state.expanded };
};

const formatUserMessageTime = (msg: any): string => {
  const createdAt = msg?.created_at;
  if (!createdAt) return '';
  const date = new Date(createdAt);
  const now = new Date();
  const diffMs = Math.max(0, now.getTime() - date.getTime());
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);

  if (diffDay >= 1) {
    const month = date.getMonth() + 1;
    const day = date.getDate();
    const hour = String(date.getHours()).padStart(2, '0');
    const minute = String(date.getMinutes()).padStart(2, '0');
    return `${month}月${day}日 ${hour}:${minute}`;
  }
  if (diffHour > 0) return `${diffHour}小时前`;
  if (diffMin > 0) return `${diffMin}分钟前`;
  if (diffSec > 0) return `${diffSec}秒前`;
  return '刚刚';
};

const isUserBubbleCopied = (msg: any, index: number) => {
  const key = getUserBubbleKey(msg, index);
  return copiedBubbleKeys.value.has(key);
};

const copyUserMessage = async (msg: any, index: number) => {
  const key = getUserBubbleKey(msg, index);
  const text = typeof msg?.content === 'string' ? msg.content : '';
  if (!text || copiedBubbleKeys.value.has(key)) return;
  try {
    await navigator.clipboard.writeText(text);
    copiedBubbleKeys.value.add(key);
    const existing = copiedBubbleTimeouts.get(key);
    if (existing) window.clearTimeout(existing);
    const timeout = window.setTimeout(() => {
      copiedBubbleKeys.value.delete(key);
      copiedBubbleTimeouts.delete(key);
    }, 5000);
    copiedBubbleTimeouts.set(key, timeout);
  } catch (err) {
    console.warn('复制用户消息失败:', err);
  }
};

const branchUserMessage = (_msg: any) => {
  // TODO: 分支功能占位
  void _msg;
};

const observeUserBubbles = () => {
  if (!userBubbleResizeObserver) return;
  userBubbleResizeObserver.disconnect();
  userBubbleRefs.forEach((el) => {
    if (el.isConnected) userBubbleResizeObserver!.observe(el);
  });
};

const releaseUserBubbleTransitionAfterLayout = () => {
  const seq = ++userBubbleTransitionSeq;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      if (seq === userBubbleTransitionSeq) {
        userBubbleTransitionSuppressed.value = false;
      }
    });
  });
};

const isSystemAutoUserMessage = (message: any) => {
  if (!message || message.role !== 'user') {
    return false;
  }
  const meta = message.metadata || {};
  return !!(
    meta.is_auto_generated ||
    meta.auto_message_type ||
    meta.sub_agent_notice ||
    meta.background_command_notice
  );
};

// ---------- 多智能体消息渲染 ----------

interface MultiAgentMessageInfo {
  displayName: string;
  subtype: string;
  content: string;
}

const MULTI_AGENT_MESSAGE_RE =
  /^来自\s+(.+?)\s+的(.+?)\nid:\s*(\S+)\n\n<(.+?)>\n<(\w+)([^>]*)>\n([\s\S]*?)\n<\/\5>\n<\/\4>$/;

function parseMultiAgentMessage(content: string): MultiAgentMessageInfo | null {
  const text = String(content || '');
  const m = text.match(MULTI_AGENT_MESSAGE_RE);
  if (!m) {
    return null;
  }
  const attrs = m[6] || '';
  const subtypeMatch = attrs.match(/subtype="([^"]+)"/);
  return {
    displayName: m[1].trim(),
    subtype: subtypeMatch ? subtypeMatch[1] : '',
    content: m[7]
  };
}

function isMultiAgentMessage(message: any): boolean {
  if (!message || message.role !== 'user') {
    return false;
  }
  const meta = message.metadata || {};
  const autoType = String(meta.auto_message_type || message.auto_message_type || '');
  return autoType.startsWith('multi_agent_');
}

function multiAgentHeaderLabel(message: any): string {
  const displayName =
    message?.metadata?.multi_agent_display_name || message?.multi_agent_display_name;
  if (displayName) {
    return displayName;
  }
  const parsed = parseMultiAgentMessage(message?.content || '');
  return parsed?.displayName || '子智能体';
}

function isContinuousMultiAgentMessage(message: any, index: number): boolean {
  if (!isMultiAgentMessage(message)) {
    return false;
  }
  const messages = getFilteredMessagesSafe();
  const prev = messages[index - 1];
  if (!prev || !isMultiAgentMessage(prev)) {
    return false;
  }
  return multiAgentHeaderLabel(message) === multiAgentHeaderLabel(prev);
}

function multiAgentBubbleContent(message: any): string {
  const parsed = parseMultiAgentMessage(message?.content || '');
  return (parsed?.content || message?.content || '').trim();
}

const escapeUserHtml = (value: string): string =>
  String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

// 兼容 Windows 反斜杠路径：skills 目录与 SKILL.md 前的分隔符同时接受 / 和 \
// 路径前缀可选：后端自 cedef87d 起返回相对工作区路径（.astrion/skills/...），历史消息仍可能是绝对路径
const USER_SKILL_LINK_RE = /\[\$([^\]\n]+)\]\(((?:[^)\n]*[\\/])?\.astrion[\\/]skills[\\/][^)\n]+[\\/]SKILL\.md)\)/g;
const USER_FILE_LINK_RE = /\[([^\]\n]+)\]\(file:\/\/([^)\n]+)\)/g;
const SUB_AGENT_DONE_LABEL_RE = /^子智能体\d+\s*任务完成$/;
const SUB_AGENT_DONE_PREFIX_RE = /^(?:✅\s*)?子智能体\s*#?\s*(\d+)\s*任务摘要[:：]/;
const BG_RUN_COMMAND_DONE_LABEL_RE = /^(?:\[)?后台\s*run_command\s*完成(?:\])?$/;
const RENDERABLE_ACTION_TYPES = new Set([
  'thinking',
  'text',
  'tool',
  'append',
  'append_payload',
  'system'
]);

function renderUserMessageContent(content: string): string {
  const source = String(content || '');
  const combinedRe = new RegExp(
    `(?:${USER_SKILL_LINK_RE.source})|(?:${USER_FILE_LINK_RE.source})`,
    'g'
  );
  let html = '';
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = combinedRe.exec(source))) {
    html += escapeUserHtml(source.slice(cursor, match.index));
    if (match[1] !== undefined) {
      html += ` <span class="user-skill-link">${escapeUserHtml(match[1] || '')}</span> `;
    } else if (match[3] !== undefined) {
      html += ` <span class="user-file-link">${escapeUserHtml(match[3] || '')}</span> `;
    }
    cursor = match.index + match[0].length;
  }
  html += escapeUserHtml(source.slice(cursor));
  return html;
}
const isHiddenUserMessage = (message: any) => {
  if (!message || message.role !== 'user') {
    return false;
  }
  return getMessageVisibility(message) === 'hidden';
};
const isEmptyAssistantMessage = (message: any) => {
  if (!message || message.role !== 'assistant') {
    return false;
  }
  const actions = Array.isArray(message.actions) ? message.actions : [];
  const hasRenderable = actions.some((action) => isActionVisible(action));
  if (hasRenderable) {
    return false;
  }
  if (message.awaitingFirstContent) {
    return false;
  }
  // 无可渲染 action 且不在等待首包、也不在流式中的 assistant 视为“空壳”并过滤。
  const streaming = !!(message.streaming || message.currentStreamingType || message.streamingText || message.streamingThinking);
  return !streaming;
};

const filteredMessages = computed(() => {
  const source = props.messages || [];
  const droppedRoleSystem = source.filter((m) => m && m.role === 'system');
  if (droppedRoleSystem.length > 0) {
  }
  const result = source.filter((m) => {
    if (m && m.metadata && m.metadata.system_injected_image) {
      return false;
    }
    if (m?.role === 'system') {
      return false;
    }
    if (isHiddenUserMessage(m)) {
      return false;
    }
    if (isEmptyAssistantMessage(m)) {
      userMDebug('ChatArea.filteredMessages:drop-empty-assistant', {
        actions: Array.isArray(m?.actions) ? m.actions.map((a: any) => a?.type) : [],
        awaitingFirstContent: !!m?.awaitingFirstContent
      });
      return false;
    }
    return true;
  });
  if (source.length !== result.length) {
    userMDebug('ChatArea.filteredMessages:dropped', {
      sourceLength: source.length,
      resultLength: result.length,
      dropped: source.length - result.length
    });
  }
  return result;
});
const getFilteredMessagesSafe = () =>
  Array.isArray(filteredMessages.value) ? filteredMessages.value : [];
// 编辑摘要卡片挂载点映射：工作段最后一条 assistant 的下标 → 该段编辑摘要。
// 数据存于发起工作的 user 消息 metadata.edit_summary（后端实时写入并广播），
// 老对话无此 metadata 自然不渲染；工作进行中随消息推进实时挂在段尾。
const editSummarySegmentEnds = computed<Map<number, any>>(() => {
  const map = new Map<number, any>();
  const messages = getFilteredMessagesSafe();
  // 显示时机（个人空间「编辑摘要实时显示」，默认关闭）：
  // 关闭时仅在锚 user 消息的工作计时完成后显示卡片；开启后运行期间实时显示。
  const liveDisplay = !!personalization.form.edit_summary_live_display;
  for (let i = 0; i < messages.length; i += 1) {
    const msg = messages[i];
    if (!msg || msg.role !== 'user' || !messageStartsWork(msg)) continue;
    const summary = msg.metadata?.edit_summary;
    if (!summary || !Array.isArray(summary.files) || summary.files.length === 0) continue;
    if (!liveDisplay) {
      const timerStatus = msg.metadata?.work_timer?.status;
      // 工作进行中（working）不显示；无 timer 视为已完成（防御）
      if (timerStatus && timerStatus !== 'completed') continue;
    }
    let lastAssistant = -1;
    for (let j = i + 1; j < messages.length; j += 1) {
      const next = messages[j];
      if (next?.role === 'user' && messageStartsWork(next)) break;
      if (next?.role === 'assistant') lastAssistant = j;
    }
    if (lastAssistant >= 0) {
      map.set(lastAssistant, summary);
    }
  }
  return map;
});
const latestMessageIndex = computed(() => getFilteredMessagesSafe().length - 1);
const nowMs = ref(Date.now());
let timerHandle: number | null = null;
const debugLoggedSystemActionKeys = new Set<string>();
const debugLoggedUnknownActionKeys = new Set<string>();
const CHAT_DEBUG_LOGS = false;
const userMDebug = (...args: any[]) => {
  if (!CHAT_DEBUG_LOGS) {
    return;
  }
  console.log('[USERMDEBUG]', ...args);
};
const {
  scrollRef,
  contentRef,
  isAtBottom,
  isNearBottom,
  escapedFromLock,
  scrollToBottom,
  stopScroll
} = useStickToBottom({
  // 宽度拖拽挤压时不再弹簧追底：逐帧瞬时吸附底部，
  // 视觉上内容向上生长（底部锚定），消除“变高→追底”的反复计算循环
  resize: 'instant',
  initial: 'instant'
});

// —— 虚拟列表支撑 ——
// 消息对象没有后端 id，这里按对象身份（WeakMap）分配稳定 key，
// 供 virtua 的逐项高度缓存与列表项复用，避免改动 store / 后端消息结构
const messageKeyCache = new WeakMap<object, string>();
let messageKeySeq = 0;
const getMessageKey = (msg: any, index: number): string => {
  if (msg && typeof msg === 'object') {
    let key = messageKeyCache.get(msg as object);
    if (!key) {
      const rawId = (msg as any).id;
      key = rawId != null ? `msg-${String(rawId)}` : `mk-${++messageKeySeq}`;
      messageKeyCache.set(msg as object, key);
    }
    return key;
  }
  return `idx-${index}`;
};
// Virtualizer 需要显式拿到滚动容器（其直接父元素 .messages-flow 并非滚动容器），
// 等模板 ref 就绪后再挂载 Virtualizer，确保其内部滚动监听一次到位
const stickScrollElement = computed<HTMLElement | undefined>(() => scrollRef.value ?? undefined);
// 流式期间保持最后一条消息常驻，避免用户上翻时流式块反复卸载/重挂载
const keepMountedIndexes = computed<number[]>(() => {
  if (!props.streamingMessage) return [];
  const last = latestMessageIndex.value;
  return last >= 0 ? [last] : [];
});

// 公式渲染后如果已经在底部附近，主动追底，避免 KaTeX 渲染导致的高度跳变让 stick-to-bottom 锁不住
provide('mathRenderedCallback', () => {
  // 快捷导航补间进行中不追底，避免取消跳转动画
  if (quickNavJumpActive) return;
  if (isNearBottom.value && stickScrollToBottom) {
    requestAnimationFrame(() => {
      stickScrollToBottom({ animation: 'instant', preserveScrollPosition: false });
    });
  }
});

const rootEl = scrollRef;
const { anchorBlockElement, stopAll: stopBlockExpansionAnchors } = useBlockExpansionAnchor(
  scrollRef,
  { stopScroll }
);

// —— 运行中逐帧贴底锁 ——
// 背景：stick 库的 resize 追底在 ResizeObserver 回调里再经 rAF 才真正写 scrollTop，
// 且它观察的 .messages-flow 自身高度要等 virtua 回写占位高度后才变化，整体滞后内容
// 增长 1~2 帧。运行中块高度过渡（思考块自动展开/流式增长/手动展开）的每一帧会先按
// 新高度绘制、下一帧才被追底拉回，表现为「块外框先动、内部文字慢一步」的不同步。
// 这里在对话运行期间持有 rAF（绘制前强制读取 scrollHeight 并同步吸附底部），让高度
// 增长与滚动补偿落在同一帧。用户上滚脱离锁定（escapedFromLock/isAtBottom 变 false）
// 时自动停止吸附，滚回底部后自动恢复；非运行期仍由 useBlockExpansionAnchor 处理。
let liveStickLockRaf: number | null = null;

function liveStickLockTick() {
  liveStickLockRaf = null;
  if (!props.streamingMessage) return;
  const el = scrollRef.value;
  // 快捷导航补间进行中不贴底，避免与补间覆写打架
  if (el && !quickNavJumpActive && isAtBottom.value && !escapedFromLock.value) {
    const maxTop = el.scrollHeight - el.clientHeight;
    if (maxTop - el.scrollTop > 1) {
      markProgrammaticHint('ChatArea.liveStickLock');
      el.scrollTop = maxTop;
    }
  }
  liveStickLockRaf = requestAnimationFrame(liveStickLockTick);
}

function stopLiveStickLock() {
  if (liveStickLockRaf !== null) {
    cancelAnimationFrame(liveStickLockRaf);
    liveStickLockRaf = null;
  }
}

watch(
  () => props.streamingMessage,
  (streaming) => {
    if (streaming) {
      if (liveStickLockRaf === null) {
        liveStickLockRaf = requestAnimationFrame(liveStickLockTick);
      }
    } else {
      stopLiveStickLock();
    }
  },
  { immediate: true }
);

const thinkingRefs = new Map<string, HTMLElement | null>();
let scrollbarResizeObserver: ResizeObserver | null = null;
let bounceTraceCount = 0;
const BOUNCE_TRACE_MAX = 240;
const bounceTraceLastTsByKey = new Map<string, number>();
let lastObservedTop = 0;
let lastObservedHeight = 0;
let lastUserDownScrollTs = 0;
let lastUserWheelUpTs = 0;
let lastProgrammaticHintTs = 0;
let lastProgrammaticHintSource = '';
let suppressUserIntentUntil = 0;
let isHandlingLargeGrowth = false;
let scrollListener: ((event: Event) => void) | null = null;
let wheelListener: ((event: WheelEvent) => void) | null = null;
let touchStartListener: ((event: TouchEvent) => void) | null = null;
let touchMoveListener: ((event: TouchEvent) => void) | null = null;
let touchEndListener: ((event: TouchEvent) => void) | null = null;
let traceAttachLogged = false;

// —— 移动端触摸脱锁 ——
// stick 库的「用户上滚脱锁」只认 wheel 事件；移动端触摸滚动只能靠 scroll 事件推断，
// 但运行期间 resizeDifference 几乎恒非零，库内推断被跳过；同时运行期逐帧贴底锁
// 每帧都在打 programmatic 标记，scroll 意图识别也被压制。两者叠加导致移动端运行期
// 第一次上滑百分百脱不了锁、被拽回底部。这里直接监听触摸手势：手指下拉（查看更早
// 消息）超过阈值即视为脱锁意图，与桌面端 wheel-up 走同一条路径。
let touchScrollStartY = 0;
let touchEscapeFired = false;

function isNestedScrollableTarget(target: EventTarget | null): boolean {
  // 触摸发生在嵌套滚动容器（代码块/表格横向滚动壳等）内部时，不解除外层锁定
  const container = scrollRef.value;
  if (!container || !(target instanceof HTMLElement)) return false;
  let el: HTMLElement | null = target;
  while (el && el !== container) {
    if (el.scrollHeight > el.clientHeight + 2) {
      const overflowY = getComputedStyle(el).overflowY;
      if (overflowY === 'auto' || overflowY === 'scroll') {
        return true;
      }
    }
    el = el.parentElement;
  }
  return false;
}

function isScrollBounceTraceEnabled() {
  // 默认关闭滚动追踪日志（逐 scroll 事件的高频日志本身就是性能开销），
  // 排障时通过 window.__SCROLL_BOUNCE_TRACE__ = true 或 localStorage.scrollBounceTrace = '1' 显式打开
  if (typeof window === 'undefined') return false;
  try {
    const explicit = (window as any).__SCROLL_BOUNCE_TRACE__;
    if (explicit === false || explicit === '0') return false;
    if (explicit === true || explicit === '1') return true;
    const localFlag = window.localStorage?.getItem('scrollBounceTrace');
    if (localFlag === '0' || localFlag === 'false') return false;
    if (localFlag === '1' || localFlag === 'true') return true;
    return false;
  } catch {
    return false;
  }
}

function getScrollMetrics(el: HTMLElement) {
  const top = el.scrollTop;
  const height = el.scrollHeight;
  const client = el.clientHeight;
  return {
    top,
    height,
    client,
    remain: height - top - client,
    hasShowHtml: !!el.querySelector(
      'show_html, show-html, .chat-inline-html, .chat-inline-card--html'
    )
  };
}

function bounceTraceLog(
  event: string,
  payload: Record<string, any> = {},
  key = event,
  throttleMs = 120
) {
  if (!isScrollBounceTraceEnabled()) return;
  if (bounceTraceCount >= BOUNCE_TRACE_MAX) return;
  const now = Date.now();
  const last = bounceTraceLastTsByKey.get(key) || 0;
  if (throttleMs > 0 && now - last < throttleMs) return;
  bounceTraceLastTsByKey.set(key, now);
  bounceTraceCount += 1;
  if (bounceTraceCount === BOUNCE_TRACE_MAX) {
    console.warn('[SCROLL_BOUNCE_TRACE]', 'log-limit-reached', { max: BOUNCE_TRACE_MAX });
    return;
  }
  console.log('[SCROLL_BOUNCE_TRACE]', event, payload);
}

function markProgrammaticHint(source: string) {
  lastProgrammaticHintTs = Date.now();
  lastProgrammaticHintSource = source;
}

function detachBounceListener() {
  const el = scrollRef.value;
  if (el && scrollListener) {
    el.removeEventListener('scroll', scrollListener as EventListener);
  }
  if (el && wheelListener) {
    el.removeEventListener('wheel', wheelListener as EventListener, true);
  }
  if (el && touchStartListener) {
    el.removeEventListener('touchstart', touchStartListener as EventListener, true);
  }
  if (el && touchMoveListener) {
    el.removeEventListener('touchmove', touchMoveListener as EventListener, true);
  }
  if (el && touchEndListener) {
    el.removeEventListener('touchend', touchEndListener as EventListener, true);
    el.removeEventListener('touchcancel', touchEndListener as EventListener, true);
  }
  scrollListener = null;
  wheelListener = null;
  touchStartListener = null;
  touchMoveListener = null;
  touchEndListener = null;
}

function attachBounceListener() {
  const el = scrollRef.value;
  if (!el) return;
  if (scrollListener) {
    el.removeEventListener('scroll', scrollListener as EventListener);
  }
  if (wheelListener) {
    el.removeEventListener('wheel', wheelListener as EventListener, true);
  }
  if (touchStartListener) {
    el.removeEventListener('touchstart', touchStartListener as EventListener, true);
  }
  if (touchMoveListener) {
    el.removeEventListener('touchmove', touchMoveListener as EventListener, true);
  }
  if (touchEndListener) {
    el.removeEventListener('touchend', touchEndListener as EventListener, true);
    el.removeEventListener('touchcancel', touchEndListener as EventListener, true);
  }
  lastObservedTop = el.scrollTop || 0;
  lastObservedHeight = el.scrollHeight || 0;
  touchScrollStartY = 0;
  touchEscapeFired = false;
  touchStartListener = (event: TouchEvent) => {
    if (event.touches.length !== 1) {
      touchScrollStartY = 0;
      touchEscapeFired = false;
      return;
    }
    touchScrollStartY = event.touches[0].clientY;
    touchEscapeFired = false;
  };
  touchMoveListener = (event: TouchEvent) => {
    if (touchEscapeFired || !touchScrollStartY || event.touches.length !== 1) return;
    const target = scrollRef.value;
    if (!target) return;
    // 手指下拉 = 内容跟随下移 = scrollTop 减小 = 查看更早消息 = 脱锁意图
    const deltaY = event.touches[0].clientY - touchScrollStartY;
    if (deltaY <= 8) return;
    const now = Date.now();
    if (now <= suppressUserIntentUntil) return;
    if (isNestedScrollableTarget(event.target)) return;
    touchEscapeFired = true;
    lastUserWheelUpTs = now;
    stopScroll();
    stopBlockExpansionAnchors();
    cancelQuickNavJump();
    emit('user-scroll-intent', {
      ts: now,
      delta: -deltaY,
      top: target.scrollTop || 0
    });
    bounceTraceLog(
      'user-scroll-intent:touch-drag',
      { deltaY, top: target.scrollTop || 0, ...getScrollMetrics(target) },
      'user-scroll-intent:touch-drag',
      80
    );
  };
  touchEndListener = () => {
    touchScrollStartY = 0;
    touchEscapeFired = false;
  };
  el.addEventListener('touchstart', touchStartListener, { passive: true, capture: true });
  el.addEventListener('touchmove', touchMoveListener, { passive: true, capture: true });
  el.addEventListener('touchend', touchEndListener, { passive: true, capture: true });
  el.addEventListener('touchcancel', touchEndListener, { passive: true, capture: true });
  wheelListener = (event: WheelEvent) => {
    const target = scrollRef.value;
    if (!target) return;
    const trusted = (event as any).isTrusted === true;
    const deltaY = Number(event.deltaY || 0);
    if (!(trusted && deltaY < -1 && Date.now() > suppressUserIntentUntil)) {
      return;
    }
    const now = Date.now();
    lastUserWheelUpTs = now;
    stopScroll();
    stopBlockExpansionAnchors();
    cancelQuickNavJump();
    emit('user-scroll-intent', {
      ts: now,
      delta: deltaY,
      top: target.scrollTop || 0
    });
    bounceTraceLog(
      'user-scroll-intent:wheel-up',
      { deltaY, top: target.scrollTop || 0, ...getScrollMetrics(target) },
      'user-scroll-intent:wheel-up',
      80
    );
  };
  el.addEventListener('wheel', wheelListener, { passive: true, capture: true });
  scrollListener = (event: Event) => {
    const target = event.target as HTMLElement | null;
    if (!target) return;
    const trusted = (event as any).isTrusted === true;
    const top = target.scrollTop;
    const delta = top - lastObservedTop;
    const height = target.scrollHeight || 0;
    const heightDelta = height - lastObservedHeight;
    lastObservedTop = top;
    lastObservedHeight = height;

    // 左侧快捷导航：滚动位置同步（直接 DOM 监听，用户滚动与程序平滑滚动都会触发）
    updateQuickNavCurrent(top);

    if (heightDelta !== 0) {
      bounceTraceLog(
        'height-change',
        {
          heightDelta,
          scrollHeight: height,
          top,
          delta,
          escapedFromLock: escapedFromLock.value,
          isAtBottom: isAtBottom.value,
          isNearBottom: isNearBottom.value
        },
        'height-change',
        120
      );
    }

    // 大幅高度增长检测：当 scrollHeight 突增（内容大幅展开/渲染），
    // 且用户没有主动脱离锁定（escapedFromLock=false）时，
    // 用 instant 瞬间追底，绕过 spring 动画的延迟。
    // 这解决了思考块展开、Markdown 表格渲染等场景下滚动跟不上内容增长的问题。
    const largeGrowthThreshold = Math.max(200, target.clientHeight * 0.35 || 200);
    if (
      !isHandlingLargeGrowth &&
      heightDelta > largeGrowthThreshold &&
      !escapedFromLock.value
    ) {
      bounceTraceLog(
        'large-growth:trigger',
        {
          heightDelta,
          threshold: largeGrowthThreshold,
          top,
          delta,
          escapedFromLock: escapedFromLock.value,
          isAtBottom: isAtBottom.value,
          isNearBottom: isNearBottom.value
        },
        'large-growth:trigger',
        0
      );
      isHandlingLargeGrowth = true;
      markProgrammaticHint('ChatArea.largeGrowth');
      suppressUserIntentUntil = Date.now() + 900;
      scrollToBottom({ animation: 'instant', preserveScrollPosition: false });
      bounceTraceLog(
        'large-growth:instant-scroll',
        { heightDelta, threshold: largeGrowthThreshold, ...getScrollMetrics(target) },
        'large-growth:instant-scroll',
        120
      );
      setTimeout(() => {
        isHandlingLargeGrowth = false;
      }, 400);
    }

    const now = Date.now();
    const closeToProgrammaticHint = now - lastProgrammaticHintTs <= 140;
    const likelyLayoutDriven = Math.abs(heightDelta) > 2;
    const manualUpByWheel = delta < -3 && now - lastUserWheelUpTs <= 240;
    const strongManualUp = delta < -18 && now > suppressUserIntentUntil && !closeToProgrammaticHint;
    const shouldTreatAsUserIntent =
      trusted &&
      Math.abs(delta) > 5 &&
      now > suppressUserIntentUntil &&
      (!closeToProgrammaticHint || strongManualUp) &&
      (delta < 0 ? strongManualUp || manualUpByWheel || !likelyLayoutDriven : !likelyLayoutDriven);
    if (shouldTreatAsUserIntent) {
      // 用户手动滚动时，立即中断 stick 引擎中可能仍在运行的动画滚动
      stopScroll();
      stopBlockExpansionAnchors();
      lastUserDownScrollTs = now;
      emit('user-scroll-intent', {
        ts: lastUserDownScrollTs,
        delta,
        top
      });
      bounceTraceLog(
        'user-scroll-intent',
        {
          delta,
          top,
          ts: lastUserDownScrollTs,
          closeToProgrammaticHint,
          likelyLayoutDriven,
          manualUpByWheel,
          strongManualUp,
          ...getScrollMetrics(target)
        },
        'user-scroll-intent',
        120
      );
    }
    if (delta < -8) {
      const reboundWindow = now - lastUserDownScrollTs <= 1400;
      if (reboundWindow) {
        const byProgrammatic = now - lastProgrammaticHintTs <= 1200;
        bounceTraceLog(
          'rebound:detected',
          {
            delta,
            trusted,
            likelySource: byProgrammatic
              ? lastProgrammaticHintSource || 'programmatic-unknown'
              : 'unknown(internal-stick/browser-anchor)',
            msSinceUserDown: now - lastUserDownScrollTs,
            msSinceProgrammatic: now - lastProgrammaticHintTs,
            ...getScrollMetrics(target),
            stickState: {
              isAtBottom: !!isAtBottom.value,
              isNearBottom: !!isNearBottom.value,
              escapedFromLock: !!escapedFromLock.value
            }
          },
          'rebound:detected',
          0
        );
      }
    } else {
      bounceTraceLog(
        'scroll:event',
        {
          delta,
          trusted,
          ...getScrollMetrics(target),
          stickState: {
            isAtBottom: !!isAtBottom.value,
            isNearBottom: !!isNearBottom.value,
            escapedFromLock: !!escapedFromLock.value
          }
        },
        'scroll:event',
        180
      );
    }
  };
  el.addEventListener('scroll', scrollListener, { passive: true });
  if (!traceAttachLogged && isScrollBounceTraceEnabled()) {
    traceAttachLogged = true;
    console.warn('[SCROLL_BOUNCE_TRACE]', 'listener-attached', {
      hasElement: !!el,
      className: el.className || ''
    });
  }
}

function updateChatScrollbarWidth() {
  const el = scrollRef.value;
  if (!el) return;
  const scrollbarWidth = Math.max(0, el.offsetWidth - el.clientWidth);
  el.style.setProperty('--chat-scrollbar-width', `${scrollbarWidth}px`);
}

watch(
  [isAtBottom, isNearBottom, escapedFromLock],
  ([atBottom, nearBottom, escaped]) => {
    emit('stick-state-change', {
      isAtBottom: !!atBottom,
      isNearBottom: !!nearBottom,
      escapedFromLock: !!escaped
    });
  },
  { immediate: true }
);

function escapeBlockSelector(value: string) {
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
    return CSS.escape(value);
  }
  return value.replace(/["\\]/g, '\\$&');
}

const prevExpandedBlocks = ref(new Set<string>());

// 运行期间是否跳过展开锚定（保持 f43b5ea7 的旧行为）：
// 仅当「贴底锁定中」且目标块是运行态块（自动展开/收起的 thinking/tool）时，
// 才交给 stick-to-bottom / liveStickLock 逐帧追底，避免锚定与贴底防输入栏遮挡打架。
// 其余情况——用户已上滑脱锁，或操作的是历史/已结束块（手动展开）——一律走锚定，
// 否则运行期间手动展开块没有任何逐帧滚动补偿，文字与外框移动会不同步。
function shouldSkipExpansionAnchor(element?: HTMLElement | null): boolean {
  if (!props.streamingMessage) return false;
  // 快捷导航补间进行中不启动新锚定，避免覆写 scrollTop 取消跳转
  if (quickNavJumpActive) return true;
  const gluedToBottom = !escapedFromLock.value && !!isAtBottom.value;
  if (!gluedToBottom || !element) return false;
  return (
    element.classList.contains('processing') ||
    element.classList.contains('running') ||
    !!element.querySelector('.processing, .running, .thinking-animation')
  );
}

watch(
  () => props.expandedBlocks,
  (newSet, oldSet) => {
    const prev = oldSet ?? prevExpandedBlocks.value;
    const all = new Set([...Array.from(newSet), ...Array.from(prev)]);
    const changed = new Set<string>();
    for (const id of all) {
      if (newSet.has(id) !== prev.has(id)) {
        changed.add(id);
      }
    }
    prevExpandedBlocks.value = new Set(newSet);

    if (changed.size === 0) return;

    const container = scrollRef.value;
    if (!container) return;

    for (const id of changed) {
      const el = container.querySelector(
        `[data-block-id="${escapeBlockSelector(id)}"]`
      ) as HTMLElement | null;
      // 运行期贴底时的运行态块交给 liveStickLock 逐帧追底；
      // 手动展开（含运行期上滑后操作历史块）走锚定
      if (shouldSkipExpansionAnchor(el)) continue;
      if (el) {
        anchorBlockElement(el, id, {
          direction: 'auto',
          duration: 260,
          phase: newSet.has(id) ? 'expand' : 'collapse'
        });
      }
    }
  },
  { flush: 'post' }
);

function handleStackedMoreToggle(payload: {
  element: HTMLElement;
  expanded: boolean;
  stackKey: string;
}) {
  // 「更多」是用户手动操作，除运行期贴底的运行态块外一律锚定
  if (shouldSkipExpansionAnchor(payload.element)) return;

  anchorBlockElement(payload.element, `more-${payload.stackKey}`, {
    direction: 'auto',
    duration: 260,
    phase: expanded ? 'expand' : 'collapse'
  });
}

function handleMinimalGroupToggle(payload: { groupId: string; expanded: boolean }) {
  const { groupId, expanded } = payload;
  const container = scrollRef.value;
  if (!container) return;
  const el = container.querySelector(
    `[data-group-id="${escapeBlockSelector(groupId)}"]`
  ) as HTMLElement | null;
  // 手动展开/收起摘要组，除运行期贴底的运行态块外一律锚定
  if (shouldSkipExpansionAnchor(el)) return;
  if (el) {
    anchorBlockElement(el, groupId, {
      direction: 'auto',
      duration: 300,
      phase: expanded ? 'expand' : 'collapse'
    });
  }
}

const registerCollapseContent = (key: string, el: Element | null) => {
  if (!(el instanceof HTMLElement)) {
    return;
  }
  const COLLAPSE_MAX_HEIGHT = 600;
  requestAnimationFrame(() => {
    const h = el.scrollHeight || el.offsetHeight || 0;
    if (h > 0) {
      el.style.setProperty('--collapse-max', `${Math.min(h, COLLAPSE_MAX_HEIGHT)}px`);
    }
  });
};

function registerThinkingRef(key: string, el: Element | null) {
  if (el instanceof HTMLElement) {
    thinkingRefs.set(key, el);
  } else {
    thinkingRefs.delete(key);
  }
}

function getThinkingRef(key: string) {
  return thinkingRefs.get(key) || null;
}

async function stickScrollToBottom(
  options: {
    behavior?: ScrollBehavior;
    force?: boolean;
    preserveScrollPosition?: boolean;
  } = {}
) {
  const el = scrollRef.value;
  if (el) {
    bounceTraceLog(
      'programmatic:scrollToBottom:before',
      {
        source: 'ChatArea.stickScrollToBottom',
        behavior: options.behavior || 'auto',
        force: !!options.force,
        preserveScrollPosition: !!options.preserveScrollPosition,
        ...getScrollMetrics(el)
      },
      'programmatic:scrollToBottom:before',
      80
    );
  }
  markProgrammaticHint('ChatArea.stickScrollToBottom');
  // 点击“滚动到底部”按钮时会触发 smooth 动画滚动；期间会产生 trusted scroll 事件，
  // 若不短暂抑制会被误判为“用户手动滚动”从而中断动画，表现为只滚动一点点。
  if (options.force || options.behavior === 'smooth') {
    suppressUserIntentUntil = Date.now() + 900;
  }
  return await scrollToBottom({
    animation: options.behavior === 'smooth' ? 'smooth' : 'auto',
    ignoreEscapes: !!options.force,
    preserveScrollPosition: !!options.preserveScrollPosition,
    duration: options.force ? 260 : 0
  });
}

async function stickConditionalScrollToBottom(options: { force?: boolean } = {}) {
  const el = scrollRef.value;
  if (el) {
    bounceTraceLog(
      'programmatic:conditional:before',
      {
        source: 'ChatArea.stickConditionalScrollToBottom',
        force: !!options.force,
        ...getScrollMetrics(el)
      },
      'programmatic:conditional:before',
      100
    );
  }
  markProgrammaticHint('ChatArea.stickConditionalScrollToBottom');
  if (options.force) {
    return await stickScrollToBottom({ force: true, preserveScrollPosition: false });
  }
  // conditional 场景由上层先判断“是否需要追底”，这里不要再使用 preserveScrollPosition，
  // 否则一旦高度突增导致 escapedFromLock=true，后续会被卡在非底部。
  return await stickScrollToBottom({ preserveScrollPosition: false });
}

function getStickState() {
  return {
    isAtBottom: !!isAtBottom.value,
    isNearBottom: !!isNearBottom.value,
    escapedFromLock: !!escapedFromLock.value
  };
}

function iconStyleSafe(key: string, size?: string) {
  if (typeof props.iconStyle === 'function') {
    return props.iconStyle(key, size);
  }
  return {};
}

function normalizeMediaPath(input: any): string {
  if (!input) return '';
  if (typeof input === 'string') {
    return input;
  }
  if (typeof input?.path === 'string') {
    return input.path;
  }
  if (typeof input?.source_path === 'string') {
    return input.source_path;
  }
  return '';
}

function formatImageName(input: any): string {
  const path = normalizeMediaPath(input);
  if (!path) {
    if (typeof input?.name === 'string' && input.name) {
      return input.name;
    }
    if (typeof input?.title === 'string' && input.title) {
      return input.title;
    }
    if (typeof input?.media_id === 'string' && input.media_id) {
      return input.media_id;
    }
    return '';
  }
  const parts = path.split(/[/\\]/);
  return parts[parts.length - 1] || path;
}

// 用户消息附带的文件列表（记录在消息 metadata.files，历史恢复后同样可用）
function messageFiles(message: any): any[] {
  const raw = message?.metadata?.files;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item: any) => (typeof item === 'string' && item) || (item && typeof item.path === 'string' && item.path))
    .slice(0, 9);
}

function previewMessageImage(message: any, input: any) {
  const url = getPreviewUrl(message, input, 'image');
  if (!url) return;
  useUiStore().openImagePreview({ url, name: formatImageName(input) });
}

function resolveMessageMediaRef(message: any, input: any, kind: 'image' | 'video'): any | null {
  const refs = message?.media_refs || message?.metadata?.media_refs || [];
  if (!Array.isArray(refs) || !refs.length) {
    return null;
  }
  const mediaId = typeof input?.media_id === 'string' ? input.media_id : '';
  if (mediaId) {
    const matched = refs.find((item: any) => item?.media_id === mediaId);
    if (matched) return matched;
  }
  const sourcePath = normalizeMediaPath(input);
  if (sourcePath) {
    const matched = refs.find(
      (item: any) =>
        item &&
        item.kind === kind &&
        typeof item.source_path === 'string' &&
        item.source_path === sourcePath
    );
    if (matched) return matched;
  }
  const firstSameKind = refs.find((item: any) => item && item.kind === kind);
  return firstSameKind || null;
}

function getPreviewUrl(message: any, input: any, kind: 'image' | 'video'): string {
  const mediaRef = resolveMessageMediaRef(message, input, kind);
  const mediaId = typeof mediaRef?.media_id === 'string' ? mediaRef.media_id : '';
  if (mediaId) {
    return `/api/conversations/media/${encodeURIComponent(mediaId)}`;
  }
  const path = normalizeMediaPath(input);
  if (!path) return '';
  return `/api/gui/files/download?path=${encodeURIComponent(path)}`;
}

function mediaPreviewKey(message: any, input: any, index: number): string {
  const mediaRef =
    resolveMessageMediaRef(message, input, 'image') ||
    resolveMessageMediaRef(message, input, 'video');
  if (typeof mediaRef?.media_id === 'string' && mediaRef.media_id) {
    return `${mediaRef.media_id}-${index}`;
  }
  const path = normalizeMediaPath(input);
  if (path) return `${path}-${index}`;
  return `media-${index}`;
}

function formatDurationMs(durationMs: number): string {
  const totalSeconds = Math.max(0, Math.floor((durationMs || 0) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function getSubAgentSystemNoticeLabel(action: any): string | null {
  if (!action || action.type !== 'system') {
    return null;
  }
  const content = typeof action.content === 'string' ? action.content.trim() : '';
  const key = action?.id || `no-id-${content.slice(0, 32)}`;
  if (!content) {
    if (!debugLoggedSystemActionKeys.has(`${key}-empty`)) {
      debugLoggedSystemActionKeys.add(`${key}-empty`);
    }
    return null;
  }
  if (
    action.variant === 'sub_agent_done' &&
    (SUB_AGENT_DONE_LABEL_RE.test(content) ||
      BG_RUN_COMMAND_DONE_LABEL_RE.test(content))
  ) {
    if (!debugLoggedSystemActionKeys.has(`${key}-variant-pass`)) {
      debugLoggedSystemActionKeys.add(`${key}-variant-pass`);
    }
    return content;
  }
  const m = content.match(SUB_AGENT_DONE_PREFIX_RE);
  if (m && /已完成/.test(content)) {
    if (!debugLoggedSystemActionKeys.has(`${key}-regex-pass`)) {
      debugLoggedSystemActionKeys.add(`${key}-regex-pass`);
    }
    return `子智能体${m[1]} 任务完成`;
  }
  if (!debugLoggedSystemActionKeys.has(`${key}-blocked`)) {
    debugLoggedSystemActionKeys.add(`${key}-blocked`);
  }
  return null;
}

function isActionVisible(action: any): boolean {
  if (!action) {
    return false;
  }
  const actionType = action.type;
  if (!RENDERABLE_ACTION_TYPES.has(actionType)) {
    const key = action?.id || `unknown-${actionType || 'none'}`;
    if (!debugLoggedUnknownActionKeys.has(key)) {
      debugLoggedUnknownActionKeys.add(key);
    }
    return false;
  }
  if (action.type === 'tool') {
    const tool = action?.tool || {};
    const hasName = typeof tool.name === 'string' && tool.name.trim().length > 0;
    const hasMessage = typeof tool.message === 'string' && tool.message.trim().length > 0;
    const hasResult = tool.result !== null && tool.result !== undefined;
    const hasArgs = tool.arguments && typeof tool.arguments === 'object' && Object.keys(tool.arguments).length > 0;
    const visible = hasName || hasMessage || hasResult || hasArgs;
    return visible;
  }
  if (action.type !== 'system') {
    return true;
  }
  return !!getSubAgentSystemNoticeLabel(action);
}

function hasRenderableAssistantActions(actions: any[] = []): boolean {
  return (actions || []).some((action) => isActionVisible(action));
}

function isSubAgentNoticeOnlyMessage(msg: any): boolean {
  if (!msg || msg.role !== 'assistant') {
    return false;
  }
  const visibleActions = (msg.actions || []).filter((action: any) => isActionVisible(action));
  if (visibleActions.length !== 1) {
    return false;
  }
  const onlyAction = visibleActions[0];
  const yes = onlyAction?.type === 'system' && !!getSubAgentSystemNoticeLabel(onlyAction);
  if (yes) {
    userMDebug('ChatArea.isSubAgentNoticeOnlyMessage:hit', {
      actionId: onlyAction?.id,
      content: onlyAction?.content
    });
  }
  return yes;
}

function isFollowedBySubAgentNotice(index: number): boolean {
  const messages = getFilteredMessagesSafe();
  const next = messages[index + 1];
  const yes = !!next && isSubAgentNoticeOnlyMessage(next);
  if (yes) {
    userMDebug('ChatArea.isFollowedBySubAgentNotice:hit', {
      index,
      currentRole: messages[index]?.role,
      nextRole: next?.role
    });
  }
  return yes;
}

function assistantWorkLabel(index: number): string {
  if (index <= 0) {
    return '';
  }
  const workUser = findAssistantHeaderAnchorUser(index);
  const timer = workUser?.metadata?.work_timer;
  if (!timer || !timer.started_at) {
    return '';
  }
  const startMs = Date.parse(timer.started_at);
  if (!Number.isFinite(startMs)) {
    return '';
  }
  const status = timer.status || 'working';
  if (status === 'working') {
    return `工作中 ${formatDurationMs(nowMs.value - startMs)}`;
  }
  const durationMs =
    typeof timer.duration_ms === 'number'
      ? timer.duration_ms
      : Math.max(
          0,
          (Number.isFinite(Date.parse(timer.finished_at || ''))
            ? Date.parse(timer.finished_at)
            : nowMs.value) - startMs
        );
  return `工作完成 ${formatDurationMs(durationMs)}`;
}

function shouldShowAssistantHeader(index: number): boolean {
  return !!findAssistantHeaderAnchorUser(index);
}

function findAssistantHeaderAnchorUser(index: number): any | null {
  if (index <= 0) {
    return null;
  }
  const messages = getFilteredMessagesSafe();
  const prev = messages[index - 1];
  if (!prev || prev.role !== 'user') {
    return null;
  }
  // assistant 头部属于“紧挨着它上方的一组连续 user-side 消息”。
  // 只有 starts_work=true 的消息才会开启新 work segment；运行期 guidance
  // 只是当前 segment 内的补充，不会重置头部与计时器。
  for (let i = index - 1; i >= 0; i -= 1) {
    const item = messages[i];
    if (!item || item.role !== 'user') {
      break;
    }
    if (messageStartsWork(item)) {
      return item;
    }
  }
  return null;
}

function userHeaderSource(msg: any): string {
  return String(msg?.metadata?.message_source || 'user')
    .trim()
    .toLowerCase();
}

function userHeaderLabel(msg: any): string {
  const source = userHeaderSource(msg);
  if (source === 'guidance') {
    return '引导';
  }
  if (source === 'goal') {
    return '目标';
  }
  if (source === 'goal_review') {
    return '审核';
  }
  if (source === 'compression' || source === 'compression_handoff') {
    return '压缩';
  }
  if (source === 'notify' || source === 'sub_agent' || source === 'background_command') {
    return '通知';
  }
  return userName.value;
}

function userHeaderIconKey(msg: any): string {
  const source = userHeaderSource(msg);
  if (source === 'guidance') {
    return 'navigation';
  }
  if (source === 'goal') {
    return 'flag';
  }
  if (source === 'goal_review') {
    return 'circleAlert';
  }
  if (source === 'compression' || source === 'compression_handoff') {
    return 'layers';
  }
  if (source === 'notify' || source === 'sub_agent' || source === 'background_command') {
    return 'bell';
  }
  return 'user';
}

// ---- 简略信息渲染（compact 消息可选显示形式）----
// 命中条件：当前消息为 compact 可见性，且用户在个人空间选择了“简略信息”。
function isBriefCompactMessage(msg: any): boolean {
  if (!msg || msg.role !== 'user') {
    return false;
  }
  if (compactMessageDisplay.value !== 'brief') {
    return false;
  }
  return getMessageVisibility(msg) === 'compact';
}

// 简略信息只展示一行概要标签（不显示原始内容），按来源给出固定文案。
function compactBriefLabel(msg: any): string {
  const source = userHeaderSource(msg);
  switch (source) {
    case 'goal_review':
      return '目标审核完成';
    case 'sub_agent':
      return '后台子智能体完成';
    case 'background_command':
      return '后台指令完成';
    case 'compression':
    case 'compression_handoff':
      return '对话压缩完成';
    case 'goal':
      return '目标进行中';
    case 'guidance':
      return '运行中引导';
    case 'notify':
      return '系统通知';
    default:
      return '系统消息';
  }
}

watch(
  () =>
    getFilteredMessagesSafe().map((m) => m?.id ?? m?.content?.slice(0, 80)),
  () => {
    userBubbleTransitionSuppressed.value = true;
    // 先同步估算折叠状态，再异步精确测量，避免长气泡先展开后收起
    preMeasureUserBubbles();
    nextTick(() => {
      requestAnimationFrame(() => {
        measureUserBubbles();
        observeUserBubbles();
        releaseUserBubbleTransitionAfterLayout();
      });
    });
  },
  { immediate: true }
);

onMounted(() => {
  attachBounceListener();
  updateChatScrollbarWidth();
  if (typeof ResizeObserver !== 'undefined' && scrollRef.value) {
    scrollbarResizeObserver = new ResizeObserver(updateChatScrollbarWidth);
    scrollbarResizeObserver.observe(scrollRef.value);
  }
  if (typeof ResizeObserver !== 'undefined') {
    userBubbleResizeObserver = new ResizeObserver(() => {
      requestAnimationFrame(measureUserBubbles);
    });
    nextTick(() => observeUserBubbles());
  }
  if (isScrollBounceTraceEnabled()) {
    console.warn('[SCROLL_BOUNCE_TRACE]', 'mounted', {
      hasScrollRef: !!scrollRef.value
    });
  }
  timerHandle = window.setInterval(() => {
    nowMs.value = Date.now();
  }, 1000);
  setupQuickNavLayoutObserver();
});

watch(scrollRef, () => {
  attachBounceListener();
  updateChatScrollbarWidth();
  if (scrollbarResizeObserver) {
    scrollbarResizeObserver.disconnect();
    scrollbarResizeObserver = null;
  }
  if (typeof ResizeObserver !== 'undefined' && scrollRef.value) {
    scrollbarResizeObserver = new ResizeObserver(updateChatScrollbarWidth);
    scrollbarResizeObserver.observe(scrollRef.value);
  }
});

onBeforeUnmount(() => {
  detachBounceListener();
  stopBlockExpansionAnchors();
  cancelQuickNavJump();
  stopLiveStickLock();
  if (scrollbarResizeObserver) {
    scrollbarResizeObserver.disconnect();
    scrollbarResizeObserver = null;
  }
  if (userBubbleResizeObserver) {
    userBubbleResizeObserver.disconnect();
    userBubbleResizeObserver = null;
  }
  userBubbleRefs.clear();
  copiedBubbleTimeouts.forEach((t) => window.clearTimeout(t));
  copiedBubbleTimeouts.clear();
  if (timerHandle !== null) {
    clearInterval(timerHandle);
    timerHandle = null;
  }
  teardownQuickNavLayoutObserver();
});

const isStackable = (action: any) =>
  action && (action.type === 'thinking' || action.type === 'tool');
const isEmptyTextAction = (action: any) => {
  if (!action || action.type !== 'text') {
    return false;
  }
  const content = typeof action.content === 'string' ? action.content : '';
  return !content.trim();
};
const splitActionGroups = (actions: any[] = [], messageIndex = 0) => {
  const result: Array<
    | { kind: 'stack'; actions: any[]; key: string }
    | { kind: 'single'; action: any; actionIndex: number; key: string }
  > = [];
  let buffer: any[] = [];

  // 调试：记录输入
  if (CHAT_DEBUG_LOGS && actions.length > 0) {
  }

  const flushBuffer = () => {
    // 单个可堆叠块(thinking/tool)也走 stack 路径，避免 1↔2 块时在 single/stack
    // 两套渲染路径之间整体切换导致 StackedBlocks 重新挂载(表现为外框瞬间塌成最扁再展开)。
    // 单块时 StackedBlocks 内部 moreVisible=false，不显示「更多」头，外观由 CSS 对齐 single。
    if (buffer.length >= 1) {
      result.push({
        kind: 'stack',
        actions: buffer.slice(),
        key: `stack-${messageIndex}-${result.length}`
      });
    }
    buffer = [];
  };

  actions.forEach((action, idx) => {
    if (isEmptyTextAction(action)) {
      return;
    }
    if (isStackable(action)) {
      buffer.push(action);
    } else {
      flushBuffer();
      result.push({
        kind: 'single',
        action,
        actionIndex: idx,
        key: action.id || `single-${messageIndex}-${idx}`
      });
    }
  });
  flushBuffer();

  // 调试：记录输出
  if (CHAT_DEBUG_LOGS && actions.length > 0) {
  }

  return result;
};

function getGeneratingLetters(message: any) {
  const label =
    typeof message?.generatingLabel === 'string' && message.generatingLabel.trim()
      ? message.generatingLabel.trim()
      : DEFAULT_GENERATING_TEXT;
  return Array.from(label);
}

// ===== 左侧用户输入快捷跳转导航 =====
// 数据口径与深度压缩一致：role === 'user' 且 metadata.message_source === 'user'（引导/通知/压缩等不计入）
const shellRef = ref<HTMLElement | null>(null);
const virtualizerRef = ref<any>(null);
const quickNavActiveIndex = ref(-1);
const quickNavLineEls = new Map<number, HTMLElement>();

const isRealUserNavMessage = (msg: any): boolean => {
  if (!msg || msg.role !== 'user') {
    return false;
  }
  return String(msg?.metadata?.message_source || 'user').trim().toLowerCase() === 'user';
};

function extractUserFirstLine(msg: any): string {
  const content = msg?.content;
  let text = '';
  if (typeof content === 'string') {
    text = content;
  } else if (Array.isArray(content)) {
    text = content
      .filter((it: any) => it && it.type === 'text' && typeof it.text === 'string')
      .map((it: any) => it.text)
      .join('\n');
  } else if (content != null) {
    text = String(content);
  }
  const lines = text
    .split('\n')
    .map((l: string) => l.trim())
    .filter((l: string) => l && !l.startsWith('[系统通知|'));
  return lines[0] || '（空输入）';
}

function extractAssistantLastText(msg: any): string {
  const actions = Array.isArray(msg?.actions) ? msg.actions : [];
  for (let i = actions.length - 1; i >= 0; i -= 1) {
    const action = actions[i];
    if (action?.type === 'text' && typeof action?.content === 'string' && action.content.trim()) {
      return action.content.trim();
    }
  }
  const content = msg?.content;
  return typeof content === 'string' ? content.trim() : '';
}

const quickNavItems = computed(() => {
  const msgs = getFilteredMessagesSafe();
  const userIndexes: number[] = [];
  for (let i = 0; i < msgs.length; i += 1) {
    if (isRealUserNavMessage(msgs[i])) {
      userIndexes.push(i);
    }
  }
  return userIndexes.map((msgIndex, k) => {
    // 该输入所产生的最后一次输出：下一条真实用户输入之前、最后一条 assistant 消息的最终文本
    const end = k + 1 < userIndexes.length ? userIndexes[k + 1] : msgs.length;
    let aiText = '';
    for (let i = end - 1; i > msgIndex; i -= 1) {
      const m = msgs[i];
      if (!m || m.role !== 'assistant') {
        continue;
      }
      const text = extractAssistantLastText(m);
      if (text) {
        aiText = text;
        break;
      }
    }
    return {
      msgIndex,
      userText: extractUserFirstLine(msgs[msgIndex]),
      aiText: aiText || '（暂无回复）'
    };
  });
});

const QUICK_NAV_LINE_WIDTHS = [34, 25, 17, 12];
const QUICK_NAV_LINE_BASE_WIDTH = 8;

function quickNavLineWidth(index: number): number {
  const active = quickNavActiveIndex.value;
  if (active < 0) {
    return QUICK_NAV_LINE_BASE_WIDTH;
  }
  const dist = Math.abs(index - active);
  const raw = QUICK_NAV_LINE_WIDTHS[dist] ?? QUICK_NAV_LINE_BASE_WIDTH;
  if (raw === QUICK_NAV_LINE_BASE_WIDTH) {
    return raw;
  }
  // 接近内容轨/边界时按比例压缩波浪宽度，避免盖到消息
  const scale = quickNavWaveScale.value;
  return Math.max(QUICK_NAV_LINE_BASE_WIDTH, Math.round(raw * scale));
}

// ===== 滚动位置同步：高亮当前阅读轮次对应的横线 =====
// 触发源：attachBounceListener 里已有的 DOM scroll 监听（virtua 的 onScroll 事件转发在该版本不触发）
const quickNavCurrentIndex = ref(-1);

function updateQuickNavCurrent(offset?: number) {
  const items = quickNavItems.value;
  if (!items.length) {
    quickNavCurrentIndex.value = -1;
    return;
  }
  const v = virtualizerRef.value;
  if (!v) {
    return;
  }
  const scrollOffset = typeof offset === 'number' ? offset : Number(v.scrollOffset) || 0;
  const viewport = Number(v.viewportSize) || shellRef.value?.clientHeight || 0;
  // 视口 25% 位置作为「正在阅读」的判定线
  const mark = scrollOffset + viewport * 0.25;
  const itemIdx = v.findItemIndex(mark);
  // 找 ≤ itemIdx 的最近真实用户输入对应的导航项下标（msgIndex 递增）
  let k = -1;
  for (let i = 0; i < items.length; i += 1) {
    if (items[i].msgIndex <= itemIdx) {
      k = i;
    } else {
      break;
    }
  }
  quickNavCurrentIndex.value = k < 0 ? 0 : k;
}

watch(quickNavItems, () => {
  updateQuickNavCurrent();
});

// ===== 横线位置自适应：钉死（向右延伸）/ 居中（两侧延伸）/ 隐藏 =====
// 与 _chat-area.scss 轨宽公式对应：rail = min(900px, 100% - 57px)，流式区间空白固定 ≈ 48.7px。
// 判定：空白达到最小值 ⇔ 内容轨已低于上限（轨宽满 → 钉死；轨宽未满 → 空白正中）；空白太短 → 隐藏。
const QUICK_NAV_RAIL_MAX = 900;
const QUICK_NAV_GAP_HIDE = 36; // 空白 < 36px 时隐藏（移动端轨宽 100% 时自然命中）
// 临界固定空白 = 滚动区 padding 24 + rail 公式固定余量 (57-7.52)/2 + 实测滚动条宽/2
// （57/7.52 见 _chat-area.scss 的 --chat-rail-fluid / --chat-message-rail-width）。
// 钉死线中心 = 临界空白的一半：钉死→居中切换瞬间线中心重合，无瞬移。
// 注意不能写死：macOS 经典滚动条（接鼠标）会让空白多 4px，写死会右跳 2px。
const QUICK_NAV_GAP_CRITICAL_BASE = 48.74;
const QUICK_NAV_LINE_MAX_WIDTH = 34;

const quickNavGap = ref(100); // 初始给足，挂载后由 ResizeObserver 校准
const quickNavRailAtMax = ref(true);
const quickNavPinCenter = ref(QUICK_NAV_GAP_CRITICAL_BASE / 2);

const quickNavMode = computed<'pinned' | 'center' | 'hidden'>(() => {
  if (quickNavGap.value < QUICK_NAV_GAP_HIDE) {
    return 'hidden';
  }
  return quickNavRailAtMax.value ? 'pinned' : 'center';
});

const quickNavWaveScale = computed(() => {
  const gap = quickNavGap.value;
  if (quickNavMode.value === 'pinned') {
    const pinLeft = quickNavPinCenter.value - QUICK_NAV_LINE_BASE_WIDTH / 2;
    return Math.max(0, Math.min(1, (gap - pinLeft - 6) / QUICK_NAV_LINE_MAX_WIDTH));
  }
  if (quickNavMode.value === 'center') {
    return Math.max(0, Math.min(1, (gap - 6) / QUICK_NAV_LINE_MAX_WIDTH));
  }
  return 1;
});

const quickNavNavStyle = computed<Record<string, string>>(() => {
  const base: Record<string, string> = { '--qn-count': String(quickNavItems.value.length) };
  if (quickNavMode.value === 'center') {
    // 空白内居中：切换瞬间空白 = 临界值，线中心与钉死位置重合，无瞬移
    return { ...base, left: '0px', width: `${quickNavGap.value}px` };
  }
  // 钉死：位置与窗口宽度无关，左不动右变长
  const pinLeft = quickNavPinCenter.value - QUICK_NAV_LINE_BASE_WIDTH / 2;
  return { ...base, left: `${pinLeft}px`, width: `${QUICK_NAV_LINE_MAX_WIDTH}px` };
});

function updateQuickNavLayout() {
  const shell = shellRef.value;
  const rail = contentRef.value;
  if (!shell || !rail) {
    return;
  }
  const shellRect = shell.getBoundingClientRect();
  const railRect = rail.getBoundingClientRect();
  // 实测滚动条宽度（与 updateChatScrollbarWidth 同口径）：经典滚动条会增大临界空白，
  // 钉死位置必须随之调整，否则钉死/居中切换瞬间线中心不重合
  const scroller = scrollRef.value;
  const sb = scroller ? Math.max(0, scroller.offsetWidth - scroller.clientWidth) : 0;
  quickNavPinCenter.value = (QUICK_NAV_GAP_CRITICAL_BASE + sb / 2) / 2;
  quickNavGap.value = railRect.left - shellRect.left;
  quickNavRailAtMax.value = railRect.width >= QUICK_NAV_RAIL_MAX - 0.5;
}

let quickNavLayoutObserver: ResizeObserver | null = null;

function registerQuickNavLine(index: number, el: any) {
  if (el) {
    quickNavLineEls.set(index, el as HTMLElement);
  } else {
    quickNavLineEls.delete(index);
  }
}

function onQuickNavMouseMove(event: MouseEvent) {
  let best = -1;
  let bestDist = Infinity;
  quickNavLineEls.forEach((el, i) => {
    const rect = el.getBoundingClientRect();
    const d = Math.abs(event.clientY - (rect.top + rect.height / 2));
    if (d < bestDist) {
      bestDist = d;
      best = i;
    }
  });
  quickNavActiveIndex.value = best;
}

function onQuickNavMouseLeave() {
  quickNavActiveIndex.value = -1;
}

function setupQuickNavLayoutObserver() {
  if (typeof ResizeObserver === 'undefined' || !shellRef.value) {
    return;
  }
  quickNavLayoutObserver = new ResizeObserver(() => {
    updateQuickNavLayout();
  });
  quickNavLayoutObserver.observe(shellRef.value);
  // 轨宽随滚动条出现/消失也会变化（不改变 shell 尺寸），需要一并观察
  if (contentRef.value) {
    quickNavLayoutObserver.observe(contentRef.value);
  }
  updateQuickNavLayout();
}

function teardownQuickNavLayoutObserver() {
  if (quickNavLayoutObserver) {
    quickNavLayoutObserver.disconnect();
    quickNavLayoutObserver = null;
  }
}

// ===== 快捷导航跳转（自驱 rAF 补间）=====
// 不用 virtua scrollToIndex 的 smooth：其内部是原生 scrollTo({behavior:'smooth'})，
// 会被任何一帧外部 scrollTop 覆写（liveStickLock 追底/块过渡锚定/KaTeX 回调）取消，
// 且平滑滚动前等待未测量条目仅有 150ms 引信，超时静默放弃（运行期表现为
// 「点第一下不动、第二下才跳」）。这里改为每帧自写 scrollTop 的补间：
// 目标偏移每帧经 getItemOffset 重新解析（随条目测量/流式增长自我校正），
// 外部覆写最多抢走一帧、下一帧即被补间夺回，保证平滑且必达。
let quickNavJumpRaf: number | null = null;
let quickNavJumpActive = false;

function cancelQuickNavJump() {
  if (quickNavJumpRaf !== null) {
    cancelAnimationFrame(quickNavJumpRaf);
    quickNavJumpRaf = null;
  }
  quickNavJumpActive = false;
}

function startQuickNavJump(msgIndex: number) {
  const el = scrollRef.value;
  const v: any = virtualizerRef.value;
  if (!el || !v || typeof v.getItemOffset !== 'function') {
    // 兜底：拿不到偏移解析能力时退回 virtua 自带跳转
    v?.scrollToIndex?.(msgIndex, { align: 'start', smooth: true });
    return;
  }
  cancelQuickNavJump();
  quickNavJumpActive = true;
  const startTop = el.scrollTop;
  const startTs = performance.now();
  const duration = 380;
  const tick = () => {
    quickNavJumpRaf = null;
    if (!quickNavJumpActive) return;
    const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
    const targetTop = Math.max(0, Math.min(Number(v.getItemOffset(msgIndex)) || 0, maxTop));
    const t = Math.min(1, (performance.now() - startTs) / duration);
    const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
    markProgrammaticHint('ChatArea.quickNavJump');
    el.scrollTop = startTop + (targetTop - startTop) * eased;
    if (t < 1) {
      quickNavJumpRaf = requestAnimationFrame(tick);
    } else {
      // 末帧精确落定，避免测量精化残留误差
      markProgrammaticHint('ChatArea.quickNavJump');
      el.scrollTop = targetTop;
      quickNavJumpActive = false;
    }
  };
  quickNavJumpRaf = requestAnimationFrame(tick);
}

function onQuickNavLineClick(item: any) {
  // 点击横线跳转 = 用户主动脱离底部锁定，与 wheel-up / 触摸下拉同路径：
  // 先 stopScroll() 解除 stick 锁定（库内置 escapedFromLock=true / isAtBottom=false，
  // 运行期 liveStickLock 随之停止逐帧拽回），再同步 emit 用户滚动意图让父级
  // 抑制自动追底，最后由自驱补间平滑滚动到目标轮次。
  const target = scrollRef.value;
  const now = Date.now();
  stopScroll();
  stopBlockExpansionAnchors();
  emit('user-scroll-intent', {
    ts: now,
    delta: -1,
    top: target ? target.scrollTop || 0 : 0
  });
  startQuickNavJump(item.msgIndex);
}

// ===== 悬浮预览卡 =====
const QUICK_NAV_PREVIEW_HEIGHT = 116;
const quickNavPreviewUser = ref('');
const quickNavPreviewAi = ref('');
const quickNavPreviewStyle = ref<Record<string, string>>({});

watch(quickNavActiveIndex, (idx) => {
  if (idx < 0) {
    return;
  }
  const item = quickNavItems.value[idx];
  if (!item) {
    return;
  }
  quickNavPreviewUser.value = item.userText;
  quickNavPreviewAi.value = item.aiText;
  const shell = shellRef.value;
  const lineEl = quickNavLineEls.get(idx);
  if (!shell || !lineEl) {
    return;
  }
  const shellRect = shell.getBoundingClientRect();
  const lineRect = lineEl.getBoundingClientRect();
  const centerY = lineRect.top - shellRect.top + lineRect.height / 2;
  let top = centerY - QUICK_NAV_PREVIEW_HEIGHT / 2;
  top = Math.max(8, Math.min(top, shellRect.height - QUICK_NAV_PREVIEW_HEIGHT - 8));
  const left = lineRect.right - shellRect.left + 14;
  quickNavPreviewStyle.value = { top: `${top}px`, left: `${left}px` };
});

defineExpose({
  rootEl,
  getThinkingRef,
  isUsingStickToBottom: () => true,
  getStickState,
  stopStickScroll: stopScroll,
  scrollToBottom: stickScrollToBottom,
  conditionalStickToBottom: stickConditionalScrollToBottom
});
</script>
