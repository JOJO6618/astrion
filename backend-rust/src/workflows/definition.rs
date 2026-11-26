use super::{Result, WorkflowError};
use serde::{Deserialize, Serialize};
use serde_json::json;
use serde_yaml_ng::Value as Yaml;
use std::collections::HashMap;

pub(crate) const DEFAULT_STAGE_ROUNDS: i64 = 20;
const DEFAULT_REJECTS: i64 = 3;

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Source {
    Builtin,
    #[default]
    User,
    Snapshot,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum ReviewMode {
    Readonly,
    #[default]
    Active,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(default, rename_all = "camelCase")]
pub struct Workflow {
    pub name: String,
    pub description: String,
    pub review_mode: ReviewMode,
    pub max_stage_rounds: i64,
    pub end_conditions: String,
    pub updated_at: String,
    pub body: String,
    pub source: Source,
    pub nodes: Vec<Node>,
}

impl Default for Workflow {
    fn default() -> Self {
        Self {
            name: String::new(),
            description: String::new(),
            review_mode: ReviewMode::Active,
            max_stage_rounds: DEFAULT_STAGE_ROUNDS,
            end_conditions: String::new(),
            updated_at: String::new(),
            body: String::new(),
            source: Source::User,
            nodes: Vec::new(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct Node {
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub position: Option<Position>,
    #[serde(flatten)]
    pub kind: NodeKind,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(default)]
pub struct Position {
    pub x: f64,
    pub y: f64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(
    tag = "kind",
    rename_all = "lowercase",
    rename_all_fields = "camelCase"
)]
pub enum NodeKind {
    Start {
        next: Option<String>,
    },
    End,
    Stage {
        #[serde(default)]
        goal: String,
        #[serde(default)]
        instructions: String,
        next: Option<String>,
    },
    Review {
        #[serde(default)]
        prompt: String,
        next: Option<String>,
        reject_to: Option<String>,
        max_rejects: i64,
    },
    Branch {
        #[serde(default)]
        next: Vec<BranchRoute>,
    },
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
pub struct BranchRoute {
    pub target: String,
    #[serde(default)]
    pub condition: String,
}

impl Node {
    pub(super) fn from_yaml(data: &Yaml) -> Result<Self> {
        let id = text(&data["id"]);
        let name = text(&data["name"]);
        let next = || reference(&data["next"]);
        let kind = match data["kind"].as_str().unwrap_or_default() {
            "start" => NodeKind::Start { next: next()? },
            "end" => NodeKind::End,
            "review" => NodeKind::Review {
                prompt: text(&data["prompt"]),
                next: next()?,
                reject_to: reference(&data["reject_to"])?,
                max_rejects: integer(&data["max_rejects"], DEFAULT_REJECTS)?,
            },
            "branch" => NodeKind::Branch {
                next: sequence(&data["next"], "next")?
                    .iter()
                    .filter_map(|route| {
                        let target = text(&route["target"]);
                        (!target.is_empty()).then(|| BranchRoute {
                            target,
                            condition: text(&route["condition"]),
                        })
                    })
                    .collect(),
            },
            _ => NodeKind::Stage {
                goal: text(&data["goal"]),
                instructions: text(&data["instructions"]),
                next: next()?,
            },
        };
        let xy = &data["position"];
        let position = xy["x"].as_f64().map(|x| Position {
            x,
            y: xy["y"]
                .as_f64()
                .or_else(|| xy["y"].as_str()?.parse().ok())
                .unwrap_or_default(),
        });
        if position.is_some_and(|point| !point.x.is_finite() || !point.y.is_finite()) {
            return Err(invalid("节点坐标必须为有限数值"));
        }
        Ok(Self {
            name: if name.is_empty() { id.clone() } else { name },
            id,
            kind,
            position,
        })
    }

    pub(super) fn targets(&self) -> Vec<&str> {
        match &self.kind {
            NodeKind::Start { next } | NodeKind::Stage { next, .. } => {
                next.iter().map(String::as_str).collect()
            }
            NodeKind::End => Vec::new(),
            NodeKind::Review {
                next, reject_to, ..
            } => next.iter().chain(reject_to).map(String::as_str).collect(),
            NodeKind::Branch { next } => next.iter().map(|route| route.target.as_str()).collect(),
        }
    }
}

impl Workflow {
    pub fn from_markdown(markdown: &str, source: Source) -> Result<Self> {
        let normalized = markdown.replace("\r\n", "\n").replace('\r', "\n");
        let mut lines = normalized.split_inclusive('\n');
        let first = lines
            .next()
            .filter(|line| line.trim_end() == "---")
            .ok_or_else(|| invalid("WORKFLOW.md 缺少 YAML frontmatter"))?;
        let start = first.len();
        let mut offset = start;
        let mut bounds = None;
        for line in lines {
            if line.trim_end() == "---" {
                bounds = Some((offset, offset + line.len()));
                break;
            }
            offset += line.len();
        }
        let (end, body_start) =
            bounds.ok_or_else(|| invalid("WORKFLOW.md 缺少 YAML frontmatter"))?;
        let mut meta: Yaml = serde_yaml_ng::from_str(&normalized[start..end])
            .map_err(|error| invalid(format!("WORKFLOW.md 解析失败：{error}")))?;
        meta.apply_merge()
            .map_err(|error| invalid(format!("WORKFLOW.md 解析失败：{error}")))?;
        if !meta.is_mapping() && !meta.is_null() {
            return Err(invalid("WORKFLOW.md 的 YAML frontmatter 必须是对象"));
        }
        let nodes = sequence(&meta["nodes"], "nodes")?
            .iter()
            .filter(|node| node.is_mapping())
            .map(Node::from_yaml)
            .collect::<Result<_>>()?;
        Ok(Self {
            name: text(&meta["name"]),
            description: text(&meta["description"]),
            review_mode: match meta["review_mode"].as_str() {
                Some("readonly") => ReviewMode::Readonly,
                _ => ReviewMode::Active,
            },
            max_stage_rounds: integer(&meta["max_stage_rounds"], DEFAULT_STAGE_ROUNDS)?,
            end_conditions: text(&meta["end_conditions"]),
            updated_at: text(&meta["updated_at"]),
            body: normalized[body_start..].trim().into(),
            source,
            nodes,
        })
    }

    pub fn to_markdown(&self) -> String {
        // JSON flow mappings are valid YAML and keep strings like `on` and dates
        // scalar strings when Python's YAML 1.1 loader reads the file.
        let nodes = self
            .nodes
            .iter()
            .map(|node| {
                let mut data = serde_json::to_value(node).expect("workflow node is serializable");
                let node_map = data.as_object_mut().expect("node serializes as a mapping");
                if node.name.is_empty() {
                    node_map.shift_remove("name");
                }
                for (camel, snake) in [("rejectTo", "reject_to"), ("maxRejects", "max_rejects")] {
                    if let Some(value) = node_map.shift_remove(camel) {
                        node_map.insert(snake.into(), value);
                    }
                }
                if let Some(pos) = node.position {
                    node_map.insert(
                        "position".into(),
                        json!({
                            "x": pos.x.round_ties_even(), "y": pos.y.round_ties_even(),
                        }),
                    );
                }
                data
            })
            .collect::<Vec<_>>();
        let yaml = serde_json::to_string_pretty(&json!({
            "name": self.name,
            "description": self.description,
            "review_mode": self.review_mode,
            "max_stage_rounds": self.max_stage_rounds,
            "end_conditions": self.end_conditions,
            "updated_at": self.updated_at,
            "nodes": nodes,
        }))
        .expect("workflow metadata is serializable")
        .replace('\u{0085}', "\\u0085")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029");
        format!("---\n{yaml}\n---\n\n{}\n", self.body.trim())
    }

    pub fn validate(&self) -> Result<()> {
        let errors = self.validation_errors();
        if errors.is_empty() {
            Ok(())
        } else {
            Err(invalid(format!(
                "工作流结构校验未通过：{}",
                errors.join("；")
            )))
        }
    }

    pub fn validation_errors(&self) -> Vec<String> {
        let mut errors = Vec::new();
        if self.name.trim().is_empty() {
            errors.push("工作流缺少 name".into());
        }
        if self.nodes.is_empty() {
            errors.push("至少需要一个开始节点和一个结束节点".into());
            return errors;
        }
        let mut by_id = HashMap::new();
        let mut starts = 0;
        let mut ends = 0;
        for node in &self.nodes {
            if node.id.is_empty() {
                errors.push("节点缺少 id".into());
            }
            if by_id.insert(node.id.as_str(), &node.kind).is_some() {
                errors.push(format!("节点 id 重复：{}", node.id));
            }
            starts += usize::from(matches!(node.kind, NodeKind::Start { .. }));
            ends += usize::from(matches!(node.kind, NodeKind::End));
        }
        if starts == 0 {
            errors.push("缺少开始节点".into());
        } else if starts > 1 {
            errors.push(format!("开始节点只能有一个（当前 {starts} 个）"));
        }
        if ends == 0 {
            errors.push("缺少结束节点".into());
        }
        let mut check = |target: Option<&str>, label: &str| {
            let error = match target.filter(|value| !value.is_empty()) {
                None => Some(format!("{label}未连接")),
                Some(id) => match by_id.get(id) {
                    None => Some(format!("{label}指向不存在的节点：{id}")),
                    Some(NodeKind::Start { .. }) => Some(format!("{label}不能指向开始节点")),
                    _ => None,
                },
            };
            errors.extend(error);
        };
        for node in &self.nodes {
            let name = if node.name.is_empty() {
                &node.id
            } else {
                &node.name
            };
            match &node.kind {
                NodeKind::Start { next } => check(next.as_deref(), "开始节点"),
                NodeKind::End => (),
                NodeKind::Stage { next, .. } => check(next.as_deref(), &format!("阶段「{name}」")),
                NodeKind::Review {
                    next, reject_to, ..
                } => {
                    check(next.as_deref(), &format!("审核「{name}」的通过路由"));
                    check(reject_to.as_deref(), &format!("审核「{name}」的驳回路由"));
                }
                NodeKind::Branch { next } => {
                    for route in next {
                        check(Some(&route.target), &format!("分支「{name}」的出线"));
                    }
                }
            }
        }
        for node in &self.nodes {
            if matches!(node.kind, NodeKind::Review { max_rejects, .. } if max_rejects < 1) {
                errors.push(format!("审核「{}」驳回上限必须 ≥ 1", node.name));
            }
        }
        errors
    }

    pub fn node(&self, id: &str) -> Option<&Node> {
        self.nodes.iter().find(|node| node.id == id)
    }

    pub fn entry_node(&self) -> Option<&Node> {
        let target = self.nodes.iter().find_map(|node| match &node.kind {
            NodeKind::Start { next } => next.as_deref(),
            _ => None,
        })?;
        self.node(target)
    }
}

fn invalid(message: impl Into<String>) -> WorkflowError {
    WorkflowError::new(400, message)
}

fn sequence<'a>(value: &'a Yaml, field: &str) -> Result<&'a [Yaml]> {
    if value.is_null() {
        return Ok(&[]);
    }
    value
        .as_sequence()
        .map(Vec::as_slice)
        .ok_or_else(|| invalid(format!("WORKFLOW.md 的 {field} 必须是数组")))
}

fn text(value: &Yaml) -> String {
    match value {
        Yaml::String(text) => text.clone(),
        Yaml::Bool(true) => "True".into(),
        Yaml::Number(number) if number.as_f64() != Some(0.0) => number.to_string(),
        _ => String::new(),
    }
}

fn reference(value: &Yaml) -> Result<Option<String>> {
    match value {
        Yaml::Mapping(_) | Yaml::Sequence(_) => Err(invalid("节点连接必须是单个节点 id")),
        _ => Ok(Some(text(value)).filter(|target| !target.is_empty())),
    }
}

fn integer(value: &Yaml, default: i64) -> Result<i64> {
    match value {
        Yaml::Null | Yaml::Bool(false) => Ok(default),
        Yaml::Bool(true) => Ok(1),
        Yaml::String(text) if text.is_empty() => Ok(default),
        Yaml::String(text) => text
            .trim()
            .parse()
            .map_err(|_| invalid("工作流轮数或驳回上限必须是整数")),
        Yaml::Number(number) if number.as_f64() == Some(0.0) => Ok(default),
        Yaml::Number(number) => number
            .as_i64()
            .or_else(|| {
                number
                    .as_f64()
                    .filter(|value| {
                        value.is_finite() && *value >= i64::MIN as f64 && *value < i64::MAX as f64
                    })
                    .map(|value| value as i64)
            })
            .ok_or_else(|| invalid("工作流轮数或驳回上限必须是整数")),
        _ => Err(invalid("工作流轮数或驳回上限必须是整数")),
    }
}
