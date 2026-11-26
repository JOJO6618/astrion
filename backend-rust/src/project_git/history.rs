use std::{collections::HashMap, ffi::OsString};

use serde::Serialize;
use serde_json::{Value, json};

use super::{
    GitResult,
    discovery::{Repository, git_path},
    runner::{NULL_DEVICE, commit_sha, object_id},
};

const HISTORY_PAGE: usize = 40;
const DIFF_PAGE: usize = 1200;
const COMMIT_FIELDS: &str = "--format=%H%x00%P%x00%an%x00%aI%x00%s";
const LOG_OPTIONS: &[&str] = &[
    "log",
    "--no-notes",
    "--no-decorate",
    "--no-show-signature",
    "--encoding=UTF-8",
];

#[derive(Serialize)]
struct Commit {
    sha: String,
    parents: Vec<String>,
    author: String,
    date: String,
    subject: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<String>,
}

fn commit_rows(output: &[u8]) -> GitResult<Vec<Commit>> {
    if output.is_empty() {
        return Ok(Vec::new());
    }
    let text = String::from_utf8_lossy(output);
    let fields: Vec<_> = text.split_terminator('\0').collect();
    if fields.len() % 5 != 0 {
        return Err("Git 返回了无效的提交记录。".into());
    }
    Ok(fields
        .chunks_exact(5)
        .map(|row| Commit {
            sha: row[0].to_owned(),
            parents: row[1].split_whitespace().map(str::to_owned).collect(),
            author: row[2].to_owned(),
            date: row[3].to_owned(),
            subject: row[4].to_owned(),
            message: None,
        })
        .collect())
}

pub(super) fn branches(repo: &Repository) -> GitResult<Value> {
    let refs = repo.git().read([
        "for-each-ref",
        "--sort=refname",
        "--format=%(refname)",
        "refs/heads",
        "refs/remotes",
    ])?;
    let current = repo
        .git()
        .read_optional(["symbolic-ref", "--short", "-q", "HEAD"])?;
    let branches: Vec<_> = String::from_utf8_lossy(&refs)
        .lines()
        .filter(|reference| !reference.ends_with("/HEAD"))
        .map(|reference| {
            let name = reference
                .strip_prefix("refs/heads/")
                .or_else(|| reference.strip_prefix("refs/remotes/"));
            json!({"ref": reference, "name": name.unwrap_or(reference)})
        })
        .collect();
    let current = current.as_deref().unwrap_or_default();
    Ok(json!({"branches": branches, "current_branch": String::from_utf8_lossy(current).trim()}))
}

pub(super) fn history(
    repo: &Repository,
    reference: &str,
    search: &str,
    offset: usize,
) -> GitResult<Value> {
    let target = match commit_sha(
        repo,
        if reference.is_empty() {
            "HEAD"
        } else {
            reference
        },
    ) {
        Ok(sha) => sha,
        Err(error) => {
            if reference.is_empty()
                && repo
                    .git()
                    .read(["for-each-ref", "--format=%(objectname)"])?
                    .is_empty()
            {
                return Ok(json!({"commits": [], "has_more": false}));
            }
            return Err(error);
        }
    };
    let skip = format!("--skip={offset}");
    let count = format!("--max-count={}", HISTORY_PAGE + 1);
    let search: String = search.chars().take(500).collect();
    let grep = format!("--grep={search}");
    let mut args = LOG_OPTIONS.to_vec();
    args.extend(["-z", COMMIT_FIELDS, &skip, &count]);
    if !search.is_empty() {
        args.extend(["--fixed-strings", "--regexp-ignore-case", &grep]);
    }
    args.extend([target.as_str(), "--"]);
    let mut rows = commit_rows(&repo.git().read(args)?)?;
    let has_more = rows.len() > HISTORY_PAGE;
    rows.truncate(HISTORY_PAGE);
    Ok(json!({"commits": rows, "has_more": has_more}))
}

#[derive(Serialize)]
pub(super) struct Comparison {
    pub(super) head_sha: String,
    pub(super) base_sha: String,
    #[serde(flatten)]
    kind: Kind,
}

impl Comparison {
    pub(super) fn review_metadata(&self, repo: &Repository) -> Value {
        let mut metadata = json!(self);
        metadata["repo_id"] = json!(repo.id);
        metadata["repo_path"] = json!(repo.path.to_string_lossy());
        metadata
    }

    pub(super) fn kind(&self) -> &'static str {
        match self.kind {
            Kind::Commit { .. } => "commit",
            Kind::Branch { .. } => "branch",
        }
    }

    pub(super) fn review_label(&self) -> &str {
        match &self.kind {
            Kind::Commit { commit } => &commit.subject,
            Kind::Branch { head_ref, .. } => head_ref,
        }
    }

    pub(super) fn review_description(&self) -> String {
        match &self.kind {
            Kind::Commit { commit } => {
                format!("提交 {}\n提交说明：{}", self.head_sha, commit.subject)
            }
            Kind::Branch {
                base_ref, head_ref, ..
            } => format!("分支 {head_ref} 相对 {base_ref} 的改动"),
        }
    }
}

