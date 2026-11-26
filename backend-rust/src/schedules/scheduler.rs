use std::{
    collections::HashSet,
    fs::{File, OpenOptions},
    path::Path,
    sync::{Arc, Mutex, TryLockError, Weak},
};

use jiff::Timestamp;
use serde_json::{Value, json};
use uuid::Uuid;

use super::{
    Admission, HostPrincipal, Observation, Result, RunState, ScheduleAdapter, ScheduleError,
    StartError,
    clock::{Rule, instant, iso, zone_names},
    schema,
    store::{Begin, Plan, Run, RunUpdate, Store},
    yaml,
};

#[derive(Clone)]
pub struct ScheduleService(Arc<Service>);

#[derive(Clone)]
pub(crate) struct ScheduleLink(Weak<Service>);

impl ScheduleLink {
    pub(crate) fn get(&self) -> Option<ScheduleService> { self.0.upgrade().map(ScheduleService) }
}

pub(crate) struct AgentScheduleScope {
    pub workspace: String,
    pub conversation: String,
    pub permission: crate::approval::PermissionMode,
    pub execution: crate::approval::ExecutionMode,
    pub network: String,
}

impl AgentScheduleScope {
    fn authorize(&self, settings: &Value) -> Result<()> {
        use crate::approval::{ExecutionMode, PermissionMode};
        let permission = match self.permission {
            PermissionMode::ReadOnly => 0,
            PermissionMode::Approval => 1,
            PermissionMode::AutoApproval => 2,
            PermissionMode::Unrestricted => 3,
        };
        let target_permission = match settings["permission_mode"].as_str() {
            Some("readonly") => 0, Some("approval") => 1, Some("auto_approval") => 2, Some("unrestricted") => 3,
            _ => return Err(ScheduleError::forbidden("无法确认计划的权限档位")),
        };
        let network = |value| match value { "none"=>0, "restricted"=>1, "full"=>2, _=>3 };
        if target_permission > permission {
            return Err(ScheduleError::forbidden("计划权限高于当前对话"));
        }
        if settings["execution_mode"] == "direct" && self.execution != ExecutionMode::Direct {
            return Err(ScheduleError::forbidden("当前沙箱对话不能创建或启动完全访问计划"));
        }
        if network(settings["network_permission"].as_str().unwrap_or_default()) > network(&self.network) {
            return Err(ScheduleError::forbidden("计划网络权限高于当前对话"));
        }
        Ok(())
    }
}


struct Service {
    store: Store,
    adapter: Arc<dyn ScheduleAdapter>,
    mutations: Mutex<()>,
}

/// Holds the same lease as the Python scheduler for as long as the caller can tick.
/// The host owns lifecycle and decides when to tick. Construction launches no worker.
pub struct ScheduleOwner {
    service: ScheduleService,
    tick: Mutex<()>,
    _lease: File,
}

impl Drop for ScheduleOwner {
    fn drop(&mut self) {
        // A concurrently forked child can briefly hold a duplicate before exec.
        // Release the lease when this owner stops even while that duplicate is closing.
        let _ = fs2::FileExt::unlock(&self._lease);
    }
}

impl ScheduleService {
    pub fn open(data_root: impl AsRef<Path>, adapter: Arc<dyn ScheduleAdapter>) -> Result<Self> {
        Ok(Self(Arc::new(Service {
            store: Store::open(data_root.as_ref())?,
            adapter,
            mutations: Mutex::new(()),
        })))
    }

    pub(crate) fn link(&self) -> ScheduleLink { ScheduleLink(Arc::downgrade(&self.0)) }

    pub(crate) fn submit_agent(&self, scope: &AgentScheduleScope, path: &str, bytes: &[u8], overwrite: bool)
        -> Result<Value> {
        let text = std::str::from_utf8(bytes).map_err(|_|ScheduleError::invalid("计划文件必须为 UTF-8。"))?;
        let value = yaml::parse(text)?;
        let principal = HostPrincipal::new(Some(scope.workspace.clone()));
        let source = json!({"kind":"agent","conversation_id":scope.conversation,"file_path":path});
        self.submit_scoped_at(&principal,&value,&json!(scope.workspace),overwrite,Timestamp::now(),Some((scope,&source)))
    }

