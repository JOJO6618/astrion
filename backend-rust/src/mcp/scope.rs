use std::{
    io,
    path::{Path, PathBuf},
};

use ring::digest::{SHA256, digest};

use crate::knowledge::storage::{ensure_dir, open_canonical_dir};
use crate::workspace_catalog::RegisteredWorkspace;

use super::{access::Actor, registry::Server};

/// Binds an MCP manager to an already resolved Host workspace and conversation.
/// The child ID and cwd must come from the validated native child instance.
#[derive(Clone, Debug)]
pub struct RuntimeScope {
    pub(super) workspace: PathBuf,
    pub(super) data: PathBuf,
    pub(super) workspace_id: String,
    conversation: String,
    child: Option<(String, PathBuf)>,
}

impl RuntimeScope {
    pub fn main(
        workspace: &RegisteredWorkspace,
        data: &Path,
        conversation: &str,
    ) -> io::Result<Self> {
        if conversation.trim().is_empty() || workspace.id.trim().is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "Missing MCP scope identity",
            ));
        }
        Ok(Self {
            workspace: workspace.path.canonicalize()?,
            data: data.canonicalize()?,
            workspace_id: workspace.id.clone(),
            conversation: conversation.into(),
            child: None,
        })
    }

    pub fn child(
        workspace: &RegisteredWorkspace,
        data: &Path,
        conversation: &str,
        actor: &str,
        cwd: &Path,
    ) -> io::Result<Self> {
        let mut scope = Self::main(workspace, data, conversation)?;
        let cwd = cwd.canonicalize()?;
        if actor.trim().is_empty() || !cwd.starts_with(&scope.workspace) || !cwd.is_dir() {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Invalid child MCP scope",
            ));
        }
        scope.child = Some((actor.into(), cwd));
        Ok(scope)
    }

    pub(super) fn actor(&self) -> Actor {
        if self.child.is_some() {
            Actor::Sub
        } else {
            Actor::Main
        }
    }
}

pub(super) struct Prepared {
    pub server: Server,
    pub profile: Option<PathBuf>,
}

impl Prepared {
    pub fn new(mut server: Server, scope: Option<&RuntimeScope>) -> Self {
        let mut profile = None;
        if server.transport == "stdio"
            && let Some(scope) = scope
        {
            if let Some((_, cwd)) = &scope.child
                && server.cwd.is_empty()
            {
                server.cwd = display_path(cwd);
            }
            if is_playwright(&server) && !has_explicit_browser(&server) {
                let path = profile_path(scope, &server.id);
                server
                    .args
                    .extend(["--user-data-dir".to_owned(), display_path(&path)]);
                profile = Some(path);
            }
        }
        Self { server, profile }
    }

    pub fn prepare_launch(&self) -> io::Result<()> {
        if let Some(profile) = &self.profile {
            let root = profile
                .parent()
                .and_then(Path::parent)
                .ok_or(io::ErrorKind::InvalidInput)?;
            let base = ensure_dir(&open_canonical_dir(root)?, "mcp_profiles")?;
            let hash = profile
                .file_name()
                .and_then(|name| name.to_str())
                .ok_or(io::ErrorKind::InvalidInput)?;
            ensure_dir(&base, hash)?;
        }
        Ok(())
    }
}

fn is_playwright(server: &Server) -> bool {
    let cmd = server
        .command
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or_default()
        .to_ascii_lowercase();
    matches!(
        cmd.as_str(),
        "playwright-mcp" | "playwright-mcp.cmd" | "playwright-mcp.exe"
    ) || std::iter::once(&server.command)
        .chain(&server.args)
        .any(|token| {
            let token = token.replace('\\', "/");
            token == "@playwright/mcp"
                || token.contains("/@playwright/mcp/")
                || token
                    .strip_prefix("@playwright/mcp@")
                    .is_some_and(|version| !version.is_empty() && !version.contains('/'))
        })
}

fn has_explicit_browser(server: &Server) -> bool {
    if server.args.iter().any(|arg| {
        matches!(
            arg.split('=').next(),
            Some(
                "--isolated"
                    | "--user-data-dir"
                    | "--config"
                    | "--extension"
                    | "--cdp-endpoint"
                    | "--endpoint"
            )
        )
    }) {
        return true;
    }
    let value = |name: &str| {
        server
            .env
            .get(name)
            .cloned()
            .or_else(|| std::env::var(name).ok())
            .unwrap_or_default()
    };
    [
        "PLAYWRIGHT_MCP_USER_DATA_DIR",
        "PLAYWRIGHT_MCP_CONFIG",
        "PLAYWRIGHT_MCP_CDP_ENDPOINT",
        "PLAYWRIGHT_MCP_ENDPOINT",
    ]
    .into_iter()
    .any(|name| !value(name).trim().is_empty())
        || ["PLAYWRIGHT_MCP_ISOLATED", "PLAYWRIGHT_MCP_EXTENSION"]
            .into_iter()
            .any(|name| {
                matches!(
                    value(name).trim().to_ascii_lowercase().as_str(),
                    "1" | "true"
                )
            })
}

fn profile_path(scope: &RuntimeScope, server_id: &str) -> PathBuf {
    let mut values = vec![
        display_path(&scope.workspace),
        display_path(&scope.data),
        scope.conversation.clone(),
        server_id.into(),
    ];
    if let Some((child, _)) = &scope.child {
        values.push(child.clone());
    }
    // Match Python json.dumps(..., ensure_ascii=False), including separators,
    // so configured profiles survive a backend upgrade.
    let json = values
        .iter()
        .map(|value| serde_json::to_string(value).expect("JSON string"))
        .collect::<Vec<_>>()
        .join(", ");
    let hash: String = digest(&SHA256, format!("[{json}]").as_bytes())
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    scope.data.join("mcp_profiles").join(hash)
}

fn display_path(path: &Path) -> String {
    let value = path.to_string_lossy();
    #[cfg(windows)]
    {
        if let Some(unc) = value.strip_prefix(r"\\?\UNC\") {
            return format!(r"\\{unc}");
        }
        if let Some(path) = value.strip_prefix(r"\\?\") {
            return path.to_owned();
        }
    }
    value.into_owned()
}
