//! Model history tools use the same workspace binding and review formatter as the browser.
use serde_json::{Value, json};

use crate::conversation_db::Database;

use super::{lines, safe_title};
use crate::runtime::{Runtime, RuntimeError, writable_task};

pub(in crate::runtime) const SEARCH: &str = "conversation_search";
pub(in crate::runtime) const REVIEW: &str = "conversation_review";

pub(in crate::runtime) fn definitions() -> [Value; 2] {
    let intent = json!({"type":"string","description":"用不超过 15 个字说明操作目的。"});
    [
        json!({"type":"function","function":{"name":SEARCH,"description":"按标题、首条消息或日期搜索当前工作区的已迁移历史对话。",
            "parameters":{"type":"object","properties":{"intent":intent,"query":{"type":"string"},
                "keywords":{"type":"array","maxItems":3,"items":{"type":"string"}},
                "start_date":{"type":"string"},"end_date":{"type":"string"},"limit":{"type":"integer"}}}}}),
        json!({"type":"function","function":{"name":REVIEW,"description":"按 ID 回顾当前工作区历史对话。长记录可保存为 .astrion/review/ 下的 Markdown。",
            "parameters":{"type":"object","properties":{"intent":intent,"conversation_id":{"type":"string"},
                "mode":{"type":"string","enum":["read","save"]},"content_mode":{"type":"string","enum":["dialogue","full"]}},
                "required":["conversation_id","mode"]}}}),
    ]
}

pub(in crate::runtime) struct Review {
    pub(in crate::runtime) result: Value,
    pub(in crate::runtime) save: Option<Value>,
}

impl Runtime {
    pub(in crate::runtime) fn agent_conversations_search(
        &self,
        task_id: &str,
        args: &Value,
    ) -> Result<Value, RuntimeError> {
        let raw_query = text(args, "query").unwrap_or_default();
        let raw_keywords: Vec<_> = args["keywords"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::trim)
            .filter(|item| !item.is_empty())
            .take(3)
            .collect();
        let keywords: Vec<_> = raw_keywords
            .iter()
            .copied()
            .chain((!raw_query.is_empty()).then_some(raw_query))
            .take(3)
            .map(str::to_lowercase)
            .collect();
        let start = date(args, "start_date")?;
        let end = date(args, "end_date")?;
        if start.zip(end).is_some_and(|(start, end)| start > end) {
            return Err(RuntimeError::bad_request("起始日期不能晚于结束日期。"));
        }
        let limit = args["limit"].as_i64().unwrap_or(10).clamp(1, 100) as usize;
        let (workspace, current, mut rows, database) = {
            let mut store = self.store()?;
            let task = writable_task(&mut store, task_id).map_err(RuntimeError::bad_request)?;
            let (workspace, current) = (task.workspace_id.clone(), task.conversation_id.clone());
            let rows = store.session_rows(&workspace)?;
            let path = store.conversation_path(&workspace, &current)?;
            let database = Database::open(path.parent().ok_or_else(|| {
                RuntimeError::storage("Missing workspace conversation directory")
            })?)
            .map_err(RuntimeError::storage)?;
            (workspace, current, rows, database)
        };
        rows.sort_by(|left, right| {
            right["updated_at"]
                .as_str()
                .cmp(&left["updated_at"].as_str())
                .then_with(|| left["id"].as_str().cmp(&right["id"].as_str()))
        });
        let mut results = Vec::new();
        for row in rows {
            let Some(id) = row["id"]
                .as_str()
                .filter(|id| *id != current && row["incompatible"] != true)
            else {
                continue;
            };
            let created = row["created_at"].as_str().and_then(|text| text.get(..10));
            if created
                .zip(start)
                .is_some_and(|(created, start)| created < start)
                || created.zip(end).is_some_and(|(created, end)| created > end)
            {
                continue;
            }
            let first = database
                .first_user(id)
                .map_err(RuntimeError::storage)?
                .and_then(|row| {
                    let value = &row["content"];
                    let text = value.as_str().map(str::to_owned).unwrap_or_else(|| {
                        value
                            .as_array()
                            .into_iter()
                            .flatten()
                            .filter_map(|part| part["text"].as_str())
                            .collect::<Vec<_>>()
                            .join(" ")
                    });
                    let excerpt: String = text.trim().chars().take(100).collect();
                    (!excerpt.is_empty()).then_some(excerpt)
                });
            let Some(first) = first else {
                continue;
            };
            let title = row["title"].as_str().unwrap_or("未命名对话");
            let haystack = format!("{title} {first}").to_lowercase();
            if !keywords.is_empty() && !keywords.iter().any(|keyword| haystack.contains(keyword)) {
                continue;
            }
            results.push(json!({"id":id,"title":title,"first_user_message":first,
                "created_at":row["created_at"],"updated_at":row["updated_at"],
                "total_messages":row["total_messages"],"total_tools":row["total_tools"]}));
            if results.len() == limit {
                break;
            }
        }
        Ok(
            json!({"success":true,"query":raw_query,"keywords":raw_keywords,"start_date":start,"end_date":end,
            "workspace_id":workspace,"limit":limit,"excluded_conversation_id":current,"count":results.len(),"results":results}),
        )
    }