    pub(crate) fn manage_agent(&self, scope: &AgentScheduleScope, args: &Value) -> Result<Value> {
        let id = schema::identifier(&args["schedule_id"],"schedule_id")?;
        let action = args["action"].as_str().ok_or_else(||ScheduleError::invalid("缺少计划操作"))?;
        self.manage_scoped_at(id,action,args["revision"].as_i64(),args["run_id"].as_str(),args["request_id"].as_str(),
            Timestamp::now(),Some(scope))
    }

    /// Only the process replacing the previous runtime should acquire the lease and reconcile runs.
    pub fn acquire_owner(&self) -> Result<ScheduleOwner> {
        if let Some(reason) = self.0.adapter.scheduler_error() {
            return Err(ScheduleError::unavailable(reason));
        }
        let lease = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(self.0.store.owner_path())
            .map_err(ScheduleError::storage)?;
        fs2::FileExt::try_lock_exclusive(&lease).map_err(|error| {
            ScheduleError::unavailable(format!("定时任务调度锁不可用：{error}"))
        })?;
        self.0.store.recover(Timestamp::now())?;
        Ok(ScheduleOwner {
            service: self.clone(),
            tick: Mutex::new(()),
            _lease: lease,
        })
    }

    pub fn submit(
        &self,
        host: &HostPrincipal,
        value: &Value,
        workspace: &Value,
        overwrite: bool,
    ) -> Result<Value> {
        self.submit_at(host, value, workspace, overwrite, Timestamp::now())
    }

    fn submit_at(&self, host: &HostPrincipal, value: &Value, workspace: &Value, overwrite: bool, now: Timestamp)
        -> Result<Value> {
        self.submit_scoped_at(host,value,workspace,overwrite,now,None)
    }

    fn submit_scoped_at(
        &self,
        host: &HostPrincipal,
        value: &Value,
        workspace: &Value,
        overwrite: bool,
        now: Timestamp,
        agent: Option<(&AgentScheduleScope,&Value)>,
    ) -> Result<Value> {
        let _mutations = self.0.mutations.lock().map_err(ScheduleError::storage)?;
        let value = schema::input(value)?;
        let previous = if overwrite && !value["id"].is_null() {
            Some(
                self.0
                    .store
                    .get(schema::identifier(&value["id"], "id")?, false)?,
            )
        } else {
            None
        };
        let (definition, rule) = schema::normalize(
            &value,
            workspace,
            now,
            previous.as_ref().map(|p| &p.definition),
        )?;
        let mut principal = host.to_record();
        if let Some(old) = previous.as_ref().filter(|p| {
            p.definition["target"] == definition["target"]
                && p.definition["configuration"] == definition["configuration"]
        }) {
            // Do not carry forward snapshots that were not curried from a Host identity.
            if let Some(snapshot) = old
                .principal
                .get("configuration_snapshot")
                .filter(|_| host_scope(&old.principal))
            {
                principal["configuration_snapshot"] = snapshot.clone();
            }
        }
        let snapshot = self.0.adapter.validate(&definition, &principal)?;
        if let Some((scope,_)) = agent {
            scope.authorize(&self.0.adapter.agent_settings(&definition,&principal)?)?;
        }

        principal["host_workspace_id"] = definition["target"]["workspace_id"].clone();
        if let Some(fields) = principal.as_object_mut() {
            fields.remove("configuration_snapshot");
        }
        if definition["target"]["conversation"]["mode"] == "new" {
            let snapshot = snapshot
                .filter(Value::is_object)
                .ok_or_else(|| ScheduleError::unavailable("新对话计划的默认配置未解析"))?;
            principal["configuration_snapshot"] = snapshot;
        }
        let next = (definition["enabled"] == true)
            .then(|| rule.next(now))
            .flatten();
        let page = json!({"kind":"page"});
        let source = agent.map_or(&page, |(_,source)| source);
        let plan = self.0.store.put(
            definition,
            &principal,
            source,
            overwrite,
            next,
            now,
        )?;
        self.serialize(plan, now)
    }

    pub fn validate(
        &self,
        host: &HostPrincipal,
        value: &Value,
        workspace: &Value,
    ) -> Result<Value> {
        let now = Timestamp::now();
        let input = schema::input(value)?;
        let (definition, rule) = schema::normalize(&input, workspace, now, None)?;
        let snapshot = self.0.adapter.validate(&definition, &host.to_record())?;
        if definition["target"]["conversation"]["mode"] == "new"
            && !snapshot.is_some_and(|value| value.is_object())
        {
            return Err(ScheduleError::unavailable("新对话计划的默认配置未解析"));
        }
        Ok(
            json!({"definition":definition, "next_dates": rule.next_dates(now), "yaml": yaml::dump(&definition)?}),
        )
    }

