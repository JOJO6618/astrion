//! The Agent uses the same scheduler owner and plan store as the Host settings.
use serde_json::{Value, json};

use crate::{
    schedules::{AgentScheduleScope, ScheduleService},
    tools::read_file::ReadFile,
};

use super::{Runtime, approval::task_scope};

const QUERY: &str = "query_schedules";
const SUBMIT: &str = "submit_schedule";
const MANAGE: &str = "manage_schedule";

pub(super) fn supports(name: &str) -> bool {
    matches!(name, QUERY | SUBMIT | MANAGE)
}

pub(super) fn definitions() -> [Value; 3] {
    let definition = |name, description, mut props: Value, required: Vec<&str>| {
        props["intent"] = json!({"type":"string","description":"说明操作目的"});
        let fields = std::iter::once("intent")
            .chain(required)
            .collect::<Vec<_>>();
        json!({"type":"function","function":{"name":name,"description":description,
            "parameters":{"type":"object","properties":props,"required":fields,"additionalProperties":false}}})
    };
    [
        definition(
            QUERY,
            "查询当前账户的计划和运行记录。",
            json!({
                "schedule_id":{"type":"string"},"workspace_id":{"type":"string"},"enabled":{"type":"boolean"},
                "include_runs":{"type":"boolean"},"fields":{"type":"array","items":{"type":"string"}}
            }),
            vec![],
        ),
        definition(
            SUBMIT,
            "读取 YAML 文件并验证保存定时任务。覆盖需在文件中提供 ID 和版本号。",
            json!({
                "file_path":{"type":"string"},"overwrite":{"type":"boolean","default":false}
            }),
            vec!["file_path"],
        ),
        definition(
            MANAGE,
            "开启、暂停、删除、立即运行计划，或停止明确的一次运行。",
            json!({
                "schedule_id":{"type":"string"},"action":{"type":"string","enum":["enable","pause","delete","run","stop"]},
                "revision":{"type":"integer"},"run_id":{"type":"string"},"request_id":{"type":"string"}
            }),
            vec!["schedule_id", "action"],
        ),
    ]
}

impl Runtime {
    pub(super) fn schedules_available(&self) -> bool {
        self.schedule_service().is_some()
    }

    fn schedule_service(&self) -> Option<ScheduleService> {
        self.schedules
            .get()
            .and_then(crate::schedules::ScheduleLink::get)
    }

    fn schedule_authority(&self, task_id: &str) -> Result<AgentScheduleScope, String> {
        let store = self.store().map_err(|error| error.message)?;
        let (_, settings) = task_scope(&store, task_id)?;
        let task = store.tasks.get(task_id).ok_or("定时任务来源对话不存在。")?;
        let (permission, execution) = settings.process_modes().ok_or("定时任务来源权限无效。")?;
        let (header, _) = store
            .header(&task.workspace_id, &task.conversation_id)
            .map_err(|error| error.message)?;
        let network = header["metadata"]["network_permission"]
            .as_str()
            .filter(|value| matches!(*value, "none" | "restricted" | "full"))
            .unwrap_or("restricted")
            .to_owned();
        Ok(AgentScheduleScope {
            workspace: task.workspace_id.clone(),
            conversation: task.conversation_id.clone(),
            permission,
            execution,
            network,
        })
    }

    async fn schedule_file(&self, task_id: &str, path: &str) -> Result<Vec<u8>, String> {
        let (workspace, execution) = self.media_roots(task_id, path, &Value::Null)?;
        let selected = if std::path::Path::new(path).starts_with(&workspace)
            && !std::path::Path::new(path).starts_with(&execution)
        {
            workspace
        } else {
            execution
        };
        let data = self
            .store()
            .map_err(|error| error.message)?
            .data_dir
            .clone();
        let path = path.to_owned();
        let bytes = tokio::task::spawn_blocking(move || {
            ReadFile::open(&selected, &data)
                .map_err(|error| error.to_string())?
                .read_bytes(&path)
        })
        .await
        .map_err(|error| error.to_string())??;
        if bytes.len() > 1024 * 1024 {
            return Err("计划文件超过 1 MiB。".into());
        }
        Ok(bytes)
    }

