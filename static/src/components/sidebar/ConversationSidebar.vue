<template>
  <aside class="conversation-sidebar" :class="{ collapsed }">
    <div class="conversation-sidebar-inner">
      <div class="conversation-sidebar-header">
        <div class="conversation-sidebar-mode" :inert="collapsed" :aria-hidden="collapsed">
          <ModeSwitcher v-if="!collapsed" :mode="chatMode ? 'chat' : 'agent'" />
        </div>
        <button
          type="button"
          class="conversation-sidebar-toggle"
          :aria-label="
            collapsed ? $t('sidebar.expandConversations') : $t('sidebar.collapseConversations')
          "
          :aria-expanded="!collapsed"
          data-tutorial="conversation-menu"
          @click="$emit('toggle')"
        >
          <span
            class="icon"
            :style="iconSrcStyle(collapsed ? 'panelLeft' : 'panelRight')"
            aria-hidden="true"
          ></span>
        </button>
      </div>
      <div class="conversation-primary-actions">
        <button
          type="button"
          class="sidebar-nav-row"
          :title="$t('sidebar.newConversation')"
          @click="$emit('create')"
        >
          <span class="sidebar-nav-icon" data-tutorial="quick-new-conversation" aria-hidden="true">
            <span class="icon" :style="iconSrcStyle('pencil')"></span>
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
          <span class="sidebar-nav-icon" aria-hidden="true">
            <span class="icon" :style="iconSrcStyle('layers')"></span>
          </span>
          <span class="sidebar-nav-label">{{
            workspaceKind === 'project' ? $t('sidebar.project') : $t('sidebar.workspace')
          }}</span>
        </button>

        <button
          type="button"
          class="sidebar-nav-row"
          :title="$t('sidebar.workflows')"
          v-if="!chatMode"
          @click="$emit('open-workflows')"
        >
          <span class="sidebar-nav-icon" aria-hidden="true">
            <span class="icon" :style="iconSrcStyle('workflow')"></span>
          </span>
          <span class="sidebar-nav-label">{{ $t('sidebar.workflows') }}</span>
        </button>

        <button
          v-if="!chatMode"
          type="button"
          class="sidebar-nav-row schedules-entry-btn"
          :title="$t('sidebar.schedules')"
          @click="$emit('open-schedules')"
        >
          <span class="sidebar-nav-icon" aria-hidden="true">
            <span class="icon" :style="iconSrcStyle('timer')"></span>
          </span>
          <span class="sidebar-nav-label">{{ $t('sidebar.schedules') }}</span>
        </button>
        <button v-if="!chatMode" type="button" class="sidebar-nav-row" @click="$emit('open-git')">
          <span class="sidebar-nav-icon" aria-hidden="true">
            <span class="icon git-brand-icon" :style="iconSrcStyle('git')"></span>
          </span>
          <span class="sidebar-nav-label">{{ $t('sidebar.git') }}</span>
        </button>

        <button
          v-if="browserAvailable"
          type="button"
          class="sidebar-nav-row"
          :class="{ active: browserOpen }"
          :aria-label="$t('browser.title')"
          :aria-pressed="browserOpen"
          :disabled="props.desktopBrowser?.enabled === false"
          @click="toggleBrowser"
        >
          <span class="sidebar-nav-icon" aria-hidden="true">
            <span class="icon" :style="iconSrcStyle('globe')"></span>
          </span>
          <span class="sidebar-nav-label">{{ $t('browser.title') }}</span>
        </button>

        <!-- 刷新页面：仅移动端 App 壳内由父级开启（浏览器有自带刷新，不显示） -->
        <button
          v-if="showRefreshButton"
          type="button"
          class="sidebar-nav-row sidebar-refresh-btn"
          :title="$t('appCore.refreshPage')"
          @click="$emit('refresh')"
        >
          <span class="sidebar-nav-icon" aria-hidden="true">
            <span class="icon" :style="iconSrcStyle('refreshCw')"></span>
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
        <button
          v-if="!isGroupByWorkspaceActive"
          type="button"
          class="conversation-import-button"
          :aria-label="$t('sidebar.importConversation')"
          @click="transferDialog?.pickImport()"
        >
          <span class="icon icon-sm" :style="iconStyle('file')" aria-hidden="true"></span>
        </button>
      </div>

      <div ref="conversationListEl" class="conversation-list">
        <div class="conversation-list-pane">
          <div
            v-if="runningTaskItems.length && !searchActive && !isGroupByWorkspaceActive"
            class="running-task-section"
          >
            <div class="running-task-section-title">{{ $t('sidebar.runningTasksTitle') }}</div>
            <button
              v-for="task in runningTaskItems"
              :key="task.task_id"
              type="button"
              class="running-task-item"
              :class="{ active: task.conversation_id === currentConversationId }"
              @click="$emit('select-running-task', task)"
            >
              <span class="running-task-workspace">{{
                task.workspace_label || task.workspace_id
              }}</span>
              <span class="running-task-main">
                <span class="running-task-title">{{
                  task.conversation_title || task.message || $t('sidebar.runningConversation')
                }}</span>
                <span
                  v-if="isTaskActive(task)"
                  class="conversation-running-loader running-task-state"
                  :aria-label="$t('common.running')"
                ></span>
                <span
                  v-else
                  class="conversation-complete-check running-task-state"
                  :aria-label="$t('sidebar.completed')"
                ></span>
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
                      :style="
                        iconStyle(
                          isWorkspaceExpanded(String(ws.workspace_id || ''))
                            ? 'folderOpen'
                            : 'folderClosed'
                        )
                      "
                      aria-hidden="true"
                    ></span>
                    <span class="workspace-group-label">{{ ws.label || ws.workspace_id }}</span>
                    <span
                      v-if="String(ws.workspace_id || '') === currentWorkspaceId"
                      class="workspace-current-dot"
                      :aria-label="
                        workspaceKind === 'project'
                          ? $t('sidebar.currentProject')
                          : $t('sidebar.currentWorkspace')
                      "
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
                    :class="{
                      visible:
                        hoverWorkspaceId === String(ws.workspace_id || '') ||
                        openWorkspaceMenuId === String(ws.workspace_id || '')
                    }"
                  >
                    <button
                      type="button"
                      class="workspace-new-btn"
                      :title="$t('sidebar.newConversation')"
                      :aria-label="$t('sidebar.newConversation')"
                      @click="handleCreateWorkspaceConversation(String(ws.workspace_id || ''))"
                    >
                      <span
                        class="icon icon-sm"
                        :style="iconStyle('pencil')"
                        aria-hidden="true"
                      ></span>
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
                      <button
                        type="button"
                        @click="handlePinWorkspace(String(ws.workspace_id || ''))"
                      >
                        <span>{{
                          pinnedWorkspaceIds.has(String(ws.workspace_id || ''))
                            ? $t('sidebar.unpinWorkspace')
                            : $t('sidebar.pinWorkspace')
                        }}</span>
                      </button>
                      <button
                        type="button"
                        @click="handleImportWorkspaceConversation(String(ws.workspace_id || ''))"
                      >
                        <span>{{ $t('sidebar.importConversation') }}</span>
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
                      <button type="button" @click="copyWorkspaceId(String(ws.workspace_id || ''))">
                        <span>{{ $t('sidebar.copyId') }}</span>
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
                      v-if="
                        !getWorkspaceGroup(String(ws.workspace_id || ''))?.conversations?.length
                      "
                      class="workspace-no-conversations"
                    >
                      {{ $t('sidebar.noConversations') }}
                    </div>
                    <template v-else>
                      <div
                        class="workspace-conversations-viewport"
                        :style="{
                          '--workspace-visible-count':
                            getWorkspaceGroup(String(ws.workspace_id || ''))?.visibleLimit || 5
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
                            v-for="conv in getWorkspaceVisibleAndBufferConversations(
                              String(ws.workspace_id || '')
                            )"
                            :key="conv.id"
                            class="conversation-item workspace-conversation-item"
                            :class="{
                              active: conv.id === currentConversationId,
                              running:
                                isConversationActive(conv.id) || isConversationCompleted(conv.id),
                              'insert-from-left':
                                conversationInsertAnimations[conv.id] === 'create',
                              'insert-from-under':
                                conversationInsertAnimations[conv.id] === 'duplicate',
                              'duplicate-source-mask':
                                conversationInsertAnimations[conv.id] === 'duplicateSource'
                            }"
                            @click="
                              handleWorkspaceConversationClick(
                                conv.id,
                                String(ws.workspace_id || '')
                              )
                            "
                          >
                            <div class="conversation-title">
                              {{ conv.title }}
                              <span v-if="conv.incompatible" class="conversation-migration-label">
                                {{ $t('appMessages.storageMigration.pendingLabel') }}
                              </span>
                            </div>
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
                                @click="toggleActionMenu(conv.id, $event)"
                              >
                                <span aria-hidden="true"></span>
                              </button>
                              <Teleport to="body">
                                <Transition name="conversation-actions" @enter="positionActionMenu">
                                  <div
                                    v-if="shouldShowActionMenu(conv.id)"
                                    class="conversation-actions-menu conversation-actions-menu--fixed"
                                  >
                                    <button
                                      type="button"
                                      @click="
                                        openActionMenuId = null;
                                        $emit('duplicate', conv.id, String(ws.workspace_id || ''));
                                      "
                                    >
                                      <span
                                        class="icon icon-sm"
                                        :style="iconStyle('copy')"
                                        aria-hidden="true"
                                      ></span>
                                      <span>{{ $t('common.copy') }}</span>
                                    </button>
                                    <button
                                      type="button"
                                      @click="
                                        openActionMenuId = null;
                                        copyConversationId(conv.id);
                                      "
                                    >
                                      <span
                                        class="icon icon-sm"
                                        :style="iconStyle('hash')"
                                        aria-hidden="true"
                                      ></span>
                                      <span>{{ $t('sidebar.copyId') }}</span>
                                    </button>
                                    <ConversationTransferMenu
                                      :chat-mode="chatMode"
                                      :project="workspaceKind === 'project'"
                                      :icon-style="iconStyle"
                                      @export="
                                        openActionMenuId = null;
                                        transferDialog?.startExport(
                                          conv.id,
                                          String(ws.workspace_id || '')
                                        );
                                      "
                                      @migrate="
                                        openActionMenuId = null;
                                        transferDialog?.openMigration(
                                          conv.id,
                                          String(ws.workspace_id || '')
                                        );
                                      "
                                    />
                                    <button
                                      type="button"
                                      class="danger"
                                      @click="
                                        openActionMenuId = null;
                                        $emit('delete', {
                                          id: conv.id,
                                          workspaceId: String(ws.workspace_id || '')
                                        });
                                      "
                                    >
                                      <span
                                        class="icon icon-sm"
                                        :style="iconStyle('trash')"
                                        aria-hidden="true"
                                      ></span>
                                      <span>{{ $t('common.delete') }}</span>
                                    </button>
                                  </div>
                                </Transition>
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
                          {{
                            getWorkspaceGroup(String(ws.workspace_id || ''))?.loadingMore
                              ? $t('sidebar.loadingMore')
                              : $t('sidebar.loadMore')
                          }}
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
                    <span class="workspace-group-label">{{
                      group.label || group.workspace_id
                    }}</span>
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
                        @click="
                          handleWorkspaceConversationClick(
                            conv.id,
                            String(group.workspace_id || '')
                          )
                        "
                      >
                        <div class="conversation-title">
                          {{ conv.title }}
                          <span v-if="conv.incompatible" class="conversation-migration-label">
                            {{ $t('appMessages.storageMigration.pendingLabel') }}
                          </span>
                        </div>
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
                            @click="toggleActionMenu(conv.id, $event)"
                          >
                            <span aria-hidden="true"></span>
                          </button>
                          <Teleport to="body">
                            <Transition name="conversation-actions" @enter="positionActionMenu">
                              <div
                                v-if="shouldShowActionMenu(conv.id)"
                                class="conversation-actions-menu conversation-actions-menu--fixed"
                              >
                                <button
                                  type="button"
                                  @click="
                                    openActionMenuId = null;
                                    $emit('duplicate', conv.id, String(group.workspace_id || ''));
                                  "
                                >
                                  <span
                                    class="icon icon-sm"
                                    :style="iconStyle('copy')"
                                    aria-hidden="true"
                                  ></span>
                                  <span>{{ $t('common.copy') }}</span>
                                </button>
                                <button
                                  type="button"
                                  @click="
                                    openActionMenuId = null;
                                    copyConversationId(conv.id);
                                  "
                                >
                                  <span
                                    class="icon icon-sm"
                                    :style="iconStyle('hash')"
                                    aria-hidden="true"
                                  ></span>
                                  <span>{{ $t('sidebar.copyId') }}</span>
                                </button>
                                <ConversationTransferMenu
                                  :chat-mode="chatMode"
                                  :project="workspaceKind === 'project'"
                                  :icon-style="iconStyle"
                                  @export="
                                    openActionMenuId = null;
                                    transferDialog?.startExport(
                                      conv.id,
                                      String(group.workspace_id || '')
                                    );
                                  "
                                  @migrate="
                                    openActionMenuId = null;
                                    transferDialog?.openMigration(
                                      conv.id,
                                      String(group.workspace_id || '')
                                    );
                                  "
                                />
                                <button
                                  type="button"
                                  class="danger"
                                  @click="
                                    openActionMenuId = null;
                                    $emit('delete', {
                                      id: conv.id,
                                      workspaceId: String(group.workspace_id || '')
                                    });
                                  "
                                >
                                  <span
                                    class="icon icon-sm"
                                    :style="iconStyle('trash')"
                                    aria-hidden="true"
                                  ></span>
                                  <span>{{ $t('common.delete') }}</span>
                                </button>
                              </div>
                            </Transition>
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
              v-if="
                loading && !displayConversations.length && !searchActive && !isDeletingConversation
              "
              class="loading-conversations"
            >
              {{ $t('sidebar.loading') }}
            </div>
            <div
              v-else-if="!displayConversations.length && !loading && !isDeletingConversation"
              class="no-conversations"
            >
              {{
                searchActive
                  ? $t('sidebar.noMatchingConversations')
                  : $t('sidebar.noConversationHistory')
              }}
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
                  'duplicate-source-mask':
                    conversationInsertAnimations[conv.id] === 'duplicateSource',
                  deleting: pendingDeletingConversationIds.includes(conv.id)
                }"
                @click="
                  openActionMenuId = null;
                  handleWorkspaceConversationClick(conv.id, String(currentWorkspaceId || ''));
                "
              >
                <div class="conversation-title">
                  {{ conv.title }}
                  <span v-if="conv.incompatible" class="conversation-migration-label">
                    {{ $t('appMessages.storageMigration.pendingLabel') }}
                  </span>
                </div>
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
                    @click="toggleActionMenu(conv.id, $event)"
                  >
                    <span aria-hidden="true"></span>
                  </button>
                  <Teleport to="body">
                    <Transition name="conversation-actions" @enter="positionActionMenu">
                      <div
                        v-if="shouldShowActionMenu(conv.id)"
                        class="conversation-actions-menu conversation-actions-menu--fixed"
                      >
                        <button
                          type="button"
                          @click="
                            openActionMenuId = null;
                            $emit('duplicate', conv.id);
                          "
                        >
                          <span
                            class="icon icon-sm"
                            :style="iconStyle('copy')"
                            aria-hidden="true"
                          ></span>
                          <span>{{ $t('common.copy') }}</span>
                        </button>
                        <button
                          type="button"
                          @click="
                            openActionMenuId = null;
                            copyConversationId(conv.id);
                          "
                        >
                          <span
                            class="icon icon-sm"
                            :style="iconStyle('hash')"
                            aria-hidden="true"
                          ></span>
                          <span>{{ $t('sidebar.copyId') }}</span>
                        </button>
                        <ConversationTransferMenu
                          :chat-mode="chatMode"
                          :project="workspaceKind === 'project'"
                          :icon-style="iconStyle"
                          @export="
                            openActionMenuId = null;
                            transferDialog?.startExport(conv.id, currentWorkspaceId);
                          "
                          @migrate="
                            openActionMenuId = null;
                            transferDialog?.openMigration(conv.id, currentWorkspaceId);
                          "
                        />
                        <button
                          type="button"
                          class="danger"
                          @click="
                            openActionMenuId = null;
                            $emit('delete', conv.id);
                          "
                        >
                          <span
                            class="icon icon-sm"
                            :style="iconStyle('trash')"
                            aria-hidden="true"
                          ></span>
                          <span>{{ $t('common.delete') }}</span>
                        </button>
                      </div>
                    </Transition>
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
                  <span
                    class="icon icon-sm search-spinner-icon"
                    :style="iconStyle('search')"
                  ></span>
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
            <div v-else-if="displayConversations.length" class="search-done">
              {{ $t('sidebar.searchExhausted') }}
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="renameWorkspaceId"
        class="workspace-rename-overlay"
        @click.self="cancelRenameWorkspace"
      >
        <div class="workspace-rename-card">
          <div class="workspace-rename-title">
            {{
              $t('sidebar.renameTitle', {
                kind: $t(workspaceKind === 'workspace' ? 'sidebar.workspace' : 'sidebar.project')
              })
            }}
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

      <SoftwareUpdateDialog v-if="desktopUpdateStore.isDesktop" />

      <div
        class="conversation-personal-entry"
        :class="{ active: personalPageVisible || personalMenuOpen }"
      >
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
            <!-- 新版本红点：入口藏在菜单里，不展开菜单也要能感知 -->
            <span v-if="desktopUpdateStore.updateAvailable" class="update-dot"></span>
          </span>
          <span class="sidebar-nav-label personal-label">{{
            sessionUsername || $t('sidebar.personalSpace')
          }}</span>
          <span class="personal-menu-caret" aria-hidden="true">
            <svg viewBox="0 0 16 16" fill="none" width="12" height="12">
              <path
                d="M4 10l4-4 4 4"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </span>
        </button>
        <!-- 二级菜单：个人空间 / 个性化 / 设置 / 帮助（置灰） / 退出登录。
             Teleport 到 body + fixed 定位：侧边栏 collapsed 时自身 overflow: hidden
             会把 absolute 菜单裁成窄条，与 conversation-actions-menu--fixed 同方案 -->
        <Teleport to="body">
          <!-- Transition 提供进入/离开动画（对齐 quickdock qd-menu 方案）：
               菜单锚定在按钮上方，动画始终以按钮为起点——向上弹出、向下收回。
               位置在 personalMenuPosition ref 中保留，离开动画期间不会跳变 -->
          <Transition name="personal-menu">
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
                  :style="iconSrcStyle('user')"
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
                  :style="iconSrcStyle('userPen')"
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
                  :style="iconSrcStyle('settings')"
                  aria-hidden="true"
                ></span>
                <span class="personal-menu-label">{{ $t('common.settings') }}</span>
              </button>
              <button
                type="button"
                class="personal-menu-item"
                role="menuitem"
                @click="openHelpDocs"
              >
                <span
                  class="icon personal-menu-icon"
                  :style="iconSrcStyle('messageQuestion')"
                  aria-hidden="true"
                ></span>
                <span class="personal-menu-label">{{ $t('sidebar.help') }}</span>
              </button>
              <!-- 软件更新：仅桌面壳渲染；菜单项红点与个人按钮红点同源 -->
              <button
                v-if="desktopUpdateStore.isDesktop"
                type="button"
                class="personal-menu-item"
                role="menuitem"
                @click="openSoftwareUpdate"
              >
                <span
                  class="icon personal-menu-icon"
                  :style="iconSrcStyle('refreshCw')"
                  aria-hidden="true"
                ></span>
                <span class="personal-menu-label">{{ $t('update.entryTitle') }}</span>
                <span
                  v-if="desktopUpdateStore.updateAvailable"
                  class="update-dot update-dot--inline"
                  :title="$t('update.newVersionBadge')"
                ></span>
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
                  :style="iconSrcStyle('logOut')"
                  aria-hidden="true"
                ></span>
                <span class="personal-menu-label">{{ $t('personalization.logoutTitle') }}</span>
              </button>
            </div>
          </Transition>
        </Teleport>
      </div>
    </div>
    <ConversationTransferDialog
      ref="transferDialog"
      :chat-mode="chatMode"
      :workspace-id="currentWorkspaceId"
      :workspace-kind="workspaceKind"
      @completed="$emit('select-workspace-conversation', $event)"
    />
  </aside>