    pub fn options(&self, host: &HostPrincipal) -> Result<Value> {
        let options = self.0.adapter.options(host)?;
        Ok(
            json!({"workspaces":options.workspaces,"models":options.models,"timezones":zone_names(),"host_admin":true}),
        )
    }

    pub fn list(&self, workspace_id: Option<&str>, enabled: Option<bool>) -> Result<Value> {
        let now = Timestamp::now();
        let plans = self
            .0
            .store
            .list()?
            .into_iter()
            .filter(|plan| {
                workspace_id
                    .is_none_or(|workspace| plan.definition["target"]["workspace_id"] == workspace)
                    && enabled.is_none_or(|enabled| plan.enabled == enabled)
            })
            .map(|plan| self.serialize(plan, now))
            .collect::<Result<Vec<_>>>()?;
        Ok(json!({"plans":plans,"scheduler_error":self.0.adapter.scheduler_error()}))
    }

    pub fn detail(&self, id: &str) -> Result<Value> {
        self.serialize(self.0.store.get(id, false)?, Timestamp::now())
    }

    pub fn export_yaml(&self, id: &str) -> Result<Value> {
        Ok(json!({"yaml":yaml::dump(&self.0.store.get(id, false)?.definition)?}))
    }

    pub fn runs(&self, id: &str) -> Result<Value> {
        self.0.store.get(id, true)?;
        Ok(Value::Array(
            self.0
                .store
                .runs(id)?
                .into_iter()
                .map(|run| run.public(true))
                .collect(),
        ))
    }

    pub fn manage(
        &self,
        id: &str,
        action: &str,
        revision: Option<i64>,
        run_id: Option<&str>,
        request_id: Option<&str>,
    ) -> Result<Value> {
        self.manage_at(id, action, revision, run_id, request_id, Timestamp::now())
    }

    fn manage_at(&self, id: &str, action: &str, revision: Option<i64>, run_id: Option<&str>,
        request_id: Option<&str>, now: Timestamp) -> Result<Value> {
        self.manage_scoped_at(id,action,revision,run_id,request_id,now,None)
    }

    fn manage_scoped_at(
        &self,
        id: &str,
        action: &str,
        revision: Option<i64>,
        run_id: Option<&str>,
        request_id: Option<&str>,
        now: Timestamp,
        agent: Option<&AgentScheduleScope>,
    ) -> Result<Value> {
        let _mutations = self.0.mutations.lock().map_err(ScheduleError::storage)?;
        let mut plan = self.0.store.get(id, false)?;
        if let Some(scope) = agent {
            if revision.is_some_and(|revision| revision != plan.revision) {
                return Err(ScheduleError::conflict("计划版本已更新"));
            }
            if matches!(action,"enable"|"run") {
                scope.authorize(&self.0.adapter.agent_settings(&plan.definition,&plan.principal)?)?;
            }
        }
        match action {
            "enable" => {
                if revision.is_some_and(|revision| revision != plan.revision) {
                    return Err(ScheduleError::conflict("计划版本已更新"));
                }
                if !host_scope(&plan.principal) {
                    return Err(ScheduleError::forbidden(
                        "计划所属身份不受支持，仅允许宿主机身份",
                    ));
                }
                plan.definition["enabled"] = json!(true);
                self.0.adapter.validate(&plan.definition, &plan.principal)?;
                let (rule, _) = Rule::read(&plan.definition["schedule"], now, None)?;
                // Protect the exact revision whose trusted settings were validated above.
                let enabled =
                    self.0
                        .store
                        .set_enabled(id, true, Some(plan.revision), rule.next(now), now)?;
                self.serialize(enabled, now)
            }
            "pause" => self.serialize(
                self.0.store.set_enabled(id, false, revision, None, now)?,
                now,
            ),
            "delete" => {
                self.0.store.delete(id, now)?;
                Ok(json!({"id":id,"deleted":true}))
            }
            "run" => {
                if let Some(reason) = self.0.adapter.scheduler_error() {
                    return Err(ScheduleError::unavailable(reason));
                }
                if !host_scope(&plan.principal) {
                    return Err(ScheduleError::forbidden(
                        "计划所属身份不受支持，仅允许宿主机身份",
                    ));
                }
                self.0
                    .adapter
                    .check_dispatch(&plan.definition, &plan.principal)?;
                let generated;
                let request = match request_id.filter(|id| !id.is_empty()) {
                    Some(request) => request,
                    None => {
                        generated = Uuid::new_v4().simple().to_string();
                        &generated
                    }
                };
                Ok(self.0.store.manual(id, request, now)?.public(false))
            }
            "stop" => {
                let run_id = run_id
                    .filter(|id| !id.is_empty())
                    .ok_or_else(|| ScheduleError::invalid("停止运行需要明确 run_id"))?;
                Ok(self.0.store.stop(id, run_id, now)?.public(false))
            }
            _ => Err(ScheduleError::invalid("无效计划操作")),
        }
    }

