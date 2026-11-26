use serde_json::{Map, Value, json};

use super::transaction::TOO_LARGE;

pub(super) const MAX_GROUPS: usize = 100;
pub(super) const MAX_INPUT_BYTES: u64 = 10 * 1024 * 1024;
const MAX_REPLACEMENT_CHARS: u64 = 9_999_999_999;

pub(super) struct EditReport {
    pub content: String,
    pub fields: Map<String, Value>,
}

pub(super) fn apply(path: &str, original: &str, groups: &[Value]) -> Result<EditReport, Value> {
    let mut current = original.to_owned();
    let mut details = Vec::with_capacity(groups.len());
    let mut replaced = 0;
    let mut found = 0;
    for (offset, group) in groups.iter().enumerate() {
        let index = offset + 1;
        let operation =
            Replacement::parse(group).and_then(|replacement| replacement.apply(&current));
        match operation {
            Ok(change) => {
                current = change.content;
                replaced += change.replaced;
                found += change.found;
                let mut detail = change.detail;
                detail["index"] = index.into();
                details.push(detail);
            }
            Err(error) => {
                let detail = json!({"index": index, "status": error.status, "reason": error.reason,
                    "replace_all": group.get("replace_all").unwrap_or(&Value::Bool(false)),
                    "found_matches": 0, "replacements": 0, "matched_lines": []});
                details.push(detail.clone());
                return Err(json!({
                    "success": false, "path": path, "error": format!("第 {index} 组替换失败：{}", error.reason),
                    "failed_replacement_index": index, "failed_details": [detail], "details": details,
                    "write_performed": false, "completed_replacements": offset,
                }));
            }
        }
    }

    let count = groups.len();
    let fields = json!({
        "replacements": replaced, "found_matches": found, "replacement_groups": count,
        "details": details, "write_performed": true,
        "message": format!("共 {count} 组替换，替换 {replaced} 处"),
    });
    Ok(EditReport {
        content: current,
        fields: fields.as_object().expect("edit metadata object").clone(),
    })
}

struct Replacement<'a> {
    old: &'a str,
    new: &'a str,
    all: bool,
}

struct Change {
    content: String,
    found: usize,
    replaced: usize,
    detail: Value,
}

struct GroupFailure {
    status: &'static str,
    reason: &'static str,
}

impl GroupFailure {
    fn invalid(reason: &'static str) -> Self {
        Self {
            status: "error",
            reason,
        }
    }
}

impl<'a> Replacement<'a> {
    fn parse(group: &'a Value) -> Result<Self, GroupFailure> {
        let group = group.as_object().ok_or_else(|| {
            GroupFailure::invalid("替换项必须是对象，包含 old_string 和 new_string")
        })?;
        let (old, new) = (&group.get("old_string"), &group.get("new_string"));
        let (old, new) = match (old, new) {
            (Some(old), Some(new)) if !old.is_null() && !new.is_null() => {
                (old.as_str(), new.as_str())
            }
            _ => return Err(GroupFailure::invalid("缺少 old_string/new_string")),
        };
        let (Some(old), Some(new)) = (old, new) else {
            return Err(GroupFailure::invalid("old_string/new_string 必须是字符串"));
        };
        let all = group
            .get("replace_all")
            .map_or(Some(false), Value::as_bool)
            .ok_or_else(|| {
                GroupFailure::invalid("replace_all 必须是 true 或 false；不提供时默认为 false")
            })?;
        if old == new {
            return Err(GroupFailure::invalid(
                "old_string 与 new_string 相同，无法执行替换",
            ));
        }
        if old.is_empty() {
            return Err(GroupFailure::invalid(
                "old_string 不能为空，请从 read_file 内容中精确复制",
            ));
        }
        if exceeds_max_chars(old) {
            return Err(GroupFailure::invalid("要替换的文本过长，可能导致性能问题"));
        }
        if exceeds_max_chars(new) {
            return Err(GroupFailure::invalid("替换的新文本过长，建议分块处理"));
        }
        Ok(Self { old, new, all })
    }

    fn apply(self, current: &str) -> Result<Change, GroupFailure> {
        let old_lines = split_lines(self.old);
        if old_lines.len() < 3 {
            return Err(GroupFailure::invalid(
                "old_string 少于3行，请包含目标行及其前后上下文（至少3行）后重试",
            ));
        }
        let mut newlines = current
            .match_indices('\n')
            .map(|(offset, _)| offset)
            .peekable();
        let mut line = 1;
        let matched_lines: Vec<_> = current
            .match_indices(self.old)
            .map(|(offset, _)| {
                while newlines.next_if(|newline| *newline <= offset).is_some() {
                    line += 1;
                }
                line
            })
            .collect();
        let found = matched_lines.len();
        if found == 0 {
            return Err(GroupFailure {
                status: "not_found",
                reason: "未找到要替换的内容",
            });
        }
        let replaced = if self.all { found } else { 1 };
        let byte_count = current.len() - self.old.len() * replaced;
        let mut content = String::new();
        content
            .try_reserve_exact(byte_count.saturating_add(self.new.len().saturating_mul(replaced)))
            .map_err(|_| GroupFailure::invalid(TOO_LARGE))?;
        let mut start = 0;
        for (offset, _) in current.match_indices(self.old).take(replaced) {
            content.push_str(&current[start..offset]);
            content.push_str(self.new);
            start = offset + self.old.len();
        }
        content.push_str(&current[start..]);
        let lines = split_lines(current);
        let contexts: Vec<_> = matched_lines
            .iter()
            .take(replaced)
            .map(|&line| context(&lines, line, old_lines.len()))
            .collect();
        let new_lines = split_lines(self.new);
        let detail = json!({
            "status": "success", "replace_all": self.all, "found_matches": found, "replacements": replaced,
            "matched_lines": matched_lines, "contexts": contexts, "old_string": self.old, "new_string": self.new,
            "old_lines": old_lines, "new_lines": new_lines, "old_line_count": old_lines.len(), "new_line_count": new_lines.len(),
        });
        Ok(Change {
            content,
            found,
            replaced,
            detail,
        })
    }
}

fn exceeds_max_chars(text: &str) -> bool {
    text.len() as u64 > MAX_REPLACEMENT_CHARS && text.chars().count() as u64 > MAX_REPLACEMENT_CHARS
}

fn context(lines: &[&str], start: usize, count: usize) -> Value {
    let end = start + count - 1;
    let select = |range: std::ops::RangeInclusive<usize>| {
        range
            .filter_map(|line| {
                lines
                    .get(line - 1)
                    .map(|text| json!({"line": line, "text": text}))
            })
            .collect::<Vec<_>>()
    };
    json!({"old_start_line": start, "old_end_line": end,
        "before_lines": select(start.saturating_sub(2).max(1)..=start - 1),
        "after_lines": select(end + 1..=(end + 2).min(lines.len())),
    })
}

// Python str.splitlines recognizes Unicode separators and a CRLF is one boundary.
fn split_lines(text: &str) -> Vec<&str> {
    let mut offsets = text.char_indices().peekable();
    let mut start = 0;
    let mut lines = Vec::new();
    while let Some((offset, ch)) = offsets.next() {
        if matches!(
            ch,
            '\n' | '\r' | '\x0b' | '\x0c' | '\x1c'..='\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}'
        ) {
            lines.push(&text[start..offset]);
            start = offset + ch.len_utf8();
            if ch == '\r' && offsets.next_if(|&(_, next)| next == '\n').is_some() {
                start += 1;
            }
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}
