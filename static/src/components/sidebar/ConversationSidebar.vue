<template>
  <aside class="conversation-sidebar" :class="{ collapsed }">
    <div class="conversation-sidebar-inner">
      <div class="conversation-primary-actions">
        <button
          type="button"
          class="sidebar-nav-row conversation-menu-btn"
          :title="collapsed ? $t('sidebar.expandConversations') : $t('sidebar.collapseConversations')"
          @click="$emit('toggle')"
        >
          <span
            class="sidebar-nav-icon chat-icon"
            data-tutorial="conversation-menu"
            aria-hidden="true"
          >
            <svg viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path
                d="M5 6.5c0-1.38 1.12-2.5 2.5-2.5h13c1.38 0 2.5 1.12 2.5 2.5v8.5c0 1.38-1.12 2.5-2.5 2.5h-5.6l-3.4 3.2.6-3.2H7.5c-1.38 0-2.5-1.12-2.5-2.5V6.5z"
                stroke="currentColor"
                stroke-width="1.45"
                stroke-linejoin="round"
              />
              <path
                d="M9 9.5h10"
                stroke="currentColor"
                stroke-width="1.45"
                stroke-linecap="round"
              />
              <path d="M9 13h6" stroke="currentColor" stroke-width="1.45" stroke-linecap="round" />
            </svg>
          </span>
          <span class="sidebar-nav-label">{{ $t('sidebar.conversationHistory') }}</span>
        </button>

        <button type="button" class="sidebar-nav-row" :title="$t('sidebar.newConversation')" @click="$emit('create')">
          <span
            class="sidebar-nav-icon pencil-icon"
            data-tutorial="quick-new-conversation"
            aria-hidden="true"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path
                d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"
              />
              <path d="m15 5 4 4" />
            </svg>
          </span>
          <span class="sidebar-nav-label">{{ $t('sidebar.newConversation') }}</span>
        </button>

        <button
          v-if="hostWorkspaceEnabled"
          type="button"
          class="sidebar-nav-row workspace-entry-btn"
          :class="{ active: workspaceSwitcherOpen }"
          :title="workspaceKind === 'project' ? $t('sidebar.project') : $t('sidebar.workspace')"
          data-tutorial="workspace-toggle"
          ref="workspaceEntryBtn"
          @click="toggleWorkspaceSwitcher"
        >
          <span class="sidebar-nav-icon layers-icon" aria-hidden="true">
            <!-- 与其他导航按钮统一：内联 layers 图标，CSS 限到 18px 与铅笔一致，避免 mask 图标异步加载闪烁 -->
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path
                d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z"
              />
              <path
                d="M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12"
              />
              <path
                d="M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17"
              />
            </svg>
          </span>
          <span class="sidebar-nav-label">{{ workspaceKind === 'project' ? $t('sidebar.project') : $t('sidebar.workspace') }}</span>
        </button>

        <button
          type="button"
          class="sidebar-nav-row"
          :title="$t('sidebar.workflows')"
          @click="$emit('open-workflows')"
        >
          <span class="sidebar-nav-icon workflow-icon" aria-hidden="true">
            <!-- 与其他导航按钮统一：内联 workflow 图标，避免 mask 图标异步加载闪烁；
                 图形与 /static/icons/workflow.svg 保持一致 -->
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <circle cx="12" cy="4.6" r="2.6" />
              <path d="M12 7.2 V10.1" />
              <path d="M8.5 12.3 L12 10.1 L15.5 12.3 L12 14.5 Z" stroke-linejoin="miter" />
              <path d="M8.5 12.3 H6.1 Q4.5 12.3 4.5 13.9 V16.3" />
              <path d="M15.5 12.3 H17.9 Q19.5 12.3 19.5 13.9 V16.3" />
              <rect x="2.4" y="16.3" width="4.2" height="4.2" rx="1.1" />
              <rect x="17.4" y="16.3" width="4.2" height="4.2" rx="1.1" />
            </svg>
          </span>
          <span class="sidebar-nav-label">{{ $t('sidebar.workflows') }}</span>
        </button>

        <!-- 刷新页面：仅移动端 App 壳内由父级开启（浏览器有自带刷新，不显示） -->
        <button
          v-if="showRefreshButton"
          type="button"
          class="sidebar-nav-row sidebar-refresh-btn"
          :title="$t('appCore.refreshPage')"
          @click="$emit('refresh')"
        >
          <span class="sidebar-nav-icon refresh-icon" aria-hidden="true">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
              <path d="M21 3v5h-5" />
              <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
              <path d="M8 16H3v5" />
            </svg>
          </span>
          <span class="sidebar-nav-label">{{ $t('appCore.refreshPage') }}</span>
        </button>
      </div>

      <div class="conversation-search">
        <label class="search-input-wrap">
          <span class="sidebar-nav-icon search-inline-icon" aria-hidden="true">
            <span class="icon icon-sm" :style="iconStyle('search')"></span>
          </span>
          <input
            class="search-input"
            :value="searchQuery"
            :placeholder="$t('sidebar.searchConversationPlaceholder')"
            @input="$emit('search', ($event.target as HTMLInputElement).value)"
            @keydown.enter.prevent="
              $emit('search-submit', ($event.target as HTMLInputElement).value)
            "
          />
        </label>
      </div>

      <div v-if="!searchActive" class="conversation-type-switch">
        <button
          type="button"
          class="conversation-type-option"
          :class="{ active: sidebarType === 'normal' }"
          @click="setSidebarType('normal')"
        >
          {{ $t('sidebar.normalConversations') }}
        </button>
        <button
          type="button"
          class="conversation-type-option"
          :class="{ active: sidebarType === 'multi_agent' }"
          @click="setSidebarType('multi_agent')"
        >
          {{ $t('sidebar.multiAgent') }}
        </button>
      </div>

      <div ref="conversationListEl" class="conversation-list">
        <Transition :name="slideTransitionName">
          <div :key="sidebarType" class="conversation-list-pane">
        <div v-if="runningTaskItems.length && !searchActive && !isGroupByWorkspaceActive" class="running-task-section">
          <div class="running-task-section-title">{{ $t('sidebar.runningTasksTitle') }}</div>
          <button
            v-for="task in runningTaskItems"
            :key="task.task_id"
            type="button"
            class="running-task-item"
            :class="{ active: task.conversation_id === currentConversationId }"
            @click="$emit('select-running-task', task)"
          >
            <span class="running-task-workspace">{{ task.workspace_label || task.workspace_id }}</span>
            <span class="running-task-main">
              <span class="running-task-title">{{
                task.conversation_title || task.message || $t('sidebar.runningConversation')
              }}</span>
              <span
                v-if="isTaskActive(task)"
                class="conversation-running-loader running-task-state"
                :aria-label="$t('common.running')"
              ></span>
              <span v-else class="conversation-complete-check running-task-state" :aria-label="$t('sidebar.completed')"></span>
            </span>
          </button>
        </div>
        <template v-if="isGroupByWorkspaceActive && !searchActive">
          <div class="workspace-groups" :class="{ 'switch-instant': workspaceSwitchInstant }">
            <div
              v-for="ws in sortedWorkspaces"
              :key="String(ws.workspace_id || ws.label)"
              class="workspace-group"
            >
              <div
                class="workspace-group-header"
                :class="{ active: String(ws.workspace_id || '') === currentWorkspaceId }"
                @mouseenter="hoverWorkspaceId = String(ws.workspace_id || '')"
                @mouseleave="hoverWorkspaceId = null"
              >
                <button
                  type="button"
                  class="workspace-group-toggle"
                  @click="toggleWorkspaceExpanded(String(ws.workspace_id || ''))"
                >
                  <span
                    class="icon icon-sm workspace-folder-icon"
                    :style="iconStyle(isWorkspaceExpanded(String(ws.workspace_id || '')) ? 'folderOpen' : 'folderClosed')"
                    aria-hidden="true"
                  ></span>
                  <span class="workspace-group-label">{{ ws.label || ws.workspace_id }}</span>
                  <span
                    v-if="String(ws.workspace_id || '') === currentWorkspaceId"
                    class="workspace-current-dot"
                    :aria-label="$t('sidebar.currentWorkspace')"
                  ></span>
                  <span
                    v-if="
                      isWorkspaceRunning(String(ws.workspace_id || '')) &&
                      !isWorkspaceExpanded(String(ws.workspace_id || '')) &&
                      hoverWorkspaceId !== String(ws.workspace_id || '') &&
                      openWorkspaceMenuId !== String(ws.workspace_id || '')
                    "
                    class="workspace-running-loader"
                    :aria-label="$t('common.running')"
                  ></span>
                </button>
                <div
                  class="workspace-group-actions"
                  :class="{ visible: hoverWorkspaceId === String(ws.workspace_id || '') || openWorkspaceMenuId === String(ws.workspace_id || '') }"
                >
                  <button
                    type="button"
                    class="workspace-new-btn"
                    :title="$t('sidebar.newConversation')"
                    :aria-label="$t('sidebar.newConversation')"
                    @click="handleCreateWorkspaceConversation(String(ws.workspace_id || ''))"
                  >
                    <span class="icon icon-sm" :style="iconStyle('pencil')" aria-hidden="true"></span>
                  </button>
                  <button
                    type="button"
                    class="workspace-more-btn"
                    :title="$t('common.moreActions')"
                    :aria-label="$t('common.moreActions')"
                    :aria-expanded="openWorkspaceMenuId === String(ws.workspace_id || '')"
                    @click.stop="toggleWorkspaceMenu(String(ws.workspace_id || ''))"
                  >
                    <span aria-hidden="true"></span>
                  </button>
                  <div
                    class="workspace-actions-menu"
                    :class="{ open: openWorkspaceMenuId === String(ws.workspace_id || '') }"
                  >
                    <button type="button" @click="handlePinWorkspace(String(ws.workspace_id || ''))">
                      <span>{{ pinnedWorkspaceIds.has(String(ws.workspace_id || '')) ? $t('sidebar.unpinWorkspace') : $t('sidebar.pinWorkspace') }}</span>
                    </button>
                    <button
                      v-if="props.versioningHostMode"
                      type="button"
                      @click="handleRevealWorkspace(String(ws.workspace_id || ''))"
                    >
                      <span>{{ $t('sidebar.revealInFolder') }}</span>
                    </button>
                    <button type="button" @click="startRenameWorkspace(ws)">
                      <span>{{ $t('sidebar.rename') }}</span>
                    </button>
                  </div>
                </div>
              </div>
              <div
                class="workspace-group-children"
                :class="{ expanded: isWorkspaceExpanded(String(ws.workspace_id || '')) }"
              >
                <div class="workspace-group-children-inner">
                  <div
                    v-if="!getWorkspaceGroup(String(ws.workspace_id || ''))?.conversations?.length"
                    class="workspace-no-conversations"
                  >
                    {{ $t('sidebar.noConversations') }}
                  </div>
                  <template v-else>
                    <div
                      class="workspace-conversations-viewport"
                      :style="{
                        '--workspace-visible-count': getWorkspaceGroup(String(ws.workspace_id || ''))?.visibleLimit || 5
                      }"
                    >
                      <transition-group
                        name="conversation-delete"
                        tag="div"
                        class="workspace-conversations-list"
                        :class="`animation-mode-${conversationListAnimationMode}`"
                        @before-leave="lockLeaveItemPosition"
                      >
                        <div
                          v-for="conv in getWorkspaceVisibleAndBufferConversations(String(ws.workspace_id || ''))"
                          :key="conv.id"
                          class="conversation-item workspace-conversation-item"
                          :class="{
                            active: conv.id === currentConversationId,
                            running: isConversationActive(conv.id) || isConversationCompleted(conv.id),
                            'insert-from-left': conversationInsertAnimations[conv.id] === 'create',
                            'insert-from-under': conversationInsertAnimations[conv.id] === 'duplicate',
                            'duplicate-source-mask': conversationInsertAnimations[conv.id] === 'duplicateSource'
                          }"
                          @click="handleWorkspaceConversationClick(conv.id, String(ws.workspace_id || ''))"
                        >
                          <div class="conversation-title">{{ conv.title }}</div>
                          <div
                            class="conversation-action-wrap"
                            :class="{ 'menu-open': openActionMenuId === conv.id }"
                            @click.stop
                          >
                            <span
                              v-if="isConversationActive(conv.id)"
                              class="conversation-running-loader"
                              :aria-label="$t('common.running')"
                              :title="$t('common.running')"
                            ></span>
                            <span
                              v-else-if="isConversationCompleted(conv.id)"
                              class="conversation-complete-check"
                              :aria-label="$t('sidebar.completed')"
                              :title="$t('sidebar.completed')"
                            ></span>
                            <button
                              v-else
                              type="button"
                              class="conversation-more-btn"
                              :title="$t('common.moreActions')"
                              :aria-label="$t('common.moreActions')"
                              :data-action-trigger="conv.id"
                              :aria-expanded="openActionMenuId === conv.id"
                              @click="toggleActionMenu(conv.id)"
                            >
                              <span aria-hidden="true"></span>
                            </button>
                            <Teleport to="body">
                              <div
                                v-if="shouldShowActionMenu(conv.id)"
                                class="conversation-actions-menu conversation-actions-menu--fixed"
                                :class="{ open: openActionMenuId === conv.id }"
                                :style="{
                                  top: actionMenuPosition.top + 'px',
                                  right: actionMenuPosition.right + 'px'
                                }"
                              >
                                <button
                                  type="button"
                                  @click="
                                    openActionMenuId = null;
                                    $emit('duplicate', conv.id);
                                  "
                                >
                                  <span class="icon icon-sm" :style="iconStyle('copy')" aria-hidden="true"></span>
                                  <span>{{ $t('common.copy') }}</span>
                                </button>
                                <button
                                  type="button"
                                  class="danger"
                                  @click="
                                    openActionMenuId = null;
                                    $emit('delete', { id: conv.id, workspaceId: String(ws.workspace_id || '') });
                                  "
                                >
                                  <span class="icon icon-sm" :style="iconStyle('trash')" aria-hidden="true"></span>
                                  <span>{{ $t('common.delete') }}</span>
                                </button>
                              </div>
                            </Teleport>
                          </div>
                        </div>
                      </transition-group>
                    </div>
                    <div
                      v-if="getWorkspaceHasMore(String(ws.workspace_id || ''))"
                      class="load-more workspace-load-more"
                    >
                      <button
                        class="load-more-btn"
                        type="button"
                        :disabled="getWorkspaceGroup(String(ws.workspace_id || ''))?.loadingMore"
                        @click="loadMoreWorkspaceConversations(String(ws.workspace_id || ''))"
                      >
                        {{ getWorkspaceGroup(String(ws.workspace_id || ''))?.loadingMore ? $t('sidebar.loadingMore') : $t('sidebar.loadMore') }}
                      </button>
                    </div>
                  </template>
                </div>
              </div>
            </div>
          </div>
        </template>

        <template v-else-if="isGroupByWorkspaceActive && searchActive">
          <div class="workspace-groups search-result-groups">
            <div v-if="!searchInProgress && !sortedSearchGroups.length" class="no-conversations">
              {{ $t('sidebar.noMatchingConversations') }}
            </div>
            <div
              v-for="group in sortedSearchGroups"
              :key="String(group.workspace_id || '')"
              class="workspace-group"
            >
              <div class="workspace-group-header search-result-group-header">
                <div class="workspace-group-toggle search-result-group-toggle">
                  <span
                    class="icon icon-sm workspace-folder-icon"
                    :style="iconStyle('folderClosed')"
                    aria-hidden="true"
                  ></span>
                  <span class="workspace-group-label">{{ group.label || group.workspace_id }}</span>
                </div>
              </div>
              <div class="workspace-group-children expanded">
                <div class="workspace-group-children-inner">
                  <div class="workspace-conversations-list">
                    <div
                      v-for="conv in group.results"
                      :key="conv.id"
                      class="conversation-item workspace-conversation-item"
                      :class="{
                        active: conv.id === currentConversationId,
                        running: isConversationActive(conv.id) || isConversationCompleted(conv.id)
                      }"
                      @click="handleWorkspaceConversationClick(conv.id, String(group.workspace_id || ''))"
                    >
                      <div class="conversation-title">{{ conv.title }}</div>
                      <div
                        class="conversation-action-wrap"
                        :class="{ 'menu-open': openActionMenuId === conv.id }"
                        @click.stop
                      >
                        <span
                          v-if="isConversationActive(conv.id)"
                          class="conversation-running-loader"
                          :aria-label="$t('common.running')"
                          :title="$t('common.running')"
                        ></span>
                        <span
                          v-else-if="isConversationCompleted(conv.id)"
                          class="conversation-complete-check"
                          :aria-label="$t('sidebar.completed')"
                          :title="$t('sidebar.completed')"
                        ></span>
                        <button
                          v-else
                          type="button"
                          class="conversation-more-btn"
                          :title="$t('common.moreActions')"
                          :aria-label="$t('common.moreActions')"
                          :data-action-trigger="conv.id"
                          :aria-expanded="openActionMenuId === conv.id"
                          @click="toggleActionMenu(conv.id)"
                        >
                          <span aria-hidden="true"></span>
                        </button>
                        <Teleport to="body">
                          <div
                            v-if="shouldShowActionMenu(conv.id)"
                            class="conversation-actions-menu conversation-actions-menu--fixed"
                            :class="{ open: openActionMenuId === conv.id }"
                            :style="{
                              top: actionMenuPosition.top + 'px',
                              right: actionMenuPosition.right + 'px'
                            }"
                          >
                            <button
                              type="button"
                              @click="
                                openActionMenuId = null;
                                $emit('duplicate', conv.id);
                              "
                            >
                              <span class="icon icon-sm" :style="iconStyle('copy')" aria-hidden="true"></span>
                              <span>{{ $t('common.copy') }}</span>
                            </button>
                            <button
                              type="button"
                              class="danger"
                              @click="
                                openActionMenuId = null;
                                $emit('delete', { id: conv.id, workspaceId: String(group.workspace_id || '') });
                              "
                            >
                              <span class="icon icon-sm" :style="iconStyle('trash')" aria-hidden="true"></span>
                              <span>{{ $t('common.delete') }}</span>
                            </button>
                          </div>
                        </Teleport>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>

        <template v-else>
          <div
            v-if="loading && !displayConversations.length && !searchActive && !isDeletingConversation"
            class="loading-conversations"
          >
            {{ $t('sidebar.loading') }}
          </div>
          <div v-else-if="!displayConversations.length && !loading && !isDeletingConversation" class="no-conversations">
            {{ searchActive ? $t('sidebar.noMatchingConversations') : $t('sidebar.noConversationHistory') }}
          </div>
          <transition-group
            v-else
            name="conversation-delete"
            tag="div"
            class="conversation-list-items"
            :class="`animation-mode-${conversationListAnimationMode}`"
          >
            <div
              v-for="conv in displayConversations"
              :key="conv.id"
              class="conversation-item"
              :class="{
                active: conv.id === currentConversationId,
                running: isConversationActive(conv.id) || isConversationCompleted(conv.id),
                'insert-from-left': conversationInsertAnimations[conv.id] === 'create',
                'insert-from-under': conversationInsertAnimations[conv.id] === 'duplicate',
                'duplicate-source-mask': conversationInsertAnimations[conv.id] === 'duplicateSource',
                deleting: pendingDeletingConversationIds.includes(conv.id)
              }"
              @click="
                openActionMenuId = null;
                $emit('select', conv.id);
              "
            >
              <div class="conversation-title">{{ conv.title }}</div>
              <div
                class="conversation-action-wrap"
                :class="{ 'menu-open': openActionMenuId === conv.id }"
                @click.stop
              >
                <span
                  v-if="isConversationActive(conv.id)"
                  class="conversation-running-loader"
                  :aria-label="$t('common.running')"
                  :title="$t('common.running')"
                ></span>
                <span
                  v-else-if="isConversationCompleted(conv.id)"
                  class="conversation-complete-check"
                  :aria-label="$t('sidebar.completed')"
                  :title="$t('sidebar.completed')"
                ></span>
                <button
                  v-else
                  type="button"
                  class="conversation-more-btn"
                  :title="$t('common.moreActions')"
                  :aria-label="$t('common.moreActions')"
                  :data-action-trigger="conv.id"
                  :aria-expanded="openActionMenuId === conv.id"
                  @click="toggleActionMenu(conv.id)"
                >
                  <span aria-hidden="true"></span>
                </button>
                            <Teleport to="body">
                              <div
                                v-if="!isConversationActive(conv.id) && !isConversationCompleted(conv.id) && openActionMenuId === conv.id"
                                class="conversation-actions-menu conversation-actions-menu--fixed"
                                :class="{ open: openActionMenuId === conv.id }"
                                :style="{
                                  top: actionMenuPosition.top + 'px',
                                  right: actionMenuPosition.right + 'px'
                                }"
                              >
                                <button
                                  type="button"
                                  @click="
                                    openActionMenuId = null;
                                    $emit('duplicate', conv.id);
                                  "
                                >
                                  <span class="icon icon-sm" :style="iconStyle('copy')" aria-hidden="true"></span>
                                  <span>{{ $t('common.copy') }}</span>
                                </button>
                                <button
                                  type="button"
                                  class="danger"
                                  @click="
                                    openActionMenuId = null;
                                    $emit('delete', conv.id);
                                  "
                                >
                                  <span class="icon icon-sm" :style="iconStyle('trash')" aria-hidden="true"></span>
                                  <span>{{ $t('common.delete') }}</span>
                                </button>
                              </div>
                            </Teleport>
              </div>
            </div>
          </transition-group>
        </template>

        <div v-if="!searchActive && !isGroupByWorkspaceActive && hasMore" class="load-more">
          <button
            class="load-more-btn"
            type="button"
            :disabled="loadingMore"
            @click="$emit('load-more')"
          >
            {{ loadingMore ? $t('sidebar.loadingMore') : $t('sidebar.loadMore') }}
          </button>
        </div>
        <div v-else-if="searchActive" class="load-more search-more">
          <div v-if="searchInProgress" class="search-progress">
            <span class="search-spinner" aria-hidden="true">
              <span class="search-spinner-orbit">
                <span class="icon icon-sm search-spinner-icon" :style="iconStyle('search')"></span>
              </span>
            </span>
            <span class="search-progress-text">{{ $t('sidebar.searching') }}</span>
          </div>
          <button
            v-else-if="searchMoreAvailable"
            class="load-more-btn"
            type="button"
            @click="$emit('search-more')"
          >
            {{ $t('sidebar.searchMore') }}
          </button>
          <div v-else-if="displayConversations.length" class="search-done">{{ $t('sidebar.searchExhausted') }}</div>
        </div>
          </div>
        </Transition>
      </div>

      <div
        v-if="renameWorkspaceId"
        class="workspace-rename-overlay"
        @click.self="cancelRenameWorkspace"
      >
        <div class="workspace-rename-card">
          <div class="workspace-rename-title">
            {{ $t('sidebar.renameTitle', { kind: $t(workspaceKind === 'workspace' ? 'sidebar.workspace' : 'sidebar.project') }) }}
          </div>
          <input
            ref="renameInput"
            v-model="renameWorkspaceLabel"
            type="text"
            class="workspace-rename-input"
            :placeholder="$t('sidebar.renamePlaceholder')"
            @keydown.enter.prevent="submitRenameWorkspace"
            @keydown.esc.prevent="cancelRenameWorkspace"
          />
          <div class="workspace-rename-actions">
            <button type="button" class="workspace-rename-cancel" @click="cancelRenameWorkspace">
              {{ $t('common.cancel') }}
            </button>
            <button type="button" class="workspace-rename-confirm" @click="submitRenameWorkspace">
              {{ $t('common.confirm') }}
            </button>
          </div>
        </div>
      </div>

      <WorkspaceSwitcher
        :open="workspaceSwitcherOpen"
        :anchor-rect="workspaceAnchorRect"
        :workspaces="workspaces"
        :current-workspace-id="currentWorkspaceId"
        :default-workspace-id="defaultWorkspaceId"
        :workspace-kind="workspaceKind"
        :busy="workspaceBusy"
        :create-submitting="workspaceCreateSubmitting"
        :error-message="workspaceError"
        @close="workspaceSwitcherOpen = false"
        @switch="$emit('switch-workspace', $event)"
        @create="$emit('create-workspace', $event)"
        @rename="$emit('rename-workspace', $event)"
        @delete="$emit('delete-workspace', $event)"
        @set-default="$emit('set-default-workspace', $event)"
        @reveal="$emit('reveal-workspace', $event)"
      />

      <div class="conversation-personal-entry" :class="{ active: personalPageVisible || personalMenuOpen }">
        <button
          ref="personalEntryBtn"
          type="button"
          class="sidebar-nav-row personal-page-btn"
          data-tutorial="open-personal-space"
          :title="sessionUsername || $t('sidebar.personalSpace')"
          :aria-expanded="personalMenuOpen"
          @click.stop="togglePersonalMenu"
        >
          <span class="sidebar-nav-icon" aria-hidden="true">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
              <path
                fill-rule="evenodd"
                clip-rule="evenodd"
                d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z"
              ></path>
            </svg>
          </span>
          <span class="sidebar-nav-label personal-label">{{ sessionUsername || $t('sidebar.personalSpace') }}</span>
          <span class="personal-menu-caret" aria-hidden="true">
            <svg viewBox="0 0 16 16" fill="none" width="12" height="12">
              <path d="M4 10l4-4 4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </span>
        </button>
        <!-- 二级菜单：个人空间 / 个性化 / 设置 / 帮助（置灰） / 退出登录。
             Teleport 到 body + fixed 定位：侧边栏 collapsed 时自身 overflow: hidden
             会把 absolute 菜单裁成窄条，与 conversation-actions-menu--fixed 同方案 -->
        <Teleport to="body">
          <div
            v-if="personalMenuOpen"
            class="personal-menu"
            role="menu"
            :style="{
              bottom: personalMenuPosition.bottom + 'px',
              left: personalMenuPosition.left + 'px'
            }"
            @click.stop
          >
            <button
              type="button"
              class="personal-menu-item"
              role="menuitem"
              @click="openPersonalSpacePage"
            >
              <span
                class="icon personal-menu-icon"
                :style="personalMenuIconStyle('user')"
                aria-hidden="true"
              ></span>
              <span class="personal-menu-label">{{ $t('sidebar.personalSpace') }}</span>
            </button>
            <button
              type="button"
              class="personal-menu-item"
              role="menuitem"
              @click="openPreferencesPage"
            >
              <span
                class="icon personal-menu-icon"
                :style="personalMenuIconStyle('userPen')"
                aria-hidden="true"
              ></span>
              <span class="personal-menu-label">{{ $t('sidebar.preferences') }}</span>
            </button>
            <button
              type="button"
              class="personal-menu-item"
              role="menuitem"
              @click="openSettingsPage"
            >
              <span
                class="icon personal-menu-icon"
                :style="personalMenuIconStyle('settings')"
                aria-hidden="true"
              ></span>
              <span class="personal-menu-label">{{ $t('common.settings') }}</span>
            </button>
            <button
              type="button"
              class="personal-menu-item"
              role="menuitem"
              disabled
              :title="$t('sidebar.helpComingSoon')"
            >
              <span
                class="icon personal-menu-icon"
                :style="personalMenuIconStyle('messageQuestion')"
                aria-hidden="true"
              ></span>
              <span class="personal-menu-label">{{ $t('sidebar.help') }}</span>
            </button>
            <div class="personal-menu-divider" role="separator"></div>
            <button
              type="button"
              class="personal-menu-item danger"
              role="menuitem"
              @click="handleLogout"
            >
              <span
                class="icon personal-menu-icon"
                :style="personalMenuIconStyle('logOut')"
                aria-hidden="true"
              ></span>
              <span class="personal-menu-label">{{ $t('personalization.logoutTitle') }}</span>
            </button>
          </div>
        </Teleport>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