    pub(in crate::runtime) fn agent_conversation_review(
        &self,
        task_id: &str,
        args: &Value,
    ) -> Result<Review, RuntimeError> {
        let id = text(args, "conversation_id")
            .ok_or_else(|| RuntimeError::bad_request("缺少对话 ID。"))?;
        let mode = text(args, "mode")
            .filter(|mode| matches!(*mode, "read" | "save"))
            .ok_or_else(|| RuntimeError::bad_request("mode 必须为 read 或 save。"))?;
        let content_mode = text(args, "content_mode").unwrap_or("dialogue");
        if !matches!(content_mode, "dialogue" | "full") {
            return Err(RuntimeError::bad_request(
                "content_mode 必须为 dialogue 或 full。",
            ));
        }
        let doc = {
            let mut store = self.store()?;
            let workspace = writable_task(&mut store, task_id)
                .map_err(RuntimeError::bad_request)?
                .workspace_id
                .clone();
            let doc = store.read(&workspace, id)?;
            let root = store
                .workspaces
                .get(&workspace)
                .ok_or_else(RuntimeError::session_not_found)?;
            crate::runtime::goals::verify_conversation_root(root, doc.raw())
                .map_err(|_| RuntimeError::session_not_found())?;
            doc
        };
        let title = doc.raw()["title"].as_str().unwrap_or_default();
        let full = content_mode == "full";
        let content = lines::build(&doc.raw()["messages"], full, None).join(
            "
",
        ) + "
";
        let size = content.chars().count();
        let mut result = json!({"success":true,"mode":mode,"content_mode":content_mode,"conversation_id":id,
            "title":title,"char_count":size});
        if mode == "read" && size <= 50_000 {
            result["content"] = json!(content);
            return Ok(Review { result, save: None });
        }
        let annotation = if full {
            "回顾模式：完整记录（含工具调用与结果）"
        } else {
            "回顾模式：纯净对话"
        };
        let filename = format!(
            ".astrion/review/review_{}_{}.md",
            safe_title(title).chars().take(80).collect::<String>(),
            uuid::Uuid::new_v4().simple()
        );
        result["path"] = json!(&filename);
        result["too_long"] = json!(mode == "read");
        let save = json!({"file_path":filename,"content":format!("> {annotation}

{content}"), "intent":args["intent"]});
        Ok(Review {
            result,
            save: Some(save),
        })
    }
}

fn text<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args[key]
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn date<'a>(args: &'a Value, key: &str) -> Result<Option<&'a str>, RuntimeError> {
    let Some(date) = text(args, key) else {
        return Ok(None);
    };
    let valid = || -> Option<time::Date> {
        if date.len() != 10 || date.as_bytes()[4] != b'-' || date.as_bytes()[7] != b'-' {
            return None;
        }
        let year = date.get(..4)?.parse().ok()?;
        let month = time::Month::try_from(date.get(5..7)?.parse::<u8>().ok()?).ok()?;
        let day = date.get(8..10)?.parse().ok()?;
        time::Date::from_calendar_date(year, month, day).ok()
    };
    valid()
        .map(|_| Some(date))
        .ok_or_else(|| RuntimeError::bad_request(format!("{key} 必须为 YYYY-MM-DD 日期。")))
}
