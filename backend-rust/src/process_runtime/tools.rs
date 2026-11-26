use std::{path::PathBuf, time::Duration};

use serde_json::{Value, json};

pub(crate) const COMMAND: &str = "run_command";
pub(crate) const SESSION: &str = "terminal_session";
pub(crate) const INPUT: &str = "terminal_input";
pub(crate) const SNAPSHOT: &str = "terminal_snapshot";
pub(crate) const SLEEP: &str = "sleep";

pub(crate) fn tools_category(name: &str) -> Option<&'static str> {
    match name {
        COMMAND => Some("terminal_command"),
        SESSION | INPUT | SNAPSHOT | SLEEP => Some("terminal_realtime"),
        _ => None,
    }
}

pub(crate) fn schemas() -> [Value; 5] {
    let name = json!({"type":"string","description":"Terminal session name"});
    let command = json!({"type":"string","description":"Shell input"});
    let seconds = json!({"type":"number","exclusiveMinimum":0});
    [
        schema(COMMAND, "Run a command in the bound Host workspace. Foreground commands have a 120 second limit, background commands a 3600 second limit. Background commands return within five seconds with a command ID when still running.",
            json!({"command":command,"timeout":seconds,"run_in_background":{"type":"boolean"},
                "request_full_access":{"type":"boolean","description":"Request approval for this individual command outside the sandbox"},
                "full_access_reason":{"type":"string","description":"Reason this single command needs full access"}}), &["command", "timeout"]),
        schema(SESSION, "Open, close, reset, or list persistent terminal sessions. Use the terminal for ordinary shell commands and avoid full TTY programs.",
            json!({"action":{"type":"string","enum":["open","close","list","reset"]},"session_name":name,"working_dir":{"type":"string","description":"Existing directory relative to the bound execution root (open only)"}}), &["action"]),
        schema(INPUT, "Send input to a persistent terminal and collect available output for up to 300 seconds. Reaching the wait limit leaves the command running.",
            json!({"session_name":name,"command":command,"output_wait":seconds}), &["session_name", "command", "output_wait"]),
        schema(SNAPSHOT, "Read recent output from the named or active terminal session.",
            json!({"session_name":name,"lines":{"type":"integer"},"max_chars":{"type":"integer"}}), &[]),
        schema(SLEEP, "Wait for a specified duration or collect the result of a background command in this conversation.",
            json!({"seconds":seconds,"wait_runcommand_id":{"type":"string"},"reason":{"type":"string"}}), &[]),
    ]
}

fn schema(name: &str, description: &str, mut properties: Value, required: &[&str]) -> Value {
    properties["intent"] = json!({"type":"string","description":"Purpose of the tool call"});
    json!({"type":"function","function":{"name":name,"description":description,"parameters":{
        "type":"object","properties":properties,"required":required,"additionalProperties":false
    }}})
}

pub(crate) enum Invocation {
    Command { command: String, timeout: Duration, background: bool, full_access: bool },
    Open { name: String, cwd: PathBuf },
    Close { name: String },
    Reset { name: Option<String> },
    List,
    Input { name: String, command: String, wait: Duration },
    Snapshot { name: Option<String>, lines: Option<usize>, max_chars: usize },
    Wait { id: String },
    Sleep { wait: Duration },
}

