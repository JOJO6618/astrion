use std::{
    borrow::Cow,
    collections::HashMap,
    path::{Path, PathBuf},
};

use serde::Serialize;
use serde_json::Value;

use super::checkout::{Changes, Checkout};

const MAX_CONTEXT: usize = 200;

type Contexts = HashMap<String, usize>;

pub(super) struct DiffOptions {
    pub(super) context: usize,
    per_file: Contexts,
    folds: HashMap<String, Contexts>,
}

impl DiffOptions {
    pub(super) fn from_query(params: &HashMap<String, String>) -> Self {
        let context = params
            .get("context")
            .and_then(|value| int_context(value))
            .unwrap_or(3);
        let per_file = contexts(&query_json(params, "context_map"));
        let fold_context = query_json(params, "fold_context_map");
        let folds = fold_context
            .as_object()
            .into_iter()
            .flatten()
            .filter_map(|(path, value)| {
                let counts = contexts(value);
                (!path.is_empty() && !counts.is_empty()).then(|| (path.clone(), counts))
            })
            .collect();
        Self {
            context,
            per_file,
            folds,
        }
    }
}

fn query_json(params: &HashMap<String, String>, key: &str) -> Value {
    params
        .get(key)
        .and_then(|value| serde_json::from_str(value).ok())
        .unwrap_or_default()
}

fn contexts(input: &Value) -> Contexts {
    input
        .as_object()
        .into_iter()
        .flatten()
        .filter_map(|(key, value)| {
            if key.is_empty() {
                return None;
            }
            let count = match value {
                Value::String(value) => int_context(value),
                Value::Bool(value) => Some(usize::from(*value)),
                Value::Number(number) => {
                    let text = number.to_string();
                    if text.contains(['.', 'e', 'E']) {
                        number
                            .as_f64()
                            .filter(|value| value.is_finite())
                            .map(|value| value.clamp(0., MAX_CONTEXT as f64) as usize)
                    } else {
                        int_context(&text)
                    }
                }
                _ => None,
            }?;
            Some((key.clone(), count))
        })
        .collect()
}

fn int_context(value: &str) -> Option<usize> {
    let value = value.trim();
    let digits = value.strip_prefix(['+', '-']).unwrap_or(value);
    let mut result = 0;
    for part in digits.split('_') {
        if part.is_empty() {
            return None;
        }
        for digit in part.chars() {
            result = (result * 10 + digit.to_digit(10)? as usize).min(MAX_CONTEXT);
        }
    }
    Some(if value.starts_with('-') { 0 } else { result })
}

#[derive(Serialize)]
pub(super) struct FilePatch {
    path: String,
    old_path: String,
    additions: usize,
    deletions: usize,
    hunks: Vec<Hunk>,
    after_hidden: usize,
    after_fold_key: &'static str,
}

#[derive(Serialize)]
struct Hunk {
    header: String,
    old_start: i64,
    old_count: i64,
    new_start: i64,
    new_count: i64,
    before_hidden: usize,
    before_fold_key: String,
    lines: Vec<Line>,
}

#[derive(Serialize)]
struct Line {
    kind: &'static str,
    old_line: Option<i64>,
    new_line: Option<i64>,
    text: String,
}

pub(super) fn files(
    checkout: &Checkout,
    changes: &Changes,
    options: &DiffOptions,
) -> Vec<FilePatch> {
    let mut files = Vec::new();
    if options.per_file.is_empty() {
        parse(
            checkout,
            &checkout.diff(changes, options.context, []),
            &options.folds,
            &mut files,
        );
    } else {
        for change in &changes.entries {
            let context = options
                .per_file
                .get(change.path.to_string_lossy().as_ref())
                .copied()
                .unwrap_or(options.context);
            parse(
                checkout,
                &checkout.diff(changes, context, change.paths()),
                &options.folds,
                &mut files,
            );
        }
    }
    files
}

fn parse(
    checkout: &Checkout,
    bytes: &[u8],
    folds: &HashMap<String, Contexts>,
    files: &mut Vec<FilePatch>,
) {
    let text = String::from_utf8_lossy(bytes);
    for block in text.split("\ndiff --git ") {
        if let Some(mut file) = parse_file(block) {
            file.expand(checkout, folds.get(&file.path));
            files.push(file);
        }
    }
}