defineOptions({ name: 'ConversationSidebar' });

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useUiStore } from '@/stores/ui';
import { useConversationStore } from '@/stores/conversation';
import { usePersonalizationStore } from '@/stores/personalization';
import { ICONS } from '@/utils/icons';
import WorkspaceSwitcher from './WorkspaceSwitcher.vue';

const props = withDefaults(
  defineProps<{
    formatTime?: (input: unknown) => string;
    iconStyle?: (key: string) => Record<string, string>;
    showCollapseButton?: boolean;
    collapseButtonVariant?: 'toggle' | 'close';
    /** 移动端 App 壳内专用：在导航区末尾显示「刷新页面」按钮（2026-09-24 移动端改造） */
    showRefreshButton?: boolean;
    runningTasks?: any[];
    currentWorkspaceId?: string;
    defaultWorkspaceId?: string;
    workspaces?: any[];
    workspaceKind?: 'workspace' | 'project';
    workspaceBusy?: boolean;
    workspaceCreateSubmitting?: boolean;
    workspaceError?: string;
    hostWorkspaceEnabled?: boolean;
    groupByWorkspace?: boolean;
    versioningHostMode?: boolean;
    /** 当前会话是否仍在运行（用于标记当前正在查看的对话也显示运行中转圈） */
    currentTaskInProgress?: boolean;
    currentConversationId?: string;
  }>(),
  {
    showCollapseButton: true,
    collapseButtonVariant: 'toggle',
    showRefreshButton: false,
    runningTasks: () => [],
    currentWorkspaceId: '',
    defaultWorkspaceId: '',
    workspaces: () => [],
    workspaceKind: 'workspace',
    workspaceBusy: false,
    workspaceCreateSubmitting: false,
    workspaceError: '',
    hostWorkspaceEnabled: false,
    groupByWorkspace: false,
    versioningHostMode: false,
    currentTaskInProgress: false,
    currentConversationId: ''
  }
);

