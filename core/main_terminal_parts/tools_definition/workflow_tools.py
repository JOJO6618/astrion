"""工作流（Workflow）工具定义。

7 个主智能体工具：
- 运行时（定稿 docs/workflow_feature_plan.md §5）：activate_workflow /
  report_workflow_stage / choose_workflow_branch / get_workflow_status / deactivate_workflow
- 库管理：list_workflows（列表 / name 形态读原文）/ save_workflow（归档式创建与覆盖）

handler 分布：
- activate / get_status / list_workflows / save_workflow 走 tools_execution.py 常规链（无需 sender）
- report_workflow_stage / choose_workflow_branch / deactivate_workflow 走
  chat_flow_tool_loop.py 特判（需要 sender 发审核进度/摘牌广播、conversation_id
  与 workspace；deactivate 摘牌后需广播 {active: False} 让前端实时摘卡片）
"""
from typing import Any, Dict, List


class ToolsDefinitionWorkflowToolsMixin:
    def _build_workflow_tools(self) -> List[Dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "activate_workflow",
                    "description": (
                        "在当前对话中激活一个工作流，让后续工作按既定流程推进。"
                        "当用户明确要求按某个工作流执行（如「按代码评审流程走」），"
                        "或你判断当前任务适合套用某个已存在的工作流时调用。"
                        "同一对话同时只能激活一个工作流；重复激活同一工作流返回当前进度。"
                        "激活后按返回的当前阶段要求工作，完成后用 report_workflow_stage 汇报。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": self._inject_intent({
                            "name": {
                                "type": "string",
                                "description": "工作流名称（workflows 库中的目录名，如 code-review-pipeline）",
                            }
                        }),
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "report_workflow_stage",
                    "description": (
                        "汇报当前工作流阶段已完成并推进流程。仅当工作流激活且当前位于执行阶段时可调。"
                        "summary 写本阶段实际完成了什么、关键结论与证据（审核智能体会看到）。"
                        "返回内容视下一节点而定：下一阶段的目标与要求 / 审核结果"
                        "（通过则推进、驳回则带整改意见回到前序阶段）/ 分支菜单 / 工作流完成。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": self._inject_intent({
                            "summary": {
                                "type": "string",
                                "description": "本阶段完成内容摘要：做了什么、结论是什么、关键证据（文件/命令输出）。",
                            }
                        }),
                        "required": ["summary"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "choose_workflow_branch",
                    "description": (
                        "在工作流分支点选择后续路径。仅当前停在分支节点时可调；"
                        "target_node_id 必须在分支菜单列出的候选路径中。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": self._inject_intent({
                            "target_node_id": {
                                "type": "string",
                                "description": "候选路径的目标节点 id（分支菜单中列出）。",
                            }
                        }),
                        "required": ["target_node_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_workflow_status",
                    "description": (
                        "查询当前对话激活工作流的进度：已完成步骤、审核记录、当前位置与已进行轮数。"
                        "在长阶段中迷失进度、或用户询问工作流进展时调用。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": self._inject_intent({}),
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "deactivate_workflow",
                    "description": (
                        "退出当前对话激活的工作流。当你判断流程不适用于当前情况、"
                        "流程定义有问题导致无法推进、或用户要求退出时调用。"
                        "退出后工作流摘牌，你可以继续自由工作。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": self._inject_intent({
                            "reason": {
                                "type": "string",
                                "description": "退出原因（简述，会记录在状态里）。",
                            }
                        }),
                        "required": ["reason"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_workflows",
                    "description": (
                        "列出全部可用工作流（内置示例 + 用户创建），返回名称、描述、来源与节点数。"
                        "当需要告诉用户有哪些工作流可激活、或激活前确认工作流名是否存在时调用。"
                        "传入 name 时改为返回该工作流的完整定义文档（WORKFLOW.md 原文），"
                        "用于修改前读取现状。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": self._inject_intent({
                            "name": {
                                "type": "string",
                                "description": "可选。指定工作流名时返回其完整定义文档，而非列表。",
                            }
                        }),
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "save_workflow",
                    "description": (
                        "把已创建的工作流目录校验并归档到工作流库，使其可被 activate_workflow 激活。"
                        "先用 write_file 创建 <name>/WORKFLOW.md（格式与编写规范阅读 "
                        "workflow-authoring 技能，内含初始化与自检脚本），再调用本工具归档。"
                        "目录名必须与 WORKFLOW.md 的 name 字段一致；校验不通过返回完整错误清单且不归档；"
                        "归档成功后源目录被移除。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": self._inject_intent({
                            "source_dir": {
                                "type": "string",
                                "description": "包含 WORKFLOW.md 的目录路径（工作区内，如 drafts/my-flow）。",
                            },
                            "overwrite": {
                                "type": "boolean",
                                "description": "目标已存在（含与内置同名）时是否覆盖。默认 false：已存在则报错并提示。",
                            },
                        }),
                        "required": ["source_dir"],
                    },
                },
            },
        ]