</template>

<script setup lang="ts">
defineOptions({ name: 'ConversationSidebar' });

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { t } from '@/locales';
import { useUiStore } from '@/stores/ui';
import { useConversationStore } from '@/stores/conversation';
import { usePersonalizationStore } from '@/stores/personalization';
import { useDesktopUpdateStore } from '@/stores/desktopUpdate';
import { useConversationTabsStore } from '@/stores/conversationTabs';
// 导航与菜单图标直接用注册表（不依赖父级可选 prop iconStyle，任何宿主下都有图标）
import { iconSrcStyle } from '@/utils/icons';
import { openExternal } from '@/utils/openExternal';
import ModeSwitcher from '@/components/chat-mode/ModeSwitcher.vue';
import WorkspaceSwitcher from './WorkspaceSwitcher.vue';
import SoftwareUpdateDialog from './SoftwareUpdateDialog.vue';
import ConversationTransferDialog from './ConversationTransferDialog.vue';
import ConversationTransferMenu from './ConversationTransferMenu.vue';
import { useNativeBrowserStore, nativeBrowserAvailable } from '@/stores/nativeBrowser';
const nativeBrowser = useNativeBrowserStore();
const browserAvailable = computed(() => Boolean(props.desktopBrowser) || nativeBrowserAvailable());
const browserOpen = computed(() => props.desktopBrowser?.open ?? nativeBrowser.open);
function toggleBrowser() {
  if (props.desktopBrowser) emit('toggle-browser');
  else nativeBrowser.toggle();
}