const emit = defineEmits<{
  (event: 'toggle'): void;
  (event: 'create'): void;
  (event: 'refresh'): void;
  (event: 'search', value: string): void;
  (event: 'search-submit', value: string): void;
  (event: 'select', id: string): void;
  (event: 'select-running-task', task: any): void;
  (event: 'load-more'): void;
  (event: 'search-more'): void;
  (event: 'personal', tab?: string): void;
  (event: 'delete', id: string): void;
  (event: 'duplicate', id: string): void;
  (event: 'switch-workspace', workspaceId: string): void;
  (event: 'create-workspace', payload: { path: string; label: string }): void;
  (event: 'delete-workspace', item: any): void;
  (event: 'set-default-workspace', workspaceId: string): void;
  (event: 'select-workspace-conversation', payload: { conversationId: string; workspaceId: string }): void;
  (event: 'create-workspace-conversation', workspaceId: string): void;
  (event: 'reveal-workspace', workspaceId: string): void;
  (event: 'rename-workspace', payload: { workspaceId: string; label: string }): void;
  (event: 'pin-workspace', workspaceId: string): void;
  (event: 'conversation-type-change', type: 'normal' | 'multi_agent'): void;
  (event: 'open-workflows'): void;
}>();

const uiStore = useUiStore();
const conversationStore = useConversationStore();
const personalizationStore = usePersonalizationStore();

