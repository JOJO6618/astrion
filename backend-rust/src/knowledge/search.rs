use serde::Serialize;
use serde_json::{Value, json};

pub(super) struct Frontmatter<'a> {
    pub name: &'a str,
    pub description: &'a str,
    body_start: usize,
}

impl<'a> Frontmatter<'a> {
    pub fn parse(text: &'a str, strip_quotes: bool) -> Self {
        let mut result = Self {
            name: "",
            description: "",
            body_start: 0,
        };
        let mut lines = text.split('\n');
        if lines.next().map(str::trim) != Some("---") {
            return result;
        }
        for (index, line) in lines.enumerate() {
            if line.trim() == "---" {
                result.body_start = index + 2;
                return result;
            }
            let Some((key, value)) = line.split_once(':') else {
                continue;
            };
            let mut value = value.trim();
            if strip_quotes {
                value = value.trim_matches('"').trim_matches('\'');
                if value.is_empty() {
                    continue;
                }
            }
            match key.trim() {
                "name" => result.name = value,
                "description" => result.description = value,
                _ => {}
            }
        }
        Self {
            name: "",
            description: "",
            body_start: 0,
        }
    }
}

pub(super) struct Query<'a> {
    keywords: Vec<(&'a str, String)>,
    limit: usize,
}

impl<'a> Query<'a> {
    pub fn parse(arguments: &'a Value, tool: &str) -> Result<Self, String> {
        let mut keywords: Vec<(&str, String)> = Vec::new();
        for keyword in arguments["keywords"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            let keyword = keyword.trim();
            if !keyword.is_empty() && !keywords.iter().any(|(seen, _)| *seen == keyword) {
                keywords.push((keyword, keyword.to_lowercase()));
                if keywords.len() == 5 {
                    break;
                }
            }
        }
        if keywords.is_empty() {
            return Err(format!("{tool} 需要至少 1 个关键词"));
        }
        let raw = &arguments["max_results"];
        let limit = raw
            .as_f64()
            .or_else(|| {
                raw.as_str()
                    .and_then(|raw| raw.trim().parse::<i64>().ok())
                    .map(|limit| limit as f64)
            })
            .map_or(5, |limit| {
                if limit == 0.0 {
                    5
                } else {
                    (limit as i64).clamp(1, 10) as usize
                }
            });
        Ok(Self { keywords, limit })
    }

