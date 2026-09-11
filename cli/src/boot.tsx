// 启动编排：连接 Gateway（host Bearer）→ 工作区检查（cwd 未注册则询问创建）→ 创建会话 → 进入主界面
// 决策（2026-09-11 用户拍板）：启动指令 astrion；cwd = 工作区，运行内不可切换；
// 未注册先提示并询问创建；多语言读 OS 设置（i18n/index.ts 模块加载即检测）。
import { useKeyboard } from '@opentui/react';
import { useEffect, useRef, useState } from 'react';
import { RGBA, TextAttributes } from '@opentui/core';
import { GatewayClient, type WorkspaceItem } from './gateway';
import { t } from './i18n';
import { BOOT_STATE, INITIAL_PATH_AUTHS, MODEL_OPTIONS, SESSIONS, WORKSPACE } from './data';
import { loadCustomModels, loadPathAuths, loadPersonalizationDefaults } from './localconfig';

/** ISO 时间 → 相对时间短文案（会话列表 when 列） */
function relativeTime(iso: unknown): string {
  const ts = Date.parse(String(iso ?? ''));
  if (!Number.isFinite(ts)) return '';
  const diffMin = Math.floor((Date.now() - ts) / 60000);
  if (diffMin < 1) return t('time.justNow');
  if (diffMin < 60) return `${diffMin} ${t('time.minutesAgo')}`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour} ${t('time.hoursAgo')}`;
  return `${Math.floor(diffHour / 24)} ${t('time.daysAgo')}`;
}

/** 启动数据加载：本地配置（模型清单/个性化默认/路径授权）+ Gateway 会话列表。
 *  本地配置与读 token 文件同一安全语义（host 本机单人）；会话列表失败不阻塞启动。 */
async function loadBootData(gw: GatewayClient): Promise<void> {
  if (MODEL_OPTIONS.length === 0) MODEL_OPTIONS.push(...loadCustomModels());
  const prefs = loadPersonalizationDefaults();
  BOOT_STATE.model = prefs.model || MODEL_OPTIONS[0]?.name || '';
  BOOT_STATE.thinking = prefs.thinking;
  BOOT_STATE.effort = prefs.effort;
  BOOT_STATE.workMode = prefs.workMode;
  BOOT_STATE.permMode = prefs.permMode;
  BOOT_STATE.autoDeepCompress = prefs.autoDeepCompress;
  BOOT_STATE.deepCompressLimit = prefs.deepCompressLimit;
  if (INITIAL_PATH_AUTHS.length === 0) INITIAL_PATH_AUTHS.push(...loadPathAuths());
  try {
    const list = await gw.listSessions(20);
    for (const c of list) {
      const id = String(c?.id ?? c?.conversation_id ?? '');
      if (!id) continue;
      SESSIONS.push({ id, title: String(c?.title || t('session.untitled')), when: relativeTime(c?.updated_at) });
    }
  } catch {
    // 列表失败不阻塞启动（/session 面板将只显示当前新会话）
  }
}

const FG = RGBA.defaultForeground();
const DIM = TextAttributes.DIM;
const INVERSE = TextAttributes.INVERSE;

export interface BootResult {
  gateway: GatewayClient;
  conversationId: string;
  workspace: WorkspaceItem;
}

type Stage = 'connecting' | 'confirm' | 'creating' | 'failed';

/** 路径归一化比较（末尾斜杠差异抹平） */
function samePath(a: string, b: string): boolean {
  const norm = (p: string) => p.replace(/\/+$/, '');
  return norm(a) === norm(b);
}

export function BootFlow({ cwd, onReady, onExit }: { cwd: string; onReady: (r: BootResult) => void; onExit: (code: number) => void }) {
  const [stage, setStage] = useState<Stage>('connecting');
  const [error, setError] = useState('');
  const [phase, setPhase] = useState<'connecting' | 'starting'>('connecting');
  const [sel, setSel] = useState(0); // 确认面板选项：0=创建 1=取消
  const gwRef = useRef(new GatewayClient());
  const stageRef = useRef(stage);
  stageRef.current = stage;
  const selRef = useRef(sel);
  selRef.current = sel;

  const createSessionAndReady = async (workspace: WorkspaceItem) => {
    const gw = gwRef.current;
    gw.workspaceId = workspace.workspace_id;
    WORKSPACE.id = workspace.workspace_id;
    WORKSPACE.name = workspace.label;
    WORKSPACE.path = workspace.path;
    setStage('creating');
    try {
      await loadBootData(gw);
      const session = await gw.createSession();
      // 新会话置顶并标 current（menuState 初值读 SESSIONS）
      for (const s of SESSIONS) s.current = false;
      SESSIONS.unshift({ id: session.conversation_id, title: t('session.new'), when: t('time.justNow'), current: true });
      onReady({ gateway: gw, conversationId: session.conversation_id, workspace });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStage('failed');
    }
  };

  // 启动主流程：确保 Gateway 可用（未运行则自启动服务）→ 列工作区 → 命中 cwd 直接建会话，未命中进入询问
  useEffect(() => {
    let cancelled = false;
    const fail = (err: unknown) => {
      if (cancelled) return;
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg === 'stale_server' ? t('boot.staleServer') : msg === 'start_timeout' ? t('boot.startTimeout') : msg);
      setStage('failed');
    };
    (async () => {
      const gw = gwRef.current;
      try {
        await gw.ensureRunning(cwd, (p) => {
          if (!cancelled) setPhase(p);
        });
        const res = await gw.listWorkspaces();
        if (cancelled) return;
        const existing = (res.workspaces ?? []).find((ws) => samePath(ws.path, cwd));
        if (existing) {
          await createSessionAndReady(existing);
        } else {
          setStage('confirm');
        }
      } catch (err) {
        fail(err);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cwd]);

  useKeyboard((key) => {
    const st = stageRef.current;
    if (st === 'failed') {
      onExit(1);
      return;
    }
    if (st !== 'confirm') return;
    if (key.name === 'left' || key.name === 'right') {
      key.preventDefault();
      setSel((s) => (s === 0 ? 1 : 0));
      return;
    }
    if (key.name === 'return') {
      key.preventDefault();
      if (selRef.current === 0) {
        void (async () => {
          setStage('creating');
          try {
            const res = await gwRef.current.createWorkspace(cwd);
            await createSessionAndReady(res.workspace);
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
            setStage('failed');
          }
        })();
      } else {
        onExit(0);
      }
      return;
    }
    if (key.name === 'escape') {
      onExit(0);
    }
  });

  return (
    <box flexDirection="column" paddingX={1} paddingY={1}>
      {stage === 'connecting' ? <text fg={FG}>{phase === 'starting' ? t('boot.starting') : t('boot.connecting')}</text> : null}
      {stage === 'creating' ? <text fg={FG}>{t('boot.workspace.creating')}</text> : null}
      {stage === 'failed' ? (
        <box flexDirection="column">
          <text fg="#e06c75">{error}</text>
          <text fg={FG}>{' '}</text>
          <text fg={FG} attributes={DIM}>
            {t('boot.hint.navigate')}
          </text>
        </box>
      ) : null}
      {stage === 'confirm' ? (
        <box flexDirection="column">
          <text fg={FG}>{t('boot.workspace.notRegistered')}</text>
          <text fg={FG}>{`  ${cwd}`}</text>
          <text fg={FG}>{t('boot.workspace.askCreate')}</text>
          <text fg={FG}>{' '}</text>
          <box flexDirection="row">
            <text fg={FG}>{'  '}</text>
            <text fg={FG} attributes={sel === 0 ? INVERSE : DIM}>
              {t('boot.workspace.create')}
            </text>
            <text fg={FG}>{'  '}</text>
            <text fg={FG} attributes={sel === 1 ? INVERSE : DIM}>
              {t('boot.workspace.cancel')}
            </text>
          </box>
          <text fg={FG}>{' '}</text>
          <text fg={FG} attributes={DIM}>
            {t('boot.hint.navigate')}
          </text>
        </box>
      ) : null}
    </box>
  );
}