// ── 个人入口：用户名按钮 + 二级菜单（个人空间/个性化/设置/帮助/退出登录） ──
const personalMenuOpen = ref(false);
const sessionUsername = ref('');
const personalEntryBtn = ref<HTMLElement | null>(null);
/* 菜单 Teleport 到 body 后的 fixed 定位（向上弹出）：bottom 锚定按钮顶缘 */
const personalMenuPosition = ref<{ bottom: number; left: number }>({ bottom: 0, left: 0 });

/* 菜单项图标：本地直接查 ICONS 注册表（与 PersonalizationDrawer 同款），
   不依赖父级可选 prop iconStyle，保证菜单在任何宿主下都有图标 */
const personalMenuIconStyle = (key: keyof typeof ICONS) => ({
  '--icon-src': `url(${ICONS[key]})`
});

function updatePersonalMenuPosition() {
  const el = personalEntryBtn.value;
  if (!el || typeof window === 'undefined') return;
  const rect = el.getBoundingClientRect();
  personalMenuPosition.value = {
    bottom: window.innerHeight - rect.top + 6,
    left: rect.left
  };
}

function togglePersonalMenu() {
  if (!personalMenuOpen.value) {
    updatePersonalMenuPosition();
  }
  personalMenuOpen.value = !personalMenuOpen.value;
}
function closePersonalMenu() {
  if (personalMenuOpen.value) personalMenuOpen.value = false;
}
function openPersonalSpacePage() {
  closePersonalMenu();
  emit('personal');
}
function openPreferencesPage() {
  closePersonalMenu();
  emit('personal', 'preferences');
}
function openSettingsPage() {
  closePersonalMenu();
  // 设置页是 bootstrap 级全屏路由，整页跳转保证状态干净（与 workflows 对称）
  window.location.assign('/settings');
}
function handleLogout() {
  closePersonalMenu();
  // 与 AccountTab 原退出按钮同一入口（store 内含 POST /logout + GET 兜底），不 await
  personalizationStore.logout();
}
async function fetchSessionUsername() {
  try {
    const resp = await fetch('/api/session-status', { credentials: 'same-origin' });
    if (!resp.ok) return;
    const payload = await resp.json();
    sessionUsername.value = String(payload?.session?.username || '');
  } catch (_err) {
    sessionUsername.value = '';
  }
}