fn parse_file(block: &str) -> Option<FilePatch> {
    let mut file = FilePatch {
        path: String::new(),
        old_path: String::new(),
        additions: 0,
        deletions: 0,
        hunks: Vec::new(),
        after_hidden: 0,
        after_fold_key: "after",
    };
    let (mut old_line, mut new_line) = (0, 0);
    for line in text_lines(block) {
        if line.starts_with("@@ ") {
            let (old_start, old_count, new_start, new_count) = ranges(line).unwrap_or_default();
            file.hunks.push(Hunk {
                header: line.to_owned(),
                old_start,
                old_count,
                new_start,
                new_count,
                before_hidden: 0,
                before_fold_key: format!("before:{old_start}"),
                lines: Vec::new(),
            });
            (old_line, new_line) = (old_start, new_start);
        } else if let Some(hunk) = file.hunks.last_mut() {
            let Some(marker) = line.as_bytes().first() else {
                continue;
            };
            let (kind, old_number, new_number) = match marker {
                b' ' => {
                    let numbers = (Some(old_line), Some(new_line));
                    old_line += 1;
                    new_line += 1;
                    ("context", numbers.0, numbers.1)
                }
                b'+' => {
                    let number = new_line;
                    new_line += 1;
                    file.additions += 1;
                    ("add", None, Some(number))
                }
                b'-' => {
                    let number = old_line;
                    old_line += 1;
                    file.deletions += 1;
                    ("delete", Some(number), None)
                }
                _ => continue,
            };
            hunk.lines.push(Line {
                kind,
                old_line: old_number,
                new_line: new_number,
                text: line[1..].to_owned(),
            });
        } else if let Some(path) = line.strip_prefix("--- ") {
            file.old_path = parse_path(path, "a/");
        } else if let Some(path) = line.strip_prefix("+++ ") {
            file.path = if path.trim() == "/dev/null" {
                file.old_path.clone()
            } else {
                parse_path(path, "b/")
            };
        }
    }
    (!file.path.is_empty() && !file.hunks.is_empty()).then_some(file)
}

fn ranges(header: &str) -> Option<(i64, i64, i64, i64)> {
    let mut parts = header.strip_prefix("@@ ")?.split(' ');
    let (old, old_count) = range(parts.next()?.strip_prefix('-')?)?;
    let (new, new_count) = range(parts.next()?.strip_prefix('+')?)?;
    Some((old, old_count, new, new_count))
}

fn range(text: &str) -> Option<(i64, i64)> {
    let (start, count) = text.split_once(',').unwrap_or((text, "1"));
    Some((start.parse().ok()?, count.parse().ok()?))
}

fn parse_path(text: &str, prefix: &str) -> String {
    let text = text.strip_suffix('\t').unwrap_or(text);
    let path = match text
        .strip_prefix('"')
        .and_then(|text| text.strip_suffix('"'))
    {
        Some(quoted) => unquote_path(quoted).unwrap_or(Cow::Borrowed(text)),
        None => Cow::Borrowed(text),
    };
    if path == "/dev/null" {
        return String::new();
    }
    path.strip_prefix(prefix).unwrap_or(&path).to_owned()
}

fn unquote_path(quoted: &str) -> Option<Cow<'_, str>> {
    if !quoted.contains('\\') {
        return Some(Cow::Borrowed(quoted));
    }
    let mut bytes = quoted.bytes();
    let mut out = Vec::new();
    while let Some(byte) = bytes.next() {
        if byte != b'\\' {
            out.push(byte);
            continue;
        }
        out.push(match bytes.next()? {
            b'"' => b'"',
            b'\\' => b'\\',
            b'a' => 7,
            b'b' => 8,
            b't' => b'\t',
            b'n' => b'\n',
            b'v' => 11,
            b'f' => 12,
            b'r' => b'\r',
            digit @ b'0'..=b'3' => {
                (digit - b'0') * 64 + octal(bytes.next()?)? * 8 + octal(bytes.next()?)?
            }
            _ => return None,
        });
    }
    Some(Cow::Owned(String::from_utf8_lossy(&out).into_owned()))
}

fn octal(byte: u8) -> Option<u8> {
    (b'0'..=b'7').contains(&byte).then(|| byte - b'0')
}

