mod desktop_stop;
mod gateway;
#[cfg(test)]
#[path = "../../test/2026-11-26_Rust订阅任务/gateway_oauth.rs"]
mod subscription_gateway_tests;
mod host_crosspet;
mod host_paths;
mod host_web;
mod web_conversations;
mod workspace_routes;
mod launch;
mod lifecycle;
use astrion_headless::{
    catalog::CatalogSource,
    instance_lock::InstanceLock,
    runtime::{DesktopBrowser, Runtime, WorkflowRuntime},
    schedules::{RuntimeScheduleAdapter, ScheduleService, WorkspaceOption},
    tools::ToolSet,
    web_assets::{WebAssets, personalization::Personalization},
    web_auth::{WebAuth, WebAuthConfig},
    workflows::WorkflowRegistry,
};
use axum::{Json, Router, routing::get};
use serde_json::json;
use std::{env, io, process::ExitCode};
use tokio::net::TcpListener;

#[tokio::main]
async fn main() -> ExitCode {
    if env::args_os().nth(1).is_some_and(|arg| arg == "host-password") {
        return match launch::configure_password() {
            Ok(()) => { println!("Host 网页密码已设置。"); ExitCode::SUCCESS }
            Err(error) => { eprintln!("astrion-headless host-password: {error}"); ExitCode::FAILURE }
        };
    }
    if env::args_os()
        .nth(1)
        .is_some_and(|v| v == "--help" || v == "-h")
    {
        println!("{}", launch::USAGE);
        return ExitCode::SUCCESS;
    }
    match serve().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("astrion-headless: {error}");
            ExitCode::FAILURE
        }
    }
}
async fn serve() -> io::Result<()> {
    let config = launch::Launch::parse().map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{e}\n{}", launch::USAGE),
        )
    })?;
    let desktop_browser = if config.web { DesktopBrowser::from_parent()? } else { None };
    let paths = host_paths::HostPaths::resolve(&config.data, config.static_root.as_deref())?;
    let workspace = host_paths::Workspace::resolve(
        &config.workspace,
        &paths.workspace_config,
        &paths.resources,
    )?;
    let _writer = InstanceLock::acquire(&config.data)?;
    let listener = TcpListener::bind(config.address()).await?;
    let runtime = Runtime::open(&config.data)
        .map_err(io::Error::other)?
        .with_pricing(paths.resources.join("config"));
    runtime.configure_host_processes(&paths.deploy).map_err(io::Error::other)?;
    runtime.configure_desktop_browser(desktop_browser).map_err(io::Error::other)?;
    let registered = paths.catalog()?;
    for entry in &registered.workspaces {
        if entry.path.is_dir() {
            let tools = ToolSet::for_workspace(&entry.path, &config.data)?;
            runtime
                .bind_workspace_with_tools(&entry.id, &entry.path, tools)
                .map_err(io::Error::other)?;
        }
    }
    let token = launch::read_or_create_token(&config.data)?;
    let catalog = CatalogSource::with_paths(&paths.resources, &paths.deploy, &config.data);
    runtime.configure_automatic_titles(catalog.clone()).map_err(io::Error::other)?;
    let workflows = WorkflowRegistry::new(
        paths.resources.join("workflows"),
        paths.host_libraries.join("workflows"),
    )
    .map_err(io::Error::other)?;
    let runtime = runtime.with_workflow(WorkflowRuntime::new(
        workflows.clone(),
        &config.data,
        catalog.clone(),
        config.model.clone(),
    )).with_goal_reviewer(catalog.clone(), &paths.resources.join("config"));
    let children = astrion_headless::sub_agents::SubAgents::new(&config.data, runtime.clone())
        .with_catalog(catalog.clone(), config.model.clone());
    let runtime = runtime.with_sub_agents(children.clone());
    let global_skills = paths.resources.join(
        env::var_os("AGENT_SKILLS_DIR")
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| "agentskills".into()),
    );
    let preferences = Personalization::new(
        catalog.clone(),
        global_skills,
        paths.host_libraries.join("agentskills"),
    );
    let runtime = runtime.with_personalization(preferences.clone());
    let web = if config.web {
        let mut auth_cfg = WebAuthConfig::new(&config.data, listener.local_addr()?.port());
        auth_cfg.cookie_name = env::var("WEB_SESSION_COOKIE_NAME")
            .ok()
            .filter(|name| !name.is_empty());
        auth_cfg.secure_cookie = matches!(
            env::var("WEB_COOKIE_SECURE")
                .unwrap_or_default()
                .trim()
                .to_ascii_lowercase()
                .as_str(),
            "true" | "yes" | "1"
        );
        auth_cfg.trusted_proxies = env_list("ASTRION_TRUSTED_PROXIES")
            .into_iter()
            .map(|ip| {
                ip.parse().map_err(|_| {
                    io::Error::new(io::ErrorKind::InvalidInput, "Invalid trusted proxy address")
                })
            })
            .collect::<io::Result<_>>()?;
        auth_cfg.allowed_origins = env_list("ASTRION_ALLOWED_ORIGINS");
        auth_cfg.desktop = env::var_os("ASTRION_DESKTOP_VERSION").is_some_and(|v| !v.is_empty());
        auth_cfg.workspace_ready = !registered.workspaces.is_empty();
        Some(WebAuth::new(auth_cfg)?)
    } else {
        None
    };
    let adapter = RuntimeScheduleAdapter::new(
        runtime.clone(),
        WorkspaceOption {
            id: workspace.id.clone(),
            label: workspace.label.clone(),
        },
        &workspace.path,
        catalog.clone(),
        config.model.clone(),
    )
    .map_err(io::Error::other)?
    .with_registry(&paths.workspace_config, &paths.resources);
    let schedules = ScheduleService::open(&config.data, std::sync::Arc::new(adapter))
        .map_err(io::Error::other)?;
    runtime.set_schedule_service(&schedules);
    let navigation = astrion_headless::navigation::Navigation::new(&config.data);
    let provider_config = catalog.providers().clone();
    let crosspet_config = astrion_headless::crosspet::CrossPetConfig::for_host(&paths.deploy);
    let observer = host_crosspet::HostCrossPetRuntime::new(
        runtime.clone(), catalog.clone(), navigation.clone(), children.clone(), paths.clone(),
        provider_config.clone(), crosspet_config.clone(),
    );
    let crosspet = astrion_headless::crosspet::CrossPet::start(
        crosspet_config, std::sync::Arc::new(observer),
    )?;
    runtime.set_crosspet_events(crosspet.events()).map_err(io::Error::other)?;
    let gateway = std::sync::Arc::new(gateway::Gateway {
        runtime: runtime.clone(),
        schedules: schedules.clone(),
        children,
        paths: paths.clone(),
        workspace,
        catalog,
        workflows,
        preferences,
        fixed_model: config.model,
        token,
        web,
    });
    let project_storage = astrion_headless::project_storage::ProjectStorage::from_env(
        paths.workspace_config.clone(), paths.resources.clone(), config.data.clone(),
    )?;
    let providers = astrion_headless::providers::router(provider_config.clone());
    let desktop_bridge = astrion_headless::desktop_bridge::DesktopBridge::from_env(&config.data).map_err(io::Error::other)?;
    let routes = if let Some(auth) = &gateway.web {
        let assets = WebAssets::new(
            config
                .static_root
                .unwrap_or_else(|| paths.resources.join("static")),
        )?
        .with_host_data(&config.data);
        host_web::routes(auth, &gateway, &config.data, &paths, &assets, &navigation)?
            .merge(auth.protect_api(providers))
            .merge(auth.protect_api(astrion_headless::providers::codex_router(provider_config)))
            .merge(auth.protect_api(desktop_bridge.router()))
            .merge(auth.protect_api(crosspet.router()))
            .merge(auth.protect_api(project_storage.router()))
    } else {
        // Headless registers session-only endpoints without mounting browser
        // login or accepting Gateway Bearer credentials for these routes.
        let session_guard = WebAuth::new(WebAuthConfig::new(&config.data, listener.local_addr()?.port()))?;
        Router::new().route("/",get(headless_landing)).fallback(headless_fallback)
        .route("/schedules",get(||async { (axum::http::StatusCode::FOUND,[(axum::http::header::LOCATION,"/login"),(axum::http::header::CACHE_CONTROL,"no-store")]) }))
        .route("/api/host-mode-enabled", get(|| async {
            Json(json!({"success":true,"enabled":true,"backend":"rust","pid":std::process::id()}))
        }))
        .merge(session_guard.protect_api(Router::new().route("/api/health", get(host_web::health))))
        .merge(session_guard.protect_api(host_web::headless_workspace_routes(&gateway, &config.data)))
        .merge(session_guard.protect_api(providers))
        .merge(session_guard.protect_api(desktop_bridge.router()))
        .merge(session_guard.protect_api(crosspet.router()))
        .merge(session_guard.protect_api(project_storage.router()))
        .merge(session_guard.protect_api(astrion_headless::usage::quota_router()))
    };
    let routes = gateway::router(gateway)?.merge(routes);
    // The method router attaches Allow after its own layers return.
    let router = Router::new().fallback_service(routes)
        .layer(axum::middleware::from_fn(route_options));
    eprintln!(
        "Astrion Rust server listening on {}",
        listener.local_addr()?
    );
    let result = lifecycle::serve(listener, router, runtime, schedules).await;
    crosspet.shutdown();
    result
}