/** 侧边栏对话类型过滤器（普通/多智能体），唯一真相在 conversation store */
const sidebarType = computed(() => conversationStore.sidebarConversationType);

/** 列表切换动画方向：切到多智能体向左滑出/从右侧进入，切回反向 */
const slideTransitionName = computed(() =>
  sidebarType.value === 'multi_agent' ? 'type-slide-left' : 'type-slide-right'
);

/** 列表容器：切换类型时重置滚动位置，保证推挤动画从顶部开始 */
const conversationListEl = ref<HTMLElement | null>(null);

/* 切换为纯本地操作：store 内两种类型的列表常驻缓存，setSidebarConversationType
   同步交换 conversations 引用与分页状态，无请求无加载态；pane key 与数据同 tick 变化，
   新面板初始挂载即新数据（transition-group 初始渲染不播动画），只保留整板左右平移 */
const setSidebarType = (type: 'normal' | 'multi_agent') => {
  if (conversationStore.sidebarConversationType === type) return;
  if (conversationListEl.value) conversationListEl.value.scrollTop = 0;
  conversationStore.setSidebarConversationType(type);
  emit('conversation-type-change', type);
};

const { sidebarCollapsed: collapsed } = storeToRefs(uiStore);
const {
  searchQuery,
  searchResults,
  searchGroups,
  searchActive,
  searchInProgress,
  searchMoreAvailable,
  conversations,
  conversationInsertAnimations,
  conversationListAnimationMode,
  pendingDeletingConversationIds,
  conversationsLoading,
  hasMoreConversations: hasMore,
  loadingMoreConversations: loadingMore,
  currentConversationId
} = storeToRefs(conversationStore);
const { visible: personalPageVisible } = storeToRefs(personalizationStore);