const transferDialog = ref<InstanceType<typeof ConversationTransferDialog>>();
const props = withDefaults(
  defineProps<{
    chatMode?: boolean;
    desktopBrowser?: { enabled: boolean; open: boolean };
    /** 手机抽屉显式展开；桌面未传时沿用全局折叠状态。 */
    collapsed?: boolean;
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
    collapsed: undefined,
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
  (event: 'toggle-browser'): void;
  (event: 'create'): void;
  (event: 'refresh'): void;
  (event: 'search', value: string): void;
  (event: 'search-submit', value: string): void;
  (event: 'select-running-task', task: any): void;
  (event: 'load-more'): void;
  (event: 'search-more'): void;
  (event: 'personal', tab?: string): void;
  (event: 'delete', id: string | { id: string; workspaceId: string }): void;
  (event: 'duplicate', id: string, workspaceId?: string): void;
  (event: 'switch-workspace', workspaceId: string): void;
  (event: 'create-workspace', payload: { path: string; label: string }): void;
  (event: 'delete-workspace', item: any): void;
  (event: 'set-default-workspace', workspaceId: string): void;
  (
    event: 'select-workspace-conversation',
    payload: { conversationId: string; workspaceId: string }
  ): void;
  (event: 'create-workspace-conversation', workspaceId: string): void;
  (event: 'reveal-workspace', workspaceId: string): void;
  (event: 'rename-workspace', payload: { workspaceId: string; label: string }): void;
  (event: 'pin-workspace', workspaceId: string): void;
  (event: 'open-workflows'): void;
  (event: 'open-schedules'): void;
  (event: 'open-git'): void;
}>();

const uiStore = useUiStore();
const conversationStore = useConversationStore();
const personalizationStore = usePersonalizationStore();

/** 复制标识（对话 ID / 工作区 ID）到剪贴板；失败时必须给可见反馈，不静默失败。 */
const copyIdentifier = async (value: unknown) => {
  const text = String(value ?? '');
  if (!text) return;
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
    await navigator.clipboard.writeText(text);
    uiStore.pushToast({ message: t('sidebar.copyIdSucceeded'), type: 'success' });
  } catch (error) {
    console.warn('复制 ID 失败:', error);
    uiStore.pushToast({ message: t('sidebar.copyIdFailed'), type: 'error' });
  }
};

