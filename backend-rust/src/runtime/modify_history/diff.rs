//! Bounded per-line comparison shared by the summary card and unified patch.

use std::{fmt::Write, ops::Range};

use serde_json::{Value, json};

#[derive(Clone, Copy)]
enum Edit<'a> {
    Equal(&'a str),
    Insert(&'a str),
    Remove(&'a str),
}

impl Edit<'_> {
    fn old(self) -> usize { usize::from(!matches!(self, Self::Insert(_))) }
    fn new(self) -> usize { usize::from(!matches!(self, Self::Remove(_))) }
    fn changed(self) -> bool { !matches!(self, Self::Equal(_)) }
}

pub(super) struct Diff<'a> {
    edits: Vec<Edit<'a>>,
}

impl<'a> Diff<'a> {
    pub(super) fn new(before: &'a str, after: &'a str) -> Self {
        let old: Vec<_> = before.split_inclusive('\n').collect();
        let new: Vec<_> = after.split_inclusive('\n').collect();
        let prefix = old.iter().zip(&new).take_while(|(a,b)| a == b).count();
        let suffix = old[prefix..].iter().rev().zip(new[prefix..].iter().rev())
            .take_while(|(a,b)| a == b).count();
        let mut edits = old[..prefix].iter().copied().map(Edit::Equal).collect::<Vec<_>>();
        difference(&old[prefix..old.len() - suffix], &new[prefix..new.len() - suffix], &mut edits);
        edits.extend(old[old.len() - suffix..].iter().copied().map(Edit::Equal));
        Self { edits }
    }

    pub(super) fn counts(&self) -> (usize, usize) {
        self.edits.iter().fold((0,0), |(added, removed), edit| match edit {
            Edit::Insert(_) => (added + 1, removed),
            Edit::Remove(_) => (added, removed + 1),
            Edit::Equal(_) => (added, removed),
        })
    }

    pub(super) fn card(&self, lines: &mut usize, bytes: &mut usize) -> (Vec<Value>, bool) {
        let mut output = Vec::new();
        for (hunk, old, new) in self.hunks() {
            if !output.is_empty() {
                if *lines == 0 { return (output, true); }
                *lines -= 1;
                output.push(json!({"type":"sep"}));
            }
            let (mut old, mut new) = (old + 1, new + 1);
            for edit in &self.edits[hunk] {
                let (kind, text) = match edit {
                    Edit::Equal(text) => ("context", text),
                    Edit::Remove(text) => ("remove", text),
                    Edit::Insert(text) => ("add", text),
                };
                let text = text.strip_suffix('\n').unwrap_or(text);
                let text = text.strip_suffix('\r').unwrap_or(text);
                if *lines == 0 || *bytes < text.len() { return (output, true); }
                *lines -= 1;
                *bytes -= text.len();
                let mut row = json!({"type":kind,"content":text});
                if edit.old() > 0 { row["old_no"] = json!(old); old += 1; }
                if edit.new() > 0 { row["new_no"] = json!(new); new += 1; }
                output.push(row);
            }
        }
        (output, false)
    }

    pub(super) fn patch(&self, path: &str, added: bool, out: &mut String) {
        let old_path = git_path("a", path);
        let new_path = git_path("b", path);
        // Git suppresses a file header for equal text, but a newly created empty file still matters.
        let hunks = self.hunks();
        if !added && hunks.is_empty() { return; }
        let _ = writeln!(out, "diff --git {old_path} {new_path}");
        if added {
            out.push_str("new file mode 100644\n");
            if hunks.is_empty() {
                out.push_str("index 0000000..e69de29\n");
                return;
            }
        }
        let _ = writeln!(out, "--- {}\n+++ {new_path}", if added { "/dev/null" } else { &old_path });
        for (hunk, old_at, new_at) in hunks {
            let edits = &self.edits[hunk];
            let old_count: usize = edits.iter().map(|edit| edit.old()).sum();
            let new_count: usize = edits.iter().map(|edit| edit.new()).sum();
            let old = old_at + usize::from(old_count != 0);
            let new = new_at + usize::from(new_count != 0);
            let _ = writeln!(out, "@@ -{old},{old_count} +{new},{new_count} @@");
            for edit in edits {
                let (prefix, line) = match edit {
                    Edit::Equal(text) => (' ', text),
                    Edit::Remove(text) => ('-', text),
                    Edit::Insert(text) => ('+', text),
                };
                out.push(prefix);
                out.push_str(line);
                if !line.ends_with('\n') {
                    out.push_str("\n\\ No newline at end of file\n");
                }
            }
        }
    }

    fn hunks(&self) -> Vec<(Range<usize>, usize, usize)> {
        let mut windows: Vec<Range<usize>> = Vec::new();
        for (i, edit) in self.edits.iter().enumerate() {
            if !edit.changed() { continue; }
            let start = i.saturating_sub(3);
            let end = (i + 4).min(self.edits.len());
            if let Some(previous) = windows.last_mut().filter(|previous| start <= previous.end) {
                previous.end = end;
            } else {
                windows.push(start..end);
            }
        }
        let mut cursor = 0;
        let mut old = 0;
        let mut new = 0;
        windows.into_iter().map(|range| {
            for edit in &self.edits[cursor..range.start] {
                old += edit.old();
                new += edit.new();
            }
            cursor = range.start;
            (range, old, new)
        }).collect()
    }
}

/// Use exact LCS for bounded windows. For giant rewrites retain exact common
/// ends but emit the rest as one replacement rather than spending quadratic time.
fn difference<'a>(old: &[&'a str], new: &[&'a str], out: &mut Vec<Edit<'a>>) {
    let width = new.len() + 1;
    let cells = (old.len() + 1).saturating_mul(width);
    if old.is_empty() || new.is_empty() || cells > 2_000_000 {
        out.extend(old.iter().copied().map(Edit::Remove));
        out.extend(new.iter().copied().map(Edit::Insert));
        return;
    }
    let mut lcs = vec![0_u32; cells];
    for i in (0..old.len()).rev() {
        for j in (0..new.len()).rev() {
            let at = i * width + j;
            lcs[at] = if old[i] == new[j] { lcs[at + width + 1] + 1 }
                else { lcs[at + 1].max(lcs[at + width]) };
        }
    }
    let (mut i, mut j) = (0,0);
    while i < old.len() && j < new.len() {
        let at = i * width + j;
        if old[i] == new[j] {
            out.push(Edit::Equal(old[i]));
            i += 1;
            j += 1;
        } else if lcs[at + width] >= lcs[at + 1] {
            out.push(Edit::Remove(old[i]));
            i += 1;
        } else {
            out.push(Edit::Insert(new[j]));
            j += 1;
        }
    }
    out.extend(old[i..].iter().copied().map(Edit::Remove));
    out.extend(new[j..].iter().copied().map(Edit::Insert));
}

fn git_path(prefix: &str, path: &str) -> String {
    let unquoted = format!("{prefix}/{path}");
    // Git patch paths use quoted C strings (and octal for arbitrary UTF-8 bytes).
    if unquoted.bytes().all(|byte| byte.is_ascii_graphic() && !matches!(byte,b'"'|b'\\')) {
        return unquoted;
    }
    let mut quoted = String::from("\"");
    for byte in unquoted.bytes() {
        match byte {
            b'"' | b'\\' => { quoted.push('\\'); quoted.push(byte.into()); }
            32..=126 => quoted.push(byte.into()),
            _ => { let _ = write!(quoted, "\\{byte:03o}"); }
        }
    }
    quoted.push('"');
    quoted
}