const iconStyle = (key: string) => (props.iconStyle ? props.iconStyle(key) : {});

/* ---------- 工作区切换浮层 ---------- */
const workspaceEntryBtn = ref<HTMLElement | null>(null);
const workspaceSwitcherOpen = ref(false);
const workspaceAnchorRect = ref<{ left: number; top: number; right: number; bottom: number } | null>(null);

const toggleWorkspaceSwitcher = () => {
  if (workspaceSwitcherOpen.value) {
    workspaceSwitcherOpen.value = false;
    return;
  }
  const el = workspaceEntryBtn.value;
  if (el) {
    const rect = el.getBoundingClientRect();
    /* 折叠时 sidebar-inner 保持展开宽、超出部分被 overflow 裁切，
       按钮布局右缘在可视区外；锚点右缘需夹紧到侧边栏可视右缘，
       否则浮层会出现在「展开后的位置」而不是紧贴折叠栏 */
    const sidebarEl = el.closest('.conversation-sidebar');
    const visibleRight = sidebarEl ? sidebarEl.getBoundingClientRect().right : rect.right;
    workspaceAnchorRect.value = {
      left: rect.left,
      top: rect.top,
      right: Math.min(rect.right, visibleRight),
      bottom: rect.bottom
    };
  }
  workspaceSwitcherOpen.value = true;
};
const openActionMenuId = ref<string | null>(null);
const actionMenuPosition = ref<{ top: number; right: number }>({ top: 0, right: 0 });
/* 切换工作区重排期间为 true：禁用子对话 FLIP 位移与分组展开折叠过渡，
   使分组头与子对话全部瞬间移动（不影响新建/复制/删除动画） */
const workspaceSwitchInstant = ref(false);
const hoverWorkspaceId = ref<string | null>(null);
const openWorkspaceMenuId = ref<string | null>(null);
const renameWorkspaceId = ref<string | null>(null);
const renameWorkspaceLabel = ref('');
const renameInput = ref<HTMLInputElement | null>(null);

watch(renameWorkspaceId, async (id) => {
  if (id) {
    await nextTick();
    renameInput.value?.focus();
    renameInput.value?.select();
  }
});

const updateActionMenuPosition = (conversationId: string) => {
  if (typeof document === 'undefined') return;
  const trigger = document.querySelector(`[data-action-trigger="${conversationId}"]`) as HTMLElement | null;
  if (!trigger) return;
  const rect = trigger.getBoundingClientRect();
  actionMenuPosition.value = {
    top: rect.bottom + 6,
    right: (typeof window !== 'undefined' ? window.innerWidth : 0) - rect.right
  };
};

const toggleActionMenu = (conversationId: string) => {
  if (openActionMenuId.value === conversationId) {
    openActionMenuId.value = null;
  } else {
    updateActionMenuPosition(conversationId);
    openActionMenuId.value = conversationId;
  }
};

watch(openActionMenuId, async (id) => {
  if (id) {
    await nextTick();
    updateActionMenuPosition(id);
  }
});

const shouldShowActionMenu = (conversationId: string) => {
  return (
    openActionMenuId.value === conversationId &&
    !isConversationActive(conversationId) &&
    !isConversationCompleted(conversationId)
  );
};

/* 删除离场动画定位修复：分组列表是 flex 容器，按 CSS Flexbox 规范，
   绝对定位子元素的静态位置会跑到容器顶部（而不是元素原来的行位置），
   导致删除非首条对话时离场元素瞬移到顶部。before-leave 时元素仍在文档流中，
   用 offsetTop 锁定原位，leave-active 的 position:absolute 生效后即停在原行。 */
const lockLeaveItemPosition = (el: Element) => {
  const item = el as HTMLElement;
  item.style.top = `${item.offsetTop}px`;
};

const closeActionMenu = () => {
  openActionMenuId.value = null;
};

const pinnedWorkspaceIds = ref<Set<string>>(new Set());
const workspaceOrder = ref<string[]>([]);
const sidebarSortLoaded = ref(false);

const syncPinnedFromStore = () => {
  pinnedWorkspaceIds.value = new Set(
    Array.isArray(personalizationStore.form.sidebar_pinned_workspaces)
      ? personalizationStore.form.sidebar_pinned_workspaces
      : []
  );
};

const syncOrderFromStore = () => {
  workspaceOrder.value = Array.isArray(personalizationStore.form.sidebar_workspace_order)
    ? [...personalizationStore.form.sidebar_workspace_order]
    : [];
};

const persistPinnedWorkspaces = () => {
  if (!sidebarSortLoaded.value) return;
  const next = Array.from(pinnedWorkspaceIds.value);
  const current = personalizationStore.form.sidebar_pinned_workspaces || [];
  if (JSON.stringify(next) === JSON.stringify(current)) return;
  personalizationStore.updateField({
    key: 'sidebar_pinned_workspaces',
    value: next
  });
};

