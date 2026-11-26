use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

const MAX_RESIDENT_GROUPS: usize = 3;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BrowserKind {
    Agent,
    Chat,
}

/// Tuple form matches the scope key used by the Electron renderer.
#[derive(Clone, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
pub struct BrowserScope(pub BrowserKind, pub String, pub String);

#[derive(Clone, Copy, Debug, Default)]
pub struct BrowserObservation {
    pub exists: Option<bool>,
    pub open: bool,
    pub running: bool,
}

#[derive(Deserialize)]
struct Header {
    owners: Vec<Owner>,
}

#[derive(Deserialize)]
struct Owner {
    scope: BrowserScope,
    visit: Value,
    closed: bool,
}

#[derive(Clone, Copy)]
pub(super) struct Group {
    visit: Option<u64>,
    closed: bool,
}

pub(super) fn parse(raw: &str) -> Option<BTreeMap<BrowserScope, Group>> {
    let header: Header = serde_json::from_str(raw).ok()?;
    if header.owners.len() > 300 {
        return None;
    }
    let mut groups = BTreeMap::new();
    for owner in header.owners {
        let BrowserScope(_, workspace, conversation) = &owner.scope;
        if workspace.chars().count() > 256 || conversation.chars().count() > 256 {
            return None;
        }
        let visit = if owner.visit.is_null() {
            None
        } else {
            Some(owner.visit.as_u64()?)
        };
        groups.insert(
            owner.scope,
            Group {
                visit,
                closed: owner.closed,
            },
        );
    }
    Some(groups)
}

#[derive(Serialize)]
pub(super) struct Decision {
    pub deleted: Vec<String>,
    pub release: Vec<String>,
}

pub(super) fn decide(
    groups: BTreeMap<BrowserScope, Group>,
    observations: &BTreeMap<BrowserScope, BrowserObservation>,
) -> Decision {
    let mut deleted = Vec::new();
    let mut closed = Vec::new();
    let mut oldest = Vec::new();
    let mut resident = 0usize;
    for (scope, group) in groups {
        let state = observations.get(&scope).copied().unwrap_or_default();
        let key = || serde_json::to_string(&scope).expect("browser scope is JSON serializable");
        if state.exists == Some(false) {
            deleted.push(key());
            continue;
        }
        let Some(visit) = group.visit else { continue };
        resident += 1;
        if state.running || state.exists != Some(true) {
            continue;
        }
        if group.closed && !state.open {
            closed.push(key());
        } else {
            oldest.push((visit, key()));
        }
    }
    let capacity = resident.saturating_sub(closed.len() + MAX_RESIDENT_GROUPS);
    deleted.sort_unstable();
    closed.sort_unstable();
    oldest.sort_unstable();
    closed.extend(oldest.into_iter().take(capacity).map(|(_, scope)| scope));
    Decision {
        deleted,
        release: closed,
    }
}
