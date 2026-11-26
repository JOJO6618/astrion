//! Read-only Host adapters for the CrossPet protocol worker.

use std::{collections::BTreeMap, io};

use astrion_headless::{
    catalog::CatalogSource,
    crosspet::{
        CrossPetConfig, CrossPetRuntime, Foreground, GptQuota, SessionIdentity, SubscriptionQuota,
        TaskObservation, TaskRef,
    },
    navigation::Navigation,
    providers::ProviderConfig,
    runtime::Runtime,
    sub_agents::SubAgents,
};

use crate::host_paths::HostPaths;

pub(crate) struct HostCrossPetRuntime {
    runtime: Runtime,
    catalog: CatalogSource,
    navigation: Navigation,
    children: SubAgents,
    paths: HostPaths,
    quota: SubscriptionQuota,
}

impl HostCrossPetRuntime {
    pub(crate) fn new(
        runtime: Runtime,
        catalog: CatalogSource,
        navigation: Navigation,
        children: SubAgents,
        paths: HostPaths,
        providers: ProviderConfig,
        config: CrossPetConfig,
    ) -> Self {
        Self {
            runtime,
            catalog,
            navigation,
            children,
            paths,
            quota: SubscriptionQuota::new(config, providers),
        }
    }
}

impl CrossPetRuntime for HostCrossPetRuntime {
    fn model_id(&self, model_key: &str) -> Option<String> {
        self.catalog
            .load()
            .select(Some(model_key), false)
            .ok()
            .map(|model| model.model_id)
    }

    fn foreground(&self) -> io::Result<Option<Foreground>> {
        let Some((workspace_id, conversation_id)) = self
            .navigation
            .focused_session("host")
            .map_err(|_| io::Error::other("CrossPet focus is unavailable"))?
        else {
            return Ok(None);
        };
        if self.paths.catalog()?.by_id(&workspace_id).is_none() {
            return Ok(None);
        }
        let metadata = match self.runtime.web_metadata(&workspace_id, &conversation_id) {
            Ok(metadata) => metadata,
            Err(error) if error.status == 404 => return Ok(None),
            Err(_) => {
                return Err(io::Error::other(
                    "CrossPet foreground metadata is unavailable",
                ));
            }
        };
        let model_key = metadata["model_key"]
            .as_str()
            .unwrap_or_default()
            .to_owned();
        Ok(Some(Foreground {
            session: SessionIdentity {
                workspace_id,
                conversation_id,
            },
            model_key,
        }))
    }

    fn observe(&self, tasks: &[TaskRef]) -> BTreeMap<TaskRef, TaskObservation> {
        let catalog = self.paths.catalog().ok();
        tasks
            .iter()
            .filter_map(|task| {
                let terminal = self.runtime.crosspet_status(task);
                // Rust currently has no background-command or multi-agent executor.
                // The child supervisor is the only supported background activity source.
                let background = catalog
                    .as_ref()
                    .and_then(|catalog| catalog.by_id(&task.session.workspace_id))
                    .and_then(|workspace| {
                        self.children
                            .has_running(workspace, &task.session.conversation_id)
                            .ok()
                    });
                (terminal.is_some() || background.is_some()).then(|| {
                    (
                        task.clone(),
                        TaskObservation {
                            terminal,
                            background,
                        },
                    )
                })
            })
            .collect()
    }

    fn quota(&self, enabled: bool, busy: bool) -> Option<GptQuota> {
        Some(self.quota.snapshot(enabled, busy))
    }
}

#[cfg(test)]
#[path = "../../test/2026-11-25_RustCrossPet/host_quota_contract.rs"]
mod tests;
