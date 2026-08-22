import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Text, useApp, useInput, useStdout } from 'ink';
import { setTimeout as sleep } from 'node:timers/promises';
import { slashCommands, filterCommands } from './commands.js';
import { createWorkspaceForPath, findWorkspaceByPath, loadWorkspaceCatalog, repoRoot } from './workspaces.js';
import type { CliStatus, ExecutionMode, ModelDefinition, PermissionMode, RunMode, SkillDefinition, SlashCommand, TimelineItem, Workspace, WorkspaceCatalog } from './types.js';
import { nowSessionId, shortPath } from './format.js';
import { Composer, HelpPanel, Picker, SkillHint, SlashMenu, StatusLine, StatusPanel, Timeline, WelcomePanel, WorkspacePrompt } from './components.js';
import { ApiClient, createDefaultApiClient } from './api.js';
import { createEventRenderState, reduceTaskEvent } from './eventMapper.js';

type InputMode = 'workspace_prompt' | 'composer' | 'slash_menu' | 'picker' | 'status_panel' | 'help_panel' | 'approval';

type PickerOption = {
  label: string;
  description?: string;
  disabled?: boolean;
  onSelect: () => void | Promise<void>;
};

type ApprovalOption = {
  label: string;
  description?: string;
  decision: 'approved' | 'rejected';
};

const cwd = process.cwd();
const initialCatalog = loadWorkspaceCatalog();
const matchedWorkspace = findWorkspaceByPath(initialCatalog, cwd);

