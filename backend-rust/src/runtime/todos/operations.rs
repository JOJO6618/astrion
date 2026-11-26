use serde_json::{Value, json};

pub(crate) const CREATE: &str = "todo_create";
pub(crate) const UPDATE: &str = "todo_update_task";
const MAX_TEXT: usize = 999;

pub(crate) fn schemas() -> [Value; 2] {
    let intent = json!({"type":"string", "description":"用不超过15个字说明操作目的。"});
    [
        json!({"type":"function","function":{"name":CREATE,"description":"创建待办列表，最多 8 条；覆盖已有列表。","parameters":{"type":"object","properties":{"intent":intent,"overview":{"type":"string"},"tasks":{"type":"array","minItems":1,"maxItems":8,"items":{"type":"object","properties":{"title":{"type":"string"}},"required":["title"]}}},"required":["overview","tasks"]}}}),
        json!({"type":"function","function":{"name":UPDATE,"description":"批量勾选或取消待办任务。","parameters":{"type":"object","properties":{"intent":intent,"task_index":{"type":"integer"},"task_indices":{"type":"array","items":{"type":"integer"},"minItems":1,"maxItems":8},"completed":{"type":"boolean"}},"required":["completed"]}}}),
    ]
}

fn language(en: bool, zh: &str, english: &str) -> String {
    if en { english } else { zh }.to_owned()
}

pub(crate) fn create(args: &Value, en: bool) -> Result<(Value, String), String> {
    let overview = args["overview"].as_str().unwrap_or_default().trim();
    let length = overview.chars().count();
    if length == 0 {
        return Err(language(
            en,
            "任务概述不能为空。",
            "Task overview cannot be empty.",
        ));
    }
    if length > MAX_TEXT {
        return Err(language(
            en,
            &format!("任务概述过长（当前 {length} 字），请精简至 {MAX_TEXT} 字以内。"),
            &format!(
                "Task overview is too long ({length} chars). Please keep it within {MAX_TEXT} chars."
            ),
        ));
    }
    let raw = args["tasks"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default();
    let titles: Vec<_> = raw
        .iter()
        .filter_map(|t| t.as_str().or_else(|| t["title"].as_str()))
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect();
    if titles.is_empty() {
        return Err(language(
            en,
            "需要至少提供一个任务。",
            "Please provide at least one task.",
        ));
    }
    if raw.len() > 8 {
        return Err(language(
            en,
            "任务数量过多，最多允许 8 个任务。",
            "Too many tasks; at most 8 tasks are allowed.",
        ));
    }
    if let Some(title) = titles.iter().find(|s| s.chars().count() > MAX_TEXT) {
        return Err(language(
            en,
            &format!("任务「{title}」过长，请控制在 {MAX_TEXT} 字以内。"),
            &format!("Task \"{title}\" is too long. Please keep it within {MAX_TEXT} chars."),
        ));
    }
    let tasks: Vec<_> = titles
        .into_iter()
        .enumerate()
        .map(|(i, t)| json!({"index":i+1,"title":t,"status":"pending"}))
        .collect();
    Ok((
        json!({"overview":overview,"tasks":tasks,"status":"active","forced_finish":false,"forced_reason":null}),
        language(
            en,
            "待办列表已创建（覆盖之前的列表）。",
            "Todo list created (overwrites a previous list).",
        ),
    ))
}

pub(crate) fn update(mut todo: Value, args: &Value, en: bool) -> Result<(Value, String), String> {
    if !todo.is_object() {
        return Err(language(
            en,
            "当前没有待办列表，请先创建。",
            "No todo list yet; please create one first.",
        ));
    }
    if matches!(todo["status"].as_str(), Some("closed" | "completed")) {
        return Err(language(
            en,
            "待办列表已结束，无法继续修改。",
            "The todo list is finished and cannot be modified.",
        ));
    }
    let indices = args
        .get("task_indices")
        .filter(|v| !v.is_null())
        .unwrap_or(&args["task_index"]);
    let indices = match indices {
        Value::Array(items) => items.iter().filter_map(Value::as_i64).collect::<Vec<_>>(),
        v if v.is_i64() => vec![v.as_i64().unwrap()],
        _ => {
            return Err(language(
                en,
                "task_index 或 task_indices 必须是数字或数字数组。",
                "task_index or task_indices must be a number or an array of numbers.",
            ));
        }
    };
    if indices.is_empty() {
        return Err(language(
            en,
            "请提供至少一个任务序号。",
            "Please provide at least one task index.",
        ));
    }
    let tasks = todo
        .get_mut("tasks")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| "Invalid stored todo tasks".to_owned())?;
    let max = tasks.len() as i64;
    let invalid: Vec<_> = indices.iter().filter(|i| !(1..=max).contains(i)).collect();
    if !invalid.is_empty() {
        return Err(language(
            en,
            &format!("任务序号超出范围（1-{max}）：{invalid:?}"),
            &format!("Task index out of range (1-{max}): {invalid:?}"),
        ));
    }
    let completed = args["completed"].as_bool().unwrap_or(true);
    let mut unique = Vec::new();
    for i in indices {
        if !unique.contains(&i) {
            let row = tasks[i as usize - 1]
                .as_object_mut()
                .ok_or_else(|| "Invalid stored todo entry".to_owned())?;
            row.insert(
                "status".into(),
                json!(if completed { "done" } else { "pending" }),
            );
            unique.push(i);
        }
    }
    let indices = unique
        .iter()
        .map(i64::to_string)
        .collect::<Vec<_>>()
        .join(", ");
    let message = if tasks.iter().all(|t| t["status"] == "done") {
        language(en, "所有任务已完成。", "All tasks completed.")
    } else {
        match (unique.len() == 1, completed) {
            (true, true) => language(
                en,
                &format!("任务 {indices}完成。"),
                &format!("Task {indices} marked as done."),
            ),
            (true, false) => language(
                en,
                &format!("任务 {indices}取消完成。"),
                &format!("Task {indices} unmarked as done."),
            ),
            (false, true) => language(
                en,
                &format!("任务 {indices}全部完成。"),
                &format!("Tasks {indices} all completed."),
            ),
            (false, false) => language(
                en,
                &format!("任务 {indices}已取消完成。"),
                &format!("Tasks {indices} unmarked as done."),
            ),
        }
    };
    Ok((todo, message))
}

pub(crate) fn snapshot(mut todo: Value) -> Value {
    if !todo.is_object() {
        return Value::Null;
    }
    let done = todo["tasks"]
        .as_array()
        .into_iter()
        .flatten()
        .all(|t| t["status"] == "done");
    todo["all_done"] = json!(done);
    todo["instruction"] = json!(if todo["status"] == "closed" {
        "任务已结束，请在总结中说明未完成的事项。"
    } else if todo["status"] == "completed" || done {
        "所有任务已完成，可以结束任务并向用户汇报"
    } else {
        "请在确认完成某项任务后再勾选，然后继续下一步"
    });
    todo
}