const persistWorkspaceOrder = () => {
  if (!sidebarSortLoaded.value) return;
  const next = Array.from(workspaceOrder.value);
  const current = personalizationStore.form.sidebar_workspace_order || [];
  if (JSON.stringify(next) === JSON.stringify(current)) return;
  personalizationStore.updateField({
    key: 'sidebar_workspace_order',
    value: next
  });
};

const bumpCurrentWorkspaceToTop = () => {
  if (!sidebarSortLoaded.value) return;
  const currentId = String(props.currentWorkspaceId || '');
  if (!currentId || pinnedWorkspaceIds.value.has(currentId)) return;
  if (workspaceOrder.value[0] === currentId) return;
  const order = workspaceOrder.value.filter((item) => item !== currentId);
  order.unshift(currentId);
  workspaceOrder.value = order;
  persistWorkspaceOrder();
};

const syncWorkspaceOrderWithWorkspaces = (workspaces: any[]) => {
  if (!sidebarSortLoaded.value || !Array.isArray(workspaces)) return;
  const ids = workspaces
    .map((ws) => String(ws?.workspace_id || ''))
    .filter(Boolean);
  const existing = new Set(workspaceOrder.value);
  const pinnedIds = new Set(
    ids.filter((id) => pinnedWorkspaceIds.value.has(id))
  );
  const newIds = ids.filter((id) => !existing.has(id) && !pinnedIds.has(id));
  const removedIds = new Set(
    workspaceOrder.value.filter((id) => !ids.includes(id))
  );
  if (newIds.length || removedIds.size) {
    workspaceOrder.value = workspaceOrder.value
      .filter((id) => !removedIds.has(id))
      .concat(newIds);
    persistWorkspaceOrder();
  }
};

const loadSidebarSortFromStore = () => {
  if (!personalizationStore.loaded || sidebarSortLoaded.value) return;
  syncPinnedFromStore();
  syncOrderFromStore();
  sidebarSortLoaded.value = true;
  if (Array.isArray(props.workspaces) && props.workspaces.length > 0) {
    syncWorkspaceOrderWithWorkspaces(props.workspaces);
  }
  bumpCurrentWorkspaceToTop();
};

watch(
  () => personalizationStore.loaded,
  (loaded) => {
    if (loaded) loadSidebarSortFromStore();
  },
  { immediate: true }
);

watch(
  () => personalizationStore.form.sidebar_pinned_workspaces,
  () => {
    if (!sidebarSortLoaded.value) return;
    syncPinnedFromStore();
  },
  { deep: true }
);

watch(
  () => personalizationStore.form.sidebar_workspace_order,
  () => {
    if (!sidebarSortLoaded.value) return;
    syncOrderFromStore();
    if (Array.isArray(props.workspaces) && props.workspaces.length > 0) {
      syncWorkspaceOrderWithWorkspaces(props.workspaces);
    }
    bumpCurrentWorkspaceToTop();
  },
  { deep: true }
);

watch(
  () => props.workspaces,
  (list) => {
    if (Array.isArray(list) && list.length > 0) {
      syncWorkspaceOrderWithWorkspaces(list);
    }
  },
  { immediate: true, deep: true }
);

const isGroupByWorkspaceActive = computed(
  () => props.groupByWorkspace && props.hostWorkspaceEnabled && Array.isArray(props.workspaces) && props.workspaces.length > 0
);

/* 注意：本 watcher 声明必须放在 isGroupByWorkspaceActive 之后——
   immediate 回调会在 setup 阶段同步执行，提前引用会触发 TDZ 报错。 */
watch(
  () => props.currentWorkspaceId,
  async () => {
    /* 切换工作区会触发 bumpCurrentWorkspaceToTop 重排分组：分组头是瞬时
       移动的，而 transition-group 会对子对话做 FLIP 位移动画，节奏不一致
       很诡异。重排期间挂上 switch-instant 让两者都瞬间完成。 */
    if (isGroupByWorkspaceActive.value) {
      workspaceSwitchInstant.value = true;
      bumpCurrentWorkspaceToTop();
      await nextTick();
      workspaceSwitchInstant.value = false;
    } else {
      bumpCurrentWorkspaceToTop();
    }
  },
  { immediate: true }
);
const sortedWorkspaces = computed(() => {
  const list = Array.isArray(props.workspaces) ? [...props.workspaces] : [];
  return list.sort((a: any, b: any) => {
    const aId = String(a?.workspace_id || '');
    const bId = String(b?.workspace_id || '');
    const aPinned = pinnedWorkspaceIds.value.has(aId) ? 1 : 0;
    const bPinned = pinnedWorkspaceIds.value.has(bId) ? 1 : 0;
    if (aPinned !== bPinned) return bPinned - aPinned;
    const aIndex = workspaceOrder.value.indexOf(aId);
    const bIndex = workspaceOrder.value.indexOf(bId);
    if (aIndex !== -1 && bIndex !== -1) return aIndex - bIndex;
    if (aIndex !== -1) return -1;
    if (bIndex !== -1) return 1;
    return String(a?.label || aId).localeCompare(String(b?.label || bId));
  });
});

const displayConversations = computed(() =>
  searchActive.value ? searchResults.value : conversations.value
);

/* 跨工作区搜索结果：按与工作区分组列表一致的顺序排列（置顶 > 自定义顺序 > 名称） */
const sortedSearchGroups = computed(() => {
  const groups = Array.isArray(searchGroups.value) ? [...searchGroups.value] : [];
  return groups.sort((a: any, b: any) => {
    const aId = String(a?.workspace_id || '');
    const bId = String(b?.workspace_id || '');
    const aPinned = pinnedWorkspaceIds.value.has(aId) ? 1 : 0;
    const bPinned = pinnedWorkspaceIds.value.has(bId) ? 1 : 0;
    if (aPinned !== bPinned) return bPinned - aPinned;
    const aIndex = workspaceOrder.value.indexOf(aId);
    const bIndex = workspaceOrder.value.indexOf(bId);
    if (aIndex !== -1 && bIndex !== -1) return aIndex - bIndex;
    if (aIndex !== -1) return -1;
    if (bIndex !== -1) return 1;
    return String(a?.label || aId).localeCompare(String(b?.label || bId));
  });
});
const loading = computed(() =>
  searchActive.value ? searchInProgress.value : conversationsLoading.value
);
const isDeletingConversation = computed(() => conversationListAnimationMode.value === 'delete');
const runningTaskItems = computed(() =>
  Array.isArray(props.runningTasks)
    ? props.runningTasks.filter(
        (task) =>
          task &&
          task.task_id &&
          task.conversation_id &&
          task.conversation_id !== currentConversationId.value &&
          String(task.workspace_id || '') !== String(props.currentWorkspaceId || '')
      )
    : []
);
const activeStatuses = new Set(['pending', 'running', 'cancel_requested']);
const terminalStatuses = new Set(['succeeded', 'failed', 'canceled']);
const isTaskActive = (task: any) => activeStatuses.has(String(task?.status || ''));
const isTaskCompleted = (task: any) => terminalStatuses.has(String(task?.status || ''));
const activeConversationIds = computed(
  () =>
    new Set(
      (Array.isArray(props.runningTasks) ? props.runningTasks : [])
        .filter((task) => isTaskActive(task))
        .map((task) => String(task?.conversation_id || ''))
        .filter(Boolean)
    )
);
const completedConversationIds = computed(
  () =>
    new Set(
      (Array.isArray(props.runningTasks) ? props.runningTasks : [])
        .filter((task) => isTaskCompleted(task))
        .map((task) => String(task?.conversation_id || ''))
        .filter(Boolean)
    )
);
const isConversationActive = (conversationId: string) => {
  if (activeConversationIds.value.has(String(conversationId || ''))) return true;
  // 后端 runningTasks 库只跟踪跨工作的任务（当前会话排除了），
  // 所以当前会话的运行态要单独用 currentTaskInProgress 标记。
  if (props.currentTaskInProgress && String(conversationId || '') === String(props.currentConversationId || '')) {
    return true;
  }
  return false;
};
const isConversationCompleted = (conversationId: string) =>
  completedConversationIds.value.has(String(conversationId || ''));
