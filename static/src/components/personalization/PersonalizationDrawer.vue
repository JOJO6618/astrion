<template>
  <transition name="personal-page-fade" appear>
    <div
      class="personal-page-overlay"
      v-if="visible"
      @click="closeDropdown"
      @mousedown="personalization.handleOverlayPressStart($event)"
      @mouseup="personalization.handleOverlayPressEnd($event)"
      @mouseleave.self="personalization.handleOverlayPressCancel"
      @touchstart.self.prevent="personalization.handleOverlayPressStart($event)"
      @touchend="personalization.handleOverlayPressEnd($event)"
      @touchcancel.self="personalization.handleOverlayPressCancel"
    >
      <div class="personal-page-card settings-redesign-card" data-tutorial="personal-card">
        <button
          type="button"
          class="settings-close-button"
          data-tutorial="personal-close"
          aria-label="关闭个人空间"
          @click="personalization.closeDrawer()"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>

        <div class="personalization-body settings-redesign-body" v-if="!loading">
          <form class="personal-form settings-redesign-form">
            <div class="settings-redesign-layout">
              <nav class="settings-redesign-tabs" aria-label="个人空间分组切换">
                <button
                  v-for="tab in personalTabs"
                  :key="tab.id"
                  type="button"
                  class="settings-redesign-tab"
                  :data-tutorial="`personal-tab-${tab.id}`"
                  :class="{ active: activeTab === tab.id }"
                  :aria-pressed="activeTab === tab.id"
                  @click.prevent="setActiveTab(tab.id)"
                >
                  <span
                    v-if="tab.id === 'context'"
                    class="settings-tab-icon settings-tab-icon--chat"
                    aria-hidden="true"
                  >
                    <svg viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
                      <path
                        d="M5 6.5c0-1.38 1.12-2.5 2.5-2.5h13c1.38 0 2.5 1.12 2.5 2.5v8.5c0 1.38-1.12 2.5-2.5 2.5h-5.6l-3.4 3.2.6-3.2H7.5c-1.38 0-2.5-1.12-2.5-2.5V6.5z"
                        stroke="currentColor"
                        stroke-width="1.7"
                        stroke-linejoin="round"
                      />
                      <path
                        d="M9 9.5h10"
                        stroke="currentColor"
                        stroke-width="1.7"
                        stroke-linecap="round"
                      />
                      <path
                        d="M9 13h6"
                        stroke="currentColor"
                        stroke-width="1.7"
                        stroke-linecap="round"
                      />
                    </svg>
                  </span>
                  <span
                    v-else
                    class="icon settings-tab-icon"
                    :style="settingsTabIconStyle(tab.icon)"
                    aria-hidden="true"
                  ></span>
                  <span>{{ tab.label }}</span>
                </button>
              </nav>

              <section class="settings-redesign-content" data-tutorial="personal-content-shell">
                <header class="settings-redesign-content-header">
                  <h2>{{ activeTabLabel }}</h2>
                </header>
                <div class="settings-redesign-title-line" aria-hidden="true"></div>

                <div class="settings-redesign-scroll">
                  <transition name="personal-page-vertical" mode="out-in">
                    <section v-if="activeTab === 'general'" key="general" class="settings-page">
                      <label class="settings-toggle-row">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">自动生成对话标题</span>
                          <span class="settings-row-desc">默认开启；关闭后标题将沿用首条消息</span>
                        </span>
                        <input
                          type="checkbox"
                          :checked="form.auto_generate_title"
                          @change="
                            personalization.updateField({
                              key: 'auto_generate_title',
                              value: $event.target.checked
                            })
                          "
                        />
                        <FancyCheck :checked="form.auto_generate_title" />
                      </label>
                      <div class="settings-action-row">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">新手教程</span>
                          <span class="settings-row-desc"
                            >通过高亮与说明窗了解主要功能入口和常用设置</span
                          >
                        </span>
                        <button
                          type="button"
                          class="settings-secondary-button"
                          @click="startTutorial"
                        >
                          开始教程
                        </button>
                      </div>
                      <div class="settings-action-row" v-if="isAppShell">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">软件更新</span>
                          <span class="settings-row-desc"
                            >当前版本：{{ appCurrentVersionText }} ·
                            {{ appUpdateCheckedText }}</span
                          >
                        </span>
                        <div class="settings-inline-actions">
                          <span class="settings-mini-status" :class="{ warning: appHasUpdate }">{{
                            appUpdateStateText
                          }}</span>
                          <button
                            type="button"
                            class="settings-secondary-button"
                            :disabled="appUpdateChecking"
                            @click="checkAppUpdate"
                          >
                            {{ appUpdateChecking ? '检查中...' : '检查更新' }}
                          </button>
                          <button
                            v-if="appHasUpdate"
                            type="button"
                            class="settings-primary-button"
                            @click="downloadLatestApp"
                          >
                            下载
                          </button>
                        </div>
                      </div>
                      <div class="settings-action-row danger-zone">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">退出登录</span>
                          <span class="settings-row-desc">结束当前账号会话</span>
                        </span>
                        <button
                          type="button"
                          class="settings-secondary-button danger"
                          @click="personalization.logout()"
                        >
                          退出登录
                        </button>
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'preferences'"
                      key="preferences"
                      class="settings-page"
                      data-tutorial="personal-page-preferences"
                    >
                      <label class="settings-toggle-row">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">启用个性化提示</span>
                          <span class="settings-row-desc">开启后才会注入您的偏好</span>
                        </span>
                        <input
                          type="checkbox"
                          :checked="form.enabled"
                          :disabled="toggleUpdating"
                          @change="personalization.toggleEnabled()"
                        />
                        <FancyCheck :checked="form.enabled" />
                      </label>
                      <label class="settings-input-row">
                        <span class="settings-row-title">AI 智能体自称</span>
                        <input
                          type="text"
                          :value="form.self_identify"
                          maxlength="20"
                          placeholder="如：小秘、助理小A"
                          @input="
                            personalization.updateField({
                              key: 'self_identify',
                              value: $event.target.value
                            })
                          "
                          @focus="personalization.clearFeedback()"
                        />
                      </label>
                      <label class="settings-input-row">
                        <span class="settings-row-title">用户称呼</span>
                        <input
                          type="text"
                          :value="form.user_name"
                          maxlength="20"
                          placeholder="如：Jojo、老师"
                          @input="
                            personalization.updateField({
                              key: 'user_name',
                              value: $event.target.value
                            })
                          "
                          @focus="personalization.clearFeedback()"
                        />
                      </label>
                      <label class="settings-input-row">
                        <span class="settings-row-title">职业</span>
                        <input
                          type="text"
                          :value="form.profession"
                          maxlength="20"
                          placeholder="如：产品经理、设计师"
                          @input="
                            personalization.updateField({
                              key: 'profession',
                              value: $event.target.value
                            })
                          "
                          @focus="personalization.clearFeedback()"
                        />
                      </label>
                      <div class="settings-input-row stackable">
                        <span class="settings-row-title">交流语气</span>
                        <div class="settings-input-stack">
                          <input
                            type="text"
                            :value="form.tone"
                            maxlength="20"
                            placeholder="请选择或输入语气"
                            @input="
                              personalization.updateField({
                                key: 'tone',
                                value: $event.target.value
                              })
                            "
                            @focus="personalization.clearFeedback()"
                          />
                          <div class="settings-chip-row">
                            <button
                              v-for="preset in tonePresets"
                              :key="preset"
                              type="button"
                              @click.prevent="personalization.applyTonePreset(preset)"
                            >
                              {{ preset }}
                            </button>
                          </div>
                        </div>
                      </div>
                      <div class="settings-textarea-row">
                        <div class="settings-row-copy">
                          <span class="settings-row-title">回答时必须考虑的信息</span>
                          <span class="settings-row-desc">每次回答前都会参考这些背景信息</span>
                        </div>
                        <textarea
                          :value="form.considerations"
                          rows="6"
                          maxlength="2000"
                          placeholder="例如：用户家有一只泰迪犬，棕色，公狗，2014年开始养的..."
                          @input="personalization.updateConsiderations($event.target.value)"
                          @focus="personalization.clearFeedback()"
                        ></textarea>
                      </div>
                      <label
                        v-if="currentBlockDisplayMode === 'stacked'"
                        class="settings-toggle-row"
                      >
                        <span class="settings-row-copy">
                          <span class="settings-row-title">隐藏块间边线</span>
                          <span class="settings-row-desc">去掉堆叠块之间的分割线，更简洁</span>
                        </span>
                        <input
                          type="checkbox"
                          :checked="stackedHideBorders"
                          @change="handleStackedHideBordersChange($event)"
                        />
                        <FancyCheck :checked="stackedHideBorders" />
                      </label>
                      <label
                        v-if="currentBlockDisplayMode === 'minimal'"
                        class="settings-toggle-row"
                      >
                        <span class="settings-row-copy">
                          <span class="settings-row-title">限制展开高度</span>
                          <span class="settings-row-desc">极简模式展开摘要行时限制最大高度，超出可在内部滚动</span>
                        </span>
                        <input
                          type="checkbox"
                          :checked="minimalExpandHeightLimited"
                          @change="handleMinimalExpandHeightLimitedChange($event)"
                        />
                        <FancyCheck :checked="minimalExpandHeightLimited" />
                      </label>
                      <div class="settings-select-row">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">智能体交流风格</span>
                          <span class="settings-row-desc">智能体和您交流时使用何种形式</span>
                        </span>
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'communication' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('communication')"
                          >
                            {{ communicationStyleLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.communication_style === 'default' }"
                              @click="selectCommunicationStyle('default')"
                            >
                              <strong>默认</strong><span>标准 AI 风格</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.communication_style === 'human_like' }"
                              @click="selectCommunicationStyle('human_like')"
                            >
                              <strong>拟人</strong><span>像聊天一样模仿人类语言风格</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.communication_style === 'auto' }"
                              @click="selectCommunicationStyle('auto')"
                            >
                              <strong>自动</strong><span>聊天时自然，复杂问题正常结构化回答</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                      <div class="settings-select-row">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">对话连续性</span>
                          <span class="settings-row-desc">控制智能体延续历史对话和记忆的程度</span>
                        </span>
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'conversation-continuity' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('conversation-continuity')"
                          >
                            {{ conversationContinuityLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.conversation_continuity === 'high' }"
                              @click="selectConversationContinuity('high')"
                            >
                              <strong>高</strong><span>更积极延续历史、记忆与项目背景</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.conversation_continuity === 'medium' }"
                              @click="selectConversationContinuity('medium')"
                            >
                              <strong>中</strong><span>当前对话优先，必要时参考历史</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.conversation_continuity === 'low' }"
                              @click="selectConversationContinuity('low')"
                            >
                              <strong>低</strong><span>当前对话尽量独立，少主动翻历史</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'model'"
                      key="model"
                      class="settings-page"
                      data-tutorial="personal-page-model"
                    >
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">默认模型</span
                          ><span class="settings-row-desc"
                            >为新对话/登录后首次请求选择首选模型</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'model' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('model')"
                          >
                            {{ defaultModelLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in filteredModelOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{
                                selected: form.default_model === option.value,
                                disabled: option.disabled
                              }"
                              :disabled="option.disabled"
                              @click="selectDefaultModel(option.value)"
                            >
                              <strong>{{ option.label }}</strong
                              ><span>{{ option.disabled ? '已被管理员禁用' : option.desc }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">默认运行模式</span
                          ><span class="settings-row-desc"
                            >登录或新建任务时的初始运行模式</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'run-mode' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('run-mode')"
                          >
                            {{ runModeLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in runModeOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: isRunModeActive(option.value) }"
                              @click="selectDefaultRunMode(option.value)"
                            >
                              <strong>{{ option.label }}</strong
                              ><span>{{ option.desc }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">默认推理强度</span
                          ><span class="settings-row-desc"
                            >思考模式下请求模型时附带的推理强度参数</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'reasoning-effort' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('reasoning-effort')"
                          >
                            {{ reasoningEffortLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in reasoningEffortOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: isEffortActive(option.value) }"
                              @click="selectDefaultReasoningEffort(option.value)"
                            >
                              <strong>{{ option.label }}</strong
                              ><span>{{ option.desc }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'appearance'"
                      key="appearance"
                      class="settings-page"
                    >
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">主题切换</span
                          ><span class="settings-row-desc">在经典、明亮和夜间之间切换</span></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'theme' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('theme')"
                          >
                            {{ themeLabel }} <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in themeOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: activeTheme === option.id }"
                              @click="selectThemeOption(option.id)"
                            >
                              <strong>{{ option.label }}</strong
                              ><span>{{ option.desc }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">按项目分组对话</span
                          ><span class="settings-row-desc"
                            >侧边栏对话记录按工作区/项目折叠展示</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.group_sidebar_by_workspace"
                          @change="
                            personalization.updateField({
                              key: 'group_sidebar_by_workspace',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.group_sidebar_by_workspace" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">新建对话跳转空白页</span
                          ><span class="settings-row-desc"
                            >开启后点击「新建对话」跳转新对话页，发送首条消息时才创建；关闭则立即创建空对话</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.new_chat_button_behavior === 'route'"
                          @change="
                            personalization.updateField({
                              key: 'new_chat_button_behavior',
                              value: $event.target.checked ? 'route' : 'blank'
                            })
                          " /><FancyCheck :checked="form.new_chat_button_behavior === 'route'" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">使用自定义称呼</span
                          ><span class="settings-row-desc"
                            >对话区域使用个性化设置中的自称和称呼</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.use_custom_names"
                          @change="
                            personalization.updateField({
                              key: 'use_custom_names',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.use_custom_names" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">增强工具显示</span
                          ><span class="settings-row-desc"
                            >工具块显示格式化内容，关闭则显示原始 JSON</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.enhanced_tool_display"
                          @change="
                            personalization.updateField({
                              key: 'enhanced_tool_display',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.enhanced_tool_display" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">显示助手状态形象</span
                          ><span class="settings-row-desc"
                            >在欢迎页和对话末尾显示空闲、思考、工具调用等状态动画</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.show_status_avatar"
                          @change="
                            personalization.updateField({
                              key: 'show_status_avatar',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.show_status_avatar" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">显示 Git 状态栏</span
                          ><span class="settings-row-desc"
                            >工作区存在 Git 仓库时，在输入栏上方显示分支和变更统计</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.show_git_status_bar"
                          @change="
                            personalization.updateField({
                              key: 'show_git_status_bar',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.show_git_status_bar" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">自动打开终端面板</span
                          ><span class="settings-row-desc"
                            >创建终端时自动打开侧边栏终端面板</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.auto_open_terminal_panel"
                          @change="
                            personalization.updateField({
                              key: 'auto_open_terminal_panel',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.auto_open_terminal_panel" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">快捷窗口自动展开</span
                          ><span class="settings-row-desc"
                            >有内容时自动展开快捷窗口；关闭后只能通过右侧按钮手动展开</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.quick_dock_auto_expand"
                          @change="
                            personalization.updateField({
                              key: 'quick_dock_auto_expand',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.quick_dock_auto_expand" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">编辑摘要实时显示</span
                          ><span class="settings-row-desc"
                            >工作运行期间实时显示本次编辑过的文件；关闭时仅在每次工作完成后显示</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.edit_summary_live_display"
                          @change="
                            personalization.updateField({
                              key: 'edit_summary_live_display',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.edit_summary_live_display" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">预览窗口自动换行显示</span
                          ><span class="settings-row-desc"
                            >文件预览按面板宽度自动换行；关闭时长行横向滚动</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.file_preview_auto_wrap"
                          @change="
                            personalization.updateField({
                              key: 'file_preview_auto_wrap',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.file_preview_auto_wrap" /></label>
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">堆叠块显示模式</span
                          ><span class="settings-row-desc">选择思考/工具块的显示方式</span></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'block-display' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('block-display')"
                          >
                            {{ blockDisplayLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in blockDisplayOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: currentBlockDisplayMode === option.value }"
                              @click="selectBlockDisplayMode(option.value)"
                            >
                              <strong>{{ option.label }}</strong
                              ><span>{{ option.desc }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                      <label
                        v-if="currentBlockDisplayMode === 'stacked'"
                        class="settings-toggle-row"
                      >
                        <span class="settings-row-copy">
                          <span class="settings-row-title">隐藏块间边线</span>
                          <span class="settings-row-desc">去掉堆叠块之间的分割线，更简洁</span>
                        </span>
                        <input
                          type="checkbox"
                          :checked="stackedHideBorders"
                          @change="handleStackedHideBordersChange($event)"
                        />
                        <FancyCheck :checked="stackedHideBorders" />
                      </label>
                      <label
                        v-if="currentBlockDisplayMode === 'minimal'"
                        class="settings-toggle-row"
                      >
                        <span class="settings-row-copy">
                          <span class="settings-row-title">限制展开高度</span>
                          <span class="settings-row-desc">极简模式展开摘要行时限制最大高度，超出可在内部滚动</span>
                        </span>
                        <input
                          type="checkbox"
                          :checked="minimalExpandHeightLimited"
                          @change="handleMinimalExpandHeightLimitedChange($event)"
                        />
                        <FancyCheck :checked="minimalExpandHeightLimited" />
                      </label>
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">简略消息显示</span
                          ><span class="settings-row-desc"
                            >审核、子智能体等紧凑系统消息的显示形式</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'compact-message' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('compact-message')"
                          >
                            {{ compactMessageDisplayLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in compactMessageDisplayOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: currentCompactMessageDisplay === option.value }"
                              @click="selectCompactMessageDisplay(option.value)"
                            >
                              <strong>{{ option.label }}</strong
                              ><span>{{ option.desc }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'workspace'"
                      key="workspace"
                      class="settings-page"
                      data-tutorial="personal-page-workspace"
                    >
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">默认权限</span
                          ><span class="settings-row-desc">新建对话时的默认权限模式</span></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'permission' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('permission')"
                          >
                            {{ permissionModeLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in permissionModeOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.default_permission_mode === option.id }"
                              @click="selectDefaultPermissionMode(option.id)"
                            >
                              <strong>{{ option.label }}</strong
                              ><span>{{ option.desc }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">默认运行模式</span
                          ><span class="settings-row-desc"
                            >新建对话时的默认运行模式；计划模式下权限锁定为只读</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'work-mode' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('work-mode')"
                          >
                            {{ workModeLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in workModeOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.default_work_mode === option.id }"
                              @click="selectDefaultWorkMode(option.id)"
                            >
                              <strong>{{ option.label }}</strong
                              ><span>{{ option.desc }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">AGENTS.md 自动注入</span
                          ><span class="settings-row-desc"
                            >工作区根目录存在 AGENTS.md 时自动注入系统提示词</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.agents_md_auto_inject"
                          @change="
                            personalization.updateField({
                              key: 'agents_md_auto_inject',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.agents_md_auto_inject" /></label>

                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">文件修改留痕</span
                          ><span class="settings-row-desc"
                            >每次任务完成后，把本轮 write/edit 的文件修改以 diff 形式保存到工作区
                            .astrion/modify_history/，误操作（如被 git checkout 覆盖）后可据此人工恢复</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.modify_history_enabled"
                          @change="
                            personalization.updateField({
                              key: 'modify_history_enabled',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.modify_history_enabled" /></label>

                      <div class="settings-section-divider">
                        <span class="settings-section-divider__label">版本控制</span>
                      </div>

                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">新对话默认开启版本控制</span
                          ><span class="settings-row-desc"
                            >创建新对话时自动启用，可在输入栏下方手动关闭</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.versioning_enabled_by_default"
                          @change="
                            personalization.updateField({
                              key: 'versioning_enabled_by_default',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.versioning_enabled_by_default" /></label>

                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">文件备份方式</span
                          ><span class="settings-row-desc"
                            >浅备份只记录 AI 编辑的文件，完全备份会保存整个工作区快照</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'versioning-backup-mode' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('versioning-backup-mode')"
                          >
                            {{ versioningBackupModeLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.versioning_backup_mode === 'shallow' }"
                              @click="selectVersioningBackupMode('shallow')"
                            >
                              <strong>浅备份</strong><span>速度快，只回溯被编辑过的文件</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.versioning_backup_mode === 'full' }"
                              @click="selectVersioningBackupMode('full')"
                            >
                              <strong>完全备份</strong><span>完整工作区快照，首次创建可能较慢</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>

                      <div class="settings-section-divider">
                        <span class="settings-section-divider__label">目标模式</span>
                      </div>

                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">审核智能体主动取证</span
                          ><span class="settings-row-desc"
                            >开启后可运行只读命令核实目标，关闭则仅依据对话判断</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.goal_review_mode === 'active'"
                          @change="
                            personalization.updateField({
                              key: 'goal_review_mode',
                              value: $event.target.checked ? 'active' : 'readonly'
                            })
                          " /><FancyCheck :checked="form.goal_review_mode === 'active'" /></label>

                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">最大自动续命轮数</span
                          ><span class="settings-row-desc"
                            >主模型停下后最多自动继续 1-100 轮</span
                          ></span
                        >
                        <input
                          type="number"
                          class="settings-number-input"
                          min="1"
                          max="100"
                          :value="form.goal_max_turns"
                          @change="
                            personalization.updateField({
                              key: 'goal_max_turns',
                              value: clampGoalMaxTurns($event.target.value)
                            })
                          "
                        />
                      </div>

                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">启用累计 token 上限</span
                          ><span class="settings-row-desc"
                            >累计输入输出超过上限时停止目标模式</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="goalTokenLimitEnabled"
                          @change="toggleGoalTokenLimit($event.target.checked)" /><FancyCheck :checked="goalTokenLimitEnabled" /></label>

                      <div v-if="goalTokenLimitEnabled" class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">累计 token 上限</span
                          ><span class="settings-row-desc">达到该累计消耗即停止目标模式</span></span
                        >
                        <input
                          type="number"
                          class="settings-number-input"
                          min="1000"
                          step="1000"
                          :value="form.goal_max_tokens || 100000"
                          @change="
                            personalization.updateField({
                              key: 'goal_max_tokens',
                              value: clampGoalMaxTokens($event.target.value)
                            })
                          "
                        />
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'context'"
                      key="context"
                      class="settings-page"
                    >
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">最近对话提示</span
                          ><span class="settings-row-desc"
                            >把当前工作区最近若干个非空对话注入系统提示词</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.recent_conversations_prompt_enabled"
                          @change="
                            personalization.updateField({
                              key: 'recent_conversations_prompt_enabled',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.recent_conversations_prompt_enabled" /></label>
                      <div
                        class="settings-input-row"
                        v-if="form.recent_conversations_prompt_enabled"
                      >
                        <span class="settings-row-title">最近对话注入数量</span>
                        <div class="settings-number-row">
                          <input
                            type="number"
                            :min="recentConversationsPromptLimitRange.min"
                            :max="recentConversationsPromptLimitRange.max"
                            :value="form.recent_conversations_prompt_limit"
                            @input="handleRecentConversationsPromptLimitInput"
                            @blur="commitRecentConversationsPromptLimitInput"
                          /><button type="button" @click="restoreRecentConversationsPromptLimit">
                            恢复默认
                          </button>
                        </div>
                      </div>
                      <div class="settings-input-row">
                        <span class="settings-row-title">最大记忆注入</span>
                        <div class="settings-number-row">
                          <input
                            type="number"
                            :min="projectMemoryInjectLimitMin"
                            :value="form.project_memory_inject_limit ?? ''"
                            placeholder="无上限"
                            @input="handleProjectMemoryInjectLimitInput"
                            @blur="commitProjectMemoryInjectLimitInput"
                          /><button type="button" @click="restoreProjectMemoryInjectLimit">
                            恢复默认
                          </button>
                        </div>
                      </div>
                      <div class="settings-group-block">
                        <div class="settings-group-title">
                          <span class="settings-row-title">上下文压缩策略</span
                          ><span class="settings-row-desc"
                            >可分别控制自动浅层压缩与自动深层压缩</span
                          >
                        </div>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-copy"
                            ><span class="settings-row-title">自动浅层压缩</span></span
                          ><input
                            type="checkbox"
                            :checked="form.auto_shallow_compress_enabled"
                            @change="
                              personalization.updateField({
                                key: 'auto_shallow_compress_enabled',
                                value: $event.target.checked
                              })
                            " /><FancyCheck :checked="form.auto_shallow_compress_enabled" /></label>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-copy"
                            ><span class="settings-row-title">自动深层压缩</span></span
                          ><input
                            type="checkbox"
                            :checked="form.auto_deep_compress_enabled"
                            @change="
                              personalization.updateField({
                                key: 'auto_deep_compress_enabled',
                                value: $event.target.checked
                              })
                            " /><FancyCheck :checked="form.auto_deep_compress_enabled" /></label>
                        <div class="settings-compression-grid">
                          <label
                            ><span>浅压缩触发上下文</span
                            ><input
                              type="number"
                              :value="form.shallow_compress_trigger_tokens ?? ''"
                              @input="
                                handleCompressionNumberInput(
                                  'shallow_compress_trigger_tokens',
                                  $event
                                )
                              "
                          /></label>
                          <label
                            ><span>浅压缩保留最近工具数</span
                            ><input
                              type="number"
                              :value="form.shallow_compress_keep_recent_tools ?? ''"
                              @input="
                                handleCompressionNumberInput(
                                  'shallow_compress_keep_recent_tools',
                                  $event
                                )
                              "
                          /></label>
                          <label
                            ><span>浅压缩保留当前轮工具数</span
                            ><input
                              type="number"
                              :value="form.shallow_compress_keep_user_turn_tools ?? ''"
                              @input="
                                handleCompressionNumberInput(
                                  'shallow_compress_keep_user_turn_tools',
                                  $event
                                )
                              "
                          /></label>
                          <label
                            ><span>每轮最大替换数量</span
                            ><input
                              type="number"
                              :value="form.shallow_compress_max_replace_per_round ?? ''"
                              @input="
                                handleCompressionNumberInput(
                                  'shallow_compress_max_replace_per_round',
                                  $event
                                )
                              "
                          /></label>
                          <label
                            ><span>工具调用间隔触发</span
                            ><input
                              type="number"
                              :value="form.shallow_compress_trigger_tool_calls_interval ?? ''"
                              @input="
                                handleCompressionNumberInput(
                                  'shallow_compress_trigger_tool_calls_interval',
                                  $event
                                )
                              "
                          /></label>
                          <label
                            ><span>深压缩触发上下文</span
                            ><input
                              type="number"
                              :value="form.deep_compress_trigger_tokens ?? ''"
                              @input="
                                handleCompressionNumberInput('deep_compress_trigger_tokens', $event)
                              "
                          /></label>
                        </div>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-copy"
                            ><span class="settings-row-title">深压缩直接注入</span
                            ><span class="settings-row-desc"
                              >开启后直接注入历次压缩全文，关闭则只提示文件位置</span
                            ></span
                          ><input
                            type="checkbox"
                            :checked="form.deep_compress_form === 'inject'"
                            @change="
                              personalization.updateField({
                                key: 'deep_compress_form',
                                value: $event.target.checked ? 'inject' : 'file'
                              })
                            " /><FancyCheck :checked="form.deep_compress_form === 'inject'" /></label>
                        <div class="settings-inline-actions right">
                          <button
                            type="button"
                            class="settings-secondary-button"
                            @click="restoreCompressionDefaults"
                          >
                            恢复压缩默认值
                          </button>
                        </div>
                      </div>
                    </section>

                    <section v-else-if="activeTab === 'tools'" key="tools" class="settings-page">
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">静默禁用工具</span
                          ><span class="settings-row-desc"
                            >禁用工具时不再注入提示消息，模型不会感知被禁用项</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.silent_tool_disable"
                          @change="
                            personalization.updateField({
                              key: 'silent_tool_disable',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.silent_tool_disable" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">隐藏工具审核面板</span
                          ><span class="settings-row-desc"
                            >自动审核模式下，后台审核工具时不再自动展开审核面板</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.hide_tool_approval_panel"
                          @change="
                            personalization.updateField({
                              key: 'hide_tool_approval_panel',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.hide_tool_approval_panel" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">工具意图提示</span
                          ><span class="settings-row-desc"
                            >调用工具时先用简短文字告诉你要做什么</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.tool_intent_enabled"
                          @change="
                            personalization.updateField({
                              key: 'tool_intent_enabled',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.tool_intent_enabled" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">Skill 提示系统</span
                          ><span class="settings-row-desc"
                            >检测到特定关键词时自动提示模型阅读相关 skill</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.skill_hints_enabled"
                          @change="
                            personalization.updateField({
                              key: 'skill_hints_enabled',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.skill_hints_enabled" /></label>
                      <div class="settings-group-block">
                        <div class="settings-group-title">
                          <span class="settings-row-title">强约束系统</span
                          ><span class="settings-row-desc">仅对个人空间中已启用的 skill 生效</span>
                        </div>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-title">Terminal 系列工具</span
                          ><input
                            type="checkbox"
                            :checked="form.skill_strict_terminal_enabled"
                            @change="
                              personalization.updateField({
                                key: 'skill_strict_terminal_enabled',
                                value: $event.target.checked
                              })
                            " /><FancyCheck :checked="form.skill_strict_terminal_enabled" /></label>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-title">子智能体系列工具</span
                          ><input
                            type="checkbox"
                            :checked="form.skill_strict_sub_agent_enabled"
                            @change="
                              personalization.updateField({
                                key: 'skill_strict_sub_agent_enabled',
                                value: $event.target.checked
                              })
                            " /><FancyCheck :checked="form.skill_strict_sub_agent_enabled" /></label>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-title">run_command 前台模式</span
                          ><input
                            type="checkbox"
                            :checked="form.skill_strict_run_command_foreground_enabled"
                            @change="
                              personalization.updateField({
                                key: 'skill_strict_run_command_foreground_enabled',
                                value: $event.target.checked
                              })
                            " /><FancyCheck :checked="form.skill_strict_run_command_foreground_enabled" /></label>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-title">run_command 后台模式</span
                          ><input
                            type="checkbox"
                            :checked="form.skill_strict_run_command_background_enabled"
                            @change="
                              personalization.updateField({
                                key: 'skill_strict_run_command_background_enabled',
                                value: $event.target.checked
                              })
                            " /><FancyCheck :checked="form.skill_strict_run_command_background_enabled" /></label>
                      </div>
                      <div class="settings-group-block" v-if="skillsCatalog.length">
                        <div class="settings-group-title">
                          <span class="settings-row-title">可用 Skills</span
                          ><span class="settings-row-desc"
                            >勾选后会注入 system prompt，并同步到工作区的 .astrion/skills/
                            目录</span
                          >
                        </div>
                        <div class="settings-check-grid">
                          <label
                            v-for="skill in skillsCatalog"
                            :key="skill.id"
                            class="settings-toggle-row inner"
                            ><span class="settings-row-title">{{ skill.label }}</span
                            ><input
                              type="checkbox"
                              :checked="form.enabled_skills.includes(skill.id)"
                              @change="personalization.toggleSkill(skill.id)" /><FancyCheck :checked="form.enabled_skills.includes(skill.id)" /></label>
                        </div>
                      </div>
                      <div class="settings-group-block" v-if="toolCategories.length">
                        <div class="settings-group-title">
                          <span class="settings-row-title">默认禁用工具类别</span
                          ><span class="settings-row-desc">选择后，这些类别在新任务中保持关闭</span>
                        </div>
                        <div class="settings-check-grid">
                          <label
                            v-for="category in toolCategories"
                            :key="category.id"
                            class="settings-toggle-row inner"
                            ><span class="settings-row-title">{{ category.label }}</span
                            ><input
                              type="checkbox"
                              :checked="form.disabled_tool_categories.includes(category.id)"
                              @change="toggleCategory(category.id)" /><FancyCheck :checked="form.disabled_tool_categories.includes(category.id)" /></label>
                        </div>
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'files'"
                      key="files"
                      class="settings-page"
                      data-tutorial="personal-page-image"
                    >
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">图片压缩</span
                          ><span class="settings-row-desc"
                            >发送图片或调用 view_image 时自动等比缩放</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'image' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('image')"
                          >
                            {{ imageCompressionLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in imageCompressionOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.image_compression === option.id }"
                              @click="selectImageCompression(option.id)"
                            >
                              <strong>{{ option.label }}</strong
                              ><span>{{ option.desc }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'data'"
                      key="data"
                      class="settings-page usage-summary-page"
                      data-tutorial="personal-page-usage"
                    >
                      <div class="usage-summary-card settings-stats-card">
                        <div class="usage-summary-header">
                          <div>
                            <p class="usage-summary-eyebrow">对话用量</p>
                            <h3>用量统计</h3>
                            <p class="usage-summary-desc">
                              累计统计所有对话的输入/输出 Token、对话数量、用户消息与工具调用
                            </p>
                          </div>
                        </div>
                        <div class="usage-summary-grid usage-summary-grid--tokens">
                          <div class="usage-summary-item">
                            <div class="label">累计输入</div>
                            <div class="value value--success">
                              {{ formatTokenCount(usageSummary.total_input_tokens) }}
                            </div>
                          </div>
                          <div class="usage-summary-item">
                            <div class="label">累计输出</div>
                            <div class="value value--warning">
                              {{ formatTokenCount(usageSummary.total_output_tokens) }}
                            </div>
                          </div>
                        </div>
                        <div class="usage-summary-grid usage-summary-grid--counts">
                          <div class="usage-summary-item">
                            <div class="label">总对话数</div>
                            <div class="value">
                              {{ formatTokenCount(usageSummary.total_conversations) }}
                            </div>
                          </div>
                          <div class="usage-summary-item">
                            <div class="label">用户消息数</div>
                            <div class="value">
                              {{ formatTokenCount(usageSummary.total_user_messages) }}
                            </div>
                          </div>
                          <div class="usage-summary-item">
                            <div class="label">工具调用次数</div>
                            <div class="value">
                              {{ formatTokenCount(usageSummary.total_tools) }}
                            </div>
                          </div>
                        </div>
                        <div class="usage-summary-meta">
                          <span v-if="usageError" class="usage-summary-error">{{ usageError }}</span
                          ><span v-else-if="usageLoading">正在同步最新统计...</span
                          ><span v-else>最近更新：{{ usageUpdatedText }}</span
                          ><button
                            type="button"
                            class="usage-summary-refresh"
                            @click="fetchUsageSummary"
                            :disabled="usageLoading"
                          >
                            {{ usageLoading ? '刷新中...' : '刷新数据' }}
                          </button>
                        </div>
                      </div>
                    </section>

                    <section v-else-if="activeTab === 'voice'" key="voice" class="settings-page">
                      <div class="settings-section" style="margin-bottom: 16px">
                        <p
                          class="settings-section-desc"
                          style="
                            margin: 0;
                            color: var(--text-secondary);
                            font-size: 13px;
                            line-height: 1.6;
                          "
                        >
                          端侧语音识别模型（SenseVoice int8），支持中英混说 + 自动标点。 模型约
                          228MB，仅在手机本地运行，无需网络。
                        </p>
                      </div>
                      <div class="settings-action-row">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">语音识别模型</span>
                          <span class="settings-row-desc">
                            <template v-if="voiceModelReady">已下载 (228MB)</template>
                            <template v-else-if="voiceDownloading"
                              >下载中 {{ voiceDownloadPercent }}% — {{ voiceDownloadMsg }}</template
                            >
                            <template v-else-if="voiceModelPartial"
                              >下载不完整，请重新下载</template
                            >
                            <template v-else>未下载 — 点击下载</template>
                          </span>
                        </span>
                        <div style="display: flex; gap: 6px">
                          <button
                            type="button"
                            class="settings-secondary-button"
                            :disabled="voiceDownloading"
                            @click="downloadVoiceModel"
                          >
                            {{
                              voiceDownloading
                                ? '下载中...'
                                : voiceModelReady
                                  ? '重新下载'
                                  : '下载模型'
                            }}
                          </button>
                          <button
                            v-if="voiceModelReady || voiceModelPartial"
                            type="button"
                            class="settings-secondary-button"
                            style="color: var(--state-error)"
                            :disabled="voiceDownloading"
                            @click="deleteVoiceModel"
                          >
                            删除
                          </button>
                        </div>
                      </div>
                      <div
                        v-if="voiceDownloading"
                        class="voice-download-bar"
                        style="margin: 8px 0 0"
                      >
                        <div class="voice-download-track">
                          <div
                            class="voice-download-fill"
                            :style="{ width: voiceDownloadPercent + '%' }"
                          ></div>
                        </div>
                        <span style="font-size: 11px; color: var(--text-secondary); margin-top: 4px"
                          >{{ voiceDownloadPercent }}%</span
                        >
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'sub-agents'"
                      key="sub-agents"
                      class="settings-page"
                    >
                      <div class="settings-section-desc" style="margin: 0 0 16px; color: var(--text-secondary); font-size: 13px; line-height: 1.6">
                        管理多智能体模式下的子智能体角色。预设角色可编辑（创建自定义覆盖），自定义角色可创建/编辑/删除。
                      </div>

                      <!-- 压缩阈值配置 -->
                      <div class="settings-action-row" style="margin-bottom: 16px">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">上下文压缩阈值</span>
                          <span class="settings-row-desc">
                            子智能体上下文 tokens 超过此值时触发深度压缩（默认 150000）
                          </span>
                        </span>
                        <div style="display: flex; gap: 6px; align-items: center">
                          <input
                            type="number"
                            class="settings-number-input"
                            v-model.number="subAgentCompressThreshold"
                            :min="10000"
                            :step="10000"
                            style="width: 120px"
                          />
                          <button
                            type="button"
                            class="settings-secondary-button"
                            :disabled="subAgentSettingsSaving"
                            @click="saveSubAgentSettings"
                          >
                            {{ subAgentSettingsSaving ? '保存中...' : '保存' }}
                          </button>
                        </div>
                      </div>

                      <!-- 最大执行轮次配置 -->
                      <div class="settings-action-row" style="margin-bottom: 16px">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">最大执行轮次</span>
                          <span class="settings-row-desc">
                            传统后台子智能体单次任务的最大执行轮次（一轮 = 一次模型调用）。留空默认 50 轮；填 0 表示无上限（慎用，失控任务会持续消耗 API 额度）。多智能体模式的团队成员是长期协作角色，不受此限制
                          </span>
                        </span>
                        <div style="display: flex; gap: 6px; align-items: center">
                          <input
                            type="number"
                            class="settings-number-input"
                            v-model.number="subAgentMaxTurns"
                            :min="0"
                            :step="10"
                            placeholder="50"
                            style="width: 120px"
                          />
                          <button
                            type="button"
                            class="settings-secondary-button"
                            :disabled="subAgentSettingsSaving"
                            @click="saveSubAgentSettings"
                          >
                            {{ subAgentSettingsSaving ? '保存中...' : '保存' }}
                          </button>
                        </div>
                      </div>

                      <!-- 角色列表 -->
                      <div class="settings-section-header" style="margin-bottom: 8px">
                        <span class="settings-section-title">角色列表</span>
                        <button
                          type="button"
                          class="settings-secondary-button"
                          @click="openRoleEditor()"
                        >
                          + 新建角色
                        </button>
                      </div>

                      <div v-if="subAgentRolesLoading" class="settings-empty-hint">加载中...</div>
                      <div v-else-if="subAgentRoles.length === 0" class="settings-empty-hint">
                        暂无角色
                      </div>

                      <div v-else class="sub-agent-role-list">
                        <div
                          v-for="role in subAgentRoles"
                          :key="role.role_id"
                          class="sub-agent-role-card"
                        >
                          <div class="sub-agent-role-info">
                            <div class="sub-agent-role-name">
                              {{ role.name }}
                              <span
                                class="sub-agent-role-tag"
                                :class="role.is_custom ? 'tag-custom' : 'tag-preset'"
                              >
                                {{ role.is_custom ? '自定义' : '预设' }}
                              </span>
                            </div>
                            <div class="sub-agent-role-desc">{{ role.description || '无描述' }}</div>
                            <div class="sub-agent-role-meta">
                              ID: {{ role.role_id }} · 思考模式: {{ role.thinking_mode }}<template v-if="role.model_key"> · 模型: {{ role.model_key }}</template>
                            </div>
                          </div>
                          <div class="sub-agent-role-actions">
                            <button
                              type="button"
                              class="settings-secondary-button"
                              @click="openRoleEditor(role)"
                            >编辑</button>
                            <button
                              v-if="role.is_custom"
                              type="button"
                              class="settings-secondary-button danger"
                              @click="deleteRole(role)"
                            >删除</button>
                          </div>
                        </div>
                      </div>

                      <!-- 角色编辑弹窗（与个人空间同尺寸） -->
                      <transition name="personal-page-fade" appear>
                        <div
                          v-if="roleEditorOpen"
                          class="role-editor-drawer-overlay"
                          @click="closeDropdown"
                          @mousedown="handleRoleEditorOverlayPressStart"
                          @mouseup="handleRoleEditorOverlayPressEnd"
                        >
                          <div class="role-editor-drawer-card settings-redesign-card" style="padding: 30px 42px 24px; border-radius: 32px;" @click.stop>
                            <div class="role-editor-header">
                              <h2>{{ editingRole ? '编辑角色' : '新建角色' }}</h2>
                              <button type="button" class="settings-secondary-button" @click="closeRoleEditor">关闭</button>
                            </div>
                            <div class="role-editor-body" @click="closeDropdown">
                              <label class="settings-input-row">
                                <span class="settings-row-title">角色 ID</span>
                                <input
                                  v-if="!editingRole"
                                  type="text"
                                  :value="roleForm.role_id"
                                  maxlength="40"
                                  placeholder="英文小写下划线，如 api-designer"
                                  @input="roleForm.role_id = ($event.target as HTMLInputElement).value"
                                />
                                <span v-else class="settings-row-desc" style="text-align: right; font-size: 14px;">{{ roleForm.role_id }}</span>
                              </label>
                              <label class="settings-input-row">
                                <span class="settings-row-title">显示名</span>
                                <input
                                  type="text"
                                  :value="roleForm.name"
                                  maxlength="40"
                                  placeholder="如 API Designer"
                                  @input="roleForm.name = ($event.target as HTMLInputElement).value"
                                />
                              </label>
                              <label class="settings-input-row">
                                <span class="settings-row-title">描述</span>
                                <input
                                  type="text"
                                  :value="roleForm.description"
                                  maxlength="100"
                                  placeholder="一句话描述职责"
                                  @input="roleForm.description = ($event.target as HTMLInputElement).value"
                                />
                              </label>
                              <div class="settings-select-row">
                                <span class="settings-row-copy">
                                  <span class="settings-row-title">思考模式</span>
                                  <span class="settings-row-desc">fast = 快速响应，thinking = 思考推理</span>
                                </span>
                                <div
                                  class="settings-select-wrap"
                                  :class="{ open: activeDropdown === 'role-thinking' }"
                                  @click.stop
                                >
                                  <button
                                    type="button"
                                    class="settings-select-button"
                                    @click="toggleDropdown('role-thinking')"
                                  >
                                    {{ roleForm.thinking_mode === 'thinking' ? 'thinking' : 'fast' }}
                                    <span class="select-chevron" aria-hidden="true"></span>
                                  </button>
                                  <div
                                    :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                                    :style="activeDropdown === 'role-thinking' ? floatingMenuStyle : undefined"
                                  >
                                    <button
                                      type="button"
                                      class="settings-menu-option"
                                      :class="{ selected: roleForm.thinking_mode === 'fast' }"
                                      @click="roleForm.thinking_mode = 'fast'; closeDropdown()"
                                    >
                                      <strong>fast</strong><span>快速响应模式</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                                    </button>
                                    <button
                                      type="button"
                                      class="settings-menu-option"
                                      :class="{ selected: roleForm.thinking_mode === 'thinking' }"
                                      @click="roleForm.thinking_mode = 'thinking'; closeDropdown()"
                                    >
                                      <strong>thinking</strong><span>思考推理模式</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                                    </button>
                                  </div>
                                </div>
                              </div>
                              <div class="settings-select-row">
                                <span class="settings-row-copy">
                                  <span class="settings-row-title">模型</span>
                                  <span class="settings-row-desc">留空则使用默认模型</span>
                                </span>
                                <div
                                  class="settings-select-wrap"
                                  :class="{ open: activeDropdown === 'role-model' }"
                                  @click.stop
                                >
                                  <button
                                    type="button"
                                    class="settings-select-button"
                                    @click="toggleDropdown('role-model')"
                                  >
                                    {{ roleForm.model_key || '默认模型' }}
                                    <span class="select-chevron" aria-hidden="true"></span>
                                  </button>
                                  <div
                                    :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                                    :style="activeDropdown === 'role-model' ? floatingMenuStyle : undefined"
                                  >
                                    <button
                                      type="button"
                                      class="settings-menu-option"
                                      :class="{ selected: !roleForm.model_key }"
                                      @click="roleForm.model_key = ''; closeDropdown()"
                                    >
                                      <strong>默认模型</strong><span>使用配置文件中的 default_model</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                                    </button>
                                    <button
                                      v-for="m in subAgentModels"
                                      :key="m.name"
                                      type="button"
                                      class="settings-menu-option"
                                      :class="{ selected: roleForm.model_key === m.name }"
                                      @click="roleForm.model_key = m.name; closeDropdown()"
                                    >
                                      <strong>{{ m.name }}</strong><span>{{ m.modes }} · {{ m.multimodal || '纯文本' }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                                    </button>
                                  </div>
                                </div>
                              </div>
                              <div class="settings-textarea-row">
                                <span class="settings-row-title">Prompt Body（Markdown）</span>
                                <textarea
                                  :value="roleForm.body_prompt"
                                  placeholder="角色的自定义 prompt body..."
                                  @input="roleForm.body_prompt = ($event.target as HTMLTextAreaElement).value"
                                ></textarea>
                              </div>
                            </div>
                            <div class="role-editor-footer">
                              <button type="button" class="settings-secondary-button" @click="closeRoleEditor">取消</button>
                              <button
                                type="button"
                                class="settings-primary-button"
                                :disabled="roleSaving || !roleForm.role_id || !roleForm.name || !roleForm.body_prompt"
                                @click="saveRole"
                              >
                                {{ roleSaving ? '保存中...' : '保存' }}
                              </button>
                            </div>
                          </div>
                        </div>
                      </transition>
                    </section>

                    <section
                      v-else-if="activeTab === 'review-agents'"
                      key="review-agents"
                      class="settings-page"
                    >
                      <div class="settings-section-desc" style="margin: 0 0 16px; color: var(--text-secondary); font-size: 13px; line-height: 1.6">
                        统一配置三个审核智能体的模型与运行参数。模型与子智能体共用模型库，留空则使用模型库默认模型。
                      </div>

                      <template v-for="agent in reviewAgentDefs" :key="agent.key">
                        <div class="settings-section-divider">
                          <span class="settings-section-divider__label">{{ agent.name }}</span>
                        </div>
                        <div style="margin: -4px 0 12px; color: var(--text-secondary); font-size: 12px; line-height: 1.5">
                          {{ agent.desc }}
                        </div>

                        <div class="settings-select-row">
                          <span class="settings-row-copy">
                            <span class="settings-row-title">模型</span>
                            <span class="settings-row-desc">留空则使用模型库默认模型</span>
                          </span>
                          <div
                            class="settings-select-wrap"
                            :class="{ open: activeDropdown === `review-model-${agent.key}` }"
                            @click.stop
                          >
                            <button
                              type="button"
                              class="settings-select-button"
                              @click="toggleDropdown(`review-model-${agent.key}`)"
                            >
                              {{ reviewAgentOf(agent.key).model || '默认模型' }}
                              <span class="select-chevron" aria-hidden="true"></span>
                            </button>
                            <div
                              :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                              :style="activeDropdown === `review-model-${agent.key}` ? floatingMenuStyle : undefined"
                            >
                              <button
                                type="button"
                                class="settings-menu-option"
                                :class="{ selected: !reviewAgentOf(agent.key).model }"
                                @click="updateReviewAgent(agent.key, { model: '' }); closeDropdown()"
                              >
                                <strong>默认模型</strong><span>使用模型库的 default_model</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                              </button>
                              <button
                                v-for="m in subAgentModels"
                                :key="m.name"
                                type="button"
                                class="settings-menu-option"
                                :class="{ selected: reviewAgentOf(agent.key).model === m.name }"
                                @click="updateReviewAgent(agent.key, { model: m.name }); closeDropdown()"
                              >
                                <strong>{{ m.name }}</strong><span>{{ m.modes }} · {{ m.multimodal || '纯文本' }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                              </button>
                            </div>
                          </div>
                        </div>

                        <div class="settings-select-row">
                          <span class="settings-row-copy">
                            <span class="settings-row-title">思考模式</span>
                            <span class="settings-row-desc">模型不支持思考时自动回落快速模式</span>
                          </span>
                          <div
                            class="settings-select-wrap"
                            :class="{ open: activeDropdown === `review-thinking-${agent.key}` }"
                            @click.stop
                          >
                            <button
                              type="button"
                              class="settings-select-button"
                              @click="toggleDropdown(`review-thinking-${agent.key}`)"
                            >
                              {{ reviewAgentOf(agent.key).thinking ? 'thinking' : 'fast' }}
                              <span class="select-chevron" aria-hidden="true"></span>
                            </button>
                            <div
                              :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                              :style="activeDropdown === `review-thinking-${agent.key}` ? floatingMenuStyle : undefined"
                            >
                              <button
                                type="button"
                                class="settings-menu-option"
                                :class="{ selected: !reviewAgentOf(agent.key).thinking }"
                                @click="updateReviewAgent(agent.key, { thinking: false }); closeDropdown()"
                              >
                                <strong>fast</strong><span>快速响应模式</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                              </button>
                              <button
                                type="button"
                                class="settings-menu-option"
                                :class="{ selected: reviewAgentOf(agent.key).thinking }"
                                @click="updateReviewAgent(agent.key, { thinking: true }); closeDropdown()"
                              >
                                <strong>thinking</strong><span>思考推理模式</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                              </button>
                            </div>
                          </div>
                        </div>

                        <div class="settings-select-row">
                          <span class="settings-row-copy">
                            <span class="settings-row-title">审核请求超时</span>
                            <span class="settings-row-desc">单次模型请求超时（5-3600 秒）</span>
                          </span>
                          <input
                            type="number"
                            class="settings-number-input"
                            min="5"
                            max="3600"
                            :value="reviewAgentOf(agent.key).timeout_seconds"
                            @change="updateReviewAgentInt(agent.key, 'timeout_seconds', ($event.target as HTMLInputElement).value, 5, 3600)"
                          />
                        </div>

                        <div class="settings-select-row">
                          <span class="settings-row-copy">
                            <span class="settings-row-title">最大审核轮次</span>
                            <span class="settings-row-desc">超过该轮次未产出结论时按兜底处理（1-50 轮）</span>
                          </span>
                          <input
                            type="number"
                            class="settings-number-input"
                            min="1"
                            max="50"
                            :value="reviewAgentOf(agent.key).max_rounds"
                            @change="updateReviewAgentInt(agent.key, 'max_rounds', ($event.target as HTMLInputElement).value, 1, 50)"
                          />
                        </div>

                        <div class="settings-select-row" style="margin-bottom: 8px">
                          <span class="settings-row-copy">
                            <span class="settings-row-title">取证命令超时</span>
                            <span class="settings-row-desc">只读取证命令的单次超时（1-600 秒）</span>
                          </span>
                          <input
                            type="number"
                            class="settings-number-input"
                            min="1"
                            max="600"
                            :value="reviewAgentOf(agent.key).max_command_timeout"
                            @change="updateReviewAgentInt(agent.key, 'max_command_timeout', ($event.target as HTMLInputElement).value, 1, 600)"
                          />
                        </div>
                      </template>
                    </section>

                    <section
                      key="admin"
                      class="settings-page admin-monitor-page"
                    >
                      <div class="settings-action-row" v-if="showMcpConfigEntry">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">MCP 服务器管理</span
                          ><span class="settings-row-desc"
                            >统一配置 MCP Server，集中管理工具扩展能力</span
                          ></span
                        ><button
                          type="button"
                          class="settings-secondary-button"
                          @click="openMcpConfig"
                        >
                          打开
                        </button>
                      </div>
                      <div class="settings-action-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">管理员监控入口</span
                          ><span class="settings-row-desc"
                            >展示配额趋势、容器运行、项目存储与上传安全</span
                          ></span
                        ><button
                          type="button"
                          class="settings-secondary-button"
                          @click="openAdminPanel"
                        >
                          打开
                        </button>
                      </div>
                      <div class="settings-action-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">API 管理</span
                          ><span class="settings-row-desc">管理 API 调用与账户</span></span
                        ><button
                          type="button"
                          class="settings-secondary-button"
                          @click="openApiAdmin"
                        >
                          打开
                        </button>
                      </div>
                      <div class="settings-action-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">自定义工具管理</span
                          ><span class="settings-row-desc">在线创建/编辑自定义工具文件</span></span
                        ><button
                          type="button"
                          class="settings-secondary-button"
                          @click="openCustomTools"
                        >
                          打开
                        </button>
                      </div>
                    </section>
                  </transition>
                </div>

                <div class="settings-save-bar">
                  <div class="personal-status-group">
                    <transition name="personal-status-fade"
                      ><span class="status success" v-if="status">{{ status }}</span></transition
                    ><transition name="personal-status-fade"
                      ><span class="status error" v-if="error">{{ error }}</span></transition
                    >
                  </div>
                </div>
              </section>
            </div>
          </form>
        </div>
        <div class="personalization-loading" v-else>正在加载个性化配置...</div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, nextTick, onBeforeUnmount } from 'vue';
import { storeToRefs } from 'pinia';
import FancyCheck from '@/components/common/FancyCheck.vue';
import { usePersonalizationStore } from '@/stores/personalization';
import type { ReviewAgentKey, ReviewAgentSetting } from '@/stores/personalization';
import { useResourceStore } from '@/stores/resource';
import { useUiStore } from '@/stores/ui';
import { usePolicyStore } from '@/stores/policy';
import { useTutorialStore } from '@/stores/tutorial';
import { useModelStore } from '@/stores/model';
import { formatTokenCount } from '@/utils/formatters';
import { ICONS } from '@/utils/icons';
import { useTheme } from '@/utils/theme';
import type { ThemeKey } from '@/utils/theme';

defineOptions({ name: 'PersonalizationDrawer' });

const personalization = usePersonalizationStore();
const resourceStore = useResourceStore();
const uiStore = useUiStore();
const tutorialStore = useTutorialStore();
const {
  visible,
  loading,
  form,
  tonePresets,
  status,
  error,
  saving,
  toggleUpdating,
  toolCategories,
  skillsCatalog,
  recentConversationsPromptLimitRange,
  projectMemoryInjectLimitMin,
  experiments
} = storeToRefs(personalization);

// ---- 目标模式设置辅助 ----
const goalTokenLimitEnabled = computed(
  () => typeof form.value.goal_max_tokens === 'number' && form.value.goal_max_tokens > 0
);

const clampGoalMaxTurns = (raw: any): number => {
  const n = Math.round(Number(raw));
  if (!Number.isFinite(n)) return 5;
  return Math.min(100, Math.max(1, n));
};

const clampGoalMaxTokens = (raw: any): number => {
  const n = Math.round(Number(raw));
  if (!Number.isFinite(n)) return 100000;
  return Math.min(100000000, Math.max(1000, n));
};

const toggleGoalTokenLimit = (enabled: boolean) => {
  personalization.updateField({
    key: 'goal_max_tokens',
    value: enabled ? form.value.goal_max_tokens || 100000 : null
  });
};

type IconKey = keyof typeof ICONS;

type PersonalTab =
  | 'general'
  | 'preferences'
  | 'model'
  | 'appearance'
  | 'workspace'
  | 'context'
  | 'tools'
  | 'files'
  | 'data'
  | 'voice'
  | 'sub-agents'
  | 'review-agents'
  | 'admin';

const baseTabs = [
  { id: 'general', label: '常规', icon: 'settings' },
  { id: 'preferences', label: '个性化', icon: 'userPen' },
  { id: 'model', label: '模型与思考', icon: 'brainCog' },
  { id: 'appearance', label: '外观与显示', icon: 'monitor' },
  { id: 'workspace', label: '工作区与权限', icon: 'folder' },
  { id: 'context', label: '上下文', icon: 'chatBubble' },
  { id: 'tools', label: '工具与 Skills', icon: 'wrench' },
  { id: 'files', label: '文件与图片', icon: 'file' },
  { id: 'data', label: '数据管理', icon: 'layers' },
  { id: 'voice', label: '语音模型', icon: 'mic' },
  { id: 'sub-agents', label: '子智能体', icon: 'bot' },
  { id: 'review-agents', label: '审核智能体', icon: 'checkbox' }
] as const satisfies ReadonlyArray<{ id: PersonalTab; label: string; icon: IconKey }>;

const sessionRole = ref('');
const sessionHostMode = ref(false);

const isAdmin = computed(() => {
  const quotaRole = (resourceStore.usageQuota.role || '').toLowerCase();
  const loginRole = (sessionRole.value || '').toLowerCase();
  return quotaRole === 'admin' || loginRole === 'admin';
});
const isHostMode = computed(() => {
  const mode = (resourceStore.containerStatus?.mode || '').toLowerCase();
  if (mode) {
    return mode === 'host';
  }
  return sessionHostMode.value;
});
const showMcpConfigEntry = computed(() => isAdmin.value && isHostMode.value);
const isAppShell = computed(() => {
  if (typeof window === 'undefined') return false;
  const params = new URLSearchParams(window.location.search);
  if (params.has('app_shell')) return true;
  return Boolean((window as any)?.AndroidThemeBridge);
});

const personalTabs = computed(() => {
  const tabs: Array<{ id: PersonalTab; label: string; icon: IconKey }> = [...baseTabs];
  if (isAdmin.value) {
    tabs.push({ id: 'admin', label: '管理员', icon: 'wrench' });
  }
  return tabs;
});

const activeTab = ref<PersonalTab>('general');
const activeDropdown = ref<string | null>(null);
const floatingMenuStyle = ref<Record<string, string>>({});

const activeTabLabel = computed(() => {
  return personalTabs.value.find((tab) => tab.id === activeTab.value)?.label || '常规';
});

// ── 语音模型下载 ──
const voiceModelReady = ref(false);
const voiceModelPartial = ref(false); // 文件存在但不完整
const voiceDownloading = ref(false);
const voiceDownloadPercent = ref(0);
const voiceDownloadMsg = ref('');

const checkVoiceModel = () => {
  const bridge = (window as any)?.AndroidVoiceBridge;
  if (bridge) {
    try {
      if (typeof bridge.isModelReady === 'function') {
        voiceModelReady.value = bridge.isModelReady();
      }
      if (!voiceModelReady.value && typeof bridge.isModelPartial === 'function') {
        voiceModelPartial.value = bridge.isModelPartial();
      }
    } catch (_) {
      /* ignore */
    }
  }
};

const downloadVoiceModel = () => {
  const bridge = (window as any)?.AndroidVoiceBridge;
  if (!bridge) {
    alert('仅在 App 内支持下载语音模型');
    return;
  }
  voiceDownloading.value = true;
  voiceDownloadPercent.value = 0;
  voiceDownloadMsg.value = '准备下载...';
  voiceModelPartial.value = false;

  (window as any).__onVoiceDownloadProgress = (pct: number, msg: string) => {
    voiceDownloadPercent.value = pct;
    voiceDownloadMsg.value = msg;
    if (pct >= 100) {
      voiceDownloading.value = false;
      voiceModelReady.value = true;
      voiceModelPartial.value = false;
    }
  };
  (window as any).__onVoiceModelReady = () => {
    voiceModelReady.value = true;
    voiceDownloading.value = false;
    voiceModelPartial.value = false;
  };

  bridge.downloadModel();
};

const deleteVoiceModel = () => {
  const bridge = (window as any)?.AndroidVoiceBridge;
  if (!bridge) return;
  if (typeof bridge.deleteModel === 'function') {
    bridge.deleteModel();
  }
  voiceModelReady.value = false;
  voiceModelPartial.value = false;
  voiceDownloadPercent.value = 0;
  voiceDownloadMsg.value = '';
};

// 打开个人空间时检查模型状态
checkVoiceModel();

const updateFloatingMenuPosition = async () => {
  if (!activeDropdown.value || typeof window === 'undefined') {
    floatingMenuStyle.value = {};
    return;
  }
  await nextTick();
  const button = document.querySelector<HTMLElement>(
    '.settings-select-wrap.open .settings-select-button'
  );
  const menu = document.querySelector<HTMLElement>(
    '.settings-select-wrap.open .settings-floating-menu'
  );
  if (!button) {
    return;
  }
  const rect = button.getBoundingClientRect();
  const menuWidth = Math.min(300, Math.max(240, window.innerWidth - 32));
  const left = Math.max(16, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 16));

  const padding = 16;
  const gap = 10;

  // 测量单个选项高度，计算 4 选项固定 max-height
  const optionEl = menu?.querySelector<HTMLElement>('.settings-menu-option');
  const optionHeight = optionEl ? optionEl.getBoundingClientRect().height : 44;
  const menuPadding = 12; // menu padding 6px top + 6px bottom
  const fixedMaxHeight = Math.round(optionHeight * 4) + menuPadding;

  // 先限制菜单高度为固定值，再次获取实际高度
  if (menu) {
    menu.style.maxHeight = `${fixedMaxHeight}px`;
  }
  // 重新读取受限后的菜单高度
  const menuHeight = menu?.getBoundingClientRect().height || fixedMaxHeight;

  const spaceBelow = window.innerHeight - rect.bottom - padding;
  const spaceAbove = rect.top - padding;

  let top: number;
  let maxHeight: number | undefined;

  if (spaceBelow >= menuHeight) {
    // 下方空间足够完整显示
    top = rect.bottom + gap;
  } else if (spaceAbove >= menuHeight) {
    // 上方空间足够完整显示，翻转到上方
    top = rect.top - menuHeight - gap;
  } else if (spaceBelow >= spaceAbove) {
    // 上下都不够，下方空间相对更大：限制高度到可用空间，贴按钮向下
    const availableHeight = Math.max(80, spaceBelow - gap);
    maxHeight = availableHeight;
    top = rect.bottom + gap;
  } else {
    // 上方空间相对更大：限制高度到可用空间，翻转到上方
    const availableHeight = Math.max(80, spaceAbove - gap);
    maxHeight = availableHeight;
    top = Math.max(padding, rect.top - availableHeight - gap);
  }

  floatingMenuStyle.value = {
    position: 'fixed',
    top: `${Math.round(top)}px`,
    left: `${Math.round(left)}px`,
    right: 'auto',
    width: `${Math.round(menuWidth)}px`,
    zIndex: '2147483647',
    ...(maxHeight !== undefined ? { maxHeight: `${Math.round(maxHeight)}px` } : {})
  };
};

const toggleDropdown = async (key: string) => {
  activeDropdown.value = activeDropdown.value === key ? null : key;
  await updateFloatingMenuPosition();
};

const closeDropdown = () => {
  activeDropdown.value = null;
  floatingMenuStyle.value = {};
};

const settingsTabIconStyle = (icon: IconKey) => ({
  '--icon-src': `url(${ICONS[icon]})`
});

onMounted(() => {
  window.addEventListener('resize', updateFloatingMenuPosition);
  window.addEventListener('scroll', updateFloatingMenuPosition, true);
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateFloatingMenuPosition);
  window.removeEventListener('scroll', updateFloatingMenuPosition, true);
});

type RunModeValue = 'fast' | 'thinking' | null;
type ReasoningEffortValue = 'low' | 'medium' | 'high' | 'xhigh' | 'max' | null;
type PermissionModeValue = 'readonly' | 'approval' | 'auto_approval' | 'unrestricted';
type CompressionField =
  | 'shallow_compress_trigger_tokens'
  | 'shallow_compress_keep_recent_tools'
  | 'shallow_compress_keep_user_turn_tools'
  | 'shallow_compress_max_replace_per_round'
  | 'shallow_compress_trigger_tool_calls_interval'
  | 'deep_compress_trigger_tokens';

const runModeOptions: Array<{
  id: string;
  label: string;
  desc: string;
  value: RunModeValue;
  badge?: string;
}> = [
  { id: 'fast', label: '快速模式', desc: '追求响应速度，跳过思考模型', value: 'fast' },
  { id: 'thinking', label: '思考模式', desc: '整轮对话都使用思考模型', value: 'thinking' }
];

const reasoningEffortOptions: Array<{
  id: string;
  label: string;
  desc: string;
  value: ReasoningEffortValue;
}> = [
  { id: 'default', label: '默认', desc: '不指定强度，使用 API 默认行为', value: null },
  { id: 'low', label: 'low', desc: '最低推理，响应最快', value: 'low' },
  { id: 'medium', label: 'medium', desc: '较低推理', value: 'medium' },
  { id: 'high', label: 'high', desc: '均衡推理', value: 'high' },
  { id: 'xhigh', label: 'xhigh', desc: '更高推理', value: 'xhigh' },
  { id: 'max', label: 'max', desc: '最高推理，适合最复杂任务', value: 'max' }
];

const permissionModeOptions: Array<{
  id: PermissionModeValue;
  label: string;
  desc: string;
}> = [
  { id: 'readonly', label: '只读', desc: '仅允许读取/检索类工具，修改操作将被拒绝' },
  { id: 'approval', label: '批准', desc: '对工作区文件进行修改的工具需人工批准后执行' },
  {
    id: 'auto_approval',
    label: '自动审核',
    desc: '工作区内写入直通；高风险操作由后台审核智能体自动审批'
  },
  { id: 'unrestricted', label: '无限制', desc: '工具按常规流程直接执行' }
];

type WorkModeValue = 'plan' | 'ask' | 'execute';
const workModeOptions: Array<{
  id: WorkModeValue;
  label: string;
  desc: string;
}> = [
  { id: 'plan', label: '计划', desc: '只制定计划，批准后执行' },
  { id: 'ask', label: '询问', desc: '先讨论确认，再开工' },
  { id: 'execute', label: '执行', desc: '自行补全细节，直接开工' }
];

const policyStore = usePolicyStore();
const modelStore = useModelStore();

const filteredModelOptions = computed(() =>
  (modelStore.models || []).map((opt: any) => {
    const multimodal = String(opt.multimodal || 'none');
    return {
      id: opt.key,
      value: opt.key,
      label: opt.label,
      desc: opt.description || '',
      badge: multimodal === 'image,video' ? '图文' : opt.thinkingOnly ? '思考' : undefined,
      thinkingOnly: !!opt.thinkingOnly,
      fastOnly: !!opt.fastOnly,
      disabled: policyStore.disabledModelSet.has(opt.key)
    };
  })
);

const imageCompressionOptions = [
  { id: 'original', label: '原图', desc: '不压缩' },
  { id: '1080p', label: '1080p', desc: '最长边不超过 1080p 等比缩放' },
  { id: '720p', label: '720p', desc: '最长边不超过 720p 等比缩放' },
  { id: '540p', label: '540p', desc: '最长边不超过 540p 等比缩放' }
] as const;

const defaultModelLabel = computed(() => {
  return (
    filteredModelOptions.value.find((option: any) => option.value === form.value.default_model)
      ?.label || '未设置'
  );
});

const runModeLabel = computed(() => {
  return runModeOptions.find((option) => isRunModeActive(option.value))?.label || '未设置';
});

const isEffortActive = (value: ReasoningEffortValue) => {
  if (value === null) {
    return !form.value.default_reasoning_effort;
  }
  return form.value.default_reasoning_effort === value;
};

const reasoningEffortLabel = computed(() => {
  return (
    reasoningEffortOptions.find((option) => isEffortActive(option.value))?.label || '默认'
  );
});

const selectDefaultReasoningEffort = (value: ReasoningEffortValue) => {
  personalization.setDefaultReasoningEffort(value);
  closeDropdown();
};

const permissionModeLabel = computed(() => {
  return (
    permissionModeOptions.find((option) => option.id === form.value.default_permission_mode)
      ?.label || '未设置'
  );
});

const workModeLabel = computed(() => {
  return (
    workModeOptions.find((option) => option.id === form.value.default_work_mode)?.label ||
    '未设置'
  );
});

const versioningBackupModeLabel = computed(() => {
  if (form.value.versioning_backup_mode === 'full') return '完全备份';
  return '浅备份';
});

const imageCompressionLabel = computed(() => {
  return (
    imageCompressionOptions.find((option) => option.id === form.value.image_compression)?.label ||
    '未设置'
  );
});

const communicationStyleLabel = computed(() => {
  if (form.value.communication_style === 'human_like') return '拟人';
  if (form.value.communication_style === 'auto') return '自动';
  return '默认';
});

const conversationContinuityLabel = computed(() => {
  if (form.value.conversation_continuity === 'high') return '高';
  if (form.value.conversation_continuity === 'low') return '低';
  return '中';
});

const currentBlockDisplayMode = computed(() => experiments.value.blockDisplayMode);

const stackedHideBorders = computed(() => form.value.stacked_hide_borders);
const minimalExpandHeightLimited = computed(() => form.value.minimal_expand_height_limited);

const blockDisplayLabel = computed(() => {
  return (
    blockDisplayOptions.find((option) => option.value === currentBlockDisplayMode.value)?.label ||
    '堆叠动画'
  );
});

const currentCompactMessageDisplay = computed(() => form.value.compact_message_display || 'full');

const compactMessageDisplayLabel = computed(() => {
  return (
    compactMessageDisplayOptions.find(
      (option) => option.value === currentCompactMessageDisplay.value
    )?.label || '完整信息'
  );
});

const usageSummary = ref({
  total_input_tokens: 0,
  total_output_tokens: 0,
  total_conversations: 0,
  total_user_messages: 0,
  total_tools: 0
});
const usageLoading = ref(false);
const usageError = ref('');
const usageUpdatedAt = ref<string | null>(null);

const usageUpdatedText = computed(() => {
  if (!usageUpdatedAt.value) {
    return '尚未刷新';
  }
  const date = new Date(usageUpdatedAt.value);
  if (Number.isNaN(date.getTime())) {
    return '时间未知';
  }
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
});

const appCurrentVersionCode = ref<number | null>(null);
const appCurrentVersionName = ref<string>('');
const appUpdateChecking = ref(false);
const appUpdateError = ref('');
const appUpdateCheckedAt = ref<string | null>(null);
const appUpdateInfo = ref<{
  latestVersionCode: number;
  latestVersionName: string;
  apkUrl: string;
  fileSizeBytes: number;
  changelog?: string;
  publishedAt?: string | null;
  hasUpdate?: boolean | null;
} | null>(null);

if (typeof window !== 'undefined') {
  const params = new URLSearchParams(window.location.search);
  const vcRaw = params.get('app_vc') || '';
  const vnRaw = params.get('app_vn') || '';
  const bridge = (window as any)?.AndroidThemeBridge;
  const bridgeVcRaw =
    bridge && typeof bridge.getAppVersionCode === 'function'
      ? String(bridge.getAppVersionCode() || '')
      : '';
  const bridgeVnRaw =
    bridge && typeof bridge.getAppVersionName === 'function'
      ? String(bridge.getAppVersionName() || '')
      : '';
  // App 端优先使用 Bridge 实时版本，避免 WebView 恢复旧 URL 参数导致显示过期版本号
  const finalVcRaw = bridgeVcRaw || vcRaw;
  const finalVnRaw = bridgeVnRaw || vnRaw;
  appCurrentVersionCode.value = /^\d+$/.test(finalVcRaw) ? Number(finalVcRaw) : null;
  appCurrentVersionName.value = finalVnRaw || '';
}

const hydrateAppVersionFromBridge = () => {
  if (typeof window === 'undefined') return;
  const bridge = (window as any)?.AndroidThemeBridge;
  if (!bridge) return;
  try {
    const vcRaw =
      typeof bridge.getAppVersionCode === 'function'
        ? String(bridge.getAppVersionCode() || '')
        : '';
    const vnRaw =
      typeof bridge.getAppVersionName === 'function'
        ? String(bridge.getAppVersionName() || '')
        : '';
    if (vcRaw && /^\d+$/.test(vcRaw)) {
      appCurrentVersionCode.value = Number(vcRaw);
    }
    if (vnRaw) {
      appCurrentVersionName.value = vnRaw;
    }
  } catch {
    // ignore bridge read failures
  }
};

const appCurrentVersionText = computed(() => {
  return appCurrentVersionName.value || '未知';
});

const appHasUpdate = computed(() => {
  if (!appUpdateInfo.value) return false;
  if (typeof appUpdateInfo.value.hasUpdate === 'boolean') return appUpdateInfo.value.hasUpdate;
  if (appCurrentVersionCode.value == null) return false;
  return Number(appUpdateInfo.value.latestVersionCode || 0) > appCurrentVersionCode.value;
});

const appDownloadUrl = computed(() => appUpdateInfo.value?.apkUrl || '');

const appUpdateStateText = computed(() => {
  if (!appUpdateInfo.value) return '未检查';
  return appHasUpdate.value ? '发现新版本' : '已是最新';
});

const appUpdateCheckedText = computed(() => {
  if (!appUpdateCheckedAt.value) return '尚未检查更新';
  const d = new Date(appUpdateCheckedAt.value);
  if (Number.isNaN(d.getTime())) return '刚刚检查过';
  return `最近检查：${d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}`;
});

const fetchUsageSummary = async () => {
  if (usageLoading.value) {
    return;
  }
  usageLoading.value = true;
  usageError.value = '';
  try {
    const response = await fetch('/api/conversations/statistics');
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || payload.message || '获取用量统计失败');
    }
    const data = payload.data || {};
    const tokenStats = data.token_statistics || {};
    usageSummary.value = {
      total_input_tokens: Number(tokenStats.total_input_tokens || 0),
      total_output_tokens: Number(tokenStats.total_output_tokens || 0),
      total_conversations: Number(data.total_conversations || 0),
      total_user_messages: Number(data.total_user_messages || 0),
      total_tools: Number(data.total_tools || 0)
    };
    usageUpdatedAt.value = new Date().toISOString();
  } catch (error: any) {
    usageError.value = error?.message || '获取用量统计失败';
  } finally {
    usageLoading.value = false;
  }
};

const checkAppUpdate = async () => {
  if (appUpdateChecking.value) return;
  hydrateAppVersionFromBridge();
  appUpdateChecking.value = true;
  appUpdateError.value = '';
  try {
    const params = new URLSearchParams();
    if (appCurrentVersionCode.value != null) {
      params.set('currentVersionCode', String(appCurrentVersionCode.value));
    }
    const suffix = params.toString() ? `?${params.toString()}` : '';
    const resp = await fetch(`/api/app/version${suffix}`);
    const payload = await resp.json();
    if (!resp.ok || !payload?.success) {
      throw new Error(payload?.error || '检查更新失败');
    }
    const data = payload?.data || {};
    appUpdateInfo.value = {
      latestVersionCode: Number(data.latestVersionCode || 0),
      latestVersionName: String(data.latestVersionName || ''),
      apkUrl: String(data.apkUrl || ''),
      fileSizeBytes: Number(data.fileSizeBytes || 0),
      changelog: String(data.changelog || ''),
      publishedAt: data.publishedAt || null,
      hasUpdate: typeof data.hasUpdate === 'boolean' ? data.hasUpdate : null
    };
    appUpdateCheckedAt.value = new Date().toISOString();
  } catch (error: any) {
    appUpdateError.value = error?.message || '检查更新失败';
  } finally {
    appUpdateChecking.value = false;
  }
};

const downloadLatestApp = () => {
  const url = appDownloadUrl.value;
  if (!url) {
    appUpdateError.value = '未拿到下载地址';
    return;
  }
  window.location.href = url;
};

onMounted(async () => {
  hydrateAppVersionFromBridge();
  try {
    const resp = await fetch('/api/session-status', { credentials: 'same-origin' });
    if (!resp.ok) return;
    const payload = await resp.json();
    const snapshot = payload?.session || {};
    sessionRole.value = String(snapshot.role || '');
    sessionHostMode.value = !!snapshot.host_mode;
  } catch (_err) {
    sessionRole.value = '';
  }
});

watch(
  () => [activeTab.value, visible.value],
  ([tab, isVisible]) => {
    if (isVisible && tab === 'data') {
      fetchUsageSummary();
    }
    if (isVisible && tab === 'sub-agents') {
      loadSubAgentRoles();
      loadSubAgentSettings();
      loadSubAgentModels();
    }
    if (isVisible && tab === 'review-agents') {
      loadSubAgentModels();
    }
    if (
      isVisible &&
      tab === 'general' &&
      isAppShell.value &&
      !appUpdateInfo.value &&
      !appUpdateChecking.value
    ) {
      checkAppUpdate();
    }
  }
);

// ----- 子智能体管理 -----
const subAgentRoles = ref<any[]>([]);
const subAgentRolesLoading = ref(false);
const subAgentCompressThreshold = ref(150000);
/** 子智能体最大执行轮次（仅传统后台子智能体；多智能体成员不受限）：null/'' = 默认 50；0 = 无上限；正整数 = 该值 */
const subAgentMaxTurns = ref<number | null>(null);
const subAgentSettingsSaving = ref(false);
const subAgentModels = ref<any[]>([]);
const roleEditorOpen = ref(false);
const editingRole = ref<any>(null);
const roleSaving = ref(false);
const roleForm = ref({
  role_id: '',
  name: '',
  description: '',
  body_prompt: '',
  thinking_mode: 'fast',
  model_key: ''
});

const loadSubAgentRoles = async () => {
  subAgentRolesLoading.value = true;
  try {
    const resp = await fetch('/api/multiagent/roles', { credentials: 'same-origin' });
    const data = await resp.json();
    if (data.success) {
      subAgentRoles.value = data.roles || [];
    }
  } catch (e) {
    // 静默处理
  } finally {
    subAgentRolesLoading.value = false;
  }
};

const loadSubAgentModels = async () => {
  try {
    const resp = await fetch('/api/multiagent/models', { credentials: 'same-origin' });
    const data = await resp.json();
    if (data.success) {
      subAgentModels.value = data.models || [];
    }
  } catch (e) {
    // 静默处理
  }
};

// ----- 审核智能体（模型与运行参数统一配置，模型库复用子智能体模型列表） -----
const reviewAgentDefs: Array<{ key: ReviewAgentKey; name: string; desc: string }> = [
  { key: 'auto_approval', name: '自动审批智能体', desc: '自动审批模式下判断工具调用是否越权/危险' },
  { key: 'goal_review', name: '目标审核智能体', desc: '目标模式下判断长期目标是否真正达成' },
  { key: 'workflow_review', name: '工作流审核智能体', desc: '工作流审核节点判断阶段产出是否达标' }
];

const reviewAgentOf = (key: ReviewAgentKey): ReviewAgentSetting => {
  const agents = (form.value as any).review_agents;
  return agents?.[key] || { model: '', thinking: false, timeout_seconds: 60, max_rounds: 3, max_command_timeout: 60 };
};

const updateReviewAgent = (key: ReviewAgentKey, patch: Partial<ReviewAgentSetting>) => {
  const agents = { ...((form.value as any).review_agents || {}) };
  agents[key] = { ...reviewAgentOf(key), ...patch };
  personalization.updateField({ key: 'review_agents', value: agents });
};

const updateReviewAgentInt = (key: ReviewAgentKey, field: 'timeout_seconds' | 'max_rounds' | 'max_command_timeout', raw: string, lo: number, hi: number) => {
  const parsed = parseInt(raw, 10);
  if (!Number.isFinite(parsed)) return;
  updateReviewAgent(key, { [field]: Math.max(lo, Math.min(parsed, hi)) });
};

const loadSubAgentSettings = async () => {
  try {
    const resp = await fetch('/api/multiagent/settings', { credentials: 'same-origin' });
    const data = await resp.json();
    if (data.success && data.settings) {
      subAgentCompressThreshold.value = data.settings.sub_agent_compress_threshold_tokens || 150000;
      // 未设置时为 null，输入框留空（placeholder 提示默认 50）
      subAgentMaxTurns.value = data.settings.sub_agent_max_turns ?? null;
    }
  } catch (e) {
    // 静默处理
  }
};

const saveSubAgentSettings = async () => {
  subAgentSettingsSaving.value = true;
  try {
    await fetch('/api/multiagent/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        settings: {
          sub_agent_compress_threshold_tokens: subAgentCompressThreshold.value,
          // 留空（null/''）提交 null → 服务端删除该键恢复默认 50；0 → 无上限
          sub_agent_max_turns:
            subAgentMaxTurns.value === null || (subAgentMaxTurns.value as unknown) === ''
              ? null
              : Number(subAgentMaxTurns.value)
        }
      })
    });
  } finally {
    subAgentSettingsSaving.value = false;
  }
};

const openRoleEditor = (role?: any) => {
  if (role) {
    editingRole.value = role;
    roleForm.value = {
      role_id: role.role_id || '',
      name: role.name || '',
      description: role.description || '',
      body_prompt: role.body_prompt || '',
      thinking_mode: role.thinking_mode || 'fast',
      model_key: role.model_key || ''
    };
  } else {
    editingRole.value = null;
    roleForm.value = { role_id: '', name: '', description: '', body_prompt: '', thinking_mode: 'fast', model_key: '' };
  }
  closeDropdown();
  roleEditorOpen.value = true;
};

const closeRoleEditor = () => {
  roleEditorOpen.value = false;
  editingRole.value = null;
  closeDropdown();
};

// 角色编辑弹窗遮罩：按下与松开都发生在遮罩上才视为「点击遮罩关闭」。
// 修复：在输入框内拖选文字、松开落在遮罩上时，click 事件 target 为遮罩，
// 会被 @click.self 误判为点击遮罩，导致整个编辑器关闭、表单内容丢失。
const roleEditorOverlayPressActive = ref(false);

const handleRoleEditorOverlayPressStart = (event: MouseEvent) => {
  roleEditorOverlayPressActive.value =
    event.target === event.currentTarget && event.button === 0;
};

const handleRoleEditorOverlayPressEnd = (event: MouseEvent) => {
  const shouldClose =
    roleEditorOverlayPressActive.value && event.target === event.currentTarget;
  roleEditorOverlayPressActive.value = false;
  if (shouldClose) {
    closeRoleEditor();
  }
};

const saveRole = async () => {
  roleSaving.value = true;
  try {
    const isEdit = !!editingRole.value;
    const url = isEdit
      ? `/api/multiagent/roles/${roleForm.value.role_id}`
      : '/api/multiagent/roles';
    const method = isEdit ? 'PUT' : 'POST';
    const resp = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        ...roleForm.value,
        model_key: roleForm.value.model_key || null
      })
    });
    const data = await resp.json();
    if (data.success) {
      closeRoleEditor();
      await loadSubAgentRoles();
    }
  } finally {
    roleSaving.value = false;
  }
};

const deleteRole = async (role: any) => {
  if (!confirm(`确认删除角色「${role.name}」？`)) return;
  try {
    const resp = await fetch(`/api/multiagent/roles/${role.role_id}`, {
      method: 'DELETE',
      credentials: 'same-origin'
    });
    const data = await resp.json();
    if (data.success) {
      await loadSubAgentRoles();
    }
  } catch (e) {
    // 静默处理
  }
};

const setActiveTab = (tab: PersonalTab) => {
  activeTab.value = tab;
  closeDropdown();
};

const startTutorial = () => {
  fetch('/api/tutorial-status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ tutorial_completed: true })
  }).catch(() => {});
  tutorialStore.markCompleted();
  personalization.closeDrawer();
  window.setTimeout(() => {
    tutorialStore.startTutorial();
  }, 320);
};

const isRunModeActive = (value: RunModeValue) => {
  if (value === null) {
    return !form.value.default_run_mode;
  }
  return form.value.default_run_mode === value;
};

const setDefaultRunMode = (value: RunModeValue) => {
  if (checkModeModelConflict(value, form.value.default_model)) {
    return;
  }
  personalization.setDefaultRunMode(value);
};

const setDefaultModel = (value: string) => {
  if (policyStore.disabledModelSet.has(value)) {
    uiStore.pushToast({
      title: '模型被禁用',
      message: '已被管理员禁用，无法选择',
      type: 'warning'
    });
    return;
  }
  if (checkModeModelConflict(form.value.default_run_mode, value)) {
    return;
  }
  personalization.setDefaultModel(value);
};

const setDefaultPermissionMode = (value: PermissionModeValue) => {
  personalization.setDefaultPermissionMode(value);
};

const setCommunicationStyle = (value: 'default' | 'human_like' | 'auto') => {
  personalization.setCommunicationStyle(value);
};

const setConversationContinuity = (value: 'low' | 'medium' | 'high') => {
  personalization.setConversationContinuity(value);
};

const selectDefaultModel = (value: string) => {
  setDefaultModel(value);
  closeDropdown();
};

const selectDefaultRunMode = (value: RunModeValue) => {
  setDefaultRunMode(value);
  closeDropdown();
};

const selectDefaultPermissionMode = (value: PermissionModeValue) => {
  setDefaultPermissionMode(value);
  closeDropdown();
};

const selectDefaultWorkMode = (value: WorkModeValue) => {
  personalization.setDefaultWorkMode(value);
  closeDropdown();
};

const selectVersioningBackupMode = (value: 'shallow' | 'full') => {
  personalization.setVersioningBackupMode(value);
  closeDropdown();
};

const selectCommunicationStyle = (value: 'default' | 'human_like' | 'auto') => {
  setCommunicationStyle(value);
  closeDropdown();
};

const selectConversationContinuity = (value: 'low' | 'medium' | 'high') => {
  setConversationContinuity(value);
  closeDropdown();
};

const selectImageCompression = (value: (typeof imageCompressionOptions)[number]['id']) => {
  personalization.setImageCompression(value);
  closeDropdown();
};

const selectBlockDisplayMode = (mode: 'traditional' | 'stacked' | 'minimal') => {
  handleBlockDisplayModeChange(mode);
  closeDropdown();
};

const checkModeModelConflict = (mode: RunModeValue, model: string | null): boolean => {
  const found = (filteredModelOptions.value || []).find((item: any) => item.value === model);
  const warnings: string[] = [];
  if (found?.thinkingOnly && mode && mode !== 'thinking') {
    warnings.push(`${found.label} 仅支持思考模式，已保持原设置。`);
  }
  if (found?.fastOnly && mode && mode !== 'fast') {
    warnings.push(`${found.label} 仅支持快速模式，已保持原设置。`);
  }
  if (warnings.length) {
    uiStore.pushToast({
      title: '模型/思考模式不兼容',
      message: warnings.join(' '),
      type: 'warning',
      duration: 6000
    });
    return true;
  }
  return false;
};

const handleRecentConversationsPromptLimitInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target) {
    return;
  }
  personalization.updateField({
    key: 'recent_conversations_prompt_limit',
    value: target.value
  });
};

const commitRecentConversationsPromptLimitInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target || !target.value) {
    personalization.setRecentConversationsPromptLimit(null);
    return;
  }
  const parsed = Number(target.value);
  personalization.setRecentConversationsPromptLimit(Number.isNaN(parsed) ? null : parsed);
};

const restoreRecentConversationsPromptLimit = () => {
  personalization.setRecentConversationsPromptLimit(null);
};

const handleProjectMemoryInjectLimitInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target) {
    return;
  }
  personalization.updateField({
    key: 'project_memory_inject_limit',
    value: target.value
  });
};

const commitProjectMemoryInjectLimitInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target || !target.value) {
    // 留空 = 无上限
    personalization.setProjectMemoryInjectLimit(null);
    return;
  }
  const parsed = Number(target.value);
  personalization.setProjectMemoryInjectLimit(Number.isNaN(parsed) ? null : parsed);
};

const restoreProjectMemoryInjectLimit = () => {
  personalization.restoreProjectMemoryInjectLimit();
};

const handleCompressionNumberInput = (key: CompressionField, event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target || !target.value) {
    personalization.updateField({ key, value: null });
    return;
  }
  const parsed = Number(target.value);
  if (!Number.isFinite(parsed)) {
    personalization.updateField({ key, value: null });
    return;
  }
  personalization.updateField({ key, value: Math.round(parsed) });
};

const restoreCompressionDefaults = () => {
  personalization.updateField({ key: 'shallow_compress_trigger_tokens', value: null });
  personalization.updateField({ key: 'shallow_compress_keep_recent_tools', value: null });
  personalization.updateField({ key: 'shallow_compress_max_replace_per_round', value: null });
  personalization.updateField({ key: 'shallow_compress_trigger_tool_calls_interval', value: null });
  personalization.updateField({ key: 'deep_compress_trigger_tokens', value: null });
};

const toggleCategory = (categoryId: string) => {
  personalization.toggleDefaultToolCategory(categoryId);
};

const blockDisplayOptions = [
  {
    id: 'traditional',
    label: '传统列表',
    desc: '按时间顺序垂直排列，经典样式',
    value: 'traditional' as const
  },
  {
    id: 'stacked',
    label: '堆叠动画',
    desc: '超过 6 条自动收纳为"更多"，动态展示',
    value: 'stacked' as const,
    badge: '推荐'
  },
  {
    id: 'minimal',
    label: '极简模式',
    desc: '摘要行 + 无缝展开，流式输出效果',
    value: 'minimal' as const,
    badge: '新'
  }
];

const handleBlockDisplayModeChange = (mode: 'traditional' | 'stacked' | 'minimal') => {
  personalization.setBlockDisplayMode(mode);
};

const handleStackedHideBordersChange = (event: Event) => {
  const target = event.target as HTMLInputElement;
  personalization.updateField({
    key: 'stacked_hide_borders',
    value: target.checked
  });
};

const handleMinimalExpandHeightLimitedChange = (event: Event) => {
  const target = event.target as HTMLInputElement;
  personalization.updateField({
    key: 'minimal_expand_height_limited',
    value: target.checked
  });
};

const compactMessageDisplayOptions = [
  {
    id: 'full',
    label: '完整信息',
    desc: '审核、子智能体等系统消息显示完整原始内容',
    value: 'full' as const
  },
  {
    id: 'brief',
    label: '简略信息',
    desc: '用一行横线概要替代系统消息',
    value: 'brief' as const
  }
];

const selectCompactMessageDisplay = (mode: 'full' | 'brief') => {
  personalization.setCompactMessageDisplay(mode);
  closeDropdown();
};

const openAdminPanel = () => {
  window.open('/admin/monitor', '_blank', 'noopener');
  personalization.closeDrawer();
};

const openCustomTools = () => {
  window.open('/admin/custom-tools', '_blank', 'noopener');
  personalization.closeDrawer();
};

const openApiAdmin = () => {
  window.open('/admin/api', '_blank', 'noopener');
  personalization.closeDrawer();
};

const openMcpConfig = () => {
  window.open('/admin/policy', '_blank', 'noopener');
  personalization.closeDrawer();
};

// ===== 主题切换 =====
const { setTheme, loadTheme } = useTheme();
const themeOptions: Array<{ id: ThemeKey; label: string; desc: string; swatches: string[] }> = [
  {
    id: 'classic',
    label: '经典',
    desc: '米色质感，柔和高对比',
    swatches: ['#eeece2', '#f7f3ea', '#da7756']
  },
  {
    id: 'light',
    label: '明亮',
    desc: '纯白底色 + 优雅灰，简洁清爽',
    swatches: ['#ffffff', '#f7f7f8', '#6b7280']
  },
  {
    id: 'dark',
    label: '夜间',
    desc: '深灰 + 黑，低亮度并保持彩色点缀',
    swatches: ['#1a1a1a', '#2a2a2a', '#3a3a3a']
  }
];

const activeTheme = ref<ThemeKey>(loadTheme());

const themeLabel = computed(() => {
  return themeOptions.find((option) => option.id === activeTheme.value)?.label || '经典';
});

// 监听store中的theme变化，同步到activeTheme（用于从后端加载主题后更新UI）
watch(
  () => personalization.form.theme,
  (newTheme) => {
    if (newTheme && newTheme !== activeTheme.value) {
      activeTheme.value = newTheme as ThemeKey;
      setTheme(newTheme as ThemeKey);
    }
  },
  { immediate: true }
);

const selectThemeOption = (theme: ThemeKey) => {
  applyThemeOption(theme);
  closeDropdown();
};

const applyThemeOption = async (theme: ThemeKey) => {
  activeTheme.value = theme;
  setTheme(theme);

  // 同步更新到store并保存到后端配置文件
  personalization.updateField({ key: 'theme', value: theme });

  try {
    const resp = await fetch('/api/personalization', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme })
    });
    const result = await resp.json();
    if (!resp.ok || !result.success) {
      console.warn('保存主题到后端失败:', result.error);
    }
  } catch (error) {
    console.warn('保存主题到后端失败:', error);
  }
};

</script>

<style scoped>
.sub-agent-role-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sub-agent-role-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 11px 0;
  border-bottom: 1px solid var(--theme-control-border);
}
.sub-agent-role-info {
  flex: 1;
  min-width: 0;
}
.sub-agent-role-name {
  font-size: 14px;
  font-weight: 550;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 6px;
}
.sub-agent-role-tag {
  font-size: 10px;
  padding: 1px 8px;
  border-radius: 999px;
  font-weight: 400;
}
.sub-agent-role-tag.tag-preset {
  background: color-mix(in srgb, var(--text-secondary) 15%, transparent);
  color: var(--text-secondary);
}
.sub-agent-role-tag.tag-custom {
  background: color-mix(in srgb, var(--accent) 15%, transparent);
  color: var(--accent);
}
.sub-agent-role-desc {
  font-size: 12.5px;
  color: var(--text-secondary);
  margin-top: 3px;
  line-height: 1.38;
}
.sub-agent-role-meta {
  font-size: 11px;
  color: var(--text-secondary);
  opacity: 0.7;
  margin-top: 2px;
}
.sub-agent-role-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

/* 角色编辑弹窗 — 复用 settings-redesign-card */
.role-editor-drawer-overlay {
  position: fixed;
  inset: 0;
  background: var(--overlay-scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2147483647;
}
.role-editor-drawer-card {
  display: flex;
  flex-direction: column;
  overflow: visible;
}
.role-editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  flex-shrink: 0;
}
.role-editor-header h2 {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}
.role-editor-body {
  flex: 1;
  overflow-y: auto;
  overflow-x: visible;
  min-height: 0;
  scrollbar-width: none;
  -ms-overflow-style: none;
}
.role-editor-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 12px;
  flex-shrink: 0;
}
.settings-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.settings-section-title {
  font-size: 14px;
  font-weight: 550;
  color: var(--text-primary);
}
.settings-empty-hint {
  color: var(--text-secondary);
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}

.settings-redesign-card {
  --settings-floating-menu-bg: color-mix(in srgb, var(--surface-soft) 90%, var(--surface-raised));
  --settings-floating-menu-hover: var(--hover-bg);
  --settings-floating-menu-shadow: none;
  --settings-tab-hover-bg: var(--theme-tab-active);
  --settings-tab-active-bg: var(--theme-tab-active);
  position: relative;
  width: min(70vw, calc(100vw - 24px));
  height: min(80vh, calc(100vh - 24px));
  padding: 30px 42px 24px;
  border-radius: 32px;
  overflow: visible;
  background: var(--theme-surface-soft);
  border: 1px solid var(--theme-control-border);
  box-shadow: none;
}

:global(html[data-theme='dark']) .settings-redesign-card,
:global(body[data-theme='dark']) .settings-redesign-card {
  --settings-floating-menu-bg: var(--surface-panel);
  --settings-floating-menu-hover: var(--hover-bg);
  --settings-floating-menu-shadow: none;
  --settings-tab-hover-bg: var(--hover-bg);
  --settings-tab-active-bg: var(--hover-bg);
}

.settings-close-button {
  position: absolute;
  top: 27px;
  left: 45px;
  width: 42px;
  height: 42px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--text-primary);
  display: grid;
  place-items: center;
  cursor: pointer;
  z-index: 3;
}

.settings-close-button:hover {
  background: var(--hover-bg);
}

.settings-close-button svg {
  width: 25px;
  height: 25px;
  stroke-width: 2.15;
}

.settings-redesign-body,
.settings-redesign-form {
  height: 100%;
  min-height: 0;
}

.settings-redesign-layout {
  height: 100%;
  display: grid;
  grid-template-columns: minmax(190px, 2fr) minmax(0, 8fr);
  column-gap: 34px;
  min-height: 0;
}

.settings-redesign-tabs {
  margin-top: 68px;
  height: calc(100% - 68px);
  padding-right: 28px;
  border-right: 1px solid var(--theme-control-border);
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.settings-redesign-tab {
  width: 100%;
  height: 47px;
  flex: 0 0 auto;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: var(--text-primary);
  padding: 0 13px;
  display: flex;
  align-items: center;
  gap: 14px;
  cursor: pointer;
  text-align: left;
  font-size: 16px;
  letter-spacing: -0.01em;
}

.settings-redesign-tab:hover {
  background: var(--settings-tab-hover-bg);
}

.settings-redesign-tab.active {
  background: var(--settings-tab-active-bg);
  font-weight: 640;
}

.settings-tab-icon {
  --icon-size: 20px;
  flex: 0 0 auto;
}

.settings-tab-icon--chat {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-left: -5px;
  margin-right: -5px;
}

.settings-tab-icon--chat svg {
  width: 30px;
  height: 30px;
}

.settings-redesign-content {
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: visible;
  display: flex;
  flex-direction: column;
  position: relative;
}

.settings-redesign-content-header {
  height: 46px;
  flex: 0 0 46px;
  display: flex;
  align-items: center;
}

.settings-redesign-content-header h2 {
  margin: 0;
  color: var(--text-primary);
  font-size: 28px;
  line-height: 1;
  letter-spacing: -0.045em;
  font-weight: 730;
}

.settings-redesign-title-line {
  height: 1px;
  background: var(--theme-control-border);
  margin: 22px 0 8px;
  flex: 0 0 1px;
}

.settings-redesign-scroll {
  min-height: 0;
  flex: 1 1 auto;
  overflow-y: auto;
  overflow-x: visible;
  padding-right: 18px;
  padding-bottom: 86px;
  scroll-padding-bottom: 86px;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.settings-redesign-tabs::-webkit-scrollbar,
.settings-redesign-scroll::-webkit-scrollbar,
.settings-floating-menu::-webkit-scrollbar,
.app-update-changelog-content::-webkit-scrollbar,
.role-editor-body::-webkit-scrollbar {
  display: none;
}

.settings-floating-menu,
.app-update-changelog-content {
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.settings-page {
  max-width: 900px;
  min-height: 0;
}

.settings-select-row,
.settings-input-row,
.settings-list-row,
.settings-action-row,
.settings-toggle-row {
  min-height: 64px;
  border-bottom: 1px solid var(--theme-control-border);
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 18px;
  padding: 11px 0;
  color: var(--text-primary);
}

.settings-list-row.tall,
.settings-input-row.stackable {
  align-items: flex-start;
}

.settings-list-row.tall {
  grid-template-columns: minmax(180px, 1fr) minmax(0, 360px);
}

.settings-row-copy {
  min-width: 0;
  display: block;
}

.settings-row-title {
  display: block;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 550;
  letter-spacing: -0.01em;
  line-height: 1.32;
}

.settings-row-desc {
  display: block;
  margin-top: 3px;
  color: var(--text-secondary);
  font-size: 12.5px;
  line-height: 1.38;
}

.settings-select-wrap {
  position: relative;
  display: inline-flex;
  justify-self: end;
}

.settings-select-button {
  width: max-content;
  min-width: 0;
  height: 36px;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: var(--text-primary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 8px 0 10px;
  cursor: pointer;
  font-size: 15px;
  white-space: nowrap;
}

.settings-select-button:hover {
  background: var(--hover-bg);
}

.settings-select-button .select-chevron {
  width: 12px;
  height: 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.15s ease;
}

.settings-select-button .select-chevron::before {
  content: '';
  width: 7px;
  height: 7px;
  border-right: 1.5px solid currentColor;
  border-bottom: 1.5px solid currentColor;
  transform: translateY(-1.5px) rotate(45deg);
}

.settings-select-wrap.open .settings-select-button .select-chevron {
  transform: rotate(180deg);
}

.settings-floating-menu {
  position: fixed;
  right: auto;
  top: auto;
  width: 300px;
  max-height: calc(44px * 4 + 12px);
  overflow-y: auto;
  overflow-x: hidden;
  padding: 6px;
  border-radius: 20px;
  background: var(--settings-floating-menu-bg);
  border: 1px solid var(--theme-control-border);
  box-shadow: var(--settings-floating-menu-shadow);
  opacity: 1;
  backdrop-filter: none;
  z-index: 2147483647;
  display: none;
}

.settings-select-wrap.open .settings-floating-menu {
  display: block;
}

.settings-floating-menu.dark {
  background: var(--surface-panel) !important;
  box-shadow: none !important;
}

.settings-floating-menu.dark .settings-menu-option:hover {
  background: var(--hover-bg) !important;
}

:global(html[data-theme='dark']) :global(.settings-floating-menu),
:global(body[data-theme='dark']) :global(.settings-floating-menu) {
  background: var(--surface-panel) !important;
  box-shadow: none !important;
}

:global(html[data-theme='dark']) :global(.settings-menu-option:hover),
:global(body[data-theme='dark']) :global(.settings-menu-option:hover) {
  background: var(--hover-bg) !important;
}

.settings-menu-option {
  width: 100%;
  min-height: 38px;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: var(--text-primary);
  display: grid;
  grid-template-columns: minmax(0, 1fr) 22px;
  row-gap: 0;
  column-gap: 8px;
  align-items: center;
  padding: 7px 10px;
  text-align: left;
  cursor: pointer;
}

.settings-menu-option:hover {
  background: var(--settings-floating-menu-hover);
}

.settings-menu-option:disabled {
  opacity: 0.48;
  cursor: not-allowed;
}

.settings-menu-option strong {
  grid-column: 1;
  grid-row: 1;
  display: block;
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 550;
  letter-spacing: -0.01em;
  min-width: 0;
}

.settings-menu-option span {
  grid-column: 1;
  grid-row: 2;
  display: block;
  margin-top: 0;
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.15;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.settings-menu-option svg {
  grid-column: 2;
  grid-row: 1 / span 2;
  align-self: center;
  width: 21px;
  height: 21px;
  fill: none;
  stroke: var(--text-primary);
  stroke-width: 2.25;
  stroke-linecap: round;
  stroke-linejoin: round;
  opacity: 0;
}

.settings-menu-option.selected svg {
  opacity: 1;
}

.settings-input-row input,
.settings-add-row input,
.settings-number-row input,
.settings-number-input,
.settings-compression-grid input {
  height: 38px;
  border: 1px solid var(--theme-control-border);
  border-radius: 12px;
  background: transparent;
  color: var(--text-primary);
  padding: 0 12px;
  outline: none;
  font-size: 13px;
}

.settings-number-input {
  width: 120px;
  text-align: right;
}

.settings-section-divider {
  display: flex;
  align-items: center;
  margin: 18px 0 6px;
}
.settings-section-divider__label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  letter-spacing: 0.04em;
}
.settings-section-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  margin-left: 10px;
  background: var(--theme-control-border);
}

.settings-input-row > input {
  width: min(360px, 40vw);
  text-align: right;
}

.settings-input-row input:focus,
.settings-add-row input:focus,
.settings-number-row input:focus,
.settings-number-input:focus,
.settings-compression-grid input:focus {
  border-color: var(--text-secondary);
  box-shadow: none;
}

.settings-input-stack {
  width: min(430px, 46vw);
  display: grid;
  gap: 8px;
  justify-self: end;
}

.settings-input-stack.wide {
  width: min(360px, 42vw);
}

.settings-input-stack input {
  width: 100%;
}

.settings-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.settings-chip-row.right {
  justify-content: flex-end;
}

.settings-chip-row button,
.settings-number-row button,
.settings-add-row button,
.settings-secondary-button,
.settings-primary-button {
  border: 1px solid var(--theme-control-border);
  border-radius: 999px;
  background: transparent;
  color: var(--text-primary);
  min-height: 32px;
  padding: 0 12px;
  font-size: 12px;
  cursor: pointer;
}

.settings-chip-row button:hover,
.settings-number-row button:hover,
.settings-add-row button:hover,
.settings-secondary-button:hover {
  background: var(--hover-bg);
}

.settings-chip-row button.active,
.settings-primary-button {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--on-accent);
}

.settings-secondary-button.danger {
  color: var(--state-warning);
}

.settings-inline-actions {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.settings-inline-actions.right {
  margin-top: 10px;
}

.settings-mini-status {
  color: var(--text-secondary);
  font-size: 12px;
}

.settings-mini-status.warning {
  color: var(--state-warning);
}

.settings-add-row,
.settings-number-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.settings-add-row input,
.settings-number-row input {
  min-width: 0;
  flex: 1 1 auto;
}

.settings-textarea-row {
  min-height: 0;
  border-bottom: 1px solid var(--theme-control-border);
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 0;
  color: var(--text-primary);
}

.settings-textarea-row textarea {
  width: 100%;
  min-height: 120px;
  max-height: 320px;
  border: 1px solid var(--theme-control-border);
  border-radius: 12px;
  background: transparent;
  color: var(--text-primary);
  padding: 12px;
  outline: none;
  font-size: 13px;
  line-height: 1.55;
  resize: vertical;
  overflow-y: auto;
  font-family: inherit;
}

.settings-textarea-row textarea:focus {
  border-color: var(--text-secondary);
  box-shadow: none;
}

.settings-textarea-row textarea::placeholder {
  color: var(--text-secondary);
  opacity: 0.6;
}

.settings-toggle-row {
  cursor: pointer;
  position: relative;
}

.settings-toggle-row input[type='checkbox'] {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.settings-group-block {
  border-bottom: 1px solid var(--theme-control-border);
  padding: 14px 0 16px;
}

.settings-group-title {
  margin-bottom: 6px;
}

.settings-toggle-row.inner {
  min-height: 48px;
  padding: 7px 0 7px 18px;
  border-bottom: 0;
}

.settings-check-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 26px;
}

.settings-compression-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 14px;
  margin-top: 10px;
}

.settings-compression-grid label {
  display: grid;
  gap: 5px;
  color: var(--text-secondary);
  font-size: 12px;
}

.settings-save-bar {
  position: absolute;
  right: 18px;
  bottom: 0;
  left: 0;
  min-height: 54px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  background: transparent;
  pointer-events: none;
}

.settings-save-bar > * {
  pointer-events: auto;
}

.personal-status-group {
  min-height: 20px;
  font-size: 12px;
}

.status.success {
  color: var(--state-success);
}

.status.error,
.usage-summary-error {
  color: var(--state-warning);
}

.usage-summary-page {
  display: block;
  padding: 0;
}

.settings-stats-card,
.usage-summary-card {
  width: 100%;
  max-width: 720px;
  padding: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.usage-summary-header h3 {
  margin: 4px 0 6px;
  font-size: 20px;
}

.usage-summary-eyebrow {
  margin: 0;
  font-size: 11px;
  color: var(--accent-strong);
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.usage-summary-desc,
.usage-summary-note,
.usage-summary-meta {
  margin: 0;
  color: var(--text-secondary);
  font-size: 12px;
}

.usage-summary-grid {
  display: grid;
  gap: 10px;
}

.usage-summary-grid--tokens {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.usage-summary-grid--counts {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.usage-summary-item {
  padding: 12px 14px;
  border-radius: 14px;
  border: 1px solid var(--theme-control-border);
  background: transparent;
}

.usage-summary-item .label {
  font-size: 12px;
  color: var(--text-secondary);
}

.usage-summary-item .value {
  margin-top: 4px;
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}

.usage-summary-item .value--success {
  color: var(--state-success);
}

.usage-summary-item .value--warning {
  color: var(--state-warning);
}

.usage-summary-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.usage-summary-refresh {
  border: 1px solid var(--theme-control-border);
  border-radius: 999px;
  padding: 7px 14px;
  background: transparent;
  color: var(--text-primary);
  font-size: 12px;
  cursor: pointer;
}

.admin-monitor-page {
  display: block;
  padding: 0;
}

@media (max-width: 900px) {
  .settings-redesign-card {
    width: 100vw;
    height: 100vh;
    border-radius: 0;
    padding: 30px 24px;
  }

  .settings-close-button {
    left: 27px;
    top: 20px;
  }

  .settings-redesign-layout {
    grid-template-columns: 1fr;
    gap: 0;
  }

  .settings-redesign-tabs {
    margin-top: 68px;
    height: auto;
    padding-right: 0;
    padding-bottom: 12px;
    border-right: 0;
    border-bottom: 1px solid var(--theme-control-border);
    flex-direction: row;
    overflow: auto;
    scrollbar-width: none;
  }

  .settings-redesign-tabs::-webkit-scrollbar {
    display: none;
  }

  .settings-redesign-tab {
    width: auto;
    white-space: nowrap;
  }

  .settings-redesign-content {
    padding-top: 18px;
  }

  .settings-select-row,
  .settings-input-row,
  .settings-list-row,
  .settings-action-row,
  .settings-toggle-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }

  .settings-select-wrap,
  .settings-input-stack,
  .settings-input-row > input {
    justify-self: stretch;
    width: 100%;
  }

  .settings-select-button {
    margin-left: auto;
  }

  .settings-floating-menu {
    width: min(340px, calc(100vw - 48px));
  }

  .settings-check-grid,
  .settings-compression-grid,
  .usage-summary-grid--tokens,
  .usage-summary-grid--counts {
    grid-template-columns: 1fr;
  }
}

/* ── 语音模型下载进度条 ── */
.voice-download-bar {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.voice-download-track {
  width: 100%;
  height: 6px;
  background: var(--surface-muted);
  border-radius: 3px;
  overflow: hidden;
}
.voice-download-fill {
  height: 100%;
  background: var(--accent-primary);
  border-radius: 3px;
  transition: width 0.3s ease;
}
</style>
