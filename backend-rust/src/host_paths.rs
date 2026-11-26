use astrion_headless::workspace_catalog::HostCatalog;
use std::{
    env, io,
    path::{Path, PathBuf},
};

#[derive(Clone)]
pub struct HostPaths {
    pub deploy: PathBuf,
    pub logs: PathBuf,
    pub resources: PathBuf,
    pub host_libraries: PathBuf,
    pub workspace_config: PathBuf,
}

impl HostPaths {
    pub fn resolve(data: &Path, static_root: Option<&Path>) -> io::Result<Self> {
        // DATA_DIR normally ends in host/data. An isolated directory must not
        // silently resolve configuration or logs in the user's normal data root.
        let host = data
            .parent()
            .filter(|p| data.ends_with("data") && p.ends_with("host"));
        let runtime_root = env::var_os("ASTRION_DATA_ROOT")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .or_else(|| host.and_then(Path::parent).map(Path::to_owned));
        let host_libraries = runtime_root
            .as_ref()
            .map(|root| root.join("host"))
            .unwrap_or_else(|| data.to_owned());
        let deploy = env::var_os("DEPLOY_CONFIG_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| runtime_root.as_deref().unwrap_or(data).join("config"));
        let logs = env::var_os("LOGS_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| host_libraries.join("logs"));
        let resources = if let Some(path) = env::var_os("ASTRION_RESOURCE_ROOT") {
            PathBuf::from(path)
        } else if let Some(path) = static_root {
            std::path::absolute(path)?
                .parent()
                .ok_or_else(|| io::Error::other("Invalid static root"))?
                .to_owned()
        } else {
            env::current_dir()?
        };
        let workspace_config = env::var_os("HOST_WORKSPACES_FILE")
            .map(PathBuf::from)
            .unwrap_or_else(|| deploy.join("host_workspaces.json"));
        Ok(Self {
            deploy,
            logs,
            resources,
            host_libraries,
            workspace_config,
        })
    }
}

#[derive(Clone)]
pub struct Workspace {
    pub id: String,
    pub label: String,
    pub path: PathBuf,
}

impl Workspace {
    pub fn resolve(path: &Path, registry: &Path, resources: &Path) -> io::Result<Self> {
        let identity = path.canonicalize()?;
        if let Some(catalog) = HostCatalog::load(registry, resources)?
            && let Some(entry) = catalog.for_directory(&identity)
        {
            return Ok(Self {
                id: entry.id.clone(),
                label: entry.label.clone(),
                path: entry.path.clone(),
            });
        }
        // Keep the launch directory for bootstrapping while the CLI offers registration.
        Ok(Self {
            id: String::new(),
            label: String::new(),
            path: identity,
        })
    }
}

impl HostPaths {
    pub fn catalog(&self) -> io::Result<HostCatalog> {
        Ok(HostCatalog::load(&self.workspace_config, &self.resources)?.unwrap_or_default())
    }
}