#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum Kind {
    Commit {
        commit: Commit,
    },
    Branch {
        base_ref: String,
        head_ref: String,
        base_tip: String,
    },
}

pub(super) fn compare(
    repo: &Repository,
    sha: &str,
    parent: &str,
    base: &str,
    head: &str,
) -> GitResult<Comparison> {
    if !sha.is_empty() {
        return compare_commit(repo, sha, parent);
    }
    if base.is_empty() {
        return Err("请选择基准分支。".into());
    }
    let head_sha = commit_sha(repo, head)?;
    let base_tip = commit_sha(repo, base)?;
    let merge_base = repo
        .git()
        .read_optional(["merge-base", &base_tip, &head_sha])?
        .ok_or("两个分支没有共同祖先。")?;
    Ok(Comparison {
        head_sha,
        base_sha: object_id(merge_base)?,
        kind: Kind::Branch {
            base_ref: base.to_owned(),
            head_ref: head.to_owned(),
            base_tip,
        },
    })
}

fn compare_commit(repo: &Repository, sha: &str, parent: &str) -> GitResult<Comparison> {
    let head_sha = commit_sha(repo, sha)?;
    let mut args = LOG_OPTIONS.to_vec();
    args.extend(["-1", "-z", COMMIT_FIELDS, &head_sha, "--"]);
    let mut commit = commit_rows(&repo.git().read(args)?)?
        .pop()
        .ok_or("Git 未返回提交记录。")?;
    // A shallow boundary appears parentless to git log. Read the actual commit
    // header so an unavailable parent is never mistaken for a root commit.
    let object = repo.git().read(["cat-file", "commit", &head_sha])?;
    commit.parents = object
        .split(|byte| *byte == b'\n')
        .take_while(|line| !line.is_empty())
        .filter_map(|line| line.strip_prefix(b"parent "))
        .map(|parent| object_id(parent.to_vec()))
        .collect::<GitResult<_>>()?;
    let base_sha = if !parent.is_empty() {
        let baseline = commit_sha(repo, parent)?;
        if !commit.parents.contains(&baseline) {
            return Err("所选基准不是该提交的父提交。".into());
        }
        baseline
    } else if let Some(parent) = commit.parents.first() {
        parent.to_owned()
    } else {
        object_id(repo.git().read(["hash-object", "-t", "tree", "--stdin"])?)?
    };
    let mut args = LOG_OPTIONS.to_vec();
    args.extend(["-1", "--format=%B", &head_sha, "--"]);
    let message = repo.git().read(args)?;
    commit.message = Some(String::from_utf8_lossy(&message).trim().to_owned());
    Ok(Comparison {
        head_sha,
        base_sha,
        kind: Kind::Commit { commit },
    })
}

#[derive(Serialize)]
pub(super) struct ChangedFile {
    path: String,
    old_path: String,
    status: char,
    additions: u64,
    deletions: u64,
    binary: bool,
    #[serde(skip)]
    raw_path: Vec<u8>,
    #[serde(skip)]
    raw_old_path: Vec<u8>,
}

fn diff_args(compare: &Comparison) -> Vec<&str> {
    vec![
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--find-renames",
        "--no-relative",
        "--no-color",
        "--submodule=short",
        "--ignore-submodules=none",
        "-O",
        NULL_DEVICE,
        &compare.base_sha,
        &compare.head_sha,
    ]
}

pub(super) fn changed_files(
    repo: &Repository,
    compare: &Comparison,
) -> GitResult<Vec<ChangedFile>> {
    let mut args = diff_args(compare);
    args.extend(["--name-status", "-z", "--"]);
    let output = repo.git().attr_source(&compare.head_sha).read(args)?;
    let mut tokens = output.split(|byte| *byte == 0);
    let mut files = Vec::new();
    while let Some(&first) = tokens.next().and_then(|status| status.first()) {
        let old_path = next_field(&mut tokens)?;
        let path = if matches!(first, b'R' | b'C') {
            next_field(&mut tokens)?
        } else {
            old_path
        };
        files.push(ChangedFile {
            status: char::from(first),
            path: String::from_utf8_lossy(path).into_owned(),
            old_path: String::from_utf8_lossy(old_path).into_owned(),
            additions: 0,
            deletions: 0,
            binary: false,
            raw_path: path.to_vec(),
            raw_old_path: old_path.to_vec(),
        });
    }
    let mut args = diff_args(compare);
    args.extend(["--numstat", "-z", "--"]);
    let output = repo.git().attr_source(&compare.head_sha).read(args)?;
    let mut tokens = output.split(|byte| *byte == 0);
    let mut stats = HashMap::new();
    while let Some(stat) = tokens.next().filter(|value| !value.is_empty()) {
        let parts: Vec<_> = stat.splitn(3, |byte| *byte == b'\t').collect();
        let [added, removed, mut path] = parts[..] else {
            return Err("Git 返回了无效的变更统计。".into());
        };
        if path.is_empty() {
            next_field(&mut tokens)?; // original path
            path = next_field(&mut tokens)?;
        }
        stats.insert(path, (stat_count(added)?, stat_count(removed)?));
    }
    for file in &mut files {
        if let Some((added, removed)) = stats.get(file.raw_path.as_slice()) {
            file.binary = added.is_none() || removed.is_none();
            file.additions = added.unwrap_or(0);
            file.deletions = removed.unwrap_or(0);
        }
    }
    Ok(files)
}

