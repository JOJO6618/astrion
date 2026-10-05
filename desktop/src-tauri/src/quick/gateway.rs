use serde_json::Value;
use std::path::Path;
use std::time::Duration;

pub fn allowed(route:&str,method:&str)->bool {
    if !route.starts_with("/api/") || route.contains("..") || route.contains('#') || route.contains('\\') {return false;}
    let path=route.split('?').next().unwrap_or("");
    let parts:Vec<_>=path.trim_matches('/').split('/').collect();
    if parts.iter().any(|p|p.is_empty() || p.contains('%')) {return false;}
    match (method,parts.as_slice()) {
        ("GET",["api","host","workspaces"]|["api","v1","models"]|["api","personalization"]|["api","tasks"]|["api","status"])=>true,
        ("GET",["api","runtime","sessions"]|["api","runtime","sessions",_,"history"]|["api","tasks",_]|["api","conversations",_,"running-status"])=>true,
        ("GET",["api","tool-approvals"|"plan-approvals"|"user-questions","pending"])=>true,
        ("POST",["api","tasks"]|["api","runtime","sessions"]|["api","personalization"])=>true,
        ("POST",["api","tasks",_,"cancel"|"runtime_guidance"])=>true,
        ("POST",["api","tool-approvals",_,"decision"]|["api","plan-approvals"|"user-questions",_,"answer"])=>true,
        _=>false,
    }
}

pub fn request(port:u16,root:&Path,route:&str,method:&str,body:Option<&Value>,workspace:&str)->Result<Value,String> {
    if !allowed(route,method) {return Err("Unsupported Gateway request".into());}
    if workspace.chars().any(char::is_control) {return Err("Invalid workspace".into());}
    let token=std::fs::read_to_string(root.join("host/data/host_api_token")).map_err(|e|format!("Gateway token: {e}"))?;
    let client=reqwest::blocking::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(15)).build().map_err(|e|e.to_string())?;
    let mut request=client.request(method.parse().map_err(|_|"Invalid method")?,format!("http://127.0.0.1:{port}{route}"))
        .bearer_auth(token.trim()).header("Content-Type","application/json");
    if !workspace.is_empty() {request=request.header("X-Astrion-Workspace-Id",workspace);}
    if let Some(body)=body {request=request.json(body);}
    let response=request.send().map_err(|e|e.to_string())?;
    let status=response.status();
    let result:Value=response.json().map_err(|e|e.to_string())?;
    if !status.is_success() || result.get("success")==Some(&Value::Bool(false)) {
        return Err(result.get("error").and_then(Value::as_str).map(String::from).unwrap_or_else(||format!("HTTP {status}")));
    }
    Ok(result)
}

#[cfg(test)] mod tests {
    use super::*;
    #[test] fn gateway_scope() {
        assert!(allowed("/api/tasks/t1?offset=1","GET"));
        assert!(allowed("/api/tasks/t1/runtime_guidance","POST"));
        for route in ["https://evil/api/tasks","/api/providers","/api/tasks/../status","/api/tasks/%2e%2e","/api/tasks#x"] {assert!(!allowed(route,"GET"));}
        assert!(!allowed("/api/tasks/t1","DELETE"));
    }
}