const copyConversationId = (conversationId: unknown) => void copyIdentifier(conversationId);
const copyWorkspaceId = (workspaceId: unknown) => void copyIdentifier(workspaceId);

/** 桌面端软件更新（非桌面壳环境 isDesktop=false，入口不渲染、检查不发起） */
const desktopUpdateStore = useDesktopUpdateStore();

// ── 个人入口：用户名按钮 + 二级菜单（个人空间/个性化/设置/帮助/退出登录） ──
const personalMenuOpen = ref(false);
const sessionUsername = ref('');
const personalEntryBtn = ref<HTMLElement | null>(null);
/* 菜单 Teleport 到 body 后的 fixed 定位（向上弹出）：bottom 锚定按钮顶缘 */
const personalMenuPosition = ref<{ bottom: number; left: number }>({ bottom: 0, left: 0 });

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
  const tabs = useConversationTabsStore();
  if (tabs.enabled) {
    void tabs.openPage(props.chatMode ? '/settings/chat' : '/settings').catch(() => {});
    return;
  }
  window.location.assign(props.chatMode ? '/settings/chat?from=chat' : '/settings');
}
function handleLogout() {
  closePersonalMenu();
  // 与 AccountTab 原退出按钮同一入口（store 内含 POST /logout + GET 兜底），不 await
  personalizationStore.logout();
}
/** 软件更新（个人菜单项，仅桌面壳渲染）：关菜单开弹窗，弹窗打开即强制回源检查 */
function openSoftwareUpdate() {
  closePersonalMenu();
  desktopUpdateStore.openDialog();
}
/** 帮助文档地址（官网文档页） */
const HELP_DOCS_URL = 'https://astrion.cyjai.com/docs.html';
async function openHelpDocs() {
  closePersonalMenu();
  // 桌面壳 window.open 不可用 / 网页与 Android 开新页，差异收敛在 openExternal 内
  await openExternal(HELP_DOCS_URL);
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

const conversationListEl = ref<HTMLElement | null>(null);

const collapsed = computed(() => props.collapsed ?? uiStore.sidebarCollapsed);
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
  // 与同名 prop 区分：此处为 store 中的当前对话 id，模板中裸用 currentConversationId 指 prop
  currentConversationId: storeCurrentConversationId
} = storeToRefs(conversationStore);
const { visible: personalPageVisible } = storeToRefs(personalizationStore);