    pub fn claim_notifications(&self) -> Result<Value> {
        Ok(Value::Array(
            self.0.store.claim_notifications(Timestamp::now())?,
        ))
    }

    pub fn acknowledge(&self, run_id: &str, token: &str, error: Option<&str>) -> Result<Value> {
        let error = error
            .filter(|error| !error.is_empty())
            .map(|error| error.chars().take(1000).collect::<String>());
        Ok(json!({"acknowledged":self.0.store.ack_notification(run_id, token, error.as_deref())?}))
    }

    fn serialize(&self, plan: Plan, now: Timestamp) -> Result<Value> {
        let (rule, _) = Rule::read(&plan.definition["schedule"], now, None)?;
        let last = self
            .0
            .store
            .latest(&plan.id, false)?
            .map(|run| run.public(false));
        let active = self
            .0
            .store
            .latest(&plan.id, true)?
            .map(|run| run.public(false));
        Ok(
            json!({"definition":plan.definition,"next_at":plan.next_at,"next_dates":rule.next_dates(now),
            "created_at":plan.created_at,"updated_at":plan.updated_at,"last_run":last,"active_run":active}),
        )
    }
}

impl ScheduleOwner {
    /// Drive the acquired owner until shutdown is signaled or the sender is dropped.
    /// The current tick finishes before the lease is released.
    pub async fn run(self, mut shutdown: tokio::sync::watch::Receiver<bool>) -> Result<()> {
        let owner = Arc::new(self);
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(1));
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        while !*shutdown.borrow() {
            tokio::select! {
                biased;
                changed = shutdown.changed() => {
                    if changed.is_err() { break; }
                }
                _ = interval.tick() => {
                    let current = owner.clone();
                    let tick = tokio::task::spawn_blocking(move || current.tick(Timestamp::now()))
                        .await.map_err(ScheduleError::storage)?;
                    if let Err(error) = tick {
                        eprintln!("schedule tick failed: {error}");
                    }
                }
            }
        }
        Ok(())
    }

    pub fn tick(&self, now: Timestamp) -> Result<()> {
        let _tick = match self.tick.try_lock() {
            Ok(tick) => tick,
            Err(TryLockError::WouldBlock) => return Ok(()),
            Err(error) => return Err(ScheduleError::storage(error)),
        };
        let inner = &self.service.0;
        if let Some(reason) = inner.adapter.scheduler_error() {
            return Err(ScheduleError::unavailable(reason));
        }
        for plan in inner.store.due(now)? {
            if self.dispatchable(&plan.definition, &plan.principal) {
                inner.store.claim_due(plan, now)?;
            }
        }
        for run in inner.store.active()? {
            self.observe(&run, now)?;
        }
        let mut newest = HashSet::new();
        let queued = inner.store.queued()?;
        for run in queued
            .into_iter()
            .filter(|run| self.dispatchable(&run.definition, &run.principal))
            .take(1000)
        {
            let first = newest.insert(run.schedule_id.clone());
            if !first && run.definition["policies"]["busy"] == "merge" {
                inner.store.merged(&run, now)?;
            } else if let Begin::Dispatch = inner.store.begin(&run, now)? {
                self.dispatch(&run, now)?;
            }
        }
        inner.store.prepare_notifications()
    }

    fn dispatchable(&self, definition: &Value, principal: &Value) -> bool {
        match self.service.0.adapter.check_dispatch(definition, principal) {
            Ok(()) => true,
            Err(error) => {
                if !matches!(error.status, 501 | 503) {
                    eprintln!("schedule dispatch scope unavailable: {error}");
                }
                false
            }
        }
    }

    fn dispatch(&self, run: &Run, now: Timestamp) -> Result<()> {
        let inner = &self.service.0;
        let started = run
            .invocation()
            .map_err(StartError::Failed)
            .and_then(|request| {
                inner
                    .adapter
                    .start(&request)
                    .map(|admission| (request, admission))
            });
        match started {
            Ok((request, admission)) => {
                let committed = inner.store.admitted(run, &admission);
                match committed {
                    Ok(true) => Ok(()),
                    other => {
                        if let Err(error) = inner.adapter.cancel(&request, &admission) {
                            eprintln!("schedule {} admission cancellation failed: {error}", run.id);
                        }
                        other.and_then(|_| Err(ScheduleError::storage("受理后的运行记录已失效")))
                    }
                }
            }
            Err(StartError::Busy(reason)) => inner.store.busy(run, &reason, now),
            Err(StartError::Deferred(error)) => inner.store.defer(run, &error.message, now),
            Err(StartError::Failed(error)) => {
                inner
                    .store
                    .observe(run, terminal(RunState::Failed, error.message), now)
            }
        }
    }

    fn observe(&self, run: &Run, now: Timestamp) -> Result<()> {
        let inner = &self.service.0;
        let (request, admission) = match (run.invocation(), run.admission()) {
            (Ok(request), Some(admission)) => (request, admission),
            (Err(error), _) => {
                return inner.store.observe(
                    run,
                    terminal(RunState::Interrupted, error.message),
                    now,
                );
            }
            (_, None) => {
                return inner.store.observe(
                    run,
                    terminal(RunState::Interrupted, "task_unavailable"),
                    now,
                );
            }
        };
        if run.stop_requested {
            self.cancel(&request, &admission);
        }
        let observed = match inner.adapter.observe(&request, &admission) {
            Ok(observed) => observed,
            Err(error) if matches!(error.status, 400 | 403 | 404) => {
                self.cancel(&request, &admission);
                return inner.store.observe(
                    run,
                    terminal(RunState::Interrupted, error.message),
                    now,
                );
            }
            Err(error) => {
                eprintln!("schedule {} observation unavailable: {error}", run.id);
                return Ok(());
            }
        };
        if !matches!(
            observed.state,
            RunState::Running | RunState::Waiting | RunState::Stopping
        ) && !observed.state.terminal()
        {
            return Err(ScheduleError::storage("运行时返回了无效的定时任务状态"));
        }
        let since = if observed.state == RunState::Waiting {
            Some(
                run.waiting_since
                    .as_deref()
                    .map(instant)
                    .transpose()?
                    .unwrap_or(now),
            )
        } else {
            None
        };
        let timeout_seconds = run.definition["policies"]["approval_timeout_seconds"]
            .as_i64()
            .unwrap_or(3600);
        let timeout = !run.stop_requested
            && since.is_some_and(|since| {
                now.as_nanosecond() - since.as_nanosecond()
                    >= i128::from(timeout_seconds) * 1_000_000_000
            });
        let Observation {
            state,
            task_id,
            reason,
            summary,
        } = observed;
        inner.store.observe(
            run,
            RunUpdate {
                state,
                task_id,
                reason,
                summary,
                waiting_since: since.map(iso),
                timeout,
            },
            now,
        )?;
        // Persist the stop reason first so a reload cannot turn the timeout into success.
        if timeout {
            self.cancel(&request, &admission);
        }
        Ok(())
    }

    fn cancel(&self, request: &super::ScheduleInvocation, admission: &Admission) {
        if let Err(error) = self.service.0.adapter.cancel(request, admission) {
            eprintln!(
                "schedule {} cancellation unavailable: {error}",
                request.run_id
            );
        }
    }
}

pub(super) fn host_scope(principal: &Value) -> bool {
    principal["username"] == "host"
        && principal["host_mode"] == true
        && principal["is_api_user"] != true
}

fn terminal(state: RunState, reason: impl Into<String>) -> RunUpdate {
    RunUpdate {
        state,
        reason: Some(reason.into()),
        task_id: None,
        summary: None,
        waiting_since: None,
        timeout: false,
    }
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/schedules_service.rs"]
mod tests;
