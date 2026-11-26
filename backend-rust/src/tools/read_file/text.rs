use serde_json::{Map, Value, json};

pub(super) struct LineRange {
    pub start: usize,
    pub end: usize,
}

pub(super) struct Search<'a> {
    pub query: &'a str,
    pub max_matches: usize,
    pub before: usize,
    pub after: usize,
    pub case_sensitive: bool,
}

pub(super) struct Extraction<'a> {
    pub segments: &'a [Value],
}

const LINE_BLOCK: usize = 256;
const INVALID_UTF8: &str = "文件不是 UTF-8 文本，无法直接读取。";
type ResultFields = Map<String, Value>;

pub(super) struct TextFile {
    text: String,
    line_count: usize,
    block_offsets: Vec<usize>,
}

impl TextFile {
    pub fn new(mut bytes: Vec<u8>) -> Result<Self, String> {
        normalize_newlines(&mut bytes);
        let text = String::from_utf8(bytes).map_err(|_| INVALID_UTF8)?;
        let mut line_count = 0usize;
        let mut block_offsets = vec![0];
        for (offset, _) in text.match_indices('\n') {
            line_count += 1;
            if line_count.is_multiple_of(LINE_BLOCK) {
                block_offsets.push(offset + 1);
            }
        }
        if !text.is_empty() && !text.ends_with('\n') {
            line_count += 1;
        }
        Ok(Self {
            text,
            line_count,
            block_offsets,
        })
    }

    pub fn read(
        &self,
        path: &str,
        size: u64,
        range: LineRange,
        max_chars: usize,
    ) -> Result<ResultFields, String> {
        let (start, end, source) = if self.line_count == 0 {
            (0, 0, "")
        } else {
            if range.start > self.line_count {
                return Err("起始行超出文件长度".into());
            }
            let end = range.end.min(self.line_count);
            (range.start, end, self.span(range.start, end))
        };
        let mut budget = CharacterBudget::new(max_chars);
        let (content, _) = budget.take(source);
        Ok(result_fields(json!({
            "content": content,
            "line_start": start,
            "line_end": end,
            "total_lines": self.line_count,
            "file_size": size,
            "char_count": budget.consumed(),
            "message": format!("已读取 {path} 的内容（行 {start}~{end}）"),
            "truncated": budget.truncated,
        })))
    }

    pub fn search(
        &self,
        path: &str,
        size: u64,
        search: Search<'_>,
        max_chars: usize,
    ) -> ResultFields {
        let windows = self.search_windows(&search);
        let actual_matches = windows.len();
        let mut matches = Vec::new();
        let mut budget = CharacterBudget::new(max_chars);
        for window in windows {
            if budget.exhausted() {
                break;
            }
            let (snippet, truncated) = budget.take(self.span(window.start, window.end));
            let mut value = json!({"line_start": window.start, "line_end": window.end, "hits": window.hits, "snippet": snippet});
            if truncated {
                value["truncated"] = Value::Bool(true);
            }
            matches.push(value);
        }
        let count = matches.len();
        let query = search.query;
        result_fields(json!({
            "file_size": size,
            "query": query,
            "max_matches": search.max_matches,
            "actual_matches": actual_matches,
            "returned_matches": count,
            "context_before": search.before,
            "context_after": search.after,
            "case_sensitive": search.case_sensitive,
            "matches": matches,
            "char_count": budget.consumed(),
            "message": format!("在 {path} 中搜索 \"{query}\"，返回 {count} 条结果"),
            "truncated": budget.truncated,
        }))
    }

    fn search_windows(&self, search: &Search<'_>) -> Vec<Window> {
        let lowercase_query = (!search.case_sensitive).then(|| search.query.to_lowercase());
        let mut windows: Vec<Window> = Vec::new();
        for (index, line) in self.text.split_inclusive('\n').enumerate() {
            let matched = match &lowercase_query {
                Some(query) => line.to_lowercase().contains(query),
                None => line.contains(search.query),
            };
            if !matched {
                continue;
            }
            let index = index + 1;
            let start = index.saturating_sub(search.before).max(1);
            let end = (index + search.after).min(self.line_count);
            if let Some(window) = windows.last_mut()
                && start <= window.end
            {
                window.end = window.end.max(end);
                window.hits.push(index);
                continue;
            }
            if windows.len() >= search.max_matches {
                break;
            }
            windows.push(Window {
                start,
                end,
                hits: vec![index],
            });
        }
        windows
    }

