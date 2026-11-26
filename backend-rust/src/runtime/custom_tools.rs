//! Host administrator plugins are executed in the current Agent's process scope.
use std::{path::Path, time::Duration};

use serde_json::{Value, json};

use super::{Runtime, process_resources::live_scope};

#[derive(Clone)]
pub(super) struct Plugin {
    pub(super) name: String,
    pub(super) schema: Value,
    source: String,
    output: Value,
    timeout: Duration,
    fingerprint: String,
}

pub(super) fn load(data: &Path) -> Result<Vec<Plugin>, String> {
    let rows =
        crate::admin_routes::runtime_custom_tools(data).map_err(|error| error.to_string())?;
    Ok(rows
        .into_iter()
        .filter_map(|row| {
            let name = row["id"].as_str()?.to_owned();
            let source = row["execution_code"].as_str()?.to_owned();
            if source.trim().is_empty() {
                return None;
            }
            let mut parameters = row["parameters"].clone();
            if parameters["type"] != "object" {
                parameters = json!({"type":"object","properties":{}});
            }
            if let Some(required) = row["required"]
                .as_array()
                .filter(|fields| !fields.is_empty())
            {
                parameters["required"] = json!(
                    required
                        .iter()
                        .filter_map(Value::as_str)
                        .collect::<Vec<_>>()
                );
            }
            let schema = json!({"type":"function","function":{
                "name":name,"description":row["description"],"parameters":parameters
            }});
            let timeout = Duration::from_secs(
                row["execution"]["timeout"]
                    .as_u64()
                    .unwrap_or(30)
                    .clamp(1, 600),
            );
            let output = row["return"]
                .as_object()
                .map(|_| row["return"].clone())
                .unwrap_or_else(|| json!({}));
            let fingerprint = crate::conversation_transfer::sha256(&serde_json::to_vec(&row).ok()?);
            Some(Plugin {
                name,
                schema,
                source,
                output,
                timeout,
                fingerprint,
            })
        })
        .collect())
}

impl Runtime {
    pub(super) async fn execute_custom_plugin(
        &self,
        task: &str,
        call: &str,
        plugin: &Plugin,
        args: &Value,
        batch: &[Value],
    ) -> Value {
        if !validate(&plugin.schema["function"]["parameters"], args, 0) {
            return failed("自定义工具参数不符合其已登记的 JSON 定义。");
        }
        let approved = json!({"plugin_arguments":args,"plugin_revision":plugin.fingerprint,
            "intent":args["intent"]});
        if let Err(error) = self
            .approve_host_mutation(task, call, &plugin.name, &approved, batch)
            .await
        {
            return error;
        }
        let launched = (|| -> Result<_, String> {
            let data = self
                .store()
                .map_err(|error| error.message)?
                .data_dir
                .clone();
            if self.disabled_task_tools(task)?.contains(&plugin.name)
                || !load(&data)?.iter().any(|current| {
                    current.name == plugin.name && current.fingerprint == plugin.fingerprint
                })
            {
                return Err("自定义工具在执行前发生变化，请重新调用。".into());
            }
            let mut store = self.store().map_err(|error| error.message)?;
            let (mut scope, _) = live_scope(&store, task)?;
            scope.access.write = true;
            scope.approval_required = false;
            let request = json!({"source":plugin.source,"arguments":args,"return":plugin.output,"tool_id":plugin.name});
            store
                .process_resources
                .start_plugin(scope, &request, plugin.timeout)
                .map_err(|error| error.to_string())
        })();
        match launched {
            Ok(operation) => operation.finish().await,
            Err(error) => failed(&error),
        }
    }
}

fn failed(error: &str) -> Value {
    json!({"success":false,"custom_tool":true,"error":error})
}

fn validate(schema: &Value, value: &Value, depth: usize) -> bool {
    if depth > 32 {
        return false;
    }
    if schema["enum"]
        .as_array()
        .is_some_and(|options| !options.contains(value))
        || schema
            .get("const")
            .is_some_and(|constant| constant != value)
    {
        return false;
    }
    let obeys = |kind| match kind {
        "object" => value.is_object(),
        "array" => value.is_array(),
        "string" => value.is_string(),
        "boolean" => value.is_boolean(),
        "number" => value.is_number(),
        "integer" => value.as_i64().is_some() || value.as_u64().is_some(),
        "null" => value.is_null(),
        _ => true,
    };
    if schema["type"].as_str().is_some_and(|kind| !obeys(kind))
        || schema["type"]
            .as_array()
            .is_some_and(|kinds| !kinds.iter().filter_map(Value::as_str).any(obeys))
    {
        return false;
    }
    if schema["allOf"]
        .as_array()
        .is_some_and(|options| options.iter().any(|item| !validate(item, value, depth + 1)))
        || schema["anyOf"]
            .as_array()
            .is_some_and(|options| !options.iter().any(|item| validate(item, value, depth + 1)))
        || schema["oneOf"].as_array().is_some_and(|options| {
            options
                .iter()
                .filter(|item| validate(item, value, depth + 1))
                .count()
                != 1
        })
    {
        return false;
    }
    if let Some(fields) = value.as_object() {
        if schema["required"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .any(|key| !fields.contains_key(key))
        {
            return false;
        }
        return fields.iter().all(|(key, value)| {
            if let Some(allowed) = schema["properties"].get(key) {
                validate(allowed, value, depth + 1)
            } else if schema["additionalProperties"] == false {
                false
            } else {
                validate(&schema["additionalProperties"], value, depth + 1)
            }
        });
    }
    if let Some(items) = value.as_array() {
        let length = items.len() as u64;
        return !schema["maxItems"]
            .as_u64()
            .is_some_and(|maximum| length > maximum)
            && !schema["minItems"]
                .as_u64()
                .is_some_and(|minimum| length < minimum)
            && items
                .iter()
                .all(|item| validate(&schema["items"], item, depth + 1));
    }
    if let Some(text) = value.as_str() {
        let length = text.chars().count() as u64;
        return !schema["maxLength"]
            .as_u64()
            .is_some_and(|maximum| length > maximum)
            && !schema["minLength"]
                .as_u64()
                .is_some_and(|minimum| length < minimum);
    }
    if let Some(number) = value.as_f64() {
        return !schema["maximum"]
            .as_f64()
            .is_some_and(|maximum| number > maximum)
            && !schema["minimum"]
                .as_f64()
                .is_some_and(|minimum| number < minimum);
    }
    true
}