impl FilePatch {
    fn expand(&mut self, checkout: &Checkout, folds: Option<&Contexts>) {
        let path = PathBuf::from(&self.path);
        let old = if self.old_path.is_empty() {
            &path
        } else {
            Path::new(&self.old_path)
        };
        let old_bytes = checkout.head_bytes(old);
        let needs_context = folds.is_some_and(|folds| folds.values().any(|&count| count > 0));
        let working = (old_bytes.is_none() || needs_context)
            .then(|| checkout.working_bytes(&path))
            .flatten();
        let old_text = String::from_utf8_lossy(old_bytes.as_deref().unwrap_or_default());
        let working_text = utf8_ignoring_errors(working.as_deref().unwrap_or_default());
        let old_count = if old_bytes.is_some() {
            text_lines(&old_text).count()
        } else {
            text_lines(&working_text).count()
        };
        let old_lines: Vec<_> = if needs_context {
            text_lines(&old_text).collect()
        } else {
            Vec::new()
        };
        let fallback_bytes = if needs_context && working.is_none() {
            checkout.head_bytes(&path)
        } else {
            None
        };
        let fallback = String::from_utf8_lossy(fallback_bytes.as_deref().unwrap_or_default());
        let new_lines: Vec<_> = if needs_context {
            text_lines(if working.is_some() {
                &working_text
            } else {
                &fallback
            })
            .collect()
        } else {
            Vec::new()
        };
        let extra = |key: &str| {
            folds
                .and_then(|folds| folds.get(key))
                .copied()
                .unwrap_or_default()
        };
        let (mut previous_old_end, mut previous_new_end) = (0, 0);
        for hunk in &mut self.hunks {
            hunk.before_hidden = (hunk.old_start - previous_old_end - 1).max(0) as usize;
            let count = hunk.before_hidden.min(extra(&hunk.before_fold_key));
            if count != 0 {
                let lines = expanded_lines(
                    &old_lines,
                    &new_lines,
                    hunk.old_start - count as i64,
                    hunk.new_start - count as i64,
                    count,
                );
                hunk.lines.splice(..0, lines);
                hunk.before_hidden -= count;
            }
            previous_old_end = hunk.old_start + hunk.old_count - 1;
            previous_new_end = hunk.new_start + hunk.new_count - 1;
        }
        self.after_hidden = (old_count as i64 - previous_old_end).max(0) as usize;
        let count = self.after_hidden.min(extra(self.after_fold_key));
        if let Some(last) = self.hunks.last_mut().filter(|_| count != 0) {
            last.lines.extend(expanded_lines(
                &old_lines,
                &new_lines,
                previous_old_end + 1,
                previous_new_end + 1,
                count,
            ));
            self.after_hidden -= count;
        }
    }
}

fn expanded_lines<'a>(
    old: &'a [&str],
    new: &'a [&str],
    old_start: i64,
    new_start: i64,
    count: usize,
) -> impl Iterator<Item = Line> + 'a {
    (0..count as i64).map(move |offset| {
        let old_line = old_start + offset;
        let new_line = new_start + offset;
        let text = line_at(new, new_line)
            .or_else(|| line_at(old, old_line))
            .unwrap_or_default();
        Line {
            kind: "context",
            old_line: Some(old_line),
            new_line: Some(new_line),
            text: text.to_owned(),
        }
    })
}

fn line_at<'a>(lines: &[&'a str], number: i64) -> Option<&'a str> {
    usize::try_from(number - 1)
        .ok()
        .and_then(|index| lines.get(index))
        .copied()
}

fn text_lines(mut text: &str) -> impl Iterator<Item = &str> {
    // Python splitlines handles all these separators and treats CRLF as one.
    std::iter::from_fn(move || {
        if text.is_empty() {
            return None;
        }
        let Some(index) = text.find([
            '\n', '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}',
            '\u{2029}',
        ]) else {
            return Some(std::mem::take(&mut text));
        };
        let (line, remainder) = text.split_at(index);
        let separator = remainder.chars().next().expect("separator found");
        text = if remainder.starts_with("\r\n") {
            &remainder[2..]
        } else {
            &remainder[separator.len_utf8()..]
        };
        Some(line)
    })
}

fn utf8_ignoring_errors(mut bytes: &[u8]) -> Cow<'_, str> {
    let mut output = String::new();
    loop {
        match std::str::from_utf8(bytes) {
            Ok(text) if output.is_empty() => return Cow::Borrowed(text),
            Ok(text) => {
                output.push_str(text);
                return Cow::Owned(output);
            }
            Err(error) => {
                let (valid, invalid) = bytes.split_at(error.valid_up_to());
                output.push_str(std::str::from_utf8(valid).expect("checked UTF-8 prefix"));
                bytes = &invalid[error.error_len().unwrap_or(invalid.len())..];
            }
        }
    }
}