    pub fn extract(
        &self,
        path: &str,
        size: u64,
        extraction: Extraction<'_>,
        max_chars: usize,
    ) -> Result<ResultFields, String> {
        let mut ranges = Vec::with_capacity(extraction.segments.len());
        for segment in extraction.segments {
            if !segment.is_object() {
                return Err("segments 数组中的每一项都必须是对象".into());
            }
            let start = &segment["start_line"];
            let end = &segment["end_line"];
            if start.is_null() || end.is_null() {
                return Err("所有区间都必须包含 start_line 和 end_line".into());
            }
            let (Some(start), Some(end)) = (line_index(start), line_index(end)) else {
                return Err("行区间不合法".into());
            };
            if start == 0 || end < start {
                return Err("行区间不合法".into());
            }
            if start > self.line_count {
                return Err(format!("区间起点 {start} 超出文件行数"));
            }
            ranges.push((&segment["label"], start, end.min(self.line_count)));
        }
        let mut budget = CharacterBudget::new(max_chars);
        let mut segments = Vec::new();
        for (label, start, end) in ranges {
            if budget.exhausted() {
                break;
            }
            let (content, truncated) = budget.take(self.span(start, end));
            let mut value =
                json!({"label": label, "line_start": start, "line_end": end, "content": content});
            if truncated {
                value["truncated"] = Value::Bool(true);
            }
            segments.push(value);
        }
        let count = segments.len();
        Ok(result_fields(json!({
            "segments": segments,
            "file_size": size,
            "total_lines": self.line_count,
            "segment_count": count,
            "char_count": budget.consumed(),
            "message": format!("已从 {path} 抽取 {count} 个片段"),
            "truncated": budget.truncated,
        })))
    }

    fn span(&self, start: usize, end: usize) -> &str {
        &self.text[self.line_offset(start - 1)..self.line_offset(end)]
    }

    fn line_offset(&self, index: usize) -> usize {
        if index == self.line_count {
            return self.text.len();
        }
        let block_offset = self.block_offsets[index / LINE_BLOCK];
        let in_block = index % LINE_BLOCK;
        if in_block == 0 {
            return block_offset;
        }
        let (offset, _) = self.text[block_offset..]
            .match_indices('\n')
            .nth(in_block - 1)
            .expect("line index within the file");
        block_offset + offset + 1
    }
}

struct Window {
    start: usize,
    end: usize,
    hits: Vec<usize>,
}

struct CharacterBudget {
    limit: usize,
    remaining: usize,
    truncated: bool,
}

impl CharacterBudget {
    fn new(limit: usize) -> Self {
        Self {
            limit,
            remaining: limit,
            truncated: false,
        }
    }

    fn consumed(&self) -> usize {
        self.limit - self.remaining
    }

    fn exhausted(&mut self) -> bool {
        if self.remaining == 0 {
            self.truncated = true;
        }
        self.remaining == 0
    }

    fn take<'a>(&mut self, source: &'a str) -> (&'a str, bool) {
        let mut chars = source.char_indices();
        for consumed in 0..self.remaining {
            if chars.next().is_none() {
                self.remaining -= consumed;
                return (source, false);
            }
        }
        self.remaining = 0;
        if let Some((offset, _)) = chars.next() {
            self.truncated = true;
            return (&source[..offset], true);
        }
        (source, false)
    }
}

fn line_index(value: &Value) -> Option<usize> {
    value
        .as_u64()
        .map(|value| usize::try_from(value).unwrap_or(usize::MAX))
        .or_else(|| value.as_bool().map(usize::from))
}

fn result_fields(value: Value) -> ResultFields {
    match value {
        Value::Object(fields) => fields,
        _ => unreachable!("read_file result fields must be an object"),
    }
}

pub(in crate::tools) fn normalize_newlines(bytes: &mut Vec<u8>) {
    let Some(mut output) = bytes.iter().position(|byte| *byte == b'\r') else {
        return;
    };
    let mut input = output;
    while input < bytes.len() {
        let byte = bytes[input];
        bytes[output] = if byte == b'\r' { b'\n' } else { byte };
        input += 1;
        if byte == b'\r' && bytes.get(input) == Some(&b'\n') {
            input += 1;
        }
        output += 1;
    }
    bytes.truncate(output);
}