const iconStyle = (key: string) => (props.iconStyle ? props.iconStyle(key) : {});

/* ---------- 工作区切换浮层 ---------- */
const workspaceEntryBtn = ref<HTMLElement | null>(null);
const workspaceSwitcherOpen = ref(false);
const workspaceAnchorRect = ref<{
  left: number;
  top: number;
  right: number;
  bottom: number;
} | null>(null);

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
let actionMenuTrigger: HTMLElement | null = null;
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

const toggleActionMenu = (conversationId: string, event: MouseEvent) => {
  actionMenuTrigger = event.currentTarget as HTMLElement;
  openActionMenuId.value = openActionMenuId.value === conversationId ? null : conversationId;
};

const positionActionMenu = (element: Element) => {
  if (!actionMenuTrigger) return;
  const menu = element as HTMLElement;
  const rect = actionMenuTrigger.getBoundingClientRect();
  const below = window.innerHeight - rect.bottom;
  const above = below < menu.offsetHeight + 6 && rect.top > below;
  menu.style.top = `${above ? Math.max(0, rect.top - menu.offsetHeight - 6) : rect.bottom + 6}px`;
  menu.style.right = `${Math.max(0, Math.min(window.innerWidth - menu.offsetWidth, window.innerWidth - rect.right))}px`;
  menu.style.transformOrigin = above ? 'bottom right' : 'top right';
  menu.style.setProperty('--conversation-menu-offset', above ? '4px' : '-4px');
};

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

