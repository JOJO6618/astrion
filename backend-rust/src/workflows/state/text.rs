use super::{State, advance::StageReport};
use crate::workflows::{Node, NodeKind, Workflow};
use serde_json::Value;
use std::collections::{HashSet, VecDeque};

pub(super) fn activation(definition: &Workflow, state: &State, already: bool) -> String {
    let label = if already {
        "工作流已处于激活状态"
    } else {
        "工作流已激活"
    };
    let mut lines = vec![
        format!("【{label}】{}：{}", definition.name, definition.description),
        String::new(),
    ];
    if !definition.body.is_empty() {
        lines.extend([
            "【流程约定】".into(),
            definition.body.clone(),
            String::new(),
        ]);
    }
    lines.push("【节点目录】".into());
    let mut seen = HashSet::new();
    let mut queue: VecDeque<_> = definition
        .nodes
        .iter()
        .filter(|node| matches!(node.kind, NodeKind::Start { .. }))
        .collect();
    let mut ordered = Vec::with_capacity(definition.nodes.len());
    while let Some(node) = queue.pop_front() {
        if seen.insert(node.id.as_str()) {
            ordered.push(node);
            queue.extend(
                node.targets()
                    .into_iter()
                    .filter_map(|target| definition.node(target)),
            );
        }
    }
    ordered.extend(
        definition
            .nodes
            .iter()
            .filter(|node| !seen.contains(node.id.as_str())),
    );
    for node in ordered {
        let (kind, detail) = match &node.kind {
            NodeKind::Start { .. } => ("开始", String::new()),
            NodeKind::End => ("结束", String::new()),
            NodeKind::Stage { goal, .. } => ("阶段", goal.clone()),
            NodeKind::Review { prompt, .. } => (
                "审核",
                format!("把关：{}", fallback(prompt, "阶段产出审核")),
            ),
            NodeKind::Branch { .. } => ("分支", "按条件选择路径".into()),
        };
        let marker = if state.current_node_id.as_deref() == Some(node.id.as_str()) {
            " ← 当前"
        } else {
            ""
        };
        lines.push(format!("- [{kind}] {}：{detail}{marker}", node.name));
    }
    if let Some(current) = state
        .current_node_id
        .as_deref()
        .and_then(|id| definition.node(id))
    {
        lines.extend([String::new(), node_brief(current)]);
    }
    lines.join("\n")
}

pub(super) fn status(definition: &Workflow, state: &State) -> String {
    let seconds = state
        .started_at
        .map_or(0, |time| (super::files::now_epoch() - time) as i64);
    let mut lines = vec![
        format!(
            "【工作流状态】{}：{}",
            definition.name, definition.description
        ),
        format!("已进行时长：{seconds} 秒"),
        String::new(),
        "【已完成的步骤】".into(),
    ];
    let stage_lines = state
        .history
        .iter()
        .filter(|item| item["kind"] == "stage")
        .map(|item| {
            format!(
                "- {}（{} 轮）：{}",
                string(&item["name"]),
                item["rounds"].as_u64().unwrap_or_default(),
                truncate(string(&item["summary"]), 80, "")
            )
        })
        .collect::<Vec<_>>();
    if stage_lines.is_empty() {
        lines.push("（暂无）".into());
    } else {
        lines.extend(stage_lines);
    }
    let mut reviews = state
        .history
        .iter()
        .filter(|item| item["kind"] == "review")
        .peekable();
    if reviews.peek().is_some() {
        lines.extend([String::new(), "【审核记录】".into()]);
        for item in reviews {
            let label = if item["decision"] == "pass" {
                "通过"
            } else {
                "驳回"
            };
            lines.push(format!(
                "- {}：{label} — {}",
                string(&item["name"]),
                truncate(string(&item["message"]), 80, "")
            ));
        }
    }
    if let Some(current) = state
        .current_node_id
        .as_deref()
        .and_then(|id| definition.node(id))
    {
        lines.extend([String::new(), node_brief(current)]);
    }
    lines.push(format!("当前步骤已进行轮数：{}", state.stage_rounds));
    lines.join("\n")
}

pub(super) fn node_brief(node: &Node) -> String {
    match &node.kind {
        NodeKind::Stage {
            goal, instructions, ..
        } => {
            let goal = fallback(goal, "（未填写）");
            let mut lines = vec![
                format!("【当前阶段】{}", node.name),
                format!("目标：{goal}"),
            ];
            if !instructions.trim().is_empty() {
                lines.push(format!("要求：{}", instructions.trim()));
            }
            lines.push("完成后调用 report_workflow_stage(summary) 汇报以推进流程。".into());
            lines.join("\n")
        }
        NodeKind::Branch { next } => {
            let routes = next
                .iter()
                .map(|route| {
                    format!(
                        "- {}（{}）",
                        route.target,
                        fallback(&route.condition, "无条件描述")
                    )
                })
                .collect::<Vec<_>>()
                .join("\n");
            format!(
                "【当前位于分支点】{}\n请选择后续路径（调用 choose_workflow_branch(target_node_id)）：\n{routes}",
                node.name
            )
        }
        _ => format!("【当前位置】{}", node.name),
    }
}

