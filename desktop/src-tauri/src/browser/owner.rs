use super::{manager::Browser, policy};
use serde_json::{json, Value};
impl Browser {
    pub fn adopt_scope(&mut self, profile: &str, scope: &Value) -> Result<(), String> {
        let Some(key) = scope["key"].as_str().filter(|key| !key.is_empty()) else {
            return Ok(());
        };
        let mut alias = scope.clone();
        alias["conversation"] = json!(key);
        let from = format!("{profile}:{}", policy::scope(&alias)?);
        let to = format!("{profile}:{}", policy::scope(scope)?);
        if from == to
            || self.tabs.iter().any(|tab| tab.owner == to)
            || self.profiles[profile].data.owners.contains_key(&to)
        {
            return Ok(());
        }
        for tab in &mut self.tabs {
            if tab.owner == from {
                tab.owner = to.clone();
            }
        }
        if let Some(selected) = self.selected.remove(&from) {
            self.selected.insert(to.clone(), selected);
        }
        let profile = self
            .profiles
            .get_mut(profile)
            .ok_or("unknown_browser_profile")?;
        if let Some(saved) = profile.data.owners.remove(&from) {
            profile.data.owners.insert(to.clone(), saved);
            profile.save()?;
        }
        self.ai_post(json!({"type":"adopt","from":from,"to":to}));
        Ok(())
    }
}