function id(): string {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function initialStatus(workspace: Workspace): CliStatus {
  return {
    model: '连接中',
    runMode: 'thinking',
    directory: workspace.path,
    workspace,
    permissionMode: 'auto_approval',
    executionMode: 'sandbox',
    sessionId: nowSessionId(),
    contextUsed: 0,
    contextLimit: 0,
    versioningEnabled: false,
    backgroundAgents: 0,
    backgroundCommands: 0,
  };
}

function workspaceFromApi(item: any): Workspace {
  return {
    workspace_id: String(item.workspace_id || item.id || item.label || 'workspace'),
    label: String(item.label || item.workspace_id || 'workspace'),
    path: String(item.path || ''),
  };
}

export default function App() {
  const { exit } = useApp();
  const { stdout } = useStdout();
  const apiRef = useRef<ApiClient | null>(null);
  const runningTaskRef = useRef<string | null>(null);
  const currentConversationRef = useRef<string | undefined>(undefined);
  const bootstrappedRef = useRef(false);
  const [catalog, setCatalog] = useState<WorkspaceCatalog>(initialCatalog);
  const [mode, setMode] = useState<InputMode>(matchedWorkspace ? 'composer' : 'workspace_prompt');
  const [workspacePromptIndex, setWorkspacePromptIndex] = useState(0);
  const [input, setInput] = useState('');
  const [slashIndex, setSlashIndex] = useState(0);
  const [pickerTitle, setPickerTitle] = useState('');
  const [pickerOptions, setPickerOptions] = useState<PickerOption[]>([]);
  const [pickerIndex, setPickerIndex] = useState(0);
  const [approvalTitle, setApprovalTitle] = useState('');
  const [approvalId, setApprovalId] = useState('');
  const [approvalOptions] = useState<ApprovalOption[]>([
    { label: '允许一次', decision: 'approved' },
    { label: '拒绝', decision: 'rejected' },
  ]);
  const [approvalIndex, setApprovalIndex] = useState(0);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [status, setStatus] = useState<CliStatus>(() => initialStatus(matchedWorkspace || initialCatalog.workspaces[0]!));
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [running, setRunning] = useState(false);
  const [runningStartedAt, setRunningStartedAt] = useState<number | null>(null);
  const [nowMs, setNowMs] = useState(Date.now());
  const [workingTextTick, setWorkingTextTick] = useState(0);
  const [workingDotTick, setWorkingDotTick] = useState(0);
  const [timelineDotTick, setTimelineDotTick] = useState(0);
  const [layoutRefreshTick, setLayoutRefreshTick] = useState(0);
  const [models, setModels] = useState<ModelDefinition[]>([]);
  const [skills, setSkills] = useState<SkillDefinition[]>([]);
  const [permissionOptions, setPermissionOptions] = useState<PermissionMode[]>([]);
  const [executionOptions, setExecutionOptions] = useState<ExecutionMode[]>([]);

  const commandMatches = useMemo(() => filterCommands(input), [input]);
  const runningSeconds = runningStartedAt ? Math.max(0, Math.floor((nowMs - runningStartedAt) / 1000)) : 0;
  const hasRunningTimeline = useMemo(() => timeline.some((item) => item.status === 'running'), [timeline]);
  const slashMenuVisibleCount = mode === 'slash_menu' ? Math.max(1, Math.min(5, commandMatches.length)) : 0;
  const composerCursorYOffset = mode === 'slash_menu' ? 1 + slashMenuVisibleCount : 0;

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      setNowMs(Date.now());
    }, 1000);
    return () => clearInterval(timer);
  }, [running]);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      setWorkingTextTick((value) => value + 1);
    }, 100);
    return () => clearInterval(timer);
  }, [running]);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      setWorkingDotTick((value) => value + 1);
    }, 800);
    return () => clearInterval(timer);
  }, [running]);

  useEffect(() => {
    if (!hasRunningTimeline) return;
    let timer: ReturnType<typeof setInterval> | undefined;
    const delay = setTimeout(() => {
      setTimelineDotTick((value) => value + 1);
      timer = setInterval(() => {
        setTimelineDotTick((value) => value + 1);
      }, 800);
    }, 400);
    return () => {
      clearTimeout(delay);
      if (timer) clearInterval(timer);
    };
  }, [hasRunningTimeline]);

  useEffect(() => {
    if (mode !== 'composer' || bootstrappedRef.current) return;
    bootstrappedRef.current = true;
    getApi()
      .then((api) => bootstrapConversation(api))
      .catch((err) => addItem({ kind: 'tool', title: '初始化失败', status: 'failed', body: String(err.message || err) }));
  }, [mode]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setLayoutRefreshTick((v) => v + 1);
    }, 0);
    return () => clearTimeout(timer);
  }, [mode, commandMatches.length, running, input.length, timeline.length]);

  function addItem(item: Omit<TimelineItem, 'id'>) {
    setTimeline((prev) => [...prev, { id: id(), ...item }]);
  }

  function patchStatusFromServer(raw: any, workspaceOverride?: Workspace) {
    const execMode = typeof raw?.execution_mode === 'object' ? raw.execution_mode.mode : raw?.execution_mode;
    const contextUsed = Number(raw?.context?.total_size || raw?.context_tokens || raw?.token_statistics?.current_context_tokens || 0);
    setStatus((prev) => ({
      ...prev,
      model: raw?.model_key || prev.model,
      runMode: raw?.run_mode || prev.runMode,
      directory: raw?.project_path || workspaceOverride?.path || prev.directory,
      workspace: workspaceOverride || prev.workspace,
      permissionMode: raw?.permission_mode || prev.permissionMode,
      executionMode: execMode || prev.executionMode,
      sessionId: raw?.conversation?.current_id || prev.sessionId,
      contextUsed: Number.isFinite(contextUsed) && contextUsed > 0 ? contextUsed : prev.contextUsed,
    }));
    if (raw?.conversation?.current_id) currentConversationRef.current = raw.conversation.current_id;
  }

  async function getApi(): Promise<ApiClient> {
    if (!apiRef.current) apiRef.current = createDefaultApiClient(cwd, repoRoot);
    if (!connected && !connecting) {
      setConnecting(true);
      try {
        await apiRef.current.ensureConnected();
        setConnected(true);
      } finally {
        setConnecting(false);
      }
    } else if (!connected) {
      while (!connected && connecting) await sleep(100);
    }
    return apiRef.current;
  }

  async function syncBackendState(api = apiRef.current) {
    if (!api) return;
    try {
      const wsData = await api.listWorkspaces();
      const workspaces = (wsData.workspaces || []).map(workspaceFromApi);
      const current = workspaces.find((ws) => ws.workspace_id === wsData.current_workspace_id) || workspaces.find((ws) => ws.path === status.workspace.path) || status.workspace;
      setCatalog({ default_workspace_id: wsData.default_workspace_id, workspaces });
      const rawStatus = await api.getStatus();
      patchStatusFromServer(rawStatus, current);
      const conv = await api.getCurrentConversation().catch(() => null);
      const convId = conv?.id || rawStatus?.conversation?.current_id;
      if (convId) currentConversationRef.current = convId;
      await syncRuntimeCatalogs(api, convId).catch(() => undefined);
    } catch (err) {
      addItem({ kind: 'tool', title: '同步后端状态失败', status: 'failed', body: String((err as Error).message || err) });
    }
  }

  async function syncRuntimeCatalogs(api: ApiClient, conversationId?: string) {
    const [modelItems, personalization, permission, execution, subAgents, backgroundCommands, tokens, versioning] = await Promise.all([
      api.listModels().catch(() => []),
      api.getPersonalization().catch(() => null),
      api.getPermissionMode().catch(() => null),
      api.getExecutionMode().catch(() => null),
      api.listSubAgents().catch(() => []),
      api.listBackgroundCommands().catch(() => []),
      conversationId ? api.getConversationTokens(conversationId).catch(() => null) : Promise.resolve(null),
      conversationId ? api.getConversationVersioning(conversationId).catch(() => null) : Promise.resolve(null),
    ]);
    if (modelItems.length) setModels(modelItems);
    const enabled = new Set((personalization?.data?.enabled_skills || []).map(String));
    const skillItems = (personalization?.skills_catalog || [])
      .filter((skill: any) => !enabled.size || enabled.has(String(skill.id)))
      .map((skill: any) => ({ id: String(skill.id), label: String(skill.label || skill.id), description: String(skill.description || '') }));
    if (skillItems.length) setSkills(skillItems);
    const rawCompressionLimit = Number(
      personalization?.context_compression_settings?.context_window_tokens
      || personalization?.context_compression_settings?.deep_trigger_tokens
      || personalization?.data?.deep_compress_trigger_tokens
      || 0,
    );
    const currentModel = modelItems.find((item) => item.model_key === status.model);
    const modelWindow = Number(currentModel?.context_window || 0);
    const compressionLimit = rawCompressionLimit > 0 && modelWindow > 0
      ? Math.min(rawCompressionLimit, modelWindow)
      : rawCompressionLimit || modelWindow;
    if (Array.isArray(permission?.options)) setPermissionOptions(permission.options);
    if (Array.isArray(execution?.options)) setExecutionOptions(execution.options);
    const runningAgents = subAgents.filter((item: any) => !['completed', 'failed', 'cancelled', 'terminated', 'timeout'].includes(String(item.status || '').toLowerCase())).length;
    const runningCommands = backgroundCommands.filter((item: any) => !['completed', 'failed', 'cancelled', 'timeout'].includes(String(item.status || '').toLowerCase())).length;
    const tokenUsed = Number(tokens?.total_tokens || 0);
    setStatus((prev) => ({
      ...prev,
      contextUsed: Number.isFinite(tokenUsed) && tokenUsed > 0 ? tokenUsed : prev.contextUsed,
      contextLimit: Number.isFinite(compressionLimit) && compressionLimit > 0 ? compressionLimit : prev.contextLimit,
      versioningEnabled: versioning ? Boolean(versioning.enabled) : prev.versioningEnabled,
      backgroundAgents: runningAgents,
      backgroundCommands: runningCommands,
    }));
  }

  async function bootstrapConversation(api: ApiClient) {
    const result = await api.createConversation();
    const convId = result.conversation_id || result.data?.conversation_id || result.data?.id || result.id;
    if (convId) {
      currentConversationRef.current = String(convId);
      setStatus((prev) => ({ ...prev, sessionId: String(convId), contextUsed: 0 }));
      await syncBackendState(api);
      await syncRuntimeCatalogs(api, String(convId)).catch(() => undefined);
    }
  }

  function openPicker(title: string, options: PickerOption[]) {
    setPickerTitle(title);
    setPickerOptions(options);
    setPickerIndex(0);
    setMode('picker');
  }

  function closeOverlay() {
    setMode('composer');
    setInput('');
    setSlashIndex(0);
    setPickerIndex(0);
  }

  async function executeCommand(command: SlashCommand) {
    const name = command.name;
    try {
      if (name === '/new') {
        const api = await getApi();
        const result = await api.createConversation();
        const convId = result.conversation_id || result.data?.conversation_id;
        currentConversationRef.current = convId;
        setTimeline([]);
        setStatus((prev) => ({ ...prev, sessionId: convId || nowSessionId(), contextUsed: 0 }));
        if (convId) await syncRuntimeCatalogs(api, String(convId)).catch(() => undefined);
        closeOverlay();
        return;
      }
      if (name === '/resume') {
        const api = await getApi();
        const conversations = await api.listConversations(30);
        openPicker('Resume conversation', conversations.map((conv: any) => ({
          label: String(conv.title || conv.id || '未命名对话'),
          description: String(conv.updated_at || conv.created_at || conv.id || ''),
          onSelect: async () => {
            await api.loadConversation(String(conv.id));
            currentConversationRef.current = String(conv.id);
            setStatus((prev) => ({ ...prev, sessionId: String(conv.id) }));
            await syncRuntimeCatalogs(api, String(conv.id)).catch(() => undefined);
            addItem({ kind: 'system', body: `已切换对话：${conv.title || conv.id}` });
            closeOverlay();
          },
        })));
        return;
      }
      if (name === '/model') {
        const api = await getApi();
        const modelItems = (await api.listModels().catch(() => models)).filter((item) => item.model_key);
        if (modelItems.length) setModels(modelItems);
        const currentRunMode = status.runMode;
        openPicker('Model', modelItems.map((model) => ({
          label: model.name || model.model_key,
          description: `${model.model_key === status.model ? 'current · ' : ''}${model.description || model.model_key}`,
          onSelect: async () => {
            await api.setModel(model.model_key);
            setStatus((prev) => ({ ...prev, model: model.model_key, contextLimit: Number(model.context_window) || prev.contextLimit }));
            addItem({ kind: 'system', body: `已切换模型：${model.name || model.model_key}` });
            const modes = getRunModesForModel(model);
            openPicker('Run Mode', modes.map((runMode) => ({
              label: runMode,
              description: runMode === currentRunMode ? 'current' : '',
              onSelect: async () => {
                await api.setRunMode(runMode);
                setStatus((prev) => ({ ...prev, runMode }));
                await syncBackendState(api);
                addItem({ kind: 'system', body: `已切换运行模式：${runMode}` });
                closeOverlay();
              },
            })));
          },
        })));
        return;
      }
      if (name === '/versioning') {
        const api = await getApi();
        const convId = currentConversationRef.current || status.sessionId;
        const current = await api.getConversationVersioning(convId).catch(() => null);
        const enabled = current ? Boolean(current.enabled) : status.versioningEnabled;
        const trackingMode = current?.tracking_mode;
        openPicker('Versioning', [
          { label: enabled ? '关闭版本控制' : '开启版本控制', onSelect: async () => {
            const result = await api.setConversationVersioning(convId, !enabled, trackingMode);
            setStatus((prev) => ({ ...prev, versioningEnabled: Boolean(result.enabled) }));
            addItem({ kind: 'system', body: `版本控制：${result.enabled ? '开' : '关'}` });
            closeOverlay();
          } },
        ]);
        return;
      }
      if (name === '/permission') {
        const api = await getApi();
        const current = await api.getPermissionMode().catch(() => null);
        const modes: PermissionMode[] = Array.isArray(current?.options) && current.options.length ? current.options : (permissionOptions.length ? permissionOptions : [status.permissionMode]);
        openPicker('Permission Mode', modes.map((permissionMode) => ({ label: permissionMode, description: permissionMode === status.permissionMode ? 'current' : '', onSelect: async () => {
          await api.setPermissionMode(permissionMode);
          setStatus((prev) => ({ ...prev, permissionMode }));
          addItem({ kind: 'system', body: `权限模式：${permissionMode}` });
          closeOverlay();
        } })));
        return;
      }
      if (name === '/execution') {
        const api = await getApi();
        const current = await api.getExecutionMode().catch(() => null);
        const modes: ExecutionMode[] = Array.isArray(current?.options) && current.options.length ? current.options : (executionOptions.length ? executionOptions : [status.executionMode]);
        openPicker('Execution Environment', modes.map((executionMode) => ({ label: executionMode, description: executionMode === status.executionMode ? 'current' : executionMode === 'direct' ? '高风险' : '', onSelect: async () => {
          await api.setExecutionMode(executionMode);
          setStatus((prev) => ({ ...prev, executionMode }));
          addItem({ kind: 'system', body: `执行环境：${executionMode}` });
          closeOverlay();
        } })));
        return;
      }
      if (name === '/workspace') {
        const api = await getApi();
        const wsData = await api.listWorkspaces();
        const workspaces = (wsData.workspaces || []).map(workspaceFromApi);
        setCatalog({ default_workspace_id: wsData.default_workspace_id, workspaces });
        openPicker('Workspace', workspaces.map((workspace) => ({ label: workspace.label, description: `${shortPath(workspace.path)}${workspace.workspace_id === wsData.current_workspace_id ? ' current' : ''}`, onSelect: async () => {
          await api.selectWorkspace(workspace.workspace_id);
          setStatus((prev) => ({ ...prev, workspace, directory: workspace.path }));
          addItem({ kind: 'system', body: `已切换工作区：${workspace.label}` });
          await syncBackendState(api);
          closeOverlay();
        } })));
        return;
      }
      if (name === '/compact') {
        const api = await getApi();
        const convId = currentConversationRef.current || status.sessionId;
        const result = await api.compressConversation(convId);
        addItem({ kind: 'tool', title: '对话已压缩', body: result.compact_file || '', status: 'success' });
        if (result.compressed_conversation_id) {
          currentConversationRef.current = result.compressed_conversation_id;
          setStatus((prev) => ({ ...prev, sessionId: result.compressed_conversation_id }));
        }
        closeOverlay();
        return;
      }
      if (name === '/tools') {
        const api = await getApi();
        const snapshot = await api.getToolSettings();
        const categories = snapshot.categories || {};
        openPicker('Tools', Object.entries(categories).map(([key, value]: [string, any]) => ({ label: `${value.enabled ? '[x]' : '[ ]'} ${key}`, description: value.label || '', onSelect: async () => {
          await api.setToolCategory(key, !value.enabled);
          addItem({ kind: 'system', body: `工具 ${key}：${!value.enabled ? '启用' : '禁用'}` });
          closeOverlay();
        } })));
        return;
      }
      if (name === '/path') {
        const api = await getApi();
        const auth = await api.getPathAuthorization();
        openPicker('Authorized Paths', [
          { label: '当前可读写路径', description: (auth.writable_paths || []).join(', ') || '无', onSelect: () => closeOverlay() },
          { label: '当前只读路径', description: (auth.readable_extra_paths || []).join(', ') || '无', onSelect: () => closeOverlay() },
        ]);
        return;
      }
      if (name === '/skill') {
        const api = await getApi();
        const personalization = await api.getPersonalization().catch(() => null);
        const enabled = new Set((personalization?.data?.enabled_skills || []).map(String));
        const skillItems = (personalization?.skills_catalog || skills)
          .filter((skill: any) => !enabled.size || enabled.has(String(skill.id)))
          .map((skill: any) => ({ id: String(skill.id), label: String(skill.label || skill.id), description: String(skill.description || '') }));
        if (skillItems.length) setSkills(skillItems);
        openPicker('Skill', skillItems.map((skill: SkillDefinition) => ({ label: skill.label, description: skill.description, onSelect: () => {
          setStatus((prev) => ({ ...prev, activeSkill: skill }));
          addItem({ kind: 'system', body: `下一条消息将使用 skill：${skill.label}` });
          closeOverlay();
        } })).concat(skillItems.length ? [] : [{ label: '暂无可用 skill', disabled: true, onSelect: () => closeOverlay() }]));
        return;
      }
      if (name === '/status') {
        await getApi().then((api) => syncBackendState(api)).catch(() => undefined);
        setMode('status_panel');
        setInput('');
        return;
      }
      if (name === '/help') {
        setMode('help_panel');
        setInput('');
        return;
      }
      if (name === '/clear') {
        setTimeline([]);
        closeOverlay();
        return;
      }
      if (name === '/exit') {
        if (runningTaskRef.current) await apiRef.current?.cancelTask(runningTaskRef.current).catch(() => undefined);
        exit();
      }
    } catch (err) {
      addItem({ kind: 'tool', title: `命令失败：${name}`, status: 'failed', body: String((err as Error).message || err) });
      closeOverlay();
    }
  }

  async function submitUserMessage(text: string) {
    addItem({ kind: 'user', body: text });
    const skill = status.activeSkill;
    const message = skill ? `${text}\n\n先阅读 ${skill.label} skill` : text;
    if (skill) addItem({ kind: 'user', body: `先阅读 ${skill.label} skill` });
    setStatus((prev) => ({ ...prev, activeSkill: undefined }));
    setRunning(true);
    setRunningStartedAt(Date.now());
    try {
      const api = await getApi();
      const task = await api.createTask({ message, conversation_id: currentConversationRef.current, model_key: status.model, run_mode: status.runMode });
      runningTaskRef.current = task.task_id;
      if (task.conversation_id) currentConversationRef.current = task.conversation_id;
      await pollTask(api, task.task_id);
      await syncBackendState(api);
    } catch (err) {
      addItem({ kind: 'tool', title: '发送失败', status: 'failed', body: String((err as Error).message || err) });
    } finally {
      runningTaskRef.current = null;
      setRunning(false);
      setRunningStartedAt(null);
    }
  }

  async function pollTask(api: ApiClient, taskId: string) {
    let offset = 0;
    const renderState = createEventRenderState();
    for (;;) {
      const result = await api.pollTask(taskId, offset);
      offset = result.next_offset;
      for (const event of result.events || []) {
        if (event.type === 'tool_approval_required') {
          const approval = event.data?.approval || event.data;
          if (approval?.approval_id) {
            setApprovalId(String(approval.approval_id));
            setApprovalTitle(`需要审批：${approval.tool_name || '工具调用'}`);
            setApprovalIndex(0);
            setMode('approval');
          }
        }
        setTimeline((prev) => reduceTaskEvent(prev, renderState, event));
      }
      if (!['pending', 'running', 'cancel_requested'].includes(String(result.status))) break;
      await sleep(700);
    }
  }

  useInput((chunk, key) => {
    void handleInput(chunk, key);
  });

  function handleComposerChange(next: string) {
    setInput(next);
    if (next.startsWith('/')) {
      if (mode !== 'slash_menu') setMode('slash_menu');
      setSlashIndex(0);
    } else if (mode === 'slash_menu') {
      setMode('composer');
      setSlashIndex(0);
    }
  }

  async function handleComposerSubmit(raw: string) {
    const text = String(raw || '').trim();
    if (!text) return;
    if (text.startsWith('/')) {
      const command = filterCommands(text)[0];
      if (command) await executeCommand(command);
      return;
    }
    setInput('');
    await submitUserMessage(text);
  }

  async function handleInput(chunk: string, key: any) {
    if (mode === 'workspace_prompt') {
      if (key.upArrow || chunk === 'k') setWorkspacePromptIndex(0);
      else if (key.downArrow || chunk === 'j') setWorkspacePromptIndex(1);
      else if (key.return) {
        if (workspacePromptIndex === 0) {
          const result = createWorkspaceForPath(catalog, cwd);
          setCatalog(result.catalog);
          setStatus((prev) => ({ ...prev, workspace: result.workspace, directory: result.workspace.path }));
          bootstrappedRef.current = true;
          setMode('composer');
          getApi().then(async (api) => {
            await api.createWorkspace(result.workspace.path, result.workspace.label).catch(() => undefined);
            const wsData = await api.listWorkspaces().catch(() => null);
            const backendWs = wsData?.workspaces?.find((ws: any) => String(ws.path) === result.workspace.path);
            if (backendWs) await api.selectWorkspace(String(backendWs.workspace_id));
            await bootstrapConversation(api);
          }).catch((err) => addItem({ kind: 'tool', title: '连接后端失败', status: 'failed', body: String(err.message || err) }));
        } else {
          exit();
        }
      }
      return;
    }

    if (mode === 'approval') {
      if (key.upArrow) setApprovalIndex((prev) => Math.max(0, prev - 1));
      else if (key.downArrow) setApprovalIndex((prev) => Math.min(approvalOptions.length - 1, prev + 1));
      else if (key.return && approvalId) {
        const selected = approvalOptions[approvalIndex]!;
        await apiRef.current?.decideApproval(approvalId, selected.decision).catch((err) => addItem({ kind: 'tool', title: '审批提交失败', status: 'failed', body: String(err.message || err) }));
        addItem({ kind: 'system', body: `审批已${selected.decision === 'approved' ? '允许' : '拒绝'}` });
        setMode('composer');
      }
      return;
    }

    if (key.escape) {
      if (runningTaskRef.current) {
        await apiRef.current?.cancelTask(runningTaskRef.current).catch(() => undefined);
        addItem({ kind: 'system', body: '已请求停止当前任务。' });
        return;
      }
      closeOverlay();
      return;
    }

    if (mode === 'status_panel' || mode === 'help_panel') {
      if (key.return || chunk === 'q') closeOverlay();
      return;
    }

    if (mode === 'picker') {
      if (key.upArrow) setPickerIndex((prev) => Math.max(0, prev - 1));
      else if (key.downArrow) setPickerIndex((prev) => Math.min(pickerOptions.length - 1, prev + 1));
      else if (key.return) await pickerOptions[pickerIndex]?.onSelect();
      return;
    }

    if (key.ctrl && chunk === 'c') {
      exit();
      return;
    }

    if (mode === 'slash_menu') {
      if (key.upArrow) setSlashIndex((prev) => Math.max(0, prev - 1));
      else if (key.downArrow) setSlashIndex((prev) => Math.min(commandMatches.length - 1, prev + 1));
      else if (key.return) {
        const command = commandMatches[slashIndex];
        if (command) await executeCommand(command);
      }
      return;
    }

    if (mode === 'composer') return;

    if (key.return) {
      const text = input.trim();
      if (!text) return;
      if (text.startsWith('/')) {
        const command = filterCommands(text)[0];
        if (command) await executeCommand(command);
        return;
      }
      setInput('');
      await submitUserMessage(text);
      return;
    }

    if (key.backspace || key.delete) {
      setInput((prev) => Array.from(prev).slice(0, -1).join(''));
      return;
    }

    if (chunk && !key.ctrl && !key.meta) {
      const normalized = chunk.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
      setInput((prev) => {
        const next = prev + normalized;
        if (next.startsWith('/')) {
          setMode('slash_menu');
          setSlashIndex(0);
        }
        return next;
      });
    }
  }

  if (mode === 'workspace_prompt') {
    return <WorkspacePrompt cwd={cwd} selectedIndex={workspacePromptIndex} />;
  }

  return (
    <Box flexDirection="column" paddingX={1}>
      <Box flexDirection="column">
        {timeline.length === 0 ? <WelcomePanel status={status} /> : <Timeline items={timeline} pulseTick={timelineDotTick} width={Math.max(10, (stdout.columns || 80) - 2)} />}
        {timeline.length > 0 && timeline[timeline.length - 1]?.kind === 'assistant' ? <Text> </Text> : null}
      </Box>
      <Box flexDirection="column">
        {running ? (
          <Box marginTop={1}>
            <Text>
              <Text color={workingDotTick % 2 === 0 ? 'gray' : undefined}>{workingDotTick % 2 === 0 ? '◦' : '•'}</Text>{' '}
              <AnimatedSummary text="Working" tick={workingTextTick} />
              {` (${runningSeconds}s • 按 Esc 停止)`}
            </Text>
          </Box>
        ) : null}
        {mode === 'slash_menu' ? <SlashMenu query={input} commands={commandMatches} selectedIndex={slashIndex} /> : null}
        {mode === 'picker' ? <Picker title={pickerTitle} items={pickerOptions} selectedIndex={pickerIndex} /> : null}
        {mode === 'status_panel' ? <StatusPanel status={status} /> : null}
        {mode === 'help_panel' ? <HelpPanel commands={slashCommands} /> : null}
        {mode === 'approval' ? <Picker title={approvalTitle} items={approvalOptions} selectedIndex={approvalIndex} /> : null}
        {mode === 'composer' || mode === 'slash_menu' ? <><SkillHint skill={status.activeSkill} /><Composer value={input} disabled={running} marginTop={0} cursorYOffset={composerCursorYOffset} refreshTick={layoutRefreshTick} onChange={handleComposerChange} onSubmit={handleComposerSubmit} /></> : null}
        <StatusLine status={status} />
      </Box>
    </Box>
  );
}

function AnimatedSummary({ text, tick }: { text: string; tick: number }) {
  const chars = Array.from(text || '正在执行...');
  const cycle = chars.length + 8;
  const head = tick % cycle;
  return (
    <>
      {chars.map((char, index) => {
        const distance = head - index;
        const active = head < chars.length + 4 && distance >= 0 && distance < 5;
        return (
          <Text key={`${index}-${char}`} color={active ? undefined : 'gray'}>
            {char === ' ' ? '\u00A0' : char}
          </Text>
        );
      })}
    </>
  );
}

function getRunModesForModel(model?: ModelDefinition): RunMode[] {
  if (!model) return ['fast', 'thinking', 'deep'];
  if (model.deep_only) return ['deep'];
  if (model.fast_only || !model.supports_thinking) return ['fast'];
  return ['fast', 'thinking', 'deep'];
}