const isWorkspaceRunning = (workspaceId: string) =>
  (Array.isArray(props.runningTasks) ? props.runningTasks : []).some(
    (task) => isTaskActive(task) && String(task?.workspace_id || '') === workspaceId
  );

const getWorkspaceGroup = (workspaceId: string) => {
  return conversationStore.workspaceGroups.find((g) => g.workspaceId === workspaceId);
};

const getWorkspaceVisibleAndBufferConversations = (workspaceId: string) => {
  const group = getWorkspaceGroup(workspaceId);
  if (!group) return [];
  const end = group.visibleLimit + group.bufferLimit;
  return group.conversations.slice(0, end);
};

const getWorkspaceHasMore = (workspaceId: string) => {
  const group = getWorkspaceGroup(workspaceId);
  if (!group) return false;
  return group.hasMore || group.conversations.length > group.visibleLimit;
};

const isWorkspaceExpanded = (workspaceId: string) => {
  const group = getWorkspaceGroup(workspaceId);
  return group ? group.expanded : true;
};

const toggleWorkspaceExpanded = (workspaceId: string) => {
  const group = getWorkspaceGroup(workspaceId);
  if (!group) {
    conversationStore.ensureWorkspaceGroup(workspaceId);
    void conversationStore.loadWorkspaceConversations(workspaceId);
    return;
  }
  conversationStore.setWorkspaceGroupExpanded(workspaceId, !group.expanded);
  if (!group.expanded && group.conversations.length === 0 && !group.loading) {
    void conversationStore.loadWorkspaceConversations(workspaceId);
  }
};

const ensureWorkspaceGroup = (workspaceId: string) => {
  const group = getWorkspaceGroup(workspaceId);
  if (!group) {
    conversationStore.ensureWorkspaceGroup(workspaceId);
    void conversationStore.loadWorkspaceConversations(workspaceId);
  } else if (group.conversations.length === 0 && !group.loading && !group.loadingMore) {
    void conversationStore.loadWorkspaceConversations(workspaceId);
  }
};

watch(
  [isGroupByWorkspaceActive, sortedWorkspaces],
  ([active, list]) => {
    if (active && list.length > 0) {
      list.forEach((ws: any) => ensureWorkspaceGroup(String(ws?.workspace_id || '')));
    }
  },
  { immediate: true }
);

const handleWorkspaceConversationClick = (conversationId: string, workspaceId: string) => {
  openWorkspaceMenuId.value = null;
  emit('select-workspace-conversation', { conversationId, workspaceId });
};

const handleCreateWorkspaceConversation = (workspaceId: string) => {
  openWorkspaceMenuId.value = null;
  emit('create-workspace-conversation', workspaceId);
};

const toggleWorkspaceMenu = (workspaceId: string) => {
  openWorkspaceMenuId.value = openWorkspaceMenuId.value === workspaceId ? null : workspaceId;
};

const closeWorkspaceMenu = () => {
  openWorkspaceMenuId.value = null;
};

const handlePinWorkspace = (workspaceId: string) => {
  openWorkspaceMenuId.value = null;
  const order = [...workspaceOrder.value];
  const orderIndex = order.indexOf(workspaceId);
  if (pinnedWorkspaceIds.value.has(workspaceId)) {
    pinnedWorkspaceIds.value.delete(workspaceId);
    if (orderIndex !== -1) order.splice(orderIndex, 1);
    const currentId = String(props.currentWorkspaceId || '');
    const currentIndex = order.indexOf(currentId);
    const insertIndex = currentIndex !== -1 ? currentIndex + 1 : 0;
    order.splice(insertIndex, 0, workspaceId);
  } else {
    pinnedWorkspaceIds.value.add(workspaceId);
    if (orderIndex !== -1) order.splice(orderIndex, 1);
  }
  workspaceOrder.value = order;
  persistPinnedWorkspaces();
  persistWorkspaceOrder();
  emit('pin-workspace', workspaceId);
};

const handleRevealWorkspace = (workspaceId: string) => {
  openWorkspaceMenuId.value = null;
  emit('reveal-workspace', workspaceId);
};

const startRenameWorkspace = (workspace: any) => {
  openWorkspaceMenuId.value = null;
  renameWorkspaceId.value = String(workspace?.workspace_id || '');
  renameWorkspaceLabel.value = String(workspace?.label || workspace?.workspace_id || '');
};

const submitRenameWorkspace = () => {
  const workspaceId = renameWorkspaceId.value;
  const label = renameWorkspaceLabel.value.trim();
  if (workspaceId && label) {
    emit('rename-workspace', { workspaceId, label });
  }
  renameWorkspaceId.value = null;
  renameWorkspaceLabel.value = '';
};

const cancelRenameWorkspace = () => {
  renameWorkspaceId.value = null;
  renameWorkspaceLabel.value = '';
};

const loadMoreWorkspaceConversations = (workspaceId: string) => {
  void conversationStore.loadMoreWorkspaceConversations(workspaceId);
};

onMounted(() => {
  document.addEventListener('click', closeActionMenu);
  document.addEventListener('click', closeWorkspaceMenu);
  document.addEventListener('click', closePersonalMenu);
  fetchSessionUsername();
  if (isGroupByWorkspaceActive.value) {
    sortedWorkspaces.value.forEach((ws: any) => ensureWorkspaceGroup(String(ws?.workspace_id || '')));
  }
});

onBeforeUnmount(() => {
  document.removeEventListener('click', closeActionMenu);
  document.removeEventListener('click', closeWorkspaceMenu);
  document.removeEventListener('click', closePersonalMenu);
});
</script>
