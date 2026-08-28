"""Backend i18n message pack: 审核智能体族（modules/workflow_review_agent.py、
modules/goal_review_agent.py、server/goal_flow.py）用户可见消息。

Pure data module — do not import anything here. Auto-discovered and merged
by modules/i18n.py at import time. zh-CN copy is verbatim from source;
en-US is concise product-level English (sentence case).
插值用 str.format 命名参数：tr("workflow_review.<key>", name=value)。
"""

MESSAGES = {
    # ── modules/workflow_review_agent.py（key 前缀 workflow_review.） ──

    "workflow_review.fallback_reject": {
        "zh-CN": "本次审核未能正常完成（审核服务异常或未产出结论），按驳回处理。请告知用户审核服务可能异常；若属偶发，可稍后重新汇报本阶段。",
        "en-US": "This review could not be completed normally (review service error or no conclusion produced); it is treated as a rejection. Inform the user that the review service may be unavailable; if transient, the stage can be reported again later.",
    },
    "workflow_review.config_missing": {
        "zh-CN": "工作流审核智能体配置缺失，无法完成审核。本次审核未能正常完成（审核服务异常或未产出结论），按驳回处理。请告知用户审核服务可能异常；若属偶发，可稍后重新汇报本阶段。",
        "en-US": "Workflow review agent config is missing; the review cannot be completed. This review could not be completed normally (review service error or no conclusion produced); it is treated as a rejection. Inform the user that the review service may be unavailable; if transient, the stage can be reported again later.",
    },
    "workflow_review.max_rounds_no_conclusion": {
        "zh-CN": "审核超过最大轮次仍未产出结论。本次审核未能正常完成（审核服务异常或未产出结论），按驳回处理。请告知用户审核服务可能异常；若属偶发，可稍后重新汇报本阶段。",
        "en-US": "The review produced no conclusion within the maximum rounds. This review could not be completed normally (review service error or no conclusion produced); it is treated as a rejection. Inform the user that the review service may be unavailable; if transient, the stage can be reported again later.",
    },
    "workflow_review.request_failed": {
        "zh-CN": "审核请求失败({code})。本次审核未能正常完成（审核服务异常或未产出结论），按驳回处理。请告知用户审核服务可能异常；若属偶发，可稍后重新汇报本阶段。",
        "en-US": "Review request failed ({code}). This review could not be completed normally (review service error or no conclusion produced); it is treated as a rejection. Inform the user that the review service may be unavailable; if transient, the stage can be reported again later.",
    },
    "workflow_review.request_exception": {
        "zh-CN": "审核请求异常（{error}）。本次审核未能正常完成（审核服务异常或未产出结论），按驳回处理。请告知用户审核服务可能异常；若属偶发，可稍后重新汇报本阶段。",
        "en-US": "Review request raised an error ({error}). This review could not be completed normally (review service error or no conclusion produced); it is treated as a rejection. Inform the user that the review service may be unavailable; if transient, the stage can be reported again later.",
    },
    "workflow_review.unrecognized_conclusion": {
        "zh-CN": "审核返回了无法识别的结论。本次审核未能正常完成（审核服务异常或未产出结论），按驳回处理。请告知用户审核服务可能异常；若属偶发，可稍后重新汇报本阶段。",
        "en-US": "The review returned an unrecognized conclusion. This review could not be completed normally (review service error or no conclusion produced); it is treated as a rejection. Inform the user that the review service may be unavailable; if transient, the stage can be reported again later.",
    },
    "workflow_review.pass_default_message": {
        "zh-CN": "审核通过。",
        "en-US": "Review passed.",
    },
    "workflow_review.reject_default_message": {
        "zh-CN": "审核未通过，请按整改意见补充后重新汇报。",
        "en-US": "Review failed; supplement the work per the corrective feedback and report again.",
    },
    "workflow_review.round_progress": {
        "zh-CN": "审核轮次 {round}",
        "en-US": "Review round {round}",
    },

    # ── modules/goal_review_agent.py（key 前缀 goal_review.） ──

    "goal_review.fallback_continue": {
        "zh-CN": "审核未能给出明确结论。请重新对照目标核查当前进度，找出尚未完成的部分并继续推进。",
        "en-US": "The review produced no clear conclusion. Re-check the current progress against the goal, find what is unfinished, and keep going.",
    },
    "goal_review.config_missing": {
        "zh-CN": "目标审核智能体配置缺失，无法判断完成情况，请继续推进目标。",
        "en-US": "Goal review agent config is missing; completion cannot be assessed — continue working toward the goal.",
    },
    "goal_review.max_rounds_no_conclusion": {
        "zh-CN": "目标审核超过 {max_rounds} 轮未产出结论，请继续推进目标。",
        "en-US": "The goal review produced no conclusion in {max_rounds} rounds; continue working toward the goal.",
    },
    "goal_review.request_failed": {
        "zh-CN": "目标审核请求失败({code})，请继续推进目标。",
        "en-US": "Goal review request failed ({code}); continue working toward the goal.",
    },
    "goal_review.request_exception": {
        "zh-CN": "目标审核请求异常，请继续推进目标。",
        "en-US": "Goal review request raised an error; continue working toward the goal.",
    },
    "goal_review.unrecognized_status": {
        "zh-CN": "审核返回了无法识别的状态，请继续推进目标。",
        "en-US": "The review returned an unrecognized status; continue working toward the goal.",
    },
    "goal_review.done_default_message": {
        "zh-CN": "目标已达成。",
        "en-US": "Goal achieved.",
    },
    "goal_review.round_progress": {
        "zh-CN": "审核轮次 {round}",
        "en-US": "Review round {round}",
    },

    # ── server/goal_flow.py（key 前缀 goal_flow.，仅事件/续命短消息；
    #    GOAL_MODE_PROMPT 与 CONTINUE_PREFIX 为前端功能性匹配的注入标记，不迁移） ──

    "goal_flow.review_start_event": {
        "zh-CN": "开始审核",
        "en-US": "Review started",
    },
    "goal_flow.review_exec_exception": {
        "zh-CN": "目标审核出现异常（{error}），请继续核对目标并推进未完成的部分。",
        "en-US": "The goal review raised an error ({error}); re-check the goal and continue with the unfinished parts.",
    },
}