pub(super) fn review(
    definition: &Workflow,
    state: &State,
    stage: Option<&StageReport>,
    review: &Node,
    trace: &str,
) -> String {
    let NodeKind::Review { prompt, .. } = &review.kind else {
        unreachable!("review payload requires a review node");
    };
    let prompt = fallback(prompt, "阶段产出是否达到进入下一阶段的门槛");
    let stage_node = stage
        .and_then(|stage| definition.node(&stage.node_id))
        .or_else(|| {
            state
                .current_node_id
                .as_deref()
                .and_then(|id| definition.node(id))
        });
    let mut lines = vec![
        format!("【工作流】{}：{}", definition.name, definition.description),
        String::new(),
        format!("【本次审核把关】{}", review.name),
        format!("审核关注点：{prompt}"),
        String::new(),
        format!("【被审核阶段】{}", stage_node.map_or("", |node| &node.name)),
    ];
    if let Some(Node {
        kind: NodeKind::Stage {
            goal, instructions, ..
        },
        ..
    }) = stage_node
    {
        lines.push(format!("阶段目标：{}", fallback(goal, "（未填写）")));
        if !instructions.trim().is_empty() {
            lines.push(format!("阶段要求：{}", instructions.trim()));
        }
    }
    lines.extend([
        String::new(),
        "【阶段执行痕迹】（本阶段内的工具调用时间线）".into(),
        trace.into(),
        String::new(),
        "【主执行模型阶段汇报】".into(),
        fallback(stage.map_or("", |stage| &stage.summary), "（无）").into(),
    ]);
    let mut rejects = state
        .history
        .iter()
        .filter(|item| {
            item["kind"] == "review" && item["node_id"] == review.id && item["decision"] == "reject"
        })
        .peekable();
    if rejects.peek().is_some() {
        lines.extend([String::new(), "【历史审核意见】".into()]);
        for (index, item) in rejects.enumerate() {
            lines.push(format!(
                "第 {} 次驳回：{}",
                index + 1,
                string(&item["message"])
            ));
        }
    }
    lines.join("\n")
}

pub(super) fn stage_trace(messages: &[Value]) -> String {
    let mut lines = Vec::new();
    for message in messages {
        match string(&message["role"]) {
            "assistant" => {
                for call in message["tool_calls"].as_array().into_iter().flatten() {
                    let function = &call["function"];
                    let name = string(&function["name"]);
                    if name.is_empty() {
                        continue;
                    }
                    let mut line = format!("→ {name}");
                    if let Some(args) = tool_args(&function["arguments"]) {
                        line.push_str(&format!("：{args}"));
                    }
                    lines.push(line);
                }
            }
            "tool" => {
                let content = single_line(string(&message["content"]));
                if !content.is_empty() {
                    lines.push(format!("  ↳ {}", truncate(&content, 160, "…")));
                }
            }
            _ => (),
        }
    }
    let trace = lines.join("\n");
    if trace.is_empty() {
        return "（本阶段暂无工具调用记录）".into();
    }
    if let Some((index, _)) = trace.char_indices().rev().nth(3500) {
        return format!(
            "…（前段略）\n{}",
            &trace[index + trace[index..].chars().next().map_or(0, char::len_utf8)..]
        );
    }
    trace
}

fn tool_args(value: &Value) -> Option<String> {
    let parsed;
    let value = if let Some(text) = value.as_str() {
        parsed = serde_json::from_str(text).ok()?;
        &parsed
    } else {
        value
    };
    [
        "command",
        "path",
        "skill_name",
        "query",
        "file_path",
        "url",
        "task",
        "summary",
        "name",
    ]
    .iter()
    .find_map(|key| {
        let text = single_line(string(&value[*key]));
        (!text.is_empty()).then(|| truncate(&text, 80, "…"))
    })
}

fn string(value: &Value) -> &str {
    value.as_str().unwrap_or_default()
}

fn single_line(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn truncate(text: &str, chars: usize, suffix: &str) -> String {
    match text.char_indices().nth(chars) {
        Some((index, _)) => format!("{}{suffix}", &text[..index]),
        None => text.into(),
    }
}

fn fallback<'a>(text: &'a str, default: &'a str) -> &'a str {
    if text.is_empty() { default } else { text }
}