impl Invocation {
    pub(crate) fn parse(name: &str, args: &Value) -> Result<Self, &'static str> {
        let object = args.as_object().ok_or("Expected a tool argument object")?;
        let allowed: &[&str] = match name {
            COMMAND => &["command", "timeout", "run_in_background", "request_full_access", "full_access_reason"],
            SESSION => &["action", "session_name", "working_dir"],
            INPUT => &["command", "session_name", "output_wait"],
            SNAPSHOT => &["session_name", "lines", "max_chars"],
            SLEEP => &["seconds", "wait_runcommand_id", "reason"],
            _ => return Err("Unknown process tool"),
        };
        if object.keys().any(|key| key != "intent" && !allowed.contains(&key.as_str())) {
            return Err("Unsupported process tool argument");
        }
        match name {
            COMMAND => {
                let full_access = match &args["request_full_access"] {
                    Value::Null => false,
                    Value::Bool(value) => *value,
                    _ => return Err("request_full_access must be a boolean"),
                };
                if full_access && !args["full_access_reason"].as_str().is_some_and(|reason| {
                    !reason.trim().is_empty() && reason.chars().count() <= 1000
                }) {
                    return Err("A non-empty full_access_reason is required");
                }
                let background = match &args["run_in_background"] {
                    Value::Bool(value) => *value,
                    Value::Null => false,
                    _ => return Err("run_in_background must be a boolean"),
                };
                let timeout = duration(&args["timeout"], if background { 3600.0 } else { 120.0 })?;
                Ok(Self::Command { command: input(&args["command"], false)?, timeout, background, full_access })
            }
            SESSION => {
                let session = session(&args["session_name"])?;
                match args["action"].as_str() {
                    Some("open") => {
                        let cwd = args.get("working_dir").map(|value| value.as_str().ok_or("Expected a working directory"))
                            .transpose()?.unwrap_or_default().into();
                        Ok(Self::Open { name: session.unwrap_or_else(|| "default".into()), cwd })
                    }
                    Some("close") if args["working_dir"].is_null() => Ok(Self::Close {name:session.unwrap_or_else(|| "default".into())}),
                    Some("reset") if args["working_dir"].is_null() => Ok(Self::Reset {name:session}),
                    Some("list") if args["working_dir"].is_null() => Ok(Self::List),
                    _ => Err("Invalid terminal session action"),
                }
            }
            INPUT => Ok(Self::Input {
                name: session(&args["session_name"])?.ok_or("A terminal session name is required")?,
                command: input(&args["command"], true)?,
                wait: duration(&args["output_wait"], 300.0)?,
            }),
            SNAPSHOT => {
                let lines = match &args["lines"] {
                    Value::Null => Some(50),
                    value => {
                        let value = value.as_i64().ok_or("lines must be an integer")?;
                        (value > 0).then_some(value.min(200) as usize)
                    }
                };
                let max_chars = match &args["max_chars"] {
                    Value::Null => 6_000_000,
                    value => value.as_i64().ok_or("max_chars must be an integer")?.clamp(100, 6_000_000) as usize,
                };
                Ok(Self::Snapshot { name:session(&args["session_name"])?, lines, max_chars })
            }
            SLEEP => match (&args["seconds"], args["wait_runcommand_id"].as_str()) {
                (Value::Null, Some(id)) if !id.is_empty() => Ok(Self::Wait { id:id.to_owned() }),
                (seconds, None) if args["wait_runcommand_id"].is_null() => Ok(Self::Sleep { wait:duration(seconds, 3600.0)? }),
                _ => Err("Specify exactly one of seconds or wait_runcommand_id"),
            },
            _ => Err("Unknown process tool"),
        }
    }

    pub(crate) fn requires_terminal_panel(&self) -> bool {
        matches!(self, Self::Open { .. } | Self::Close { .. } | Self::Reset { .. } | Self::List | Self::Input { .. } | Self::Snapshot { .. })
    }

    pub(crate) fn strict_key(&self) -> Option<&'static str> {
        match self {
            Self::Command { background:true, .. } => Some("skill_strict_run_command_background_enabled"),
            Self::Command { background:false, .. } => Some("skill_strict_run_command_foreground_enabled"),
            invocation if invocation.requires_terminal_panel() => Some("skill_strict_terminal_enabled"),
            _ => None,
        }
    }
}

fn duration(value: &Value, max: f64) -> Result<Duration, &'static str> {
    let seconds = value.as_f64().filter(|seconds| *seconds > 0.0 && *seconds <= max)
        .ok_or("A positive timeout within the tool limit is required")?;
    Ok(Duration::from_secs_f64(seconds.max(0.001)))
}

fn input(value: &Value, terminal: bool) -> Result<String, &'static str> {
    value.as_str().filter(|input| !input.is_empty() && (terminal || !input.trim().is_empty())
        && !input.contains('\0') && input.chars().count() <= 50_000 && input.len() <= 128_000)
        .map(str::to_owned).ok_or("Invalid or oversized shell input")
}

fn session(value: &Value) -> Result<Option<String>, &'static str> {
    match value {
        Value::Null => Ok(None),
        Value::String(name) if !name.trim().is_empty() && name.len() <= 128 && !name.contains(['/', '\\'])
            && !name.chars().any(char::is_control) => Ok(Some(name.clone())),
        _ => Err("Invalid terminal session name"),
    }
}
