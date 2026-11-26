use std::{path::PathBuf, sync::Arc};

use serde::Deserialize;
use serde_json::{Value, json};

use super::{
    GitResult,
    discovery::Repository,
    history::{self, Comparison},
};

const CHANGES: &str = "git_review_changes";
const DIFF: &str = "git_review_diff";
const READ: &str = "git_review_read";
const MAX_RESULT_BYTES: usize = 64 * 1024;
const PAGE_LIMIT: usize = 200;

/// Read-only evidence tools for a single repository and immutable comparison.
/// The task runtime receives this capability from the trusted Git route.
#[derive(Clone)]
pub struct ReviewTools(Arc<Snapshot>);

struct Snapshot {
    workspace_root: PathBuf,
    data_dir: PathBuf,
    repo: Repository,
    compare: Comparison,
}

impl ReviewTools {
    pub(super) fn new(
        workspace_root: PathBuf,
        data_dir: PathBuf,
        repo: Repository,
        compare: Comparison,
    ) -> Self {
        Self(Arc::new(Snapshot {
            workspace_root,
            data_dir,
            repo,
            compare,
        }))
    }

    pub(crate) fn authorized_for(
        &self,
        root: &std::path::Path,
        data_dir: &std::path::Path,
        metadata: &Value,
    ) -> bool {
        super::canonicalize(root).is_ok_and(|path| path == self.0.workspace_root)
            && super::canonicalize(data_dir).is_ok_and(|path| path == self.0.data_dir)
            && *metadata == self.0.compare.review_metadata(&self.0.repo)
    }

    pub(crate) fn schemas(&self) -> Vec<Value> {
        let offset = json!({"type":"integer","minimum":0,"maximum":10000000});
        let limit = json!({"type":"integer","minimum":1,"maximum":PAGE_LIMIT});
        let path =
            json!({"type":"string","description":"Path relative to the Git repository root"});
        vec![
            function(
                CHANGES,
                "List changed files in the fixed review comparison. Use next_offset for additional pages.",
                json!({"offset":offset,"limit":limit}),
                &[],
            ),
            function(
                DIFF,
                "Read a page of the historical patch for a changed file. Use its path from git_review_changes and its next_offset for more lines.",
                json!({"path":path,"offset":offset,"limit":limit,"context":{"type":"integer","minimum":0,"maximum":100}}),
                &["path"],
            ),
            function(
                READ,
                "Read a text file from the fixed base or head, including related files that were unchanged. Use next_start_line to read more.",
                json!({"revision":{"type":"string","enum":["base","head"]},"path":path,"start_line":{"type":"integer","minimum":1,"maximum":10000000},"limit":limit}),
                &["revision", "path"],
            ),
        ]
    }

    pub(crate) fn validate(&self, name: &str, args: &Value) -> Result<(), String> {
        parse_call(name, args).map(|_| ())
    }

    pub(crate) async fn execute(&self, name: &str, args: Value) -> Result<Value, String> {
        let call = parse_call(name, &args)?;
        let snapshot = self.0.clone();
        tokio::task::spawn_blocking(move || match execute_call(&snapshot, call) {
            Ok(result) if result.to_string().len() <= MAX_RESULT_BYTES => result,
            Ok(_) => json!({"success":false,"error":"Result exceeds 64 KiB. Request fewer files or lines."}),
            Err(error) => json!({"success":false,"error":error.0}),
        }).await.map_err(|_| "Git evidence query was interrupted".into())
    }
}

fn function(name: &str, description: &str, properties: Value, required: &[&str]) -> Value {
    json!({"type":"function","function":{"name":name,"description":description,"parameters":{
        "type":"object","properties":properties,"required":required,"additionalProperties":false
    }}})
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Changes {
    offset: Option<usize>,
    limit: Option<usize>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Diff {
    path: String,
    offset: Option<usize>,
    limit: Option<usize>,
    context: Option<usize>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Read {
    revision: Revision,
    path: String,
    start_line: Option<usize>,
    limit: Option<usize>,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum Revision {
    Base,
    Head,
}

enum Call {
    Changes(Changes),
    Diff(Diff),
    Read(Read),
}

fn parse_call(name: &str, args: &Value) -> Result<Call, String> {
    let parsed = match name {
        CHANGES => serde_json::from_value(args.clone()).map(Call::Changes),
        DIFF => serde_json::from_value(args.clone()).map(Call::Diff),
        READ => serde_json::from_value(args.clone()).map(Call::Read),
        _ => return Err(format!("Unknown Git review tool: {name}")),
    }
    .map_err(|err| format!("Invalid {name} arguments: {err}"))?;
    Ok(parsed)
}

fn execute_call(snapshot: &Snapshot, call: Call) -> GitResult<Value> {
    snapshot.repo.revalidate(&snapshot.workspace_root)?;
    let repo = &snapshot.repo;
    let compare = &snapshot.compare;
    match call {
        Call::Changes(args) => {
            let files = history::changed_files(repo, compare)?;
            let offset = args.offset.unwrap_or(0).min(files.len());
            let end = (offset + page_limit(args.limit)).min(files.len());
            Ok(
                json!({"files":&files[offset..end],"total_files":files.len(),"next_offset":end,"has_more":end<files.len()}),
            )
        }
        Call::Diff(args) => history::file_diff_page(
            repo,
            compare,
            &args.path,
            args.offset.unwrap_or(0).min(10_000_000),
            args.context.unwrap_or(3).min(100),
            page_limit(args.limit),
        ),
        Call::Read(args) => read_source(repo, compare, args),
    }
}

fn page_limit(limit: Option<usize>) -> usize {
    limit.unwrap_or(PAGE_LIMIT).clamp(1, PAGE_LIMIT)
}

fn read_source(repo: &Repository, compare: &Comparison, args: Read) -> GitResult<Value> {
    if args.path.contains('\0')
        || args
            .path
            .split('/')
            .any(|part| matches!(part, "" | "." | ".."))
    {
        return Err("Use a Git repository-relative file path without dot segments.".into());
    }
    let sha = match args.revision {
        Revision::Base => &compare.base_sha,
        Revision::Head => &compare.head_sha,
    };
    let object = format!("{sha}:{}", args.path);
    let bytes = repo.git().read(["cat-file", "blob", &object])?;
    if bytes.contains(&0) {
        return Ok(json!({"path": args.path, "sha":sha, "binary":true}));
    }
    let text = String::from_utf8_lossy(&bytes);
    let total = text.lines().count();
    let start = args.start_line.unwrap_or(1).clamp(1, 10_000_000);
    let rows: Vec<_> = text
        .lines()
        .skip(start - 1)
        .take(page_limit(args.limit))
        .collect();
    let next = start + rows.len();
    Ok(
        json!({"path":args.path,"sha":sha,"binary":false,"start_line":start,"lines":rows,
        "total_lines":total,"next_start_line":(next<=total).then_some(next)}),
    )
}