    pub(super) async fn execute_schedule_tool(
        &self,
        task_id: &str,
        call_id: &str,
        name: &str,
        args: &Value,
        batch: &[Value],
    ) -> Value {
        let error = |error: String| json!({"success":false,"error":error});
        let Some(service) = self.schedule_service() else {
            return error("定时任务服务不可用。".into());
        };
        let args = args.clone();
        if name == QUERY {
            return tokio::task::spawn_blocking(move || query(&service, &args))
                .await
                .unwrap_or_else(|failure| Err(failure.to_string()))
                .unwrap_or_else(error);
        }
        let scope = match self.schedule_authority(task_id) {
            Ok(scope) if scope.permission != crate::approval::PermissionMode::ReadOnly => scope,
            Ok(_) => return error("只读或计划模式只能查询定时任务。".into()),
            Err(failure) => return error(failure),
        };
        let mut request = args.clone();
        let submitted = if name == SUBMIT {
            let Some(path) = args["file_path"]
                .as_str()
                .map(str::trim)
                .filter(|path| !path.is_empty())
            else {
                return error("缺少计划 YAML 文件路径。".into());
            };
            let bytes = match self.schedule_file(task_id, path).await {
                Ok(bytes) => bytes,
                Err(failure) => return error(failure),
            };
            request["definition_sha256"] = json!(crate::conversation_transfer::sha256(&bytes));
            Some((path.to_owned(), bytes))
        } else {
            None
        };
        if let Err(result) = self
            .approve_host_mutation(task_id, call_id, name, &request, batch)
            .await
        {
            return result;
        }
        if let Some((path, bytes)) = &submitted {
            match self.schedule_file(task_id, path).await {
                Ok(current) if current == *bytes => {}
                _ => return error("计划文件在执行前已变化。".into()),
            }
        }
        tokio::task::spawn_blocking(move || {
            if let Some((path, bytes)) = submitted {
                service
                    .submit_agent(&scope, &path, &bytes, args["overwrite"] == true)
                    .map(|value| json!({"success":true,"plan":value}))
            } else {
                service
                    .manage_agent(&scope, &args)
                    .map(|value| json!({"success":true,"result":value}))
            }
            .map_err(|failure| failure.message)
        })
        .await
        .unwrap_or_else(|failure| Err(failure.to_string()))
        .unwrap_or_else(error)
    }
}

fn query(service: &ScheduleService, args: &Value) -> Result<Value, String> {
    let mut plans = if let Some(id) = args["schedule_id"]
        .as_str()
        .filter(|id| !id.trim().is_empty())
    {
        vec![service.detail(id).map_err(|error| error.message)?]
    } else {
        let listed = service
            .list(args["workspace_id"].as_str(), args["enabled"].as_bool())
            .map_err(|error| error.message)?;
        listed["plans"].as_array().cloned().unwrap_or_default()
    };
    const FIELDS: &[&str] = &[
        "definition",
        "next_at",
        "next_dates",
        "created_at",
        "updated_at",
        "last_run",
        "active_run",
        "runs",
    ];
    let fields: Vec<_> = args["fields"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|field| {
            field
                .as_str()
                .filter(|name| FIELDS.contains(name))
                .ok_or("无效的计划投影字段。")
        })
        .collect::<Result<_, _>>()?;
    for plan in &mut plans {
        if args["include_runs"] == true {
            let id = plan["definition"]["id"].as_str().ok_or("计划 ID 丢失。")?;
            plan["runs"] = service.runs(id).map_err(|error| error.message)?;
        }
        if !fields.is_empty()
            && let Some(object) = plan.as_object_mut()
        {
            object.retain(|field, _| fields.contains(&field.as_str()));
        }
    }
    Ok(json!({"success":true,"plans":plans}))
}