fn next_field<'a>(tokens: &mut impl Iterator<Item = &'a [u8]>) -> GitResult<&'a [u8]> {
    tokens
        .next()
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "Git 返回了无效的变更路径。".into())
}

fn stat_count(value: &[u8]) -> GitResult<Option<u64>> {
    if value == b"-" {
        return Ok(None);
    }
    std::str::from_utf8(value)
        .ok()
        .and_then(|value| value.parse().ok())
        .map(Some)
        .ok_or_else(|| "Git 返回了无效的变更统计。".into())
}

pub(super) fn comparison_summary(repo: &Repository, compare: Comparison) -> GitResult<Value> {
    let files = changed_files(repo, &compare)?;
    let additions: u64 = files.iter().map(|file| file.additions).sum();
    let deletions: u64 = files.iter().map(|file| file.deletions).sum();
    #[derive(Serialize)]
    struct Summary {
        #[serde(flatten)]
        comparison: Comparison,
        files: Vec<ChangedFile>,
        additions: u64,
        deletions: u64,
    }
    Ok(json!(Summary {
        comparison: compare,
        files,
        additions,
        deletions
    }))
}

#[derive(Serialize)]
struct DiffLine<'a> {
    kind: &'static str,
    text: &'a str,
    old_line: Option<usize>,
    new_line: Option<usize>,
}

pub(super) fn file_diff(
    repo: &Repository,
    compare: &Comparison,
    path: &str,
    offset: usize,
    context: usize,
) -> GitResult<Value> {
    file_diff_page(repo, compare, path, offset, context, DIFF_PAGE)
}

pub(super) fn file_diff_page(
    repo: &Repository,
    compare: &Comparison,
    path: &str,
    offset: usize,
    context: usize,
    limit: usize,
) -> GitResult<Value> {
    let files = changed_files(repo, compare)?;
    let mut matches = files.into_iter().filter(|file| file.path == path);
    let file = matches.next().ok_or("文件不属于本次改动。")?;
    if matches.next().is_some() {
        return Err("文件名无法唯一匹配本次改动中的文件。".into());
    }
    let context = format!("--unified={context}");
    let mut args: Vec<OsString> = diff_args(compare).into_iter().map(OsString::from).collect();
    args.extend(["--patch", &context, "--"].into_iter().map(OsString::from));
    args.push(git_path(&file.raw_old_path)?.into_os_string());
    if file.raw_old_path != file.raw_path {
        args.push(git_path(&file.raw_path)?.into_os_string());
    }
    let output = repo.git().attr_source(&compare.head_sha).read(args)?;
    Ok(paginated_patch(
        file,
        &String::from_utf8_lossy(&output),
        offset,
        limit,
    ))
}

fn paginated_patch(file: ChangedFile, patch: &str, offset: usize, limit: usize) -> Value {
    let (mut old, mut new) = (0, 0);
    let mut in_hunk = false;
    let mut lines = Vec::new();
    let mut total = 0;
    for (index, text) in patch.lines().enumerate() {
        total = index + 1;
        if text.starts_with("diff --git ") {
            in_hunk = false;
        }
        let line = if let Some((old_start, new_start)) = hunk_starts(text) {
            (old, new) = (old_start, new_start);
            in_hunk = true;
            DiffLine {
                kind: "hunk",
                text,
                old_line: None,
                new_line: None,
            }
        } else if in_hunk && text.starts_with([' ', '+', '-']) {
            let marker = text.as_bytes()[0];
            let old_line = (marker != b'+').then_some(old);
            let new_line = (marker != b'-').then_some(new);
            old += usize::from(old_line.is_some());
            new += usize::from(new_line.is_some());
            let kind = match marker {
                b'+' => "add",
                b'-' => "delete",
                _ => "context",
            };
            DiffLine {
                kind,
                text: &text[1..],
                old_line,
                new_line,
            }
        } else {
            DiffLine {
                kind: "meta",
                text,
                old_line: None,
                new_line: None,
            }
        };
        if (offset..offset + limit).contains(&index) {
            lines.push(line);
        }
    }
    let next_offset = (offset + limit).min(total);
    json!({"file": file, "lines": lines, "total_lines": total, "has_more": next_offset < total, "next_offset": next_offset})
}

fn hunk_starts(line: &str) -> Option<(usize, usize)> {
    let mut fields = line.strip_prefix("@@ ")?.split(' ');
    let old = fields
        .next()?
        .strip_prefix('-')?
        .split(',')
        .next()?
        .parse()
        .ok()?;
    let new = fields
        .next()?
        .strip_prefix('+')?
        .split(',')
        .next()?
        .parse()
        .ok()?;
    (fields.next()? == "@@").then_some((old, new))
}