    pub fn matches(&self, id: &str, meta: &Frontmatter<'_>, text: &str) -> Option<Matches<'a>> {
        let name = meta.name.to_lowercase();
        let id = id.to_lowercase();
        let description = meta.description.to_lowercase();
        let mut body_hits = [0; 5];
        let mut snippets = Vec::new();
        for (index, line) in text.split('\n').enumerate().skip(meta.body_start) {
            let lower_line = line.to_lowercase();
            let mut matched = false;
            for (keyword, hits) in self.keywords.iter().zip(&mut body_hits) {
                if lower_line.contains(&keyword.1) {
                    *hits = (*hits + 1).min(5);
                    matched = true;
                }
            }
            if matched && snippets.len() < 3 {
                snippets.push(Snippet {
                    line: index + 1,
                    text: ellipsize(line.trim(), 120),
                });
            }
        }
        let mut score = 0;
        let mut matched_keywords = Vec::new();
        for ((keyword, lower), body) in self.keywords.iter().zip(body_hits) {
            let keyword_score = body
                + 10 * usize::from(id.contains(lower) || name.contains(lower))
                + 5 * usize::from(description.contains(lower));
            if keyword_score > 0 {
                score += keyword_score;
                matched_keywords.push(*keyword);
            }
        }
        (score > 0).then_some(Matches {
            score,
            matched_keywords,
            snippets,
        })
    }

    pub fn result(&self, kind: Kind, mut hits: Vec<Hit<'a>>, empty_catalog: bool) -> Value {
        hits.sort_by(|a, b| {
            b.matches
                .score
                .cmp(&a.matches.score)
                .then_with(|| {
                    b.matches
                        .matched_keywords
                        .len()
                        .cmp(&a.matches.matched_keywords.len())
                })
                .then_with(|| a.title().cmp(b.title()))
        });
        hits.truncate(self.limit);
        let count = hits.len();
        let keywords: Vec<&str> = self.keywords.iter().map(|(keyword, _)| *keyword).collect();
        let keywords_text = keywords.join(match kind {
            Kind::Skill => ", ",
            Kind::Topic => "、",
        });
        let (summary, content) = if empty_catalog {
            let message = match kind {
                Kind::Skill => "当前没有可用的技能，请按常规方式完成任务。",
                Kind::Topic => "项目记忆目录不存在，暂无项目记忆可检索。",
            };
            (message.to_owned(), message.to_owned())
        } else if count == 0 {
            match kind {
                Kind::Skill => (
                    "未找到匹配的技能".into(),
                    format!(
                        "未找到匹配的技能（关键词：{keywords_text}）。不要更换同义词重复检索，请按常规方式完成任务。"
                    ),
                ),
                Kind::Topic => (
                    "未找到匹配的项目记忆".into(),
                    format!(
                        "未找到匹配的项目记忆（关键词：{keywords_text}）。不要更换关键词重复检索；继续当前任务即可。"
                    ),
                ),
            }
        } else {
            let label = match kind {
                Kind::Skill => "技能",
                Kind::Topic => "项目记忆",
            };
            let summary = format!("找到 {count} 个匹配的{label}");
            let header = match kind {
                Kind::Skill => format!("技能检索（关键词：{keywords_text}）共命中 {count} 个："),
                Kind::Topic => format!("{summary}（关键词：{keywords_text}）："),
            };
            let mut lines = vec![header, String::new()];
            for (index, hit) in hits.iter().enumerate() {
                hit.describe(index + 1, &mut lines);
            }
            lines.push(
                match kind {
                    Kind::Skill => "如需完整内容，使用 read_skill 读取对应技能，再按其指引执行。",
                    Kind::Topic => "如需完整内容，使用 recall_project_memory 读取对应记忆。",
                }
                .into(),
            );
            (summary, lines.join("\n"))
        };
        let results: Vec<Value> = hits.into_iter().map(Hit::into_value).collect();
        json!({"success": true, "count": count, "keywords": keywords, "results": results,
            "summary": summary, "content": content})
    }
}

#[derive(Clone, Copy)]
pub(super) enum Kind {
    Skill,
    Topic,
}

pub(super) enum Entry {
    Skill {
        id: String,
        path: String,
        conflict_path: String,
    },
    Topic {
        file: String,
        id: String,
        name: String,
    },
}

pub(super) struct Hit<'a> {
    pub entry: Entry,
    pub description: String,
    pub matches: Matches<'a>,
}

pub(super) struct Matches<'a> {
    score: usize,
    matched_keywords: Vec<&'a str>,
    snippets: Vec<Snippet>,
}

#[derive(Serialize)]
struct Snippet {
    line: usize,
    text: String,
}

impl Hit<'_> {
    fn title(&self) -> &str {
        match &self.entry {
            Entry::Skill { id, .. } => id,
            Entry::Topic { name, .. } => name,
        }
    }

    fn describe(&self, rank: usize, lines: &mut Vec<String>) {
        match &self.entry {
            Entry::Skill {
                id,
                path,
                conflict_path,
            } => {
                lines.push(format!(
                    "[{rank}] {id} — {path}（命中：{}）",
                    self.matches.matched_keywords.join(", ")
                ));
                if !conflict_path.is_empty() {
                    lines.push(format!("    ⚠️ 同名重复：{conflict_path} 也存在，read_skill 会报错，请用 read_file 按具体路径读取"));
                }
            }
            Entry::Topic { file, name, .. } => lines.push(format!("[{rank}] {name}（{file}）")),
        }
        if !self.description.is_empty() {
            lines.push(format!("    描述：{}", self.description));
        }
        if !self.matches.snippets.is_empty() {
            lines.push("    匹配片段：".into());
            for snippet in &self.matches.snippets {
                lines.push(format!("      L{}: {}", snippet.line, snippet.text));
            }
        }
        lines.push(String::new());
    }

    fn into_value(self) -> Value {
        let mut value = match self.entry {
            Entry::Skill {
                id,
                path,
                conflict_path,
            } => json!({"skill_id": id, "path": path, "conflict_path": conflict_path}),
            Entry::Topic { file, id, name } => {
                json!({"file": file, "name": name, "memory_name": id})
            }
        };
        value["description"] = json!(self.description);
        value["score"] = json!(self.matches.score);
        value["matched_keywords"] = json!(self.matches.matched_keywords);
        value["snippets"] = json!(self.matches.snippets);
        value
    }
}

pub(super) fn ellipsize(text: &str, limit: usize) -> String {
    if text.char_indices().nth(limit).is_some() {
        let end = text
            .char_indices()
            .nth(limit - 3)
            .map_or(text.len(), |(index, _)| index);
        format!("{}...", &text[..end])
    } else {
        text.to_owned()
    }
}