const closeActionMenuOutside = (event: MouseEvent) => {
  if (!openActionMenuId.value) return;
  const target = event.target;
  if (target instanceof Node && actionMenuTrigger?.contains(target)) return;
  if (target instanceof Element && target.closest('.conversation-actions-menu--fixed')) return;
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
  const ids = workspaces.map((ws) => String(ws?.workspace_id || '')).filter(Boolean);
  const existing = new Set(workspaceOrder.value);
  const pinnedIds = new Set(ids.filter((id) => pinnedWorkspaceIds.value.has(id)));
  const newIds = ids.filter((id) => !existing.has(id) && !pinnedIds.has(id));
  const removedIds = new Set(workspaceOrder.value.filter((id) => !ids.includes(id)));
  if (newIds.length || removedIds.size) {
    workspaceOrder.value = workspaceOrder.value.filter((id) => !removedIds.has(id)).concat(newIds);
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
  () =>
    props.groupByWorkspace &&
    props.hostWorkspaceEnabled &&
    Array.isArray(props.workspaces) &&
    props.workspaces.length > 0
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
          task.conversation_id !== storeCurrentConversationId.value &&
          String(task.workspace_id || '') !== String(props.currentWorkspaceId || '')
      )
    : []
);
const activeStatuses = new Set(['pending', 'running', 'cancel_requested']);
const terminalStatuses = new Set(['succeeded', 'failed', 'canceled', 'stopped']);
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
  if (
    props.currentTaskInProgress &&
    String(conversationId || '') === String(props.currentConversationId || '')
  ) {
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
  openActionMenuId.value = null;
  emit('select-workspace-conversation', { conversationId, workspaceId });
};