/// Preserve Flask's automatic OPTIONS for registered methods on both host surfaces.
async fn route_options(request: axum::extract::Request, next: axum::middleware::Next) -> axum::response::Response {
    use axum::http::{header, HeaderValue, Method, StatusCode};
    let options = request.method() == Method::OPTIONS;
    let mut response = next.run(request).await;
    if options && response.status() == StatusCode::METHOD_NOT_ALLOWED {
        if let Some(allowed) = response.headers().get(header::ALLOW).and_then(|v| v.to_str().ok()) {
            if !allowed.is_empty() && !allowed.split(',').any(|v| v.trim() == "OPTIONS") {
                if let Ok(value) = HeaderValue::from_str(&format!("{allowed}, OPTIONS")) {
                    *response.status_mut() = StatusCode::OK;
                    response.headers_mut().insert(header::ALLOW, value);
                    response.headers_mut().remove(header::CONTENT_LENGTH);
                    *response.body_mut() = axum::body::Body::empty();
                }
            }
        }
    }
    response
}

async fn headless_landing() -> impl axum::response::IntoResponse {
    ([(axum::http::header::CACHE_CONTROL,"no-store")], axum::response::Html(include_str!("../../static/headless.html")))
}

async fn headless_fallback(method: axum::http::Method, uri: axum::http::Uri) -> axum::response::Response {
    use axum::response::IntoResponse;
    if method == axum::http::Method::GET && !uri.path().starts_with("/api/") {
        headless_landing().await.into_response()
    } else {
        (axum::http::StatusCode::NOT_FOUND,Json(json!({"success":false,"error":"not found"}))).into_response()
    }
}

fn env_list(name: &str) -> Vec<String> {
    env::var(name)
        .unwrap_or_default()
        .split(',')
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .collect()
}

#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/launch_contract.rs"]
mod launch_tests;
