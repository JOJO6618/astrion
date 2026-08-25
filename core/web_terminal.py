# core/web_terminal.py - Web终端（集成对话持久化）

import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable, TYPE_CHECKING
import os
from core.main_terminal import MainTerminal
from server.utils_common import debug_log
from utils.logger import setup_logger
from modules.personalization_manager import load_personalization_config
from modules.versioning_manager import ConversationVersioningManager, VersioningError
from utils.perf_log import perf_log, PerfTimer
try:
    from config import MAX_TERMINALS, TERMINAL_BUFFER_SIZE, TERMINAL_DISPLAY_SIZE, REASONING_EFFORT_LEVELS
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import MAX_TERMINALS, TERMINAL_BUFFER_SIZE, TERMINAL_DISPLAY_SIZE, REASONING_EFFORT_LEVELS
from modules.terminal_manager import TerminalManager

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle

logger = setup_logger(__name__)

class WebTerminal(MainTerminal):
    """Web版本的终端，继承自MainTerminal，包含对话持久化功能"""
    
    def _ensure_conversation(self):
        """确保Web端在首次进入时自动加载或创建对话。

        对话级 terminal（_bound_conversation_id 非空）：加载绑定的对话；
        加载失败不 fallback 到“最近对话”（避免串对话），保持无对话状态，
        由任务执行链路的 ensure_conversation_loaded 显式处理。
        对话级 terminal 只服务绑定对话，必须恢复该对话保存的模型
        （restore_model=True），否则重启后新建的对话级 terminal 会用默认
        模型跑任务/回写 metadata，把用户切换的模型覆盖掉。
        工作区级 terminal（未绑定）：保持原有“最近对话”行为（恢复焦点与模式），
        且刻意不恢复模型（restore_model=False），避免 /new 页面显示旧对话的模型。
        经由 load_conversation 的 attach_history 分流，工作区级不挂载消息历史——
        历史权威在磁盘 + 对话级实例（防 merge-on-save 旧内存写回污染源）。
        """
        if self.context_manager.current_conversation_id:
            return

        bound_id = getattr(self, "_bound_conversation_id", None)
        if bound_id:
            if not str(bound_id).startswith("conv_"):
                bound_id = f"conv_{bound_id}"
            result = self.load_conversation(bound_id, restore_model=True)
            if result.get("success"):
                debug_log(f"[WebTerminal] 已加载绑定对话: {bound_id}")
            else:
                logger.warning("[WebTerminal] 绑定对话 %s 加载失败: %s", bound_id, result.get('message') or result.get('error'))
            return

        latest_list = self.context_manager.get_conversation_list(limit=1, offset=0)
        conversations = latest_list.get("conversations", []) if latest_list else []

        if conversations:
            latest = conversations[0]
            conv_id = latest.get("id")
            if conv_id:
                result = self.load_conversation(conv_id, restore_model=False)
                if result.get("success"):
                    debug_log(f"[WebTerminal] 已加载最近对话: {conv_id}")
                    return

        conversation_id = self.context_manager.start_new_conversation(
            project_path=self.project_path,
            thinking_mode=self.thinking_mode
        )
        debug_log(f"[WebTerminal] 自动创建新对话: {conversation_id}")
    
    def __init__(
        self,
        project_path: str,
        thinking_mode: bool = False,
        run_mode: Optional[str] = None,
        message_callback: Optional[Callable] = None,
        data_dir: Optional[str] = None,
        container_session: Optional["ContainerHandle"] = None,
        usage_tracker: Optional[object] = None,
        conversation_id: Optional[str] = None,
    ):
        # 对话级隔离：绑定指定对话（必须在 super().__init__ 之前设置，
        # 因为父类初始化会调用 self._ensure_conversation()）
        self._bound_conversation_id = conversation_id
        # 24h TTL 回收器判定用：最近活动时间
        self.last_activity_at = time.time()
        # 对话级 terminal：广播回调注入 conversation_id，前端按对话过滤终端事件
        if message_callback is not None and conversation_id:
            _raw_callback = message_callback
            def message_callback(event_type, data, _cb=_raw_callback, _cid=conversation_id):
                if isinstance(data, dict):
                    data = dict(data)
                    data.setdefault("conversation_id", _cid)
                return _cb(event_type, data)
        # 调用父类初始化（包含对话持久化功能）
        super().__init__(
            project_path,
            thinking_mode,
            run_mode=run_mode,
            data_dir=data_dir,
            container_session=container_session,
            usage_tracker=usage_tracker
        )

        # 工作区级服务实例标记：不持有/不回写对话消息历史（历史权威在磁盘 + 对话级实例）。
        # 工作区级挂载历史只会成为 merge-on-save 的污染源（版本回溯被旧内存“救回”覆盖的事故根因）。
        # 注意必须在 super().__init__ 之后设置（context_manager 由父类创建）；
        # 而 load 分流不依赖此标记（用 _bound_conversation_id，super 之前已设），初始化时序安全。
        try:
            self.context_manager._service_instance_no_history = conversation_id is None
        except Exception:
            pass
        
        # Web特有属性
        self.message_callback = message_callback
        self.web_mode = True
        
        # 默认允许输出（api_client.web_mode=False 表示允许 _print），若需静默可设置 WEB_API_SILENT=1
        self.api_client.web_mode = bool(os.environ.get("WEB_API_SILENT"))
        
        # 复用父类已创建的 TerminalManager，仅注入广播回调。
        # 此前这里会新建一个实例顶掉父类的，旧实例被 GC 时触发 __del__ -> close_all()，
        # 导致每次创建对话级 terminal 都输出“关闭所有终端会话”噪音日志。
        if self.terminal_manager is not None:
            self.terminal_manager.broadcast = message_callback
            # 复用父类实例时也要确保 getter 已注入（父类创建时已传入，防御旧实例）
            if getattr(self.terminal_manager, "network_permission_getter", None) is None:
                self.terminal_manager.network_permission_getter = self.get_network_permission
        else:
            self.terminal_manager = TerminalManager(
                project_path=project_path,
                max_terminals=MAX_TERMINALS,
                terminal_buffer_size=TERMINAL_BUFFER_SIZE,
                terminal_display_size=TERMINAL_DISPLAY_SIZE,
                broadcast_callback=message_callback,
                container_session=self.container_session,
                network_permission_getter=self.get_network_permission,
            )
        # 让 run_command 与实时终端共享同一容器环境
        self.terminal_ops.attach_terminal_manager(self.terminal_manager)
        
        debug_log(f"[WebTerminal] 初始化完成，项目路径: {project_path}")
        debug_log(f"[WebTerminal] 初始模式: {self.run_mode}")
        debug_log(f"[WebTerminal] 对话管理已就绪")
        
        # 设置token更新回调
        if message_callback is not None:
            self.context_manager._web_terminal_callback = message_callback
            debug_log(f"[WebTerminal] 实时token统计已启用")
        else:
            logger.warning("[WebTerminal] message_callback为None，无法启用实时token统计")
    # ===========================================
    # 新增：对话管理相关方法（Web版本）
    # ===========================================
    
    def create_new_conversation(
        self,
        thinking_mode: bool = None,
        run_mode: Optional[str] = None,
        metadata_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """
        创建新对话（Web版本）

        Args:
            thinking_mode: 思考模式，None则使用当前设置
            run_mode: 显式的运行模式（fast/thinking/deep）
            metadata_overrides: 额外写入对话 metadata 的字段

        Returns:
            Dict: 包含新对话信息
        """
        perf_log("create_new_conversation enter")
        t0 = time.perf_counter()
        prefer_defaults = thinking_mode is None and run_mode is None
        thinking_mode_explicit = thinking_mode is not None
        # 新对话以 plan 模式创建时记录的「进入前权限模式」（供离开 plan 恢复）
        new_conv_pre_plan_permission = None

        # 先加载个性化默认配置（用于 start_new_conversation 的 metadata）
        prefs = {}
        preferred_model = None
        if prefer_defaults:
            try:
                prefs = load_personalization_config(self.data_dir)
            except Exception as exc:
                logger.warning("加载个性化偏好失败，将使用内置默认: %s", exc)
            preferred_model = prefs.get("default_model")
            preferred_mode = prefs.get("default_run_mode")
            preferred_permission_mode = prefs.get("default_permission_mode") or None
            preferred_effort = prefs.get("default_reasoning_effort")
            if isinstance(preferred_effort, str):
                preferred_effort = preferred_effort.strip().lower() or None
                if preferred_effort not in REASONING_EFFORT_LEVELS:
                    preferred_effort = None
            else:
                preferred_effort = None
            if preferred_permission_mode not in ("readonly", "approval", "auto_approval", "unrestricted"):
                try:
                    preferred_permission_mode = self.get_permission_mode()
                except Exception:
                    preferred_permission_mode = None
            if not isinstance(preferred_permission_mode, str) or not preferred_permission_mode.strip():
                preferred_permission_mode = "unrestricted"
            candidate_mode = preferred_mode.lower() if isinstance(preferred_mode, str) else None
            if candidate_mode == "deep":  # 旧版标识符映射
                candidate_mode = "thinking"
            if candidate_mode in {"fast", "thinking"}:
                try:
                    self.set_run_mode(candidate_mode)
                except ValueError as exc:
                    logger.warning("忽略无效默认运行模式 %s: %s", preferred_mode, exc)
            else:
                # 未配置默认模式时回到快速模式
                self.set_run_mode("fast")
            try:
                self.set_reasoning_effort(preferred_effort)
            except ValueError:
                self.set_reasoning_effort(None)
            # 运行模式（work_mode）：沿用 terminal 当前值，不用个性化默认值覆盖——
            # /new 页面的运行模式切换已同步到 terminal（_sync_workspace_terminal_mode），
            # 切换器上显示什么，新对话冻结进 metadata 的就是什么，第一条消息发送时
            # 按当时实际模式生成冻结提示词（与执行环境/网络权限的继承方式一致）。
            # 个性化 default_work_mode 仅在 terminal 首次构造时生效（tools_policy 加载）。
            current_work_mode = "plan"
            try:
                current_work_mode = self.get_work_mode()
            except Exception:
                pass
            if current_work_mode == "plan":
                # 不变量：plan ⇒ 权限必须只读。记录进入前权限（供离开 plan 恢复）并锁定。
                # 注意不能在 plan 状态下调用非只读的 set_permission_mode（plan 锁会 raise）。
                try:
                    pre_plan_perm = self.get_permission_mode()
                except Exception:
                    pre_plan_perm = "unrestricted"
                if pre_plan_perm != "readonly":
                    new_conv_pre_plan_permission = pre_plan_perm
                    try:
                        self.set_permission_mode("readonly", persist=False)
                    except Exception:
                        pass
            else:
                try:
                    self.set_permission_mode(preferred_permission_mode, persist=False)
                except Exception:
                    try:
                        self.set_permission_mode("unrestricted", persist=False)
                    except Exception:
                        pass

        if isinstance(run_mode, str):
            try:
                self.set_run_mode(run_mode)
                thinking_mode = self.thinking_mode
            except ValueError:
                logger.warning("无效的 run_mode 参数: %s", run_mode)
        elif thinking_mode_explicit:
            # run_mode 未显式传入但思考开关给定时，基于布尔值决定模式
            try:
                self.set_run_mode("thinking" if bool(thinking_mode) else "fast")
            except ValueError:
                pass

        if thinking_mode is None:
            thinking_mode = self.thinking_mode

        try:
            # 先创建新对话。start_new_conversation 会先 save_current_conversation()，
            # 此时 terminal.model_key 仍是旧对话的模型，避免把旧对话覆盖成默认模型。
            metadata_overrides_merged = {
                "permission_mode": self.get_permission_mode(),
                "execution_mode": self.get_execution_mode() if hasattr(self, "get_execution_mode") else "sandbox",
                "work_mode": self.get_work_mode() if hasattr(self, "get_work_mode") else "plan",
                "pre_plan_permission_mode": new_conv_pre_plan_permission,
                "pending_permission_mode": None,
                "pending_execution_mode": None,
                # 推理强度随创建写入一次：prefer_defaults 时 self 已应用个性化默认值；
                # 显式模式（/new 页发消息）时 self 即用户当前档位，两者都直接沿用
                "reasoning_effort": getattr(self, "reasoning_effort", None),
                # frozen_*_prompt 不在创建时预设，由第一次 build_messages 根据当时的实际模式懒加载并冻结
            }
            if isinstance(metadata_overrides, dict):
                metadata_overrides_merged.update(metadata_overrides)

            conversation_id = self.context_manager.start_new_conversation(
                project_path=self.project_path,
                thinking_mode=thinking_mode,
                run_mode=self.run_mode,
                metadata_overrides=metadata_overrides_merged,
            )

            # 新对话创建完成后再应用默认模型（此时旧对话已安全保存）。
            if prefer_defaults and preferred_model:
                # 新对话视为“干净”会话，清除图片限制便于切换模型
                self.context_manager.has_images = False
                self.context_manager.has_videos = False
                try:
                    self.set_model(preferred_model)
                except Exception as exc:
                    logger.warning("忽略无效默认模型 %s: %s", preferred_model, exc)

                # 把默认模型同步到新对话的 metadata
                try:
                    target_manager = self.context_manager._get_conversation_manager_for_id(conversation_id)
                    target_manager.save_conversation(
                        conversation_id=conversation_id,
                        messages=self.context_manager.conversation_history,
                        project_path=str(self.project_path),
                        todo_list=self.context_manager.todo_list,
                        thinking_mode=self.thinking_mode,
                        run_mode=self.run_mode,
                        model_key=self.model_key,
                        has_images=False,
                        has_videos=False,
                    )
                except Exception as exc:
                    logger.warning("保存新对话默认模型失败: %s", exc)

            perf_log("create_new_conversation before default versioning", elapsed_ms=(time.perf_counter() - t0) * 1000, extra={"conv_id": conversation_id})
            # 根据个性化设置默认开启版本控制
            versioning_initialized = False
            try:
                default_versioning_enabled = bool((prefs or {}).get("versioning_enabled_by_default", True))
                if default_versioning_enabled:
                    self._ensure_conversation_versioning_enabled(conversation_id)
                    versioning_initialized = True
            except Exception as exc:
                logger.warning("新对话应用默认版本控制失败: %s", exc)
            perf_log("create_new_conversation after default versioning", elapsed_ms=(time.perf_counter() - t0) * 1000, extra={"conv_id": conversation_id})

            self.current_session_id += 1

            perf_log("create_new_conversation done", elapsed_ms=(time.perf_counter() - t0) * 1000, extra={"conv_id": conversation_id})
            return {
                "success": True,
                "conversation_id": conversation_id,
                "message": f"已创建新对话: {conversation_id}",
                # 路由层据此跳过重复的版本控制初始化（此前每次新建对话初始化两遍）
                "versioning_initialized": versioning_initialized,
            }
        except Exception as e:
            perf_log("create_new_conversation error", elapsed_ms=(time.perf_counter() - t0) * 1000, extra={"error": str(e)})
            return {
                "success": False,
                "error": str(e),
                "message": f"创建新对话失败: {e}"
            }

    def _ensure_conversation_versioning_enabled(self, conversation_id: str) -> None:
        """为指定对话启用版本控制（初始快照）。"""
        normalized_id = conversation_id if conversation_id.startswith("conv_") else f"conv_{conversation_id}"
        t0 = time.perf_counter()
        perf_log("_ensure_conversation_versioning_enabled enter", extra={"conv_id": normalized_id})
        is_host = bool(getattr(self, "_is_host_mode", lambda: False)())
        from modules.personalization_manager import load_personalization_config
        personal_config = load_personalization_config(self.data_dir)
        backup_mode = str(personal_config.get("versioning_backup_mode") or "shallow").strip().lower()
        backup_mode = "full" if backup_mode == "full" else "shallow"
        # 浅备份模式下不启用完整 workspace git 备份，只保留对话记录回溯
        if backup_mode == "shallow":
            tracking_mode = ConversationVersioningManager.TRACKING_MODE_CONVERSATION_ONLY
        elif is_host:
            tracking_mode = ConversationVersioningManager.TRACKING_MODE_WORKSPACE_AND_CONVERSATION
        else:
            tracking_mode = ConversationVersioningManager.TRACKING_MODE_CONVERSATION_ONLY
        manager = ConversationVersioningManager(
            project_path=self.project_path,
            data_dir=self.data_dir,
            conversation_id=normalized_id,
        )
        perf_log("_ensure_conversation_versioning_enabled manager created", elapsed_ms=(time.perf_counter() - t0) * 1000, extra={"conv_id": normalized_id})
        meta = manager.set_enabled(enabled=True, mode="overwrite", tracking_mode=tracking_mode)
        perf_log("_ensure_conversation_versioning_enabled set_enabled done", elapsed_ms=(time.perf_counter() - t0) * 1000, extra={"conv_id": normalized_id})
        target_manager = self.context_manager._get_conversation_manager_for_id(normalized_id)
        conv_data = target_manager.load_conversation(normalized_id) or {}
        snapshot_payload = {
            "conversation_id": normalized_id,
            "title": conv_data.get("title"),
            "metadata": conv_data.get("metadata") or {},
            "messages": conv_data.get("messages") or [],
            "message_index": -1,
            "run_status": "initial",
        }
        init_result = manager.ensure_initial_checkpoint(
            workspace_path=str(self.project_path),
            conversation_snapshot=snapshot_payload,
            tracking_mode=tracking_mode,
        )
        perf_log("_ensure_conversation_versioning_enabled initial checkpoint done", elapsed_ms=(time.perf_counter() - t0) * 1000, extra={"conv_id": normalized_id})
        init_row = init_result.get("row") or {}
        if init_row.get("tree_hash"):
            meta["last_tree_hash"] = init_row.get("tree_hash")
        target_manager.update_conversation_metadata(
            normalized_id,
            {
                "versioning": {
                    "enabled": True,
                    "mode": "overwrite",
                    "tracking_mode": tracking_mode,
                    "backup_mode": backup_mode,
                    "last_commit": meta.get("last_tree_hash"),
                    "last_input_seq": int(meta.get("last_input_seq") or 0),
                    "updated_at": datetime.now().isoformat(),
                }
            },
        )
        perf_log("_ensure_conversation_versioning_enabled done", elapsed_ms=(time.perf_counter() - t0) * 1000, extra={"conv_id": normalized_id})

    def load_conversation(self, conversation_id: str, restore_model: bool = True) -> Dict:
        """
        加载指定对话（Web版本）
        
        Args:
            conversation_id: 对话ID
            restore_model: 是否从对话 metadata 恢复模型。程序启动时自动恢复最近对话不恢复模型，
                          避免 /new 页面显示旧对话模型；用户显式加载对话时恢复。
            
        Returns:
            Dict: 加载结果
        """
        try:
            # 工作区级服务实例（_bound_conversation_id 为空）：仅恢复会话焦点
            # （current_conversation_id）与运行模式，不挂载消息历史——历史权威在磁盘
            # 与对话级实例，服务实例持历史只会成为 merge-on-save 的写回污染源。
            # 对话级实例（绑定对话）：挂载历史，供任务执行链路使用。
            attach_history = bool(getattr(self, "_bound_conversation_id", None))
            success = self.context_manager.load_conversation_by_id(conversation_id, attach_history=attach_history)
            if success:
                # 根据对话元数据同步运行模式与推理强度
                try:
                    target_manager = self.context_manager._get_conversation_manager_for_id(conversation_id)
                    conv_data = target_manager.load_conversation(conversation_id) or {}
                    meta = conv_data.get("metadata", {}) or {}
                    saved_mode = str(meta.get("run_mode") or "").strip().lower()
                    if saved_mode == "deep":  # 旧版标识符映射
                        saved_mode = "thinking"
                    if saved_mode in {"fast", "thinking"}:
                        self.run_mode = saved_mode
                        self.thinking_mode = saved_mode != "fast"
                    else:
                        self.thinking_mode = bool(meta.get("thinking_mode", self.thinking_mode))
                        self.run_mode = "thinking" if self.thinking_mode else "fast"
                    self.api_client.thinking_mode = self.thinking_mode
                    try:
                        self.set_reasoning_effort(meta.get("reasoning_effort"))
                    except ValueError:
                        self.set_reasoning_effort(None)
                    # 运行模式必须先于权限恢复：set_permission_mode 带 plan 锁
                    # （plan 下禁止非只读），若 permission 先恢复，上一对话残留的
                    # work_mode=plan 会让本对话的非只读权限恢复被误拦截。
                    work_mode = str(meta.get("work_mode") or "").strip().lower()
                    if work_mode in {"plan", "ask", "execute"}:
                        try:
                            self.set_work_mode(work_mode, persist=False)
                        except Exception:
                            pass
                    permission_mode = str(meta.get("permission_mode") or "").strip().lower()
                    if permission_mode:
                        try:
                            self.set_permission_mode(permission_mode, persist=False)
                        except Exception:
                            pass
                    # 自愈：plan 模式但权限不是只读的异常数据，强制回只读
                    if self.get_work_mode() == "plan" and self.get_permission_mode() != "readonly":
                        try:
                            self.set_permission_mode("readonly", persist=False)
                        except Exception:
                            pass
                    execution_mode = str(meta.get("execution_mode") or "").strip().lower()
                    if execution_mode in {"sandbox", "direct"}:
                        try:
                            self.set_execution_mode(execution_mode)
                        except Exception:
                            pass
                    # 自愈：plan 模式但执行环境是 direct 的异常数据，强制回沙箱
                    # （只读权限依赖沙箱硬限制，direct 下只读无牙齿）
                    if self.get_work_mode() == "plan" and hasattr(self, "get_execution_mode"):
                        try:
                            if self.get_execution_mode() != "sandbox":
                                self.set_execution_mode("sandbox")
                        except Exception:
                            pass
                    network_permission = str(meta.get("network_permission") or "").strip().lower()
                    if network_permission in {"restricted", "full", "none"}:
                        try:
                            self.set_network_permission(network_permission)
                        except Exception:
                            pass
                    if restore_model:
                        saved_model_key = meta.get("model_key")
                        if saved_model_key:
                            try:
                                self.set_model(saved_model_key)
                            except Exception as exc:
                                logger.warning("加载对话模型 %s 失败: %s", saved_model_key, exc)
                    self.pending_permission_mode = str(meta.get("pending_permission_mode") or "").strip().lower() or None
                    self.pending_execution_mode = str(meta.get("pending_execution_mode") or "").strip().lower() or None
                    self.pending_network_permission = str(meta.get("pending_network_permission") or "").strip().lower() or None
                    # 多智能体模式：以会话 metadata.multi_agent_mode 为唯一切换开关
                    self.multi_agent_mode = bool(meta.get("multi_agent_mode", False))
                    # 同步主智能体 sub_agent_manager 的 开关
                    try:
                        if hasattr(self, "sub_agent_manager"):
                            self.sub_agent_manager.multi_agent_mode = self.multi_agent_mode
                    except Exception:
                        pass
                except Exception:
                    pass
                # 重置相关状态
                self.current_session_id += 1
                
                # 获取对话信息
                target_manager = self.context_manager._get_conversation_manager_for_id(conversation_id)
                conversation_data = target_manager.load_conversation(conversation_id)
                if not conversation_data:
                    return {
                        "success": False,
                        "error": "对话数据缺失",
                        "message": f"对话数据缺失: {conversation_id}"
                    }
                
                return {
                    "success": True,
                    "conversation_id": conversation_id,
                    "title": conversation_data.get("title", "未知对话"),
                    # 消息数以磁盘数据为准：服务实例不挂载历史，len(conversation_history) 恒为 0
                    "messages_count": len(conversation_data.get("messages") or []),
                    "run_mode": self.run_mode,
                    "thinking_mode": self.thinking_mode,
                    "model_key": getattr(self, "model_key", None),
                    "multi_agent_mode": bool(getattr(self, "multi_agent_mode", False)),
                    "message": f"对话已加载: {conversation_id}"
                }
            else:
                return {
                    "success": False,
                    "error": "对话不存在或加载失败",
                    "message": f"对话加载失败: {conversation_id}"
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"加载对话异常: {e}"
            }
    
    def get_conversations_list(self, limit: int = 20, offset: int = 0, non_empty: bool = False, multi_agent_mode: Optional[bool] = None) -> Dict:
        """获取对话列表（Web版本）"""
        try:
            result = self.context_manager.get_conversation_list(limit=limit, offset=offset, non_empty=non_empty, multi_agent_mode=multi_agent_mode)
            return {
                "success": True,
                "data": result
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"获取对话列表失败: {e}"
            }
    
    def delete_conversation(self, conversation_id: str) -> Dict:
        """删除指定对话（Web版本）"""
        try:
            success = self.context_manager.delete_conversation_by_id(conversation_id)
            if success:
                return {
                    "success": True,
                    "message": f"对话已删除: {conversation_id}"
                }
            else:
                return {
                    "success": False,
                    "error": "删除失败",
                    "message": f"对话删除失败: {conversation_id}"
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"删除对话异常: {e}"
            }
    
    def search_conversations(self, query: str, limit: int = 20, multi_agent_mode: Optional[bool] = None) -> Dict:
        """搜索对话（Web版本）"""
        try:
            results = self.context_manager.search_conversations(query, limit, multi_agent_mode=multi_agent_mode)
            return {
                "success": True,
                "results": results,
                "count": len(results)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"搜索对话失败: {e}"
            }
    
    # ===========================================
    # 修改现有方法，保持兼容性
    # ===========================================
    
    def get_status(self) -> Dict:
        """获取系统状态（Web版本，集成对话信息）"""
        # 获取基础状态
        context_status = self.context_manager.check_context_size()
        memory_stats = self.memory_manager.get_memory_stats()
        structure = self.context_manager.get_project_structure()
        
        # 聚焦功能已废弃
        focused_files_dict = {}
        
        # 终端状态
        terminal_status = None
        if self.terminal_manager:
            terminal_status = self.terminal_manager.list_terminals()
        
        # 构建状态信息
        limit_bytes = getattr(self, "project_storage_limit_bytes", None)
        status = {
            "project_path": self.project_path,
            "thinking_mode": self.thinking_mode,
            "thinking_status": self.get_thinking_mode_status(),
            "run_mode": self.run_mode,
            "reasoning_effort": getattr(self, "reasoning_effort", None),
            "model_key": getattr(self, "model_key", None),
            "permission_mode": self.get_permission_mode() if hasattr(self, "get_permission_mode") else "unrestricted",
            "work_mode": self.get_work_mode() if hasattr(self, "get_work_mode") else "plan",
            "execution_mode": self.get_execution_mode_state() if hasattr(self, "get_execution_mode_state") else {"mode": "sandbox"},
            "network_permission": self.get_network_permission() if hasattr(self, "get_network_permission") else "restricted",
            "pending_runtime_modes": self.get_pending_runtime_modes() if hasattr(self, "get_pending_runtime_modes") else {},
            "execution_mode_enabled": bool(self._is_host_mode()) and getattr(self, "user_role", "user") == "admin",
            "network_permission_enabled": bool(self._is_host_mode()) and getattr(self, "user_role", "user") == "admin",
            "has_images": getattr(self.context_manager, "has_images", False),
            "has_videos": getattr(self.context_manager, "has_videos", False),
            "context": {
                "usage_percent": context_status['usage_percent'],
                "total_size": context_status['sizes']['total'],
                # 本实例内存上下文中的消息数；工作区级服务实例不挂载历史，恒为 0（前端不消费该字段）
                "conversation_count": len(self.context_manager.conversation_history)
            },
            "focused_files": focused_files_dict,
            "focused_files_count": 0,
            "terminals": terminal_status,
            "project": {
                "total_files": structure['total_files'],
                "total_size": structure['total_size'],
                "limit_bytes": limit_bytes,
                "limit_label": self.project_storage_limit,
                "usage_percent": (structure['total_size'] / limit_bytes * 100) if limit_bytes else None
            },
            "memory": {
                "main": memory_stats['main_memory']['lines'],
                "task": memory_stats['task_memory']['lines']
            },
            # 新增：对话状态
            "conversation": {
                "current_id": self.context_manager.current_conversation_id,
                # 首屏不再同步计算全量对话统计；统计面板按需调用
                # /api/conversations/statistics，避免刷新时读取所有历史对话文件。
                "total_conversations": 0,
                "total_messages": 0,
                "total_tools": 0
            }
        }
        status["todo_list"] = self.context_manager.get_todo_snapshot()
        
        return status
    
    def get_thinking_mode_status(self) -> str:
        """获取思考模式状态描述"""
        return "思考模式" if self.thinking_mode else "快速模式"
    
    def broadcast(self, event_type: str, data: Dict):
        """广播事件到WebSocket"""
        if self.message_callback:
            payload = dict(data or {})
            payload.setdefault('conversation_id', self.context_manager.current_conversation_id)
            self.message_callback(event_type, payload)
    
    # ===========================================
    # 覆盖父类方法，添加Web特有的广播功能
    # ===========================================
    
    async def handle_tool_call(self, tool_name: str, arguments: Dict) -> str:
        """
        处理工具调用（Web版本）
        覆盖父类方法，添加增强的实时广播功能
        """
        # 立即广播工具执行开始事件（不等待）
        self.broadcast('tool_execution_start', {
            'tool': tool_name,
            'arguments': arguments,
            'status': 'executing',
            'message': f'正在执行 {tool_name}...'
        })
        
        # 对于某些工具，发送更详细的状态
        if tool_name == "create_file":
            self.broadcast('tool_status', {
                'tool': tool_name,
                'status': 'creating',
                'detail': f'创建文件: {arguments.get("path", "未知路径")}'
            })
        elif tool_name == "read_file":
            read_type = arguments.get("type", "read")
            self.broadcast('tool_status', {
                'tool': tool_name,
                'status': 'reading',
                'detail': f'读取文件({read_type}): {arguments.get("path", "未知路径")}'
            })
        elif tool_name == "delete_file":
            self.broadcast('tool_status', {
                'tool': tool_name,
                'status': 'deleting',
                'detail': f'删除文件: {arguments.get("path", "未知路径")}'
            })
        elif tool_name == "web_search":
            query = arguments.get("query", "")
            filters = []
            topic = arguments.get("topic")
            if topic:
                filters.append(f"topic={topic}")
            else:
                filters.append("topic=general")
            if arguments.get("time_range"):
                filters.append(f"time_range={arguments['time_range']}")
            if arguments.get("days") is not None:
                filters.append(f"days={arguments.get('days')}")
            if arguments.get("start_date") and arguments.get("end_date"):
                filters.append(f"{arguments.get('start_date')}~{arguments.get('end_date')}")
            if arguments.get("country"):
                filters.append(f"country={arguments.get('country')}")
            include_domains = arguments.get("include_domains")
            if isinstance(include_domains, list) and include_domains:
                filters.append(f"include_domains={len(include_domains)}")
            filter_text = " | ".join(filter_item for filter_item in filters if filter_item)
            self.broadcast('tool_status', {
                'tool': tool_name,
                'status': 'searching',
                'detail': f'搜索: {query}' + (f' ({filter_text})' if filter_text else '')
            })
        elif tool_name == "extract_webpage":
            self.broadcast('tool_status', {
                'tool': tool_name,
                'status': 'extracting',
                'detail': f'提取网页: {arguments.get("url", "")}'
            })
        elif tool_name == "save_webpage":
            self.broadcast('tool_status', {
                'tool': tool_name,
                'status': 'saving_webpage',
                'detail': f'保存网页: {arguments.get("url", "")}'
            })
        elif tool_name == "run_command":
            self.broadcast('tool_status', {
                'tool': tool_name,
                'status': 'running_command',
                'detail': f'执行命令: {arguments.get("command", "")}'
            })
        elif tool_name == "terminal_session":
            action = arguments.get("action", "")
            session_name = arguments.get("session_name", "default")
            self.broadcast('tool_status', {
                'tool': tool_name,
                'status': f'terminal_{action}',
                'detail': f'终端操作: {action} - {session_name}'
            })
        elif tool_name == "terminal_input":
            command = arguments.get("command", "")
            # 只显示命令的前50个字符避免过长
            display_command = command[:50] + "..." if len(command) > 50 else command
            self.broadcast('tool_status', {
                'tool': tool_name,
                'status': 'sending_input',
                'detail': f'发送终端输入: {display_command}'
            })
        elif tool_name == "sleep":
            seconds = arguments.get("seconds", 1)
            reason = arguments.get("reason", "等待操作完成")
            self.broadcast('tool_status', {
                'tool': tool_name,
                'status': 'waiting',
                'detail': f'等待 {seconds} 秒: {reason}'
            })
        
        # 调用父类的工具处理（包含我们的新逻辑）
        result = await super().handle_tool_call(tool_name, arguments)
        logger.debug(
            "[SubAgent][WebTerminal] tool=%s 执行完成，result前200=%s",
            tool_name,
            result[:200] if isinstance(result, str) else result,
        )
        
        # 解析结果并广播工具结束事件
        try:
            result_data = json.loads(result)
            success = result_data.get('success', False)
            
            # 特殊处理某些错误类型
            if not success:
                error_msg = result_data.get('error')
                if not error_msg:
                    error_msg = result_data.get('message')
                if not error_msg:
                    error_msg = '执行失败'
                if not isinstance(error_msg, str):
                    error_msg = str(error_msg)
                
                # 检查是否是参数预检查失败
                if error_msg and ('参数过大' in error_msg or '内容过长' in error_msg):
                    self.broadcast('tool_execution_end', {
                        'tool': tool_name,
                        'success': False,
                        'result': result_data,
                        'message': f'{tool_name} 执行失败: 参数过长',
                        'error_type': 'parameter_too_long',
                        'suggestion': result_data.get('suggestion', '建议分块处理')
                    })
                elif error_msg and ('JSON解析' in error_msg or '参数解析失败' in error_msg):
                    self.broadcast('tool_execution_end', {
                        'tool': tool_name,
                        'success': False,
                        'result': result_data,
                        'message': f'{tool_name} 执行失败: 参数格式错误',
                        'error_type': 'parameter_format_error',
                        'suggestion': result_data.get('suggestion', '请检查参数格式')
                    })
                else:
                    # 一般错误
                    self.broadcast('tool_execution_end', {
                        'tool': tool_name,
                        'success': False,
                        'result': result_data,
                        'message': f'{tool_name} 执行失败: {error_msg}',
                        'error_type': 'general_error'
                    })
            else:
                # 成功的情况
                success_msg = result_data.get('message', f'{tool_name} 执行成功')
                self.broadcast('tool_execution_end', {
                    'tool': tool_name,
                    'success': True,
                    'result': result_data,
                    'message': success_msg
                })
                
        except json.JSONDecodeError:
            # 无法解析JSON结果
            success = False
            result_data = {'output': result, 'raw_result': True}
            self.broadcast('tool_execution_end', {
                'tool': tool_name,
                'success': False,
                'result': result_data,
                'message': f'{tool_name} 返回了非JSON格式结果',
                'error_type': 'invalid_result_format'
            })
        
        # 如果是终端相关操作，广播终端更新
        if tool_name in ['terminal_session', 'terminal_input'] and self.terminal_manager:
            try:
                terminals = self.terminal_manager.get_terminal_list()
                self.broadcast('terminal_list_update', {
                    'terminals': terminals,
                    'active': self.terminal_manager.active_terminal
                })
            except Exception as e:
                logger.error(f"广播终端更新失败: {e}")
        
        # 如果是文件操作，广播文件树更新
        if tool_name in ['create_file', 'delete_file', 'rename_file', 'create_folder', 'save_webpage']:
            if not self.context_manager._is_host_mode_without_safety():
                try:
                    structure = self.context_manager.get_project_structure()
                    self.broadcast('file_tree_update', structure)
                except Exception as e:
                    logger.error(f"广播文件树更新失败: {e}")
        
        # 如果是记忆操作，广播记忆状态更新
        if tool_name == 'update_memory':
            try:
                memory_stats = self.memory_manager.get_memory_stats()
                self.broadcast('memory_update', {
                    'main': memory_stats['main_memory']['lines'],
                    'task': memory_stats['task_memory']['lines']
                })
            except Exception as e:
                logger.error(f"广播记忆更新失败: {e}")
        
        return result
    
    def build_context(self) -> Dict:
        """构建上下文（Web版本）"""
        context = super().build_context()
        
        # 添加Web特有的上下文信息
        context['web_mode'] = True
        context['terminal_sessions'] = []
        
        if self.terminal_manager:
            for name, terminal in self.terminal_manager.terminals.items():
                context['terminal_sessions'].append({
                    'name': name,
                    'is_active': name == self.terminal_manager.active_terminal,
                    'is_running': terminal.is_running
                })
        
        # 添加对话信息
        context['conversation_info'] = {
            'current_id': self.context_manager.current_conversation_id,
            'messages_count': len(self.context_manager.conversation_history)
        }
        
        return context
    
    async def confirm_action(self, action: str, arguments: Dict) -> bool:
        """
        确认危险操作（Web版本）
        在Web模式下，我们自动确认或通过WebSocket请求确认
        """
        # 在Web模式下，暂时自动确认
        # 未来可以通过WebSocket向前端请求确认
        print(f"[WebTerminal] 自动确认操作: {action}")
        
        # 广播确认事件，让前端知道正在执行危险操作
        self.broadcast('dangerous_action', {
            'action': action,
            'arguments': arguments,
            'auto_confirmed': True
        })
        
        return True
    
    def __del__(self):
        """析构函数，确保资源释放"""
        try:
            # 保存当前对话（仅当内存中确有消息时）。
            # 竞态重建/回收场景下，被丢弃的实例可能只设了
            # current_conversation_id 而 conversation_history 为空，
            # 此时保存会把磁盘上非空的对话覆盖成空消息。
            if hasattr(self, 'context_manager') and self.context_manager:
                cm = self.context_manager
                if cm.current_conversation_id and getattr(cm, 'conversation_history', None):
                    cm.save_current_conversation()
            
            # 关闭所有终端
            if hasattr(self, 'terminal_manager') and self.terminal_manager:
                self.terminal_manager.close_all()
                
        except Exception as e:
            print(f"[WebTerminal] 资源清理失败: {e}")