const handleCreateWorkspaceConversation = (workspaceId: string) => {
  openWorkspaceMenuId.value = null;
  emit('create-workspace-conversation', workspaceId);
};

const handleImportWorkspaceConversation = (workspaceId: string) => {
  openWorkspaceMenuId.value = null;
  transferDialog.value?.pickImport(workspaceId);
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
  document.addEventListener('click', closeActionMenuOutside, true);
  document.addEventListener('click', closeWorkspaceMenu);
  document.addEventListener('click', closePersonalMenu);
  fetchSessionUsername();
  if (isGroupByWorkspaceActive.value) {
    sortedWorkspaces.value.forEach((ws: any) =>
      ensureWorkspaceGroup(String(ws?.workspace_id || ''))
    );
  }
  // 启动即校准侧边栏状态的 localStorage 同步值：ui store 每次启动都重置为
  // 收起态，而 localStorage 可能残留上一会话的展开态——不写初始值会让桌面壳
  // 标签条的首标签融合色停留在错误状态（直到用户首次切换侧边栏才自愈）
  uiStore.setSidebarCollapsed(uiStore.sidebarCollapsed);
  // 桌面壳的启动静默更新检查已移到应用入口 App.vue（覆盖任意路由进入的场景），
  // 此处不再触发以免重复请求；侧边栏只按 store 状态渲染红点。
});

onBeforeUnmount(() => {
  document.removeEventListener('click', closeActionMenuOutside, true);
  document.removeEventListener('click', closeWorkspaceMenu);
  document.removeEventListener('click', closePersonalMenu);
});
</script>
