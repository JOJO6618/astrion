use std::{
    collections::HashMap,
    fs,
    path::{Path, PathBuf},
};

use serde_json::{Value, json};
use uuid::Uuid;

use super::{Catalog, CatalogSource};

struct Fixture {
    directory: PathBuf,
    repo: PathBuf,
    home: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let directory = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../test/2026-11-24_Rust后端")
            .join(format!("catalog_fixture_{}", Uuid::new_v4()));
        fs::create_dir(&directory).unwrap();
        let directory = directory.canonicalize().unwrap();
        let repo = directory.join("repo");
        let home = directory.join("home");
        fs::create_dir(&repo).unwrap();
        fs::create_dir(&home).unwrap();
        Self {
            directory,
            repo,
            home,
        }
    }

    fn source(&self, env: &[(&str, &str)]) -> CatalogSource {
        self.source_with_dotenv(env, "")
    }

    fn source_with_dotenv(&self, env: &[(&str, &str)], dotenv: &str) -> CatalogSource {
        let env: HashMap<_, _> = env
            .iter()
            .map(|(key, value)| ((*key).into(), (*value).into()))
            .collect();
        CatalogSource::from_inputs(&self.repo, &self.home, env, || dotenv.to_owned())
    }

    fn root(&self) -> PathBuf {
        self.home.join(".astrion/astrion")
    }

    fn data(&self) -> PathBuf {
        self.root().join("host/data")
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.directory);
    }
}

fn store(path: impl AsRef<Path>, data: Value) {
    let path = path.as_ref();
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, serde_json::to_vec(&data).unwrap()).unwrap();
}

fn custom(name: &str, upstream: &str) -> Value {
    json!({"model_name":name,"model_id":upstream,"url":"https://custom.invalid/v1/","apikey":"fixture-custom-secret"})
}

fn keys(catalog: &Catalog) -> Vec<String> {
    catalog.models_response()["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|model| model["model_key"].as_str().unwrap().to_owned())
        .collect()
}

#[test]
fn returns_the_complete_public_schema_and_never_serializes_connection_fields() {
    let fixture = Fixture::new();
    let mut entry = custom("internal-key", "upstream-fast");
    entry["display_name"] = json!("展示名");
    entry["description"] = json!("公开描述");
    entry["model_description"] = json!("private system prompt");
    entry["multimodal"] = json!(" Video,Image ");
    entry["context_window"] = json!("65536");
    entry["max_output_tokens"] = json!(4096);
    entry["reasoning_capability"] = json!("fast, thinking");
    entry["thinkmode_status"] = json!({"type":"switch_model","fast_model_id":"upstream-fast","thinking_model_id":"upstream-think","fast_extra_parameter":{"temperature":0},"thinking_parameter":{"temperature":1}});
    entry["extra_parameter"] = json!({"temperature":0.5,"password":"fixture-extra-secret"});
    let mut hidden = custom("hidden-key", "old-upstream");
    hidden["visible"] = json!(false);
    store(
        fixture.root().join("config/custom_models.json"),
        json!({"models":[entry, hidden]}),
    );

    let source = fixture.source(&[]);
    let catalog = source.load();
    assert_eq!(
        catalog.models_response(),
        json!({"success":true,"items":[{
            "model_key":"internal-key", "name":"展示名", "description":"公开描述", "visible":true,
            "supports_thinking":true, "fast_only":false, "thinking_only":false,
            "supports_reasoning_effort":true, "is_chatgpt_model":false, "supports_chatgpt_fast_mode":false,
            "multimodal":"image,video", "context_window":65536, "max_output_tokens":4096,
            "default_context_window":null, "max_context_window":65536, "max_input_tokens":null,
            "api_protocol":"chat_completions", "provider_type":null, "provider_id":null,
            "provider_name":null, "supported_reasoning_levels":null, "default_reasoning_level":null
        }]})
    );
    assert!(catalog.resolve_key(Some("hidden-key"), true).is_err());
    assert_eq!(
        catalog.select(Some("hidden-key"), false).unwrap().model_id,
        "old-upstream"
    );
    let fast = catalog.select(None, false).unwrap();
    let thinking = catalog.select(None, true).unwrap();
    assert_eq!(fast.key, "internal-key");
    assert_eq!(fast.model_id, "upstream-fast");
    assert_eq!(thinking.model_id, "upstream-think");
    assert_eq!(fast.endpoint, "https://custom.invalid/v1/chat/completions");
    assert_eq!(fast.api_key.as_deref(), Some("fixture-custom-secret"));
    assert_eq!(fast.extra_params["temperature"], 0);
    assert_eq!(thinking.extra_params["temperature"], 1);
    assert_eq!(fast.extra_params["password"], "fixture-extra-secret");
    assert_eq!(fast.max_output_tokens, 4096);
}

#[test]
fn honors_source_fallbacks_relative_overrides_and_model_environment_references() {
    let fixture = Fixture::new();
    store(
        fixture.repo.join("config/custom_models.json.example"),
        json!([custom("example", "seed")]),
    );
    let source = fixture.source(&[]);
    assert_eq!(source.host_data_dir(), fixture.data());
    assert_eq!(source.deploy_config_dir(), fixture.root().join("config"));
    assert_eq!(keys(&source.load()), ["example"]);
    store(
        fixture.repo.join("config/custom_models.json"),
        json!([custom("source", "seed")]),
    );
    assert_eq!(keys(&source.load()), ["source"]);
    fs::create_dir_all(fixture.root().join("config")).unwrap();
    fs::write(fixture.root().join("config/custom_models.json"), "{broken").unwrap();
    assert_eq!(
        source.load().models_response(),
        json!({"success":true,"items":[]})
    );

    let dotenv = "ASTRION_DATA_ROOT=runtime\nDATA_DIR=host-special\nFIXTURE_API_KEY=dotenv-value\nFIXTURE_URL=https://dotenv.invalid/v1\n";
    store(
        fixture.repo.join("runtime/settings.json"),
        json!({"env_vars":{"FIXTURE_URL":"https://settings.invalid/v1","FIXTURE_API_KEY":"settings-value"}}),
    );
    store(
        fixture.home.join("fixture-deploy/custom_models.json"),
        json!({"models":[
            {"model_name":"env-vars","model_id":"upstream-id","url":"ENV:FIXTURE_URL","apikey":"${FIXTURE_API_KEY}"},
            {"model_name":"missing-env","model_id":"id","url":"$FIXTURE_URL","apikey":"$UNSET_FIXTURE"},
            {"model_name":"plain-env","model_id":"id","url":"FIXTURE_URL","apikey":"LITERAL_API_KEY"},
            {"model_name":"missing-id","url":"$FIXTURE_URL","apikey":"LITERAL_API_KEY"}
        ]}),
    );
    let source = fixture.source_with_dotenv(
        &[
            ("ASTRION_DATA_ROOT", "wrong-process-root"),
            ("DEPLOY_CONFIG_DIR", "~/fixture-deploy"),
            ("FIXTURE_API_KEY", "process-value"),
        ],
        dotenv,
    );
    assert_eq!(source.host_data_dir(), fixture.repo.join("host-special"));
    assert_eq!(
        source.deploy_config_dir(),
        fixture.home.join("fixture-deploy")
    );
    let catalog = source.load();
    assert_eq!(keys(&catalog), ["env-vars", "plain-env"]);
    let selected = catalog.select(Some("env-vars"), false).unwrap();
    assert_eq!(
        selected.endpoint,
        "https://settings.invalid/v1/chat/completions"
    );
    assert_eq!(selected.api_key.as_deref(), Some("process-value"));
    assert_eq!(
        catalog
            .select(Some("plain-env"), false)
            .unwrap()
            .api_key
            .as_deref(),
        Some("LITERAL_API_KEY")
    );
    let ignore_dotenv = fixture.source_with_dotenv(
        &[
            ("ASTRION_IGNORE_DOTENV", "ON"),
            ("ASTRION_DATA_ROOT", "standalone"),
            ("DATA_DIR", "~/host"),
        ],
        dotenv,
    );
    assert_eq!(ignore_dotenv.host_data_dir(), fixture.home.join("host"));
    assert_eq!(
        ignore_dotenv.deploy_config_dir(),
        fixture.repo.join("standalone/config")
    );
}

#[test]
fn merges_provider_connections_in_registry_order_and_enriches_capabilities() {
    let fixture = Fixture::new();
    store(
        fixture.root().join("config/custom_models.json"),
        json!([
            custom("connection/a", "shadowed"),
            custom("manual", "manual-model")
        ]),
    );
    store(
        fixture.repo.join("config/providers_catalog.json"),
        json!({"providers":[
            {"id":"provider","name":"Provider","modelsdev_key":"upstream","protocol_source":"models.dev","protocol_map":{"a":"chat_completions"}}
        ]}),
    );
    store(
        fixture.repo.join("config/modelsdev_snapshot.json"),
        json!({"providers":{"upstream":{"npm":"@ai-sdk/openai","models":{
            "a":{"reasoning":false,"modalities_input":["text","image"],"context":500,"max_output":50},
            "unsupported":{"npm":"@ai-sdk/google","modalities_input":["video"]}
        }}}}),
    );
    store(
        fixture.data().join("providers.json"),
        json!({"version":2,"providers":{
            "connection":{"catalog_id":"provider","base_name":"Provider","display_index":2,"name":"Provider-2","base_url":"https://provider.invalid/v1/","api_key":"fixture-provider-secret","headers":{"X-Private":"fixture-header-secret"},"models":[{"id":"a","name":"A","context_length":99},{"id":"b","context_length":100},{"id":"unsupported"}]},
            "second":{"base_url":"https://local.invalid/v1","api_key":"not-required","models":[{"id":"b"}]},
            "off":{"enabled":false,"base_url":"https://off.invalid","api_key":"fixture-off-secret","models":[{"id":"x"}]},
            "bad":{"models":[{"id":"x"}]}
        }}),
    );
    let source = fixture.source(&[]);
    let catalog = source.load();
    assert_eq!(
        keys(&catalog),
        [
            "connection/a",
            "manual",
            "connection/b",
            "connection/unsupported",
            "second/b"
        ]
    );
    let response = catalog.models_response();
    let models = response["items"].as_array().unwrap();
    let first = &models[0];
    assert_eq!(first["provider_name"], "Provider-2");
    assert_eq!(first["description"], "Provider-2");
    assert_eq!(first["provider_id"], "connection");
    assert_eq!(first["provider_type"], "provider");
    assert_eq!(first["api_protocol"], "chat_completions");
    assert_eq!(first["context_window"], 500);
    assert_eq!(first["max_output_tokens"], 50);
    assert_eq!(first["supports_thinking"], false);
    assert_eq!(first["multimodal"], "image");
    assert_eq!(models[2]["api_protocol"], "responses");
    assert_eq!(models[2]["context_window"], 100);
    assert_eq!(models[2]["supports_reasoning_effort"], true);
    assert_eq!(models[2]["multimodal"], "image,video");
    assert_eq!(models[3]["api_protocol"], "google");
    assert_eq!(models[3]["multimodal"], "video");
    assert!(
        catalog
            .select(Some("connection/unsupported"), false)
            .is_err()
    );
    let serialized = response.to_string();
    for secret in [
        "fixture-provider-secret",
        "fixture-off-secret",
        "fixture-header-secret",
        "provider.invalid",
        "not-required",
    ] {
        assert!(!serialized.contains(secret));
    }
    let selected = catalog.select(Some("connection/b"), false).unwrap();
    assert_eq!(selected.endpoint, "https://provider.invalid/v1/responses");
    assert_eq!(selected.headers["X-Private"], "fixture-header-secret");
    assert_eq!(selected.api_key.as_deref(), Some("fixture-provider-secret"));

    store(
        fixture.data().join("modelsdev_cache.json"),
        json!({"providers":{}}),
    );
    let response = source.load().models_response();
    assert_eq!(response["items"][0]["context_window"], 99);
    assert_eq!(response["items"][2]["api_protocol"], "chat_completions");
}

#[test]
fn reads_personality_without_changing_the_public_catalog_or_rewriting_files() {
    let fixture = Fixture::new();
    store(
        fixture.root().join("config/custom_models.json"),
        json!([
            custom("first", "one"), custom("preferred", "two"), custom("env", "three"),
            {"model_name":"retired","model_id":"four","visible":false,"url":"https://custom.invalid","apikey":"fixture-secret"}
        ]),
    );
    let path = fixture.data().join("personalization.json");
    store(
        &path,
        json!({"default_model":"preferred","hidden_models":[" env ","preferred","",null,"env"], "service_key":"fixture-personality-secret"}),
    );
    let original = fs::read(&path).unwrap();
    let source = fixture.source(&[
        ("AGENT_DEFAULT_MODEL", "env"),
        ("DEFAULT_MODEL_KEY", "first"),
    ]);
    let catalog = source.load();
    assert_eq!(keys(&catalog), ["first", "preferred", "env"]);
    assert_eq!(catalog.hidden_models(), ["env", "preferred"]);
    assert_eq!(catalog.default_key(), Some("preferred"));
    assert_eq!(catalog.resolve_key(None, true).unwrap(), "env");
    assert!(catalog.select(Some("nonexistent"), false).is_err());
    assert_eq!(fs::read(&path).unwrap(), original);
    assert!(!fixture.data().join("service_config.json").exists());
    assert!(!fixture.data().join("secrets.json").exists());
    store(&path, json!({"default_model":"retired"}));
    assert_eq!(source.load().default_key(), Some("env"));
    assert_eq!(
        fixture
            .source(&[
                ("AGENT_DEFAULT_MODEL", "unknown"),
                ("DEFAULT_MODEL_KEY", "env")
            ])
            .load()
            .default_key(),
        Some("first")
    );
}

#[test]
fn projects_connected_codex_cache_metadata_without_exposing_tokens() {
    let fixture = Fixture::new();
    let data = fixture.data();
    store(
        data.join("codex_auth.json"),
        json!({"tokens":{"access_token":"fixture-token","refresh_token":"fixture-refresh"}}),
    );
    store(
        data.join("codex_accounts.json"),
        json!({"version":1,"accounts":{
            "openai-codex":{"display_hash":"abcd12","legacy":true},
            "account-off":{"display_hash":"off"},
            "account-on":{"display_hash":"ef9012","created_at":"2026-11-24"},
            "../bad":{"display_hash":"bad"}
        }}),
    );
    let model = json!({"slug":"model-id","display_name":"Codex model","description":"Shown","visibility":"list",
        "supported_in_api":true,"context_window":128000,"input_modalities":["text","image"],
        "supported_reasoning_levels":[{"effort":"high","description":"High","secret":"fixture-level-secret"},{}],
        "default_reasoning_level":"high","service_tiers":[{"id":"priority"}],"base_instructions":"fixture-private-instruction"});
    store(
        data.join("codex_models.json"),
        json!({"models":[model,{"slug":"hidden","visibility":"hide","supported_in_api":true}]}),
    );
    store(
        data.join("codex_models/account-on.json"),
        json!({"models":[model]}),
    );
    store(
        data.join("codex_models/account-off.json"),
        json!({"models":[model]}),
    );
    store(
        data.join("codex_accounts/account-on.json"),
        json!({"tokens":{"access_token":"on","refresh_token":"refresh"}}),
    );
    let source = fixture.source(&[]);
    let catalog = source.load();
    assert_eq!(
        keys(&catalog),
        ["openai-codex/model-id", "account-on/model-id"]
    );
    let response = catalog.models_response();
    let item = &response["items"][0];
    assert_eq!(item["provider_name"], "OpenAI（ChatGPT 订阅）-abcd12");
    assert_eq!(item["is_chatgpt_model"], true);
    assert_eq!(item["thinking_only"], true);
    assert_eq!(item["supports_chatgpt_fast_mode"], true);
    assert_eq!(item["multimodal"], "image");
    assert_eq!(item["api_protocol"], "responses");
    assert_eq!(
        item["supported_reasoning_levels"],
        json!([{"effort":"high","description":"High"}])
    );
    assert_eq!(item["default_reasoning_level"], "high");
    let serialized = response.to_string();
    assert!(!serialized.contains("fixture-"));
    for account in ["openai-codex", "account-on"] {
        let selected = catalog.select(Some(&format!("{account}/model-id")), true).unwrap();
        assert!(selected.api_key.is_none());
        let subscription = selected.codex.unwrap();
        assert_eq!(subscription.account_id, account);
        assert!(subscription.instructions().starts_with("fixture-private-instruction\n\n"));
        assert_eq!(selected.model_id, "model-id");
    }
    let mut selected = catalog.select(Some("openai-codex/model-id"), true).unwrap().codex.unwrap();
    selected.model_info["base_instructions"] = Value::Null;
    assert!(selected.instructions().starts_with(include_str!("../../prompts/codex_fallback.txt")));
    let local_override = data.parent().unwrap().join("config/codex_instructions.txt");
    fs::create_dir_all(local_override.parent().unwrap()).unwrap();
    fs::write(local_override, "  local override\n").unwrap();
    assert!(selected.instructions().starts_with("local override\n\n"));
    fs::remove_file(data.join("codex_auth.json")).unwrap();
    assert_eq!(keys(&source.load()), ["account-on/model-id"]);
    fs::remove_file(data.join("codex_accounts.json")).unwrap();
    let discovered = source.load();
    assert_eq!(keys(&discovered), ["account-on/model-id"]);
    assert_eq!(discovered.select(Some("account-on/model-id"), true).unwrap().codex.unwrap().account_id, "account-on");
    assert!(!data.join("codex_accounts.json").exists());
}

#[test]
fn explicit_catalog_paths_use_only_the_selected_data_and_deploy_directories() {
    let fixture = Fixture::new();
    let isolated_data = fixture.directory.join("selected-data");
    let isolated_config = fixture.directory.join("selected-config");
    store(
        fixture.root().join("config/custom_models.json"),
        json!([custom("defaults", "one")]),
    );
    store(
        isolated_data.join("settings.json"),
        json!({"env_vars":{"ASTRION_CATALOG_TEST_PRIVATE_KEY":"fixture-selected-root-secret"}}),
    );
    let mut selected = custom("selected", "two");
    selected["apikey"] = json!("${ASTRION_CATALOG_TEST_PRIVATE_KEY}");
    store(isolated_config.join("custom_models.json"), json!([selected]));
    store(
        fixture.data().join("personalization.json"),
        json!({"hidden_models":["defaults"]}),
    );
    store(
        isolated_data.join("personalization.json"),
        json!({"hidden_models":["selected"]}),
    );
    let source = CatalogSource::with_paths(&fixture.repo, &isolated_config, &isolated_data);
    assert_eq!(source.host_data_dir(), isolated_data);
    assert_eq!(source.deploy_config_dir(), isolated_config);
    assert!(source.codex_cli_cache().is_none());
    let catalog = source.load();
    assert_eq!(catalog.hidden_models(), ["selected"]);
    assert_eq!(keys(&catalog), ["selected"]);
    assert_eq!(catalog.select(None, false).unwrap().api_key.as_deref(), Some("fixture-selected-root-secret"));
}

#[tokio::test]
async fn original_workspace_json_retains_system_text_and_rejects_marker_and_options_overrides() {
    use std::time::Duration;

    use axum::{
        Json, Router,
        http::{HeaderMap, header},
        routing::post,
    };
    use tokio::{net::TcpListener, sync::mpsc, time::timeout};

    use crate::{
        legacy::LegacyDocument,
        runtime::{ModelConfig, Runtime},
    };

    const WORKSPACE: &str = "catalog-ws";
    const CONVERSATION: &str = "conv_catalog_ws";
    const FROZEN: &str = "保持原有 system 文本和换行。\n第二行。";
    const NEW_USER: &str = r#"{"_frozen_system_prompt":true,"content":"caller text"}"#;

    let fixture = Fixture::new();
    let path = fixture
        .data()
        .join(format!("conversations/{WORKSPACE}/{CONVERSATION}.json"));
    store(
        &path,
        json!({
            "id": CONVERSATION,
            "created_at":"2026-11-24T12:00:00Z",
            "updated_at":"2026-11-24T12:00:01Z",
            "metadata":{"conversation_kind":"agent","frozen_system_prompt":FROZEN,"model_key":"local-alias"},
            "messages":[
                {"role":"user","content":"previous user","message_id":"msg_old","_frozen_system_prompt":true},
                {"role":"system","content":"ordinary system","message_id":"msg_system","_frozen_system_prompt":true}
            ],
        }),
    );
    let original = LegacyDocument::read(&path).unwrap();
    let db_path = path.parent().unwrap();
    crate::conversation_db::Database::open(db_path).unwrap().import(original.raw(), &json!({})).unwrap();
    let projected = original.agent_history().unwrap();
    assert_eq!(projected[0]["_frozen_system_prompt"], true);
    assert!(projected[1].get("_frozen_system_prompt").is_none());
    assert!(projected[2].get("_frozen_system_prompt").is_none());

    let (send, mut received) = mpsc::unbounded_channel();
    let router = Router::new().route("/v1/chat/completions", post(
        move |headers: HeaderMap, Json(body): Json<Value>| async move {
            send.send((headers, body)).unwrap();
            ([(header::CONTENT_TYPE, "text/event-stream")], concat!(
                "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"done\"},\"finish_reason\":\"stop\"}]}\n\n",
                "data: [DONE]\n\n"
            ))
        },
    ));
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    let deploy = fixture.directory.join("deploy");
    let mut entry = custom("local-alias", "real-upstream-id");
    entry["url"] = json!(format!("http://{address}/v1"));
    entry["max_output_tokens"] = json!(124);
    entry["extra_parameter"] = json!({
        "temperature":0.25, "stream_options":{"include_usage":true}, "MODEL":"different",
        "messages":["override"],"tools":[],"tool_choice":"none","stream":false,
        "n":3,"endpoint":"https://untrusted.invalid","api_key":"fixture-extras-auth",
    });
    store(deploy.join("custom_models.json"), json!([entry]));
    let catalog = CatalogSource::with_paths(&fixture.repo, &deploy, &fixture.data()).load();
    let mut selected = catalog.select(Some("local-alias"), false).unwrap();
    selected.headers = json!({
        "Authorization":"fixture-override", "Proxy-Authorization":"fixture-proxy-auth",
        "Host":"untrusted.invalid", "Content-Type":"text/plain", "Accept":"text/plain",
        "Cookie":"fixture-private-cookie", "X-Forwarded-Host":"untrusted.invalid",
        "X-Astrion-Workspace-Id":"untrusted", "X-Provider-Header":"fixture-header"
    })
    .as_object()
    .unwrap()
    .clone();
    let runtime = Runtime::open(fixture.data()).unwrap();
    runtime.bind_workspace(WORKSPACE, &fixture.repo).unwrap();
    let accepted = runtime.create_task(WORKSPACE, &json!({
        "conversation_id":CONVERSATION,"model_key":"local-alias","message":NEW_USER,"_frozen_system_prompt":true
    }), ModelConfig::from_catalog(selected).unwrap()).unwrap();
    let (headers, body) = timeout(Duration::from_secs(5), received.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        headers[header::AUTHORIZATION],
        "Bearer fixture-custom-secret"
    );
    assert_eq!(headers[header::HOST], address.to_string());
    assert_eq!(headers[header::CONTENT_TYPE], "application/json");
    assert_eq!(headers[header::ACCEPT], "text/event-stream");
    assert_eq!(headers["x-provider-header"], "fixture-header");
    for name in [
        "x-forwarded-host",
        "x-astrion-workspace-id",
        "proxy-authorization",
        "cookie",
    ] {
        assert!(!headers.contains_key(name));
    }
    assert_eq!(body["model"], "real-upstream-id");
    assert_eq!(body["stream"], true);
    assert_eq!(body["tool_choice"], "auto");
    assert!(body["tools"].is_array());
    assert_eq!(body["temperature"], 0.25);
    assert_eq!(body["max_tokens"], 124);
    assert_eq!(body["stream_options"]["include_usage"], true);
    for key in ["MODEL", "n", "endpoint", "api_key"] {
        assert!(body.get(key).is_none());
    }
    assert_eq!(
        body["messages"],
        json!([
            {"role":"system","content":FROZEN},
            {"role":"user","content":"previous user"},
            {"role":"system","content":"ordinary system"},
            {"role":"user","content":NEW_USER}
        ])
    );
    let id = accepted["data"]["task_id"].as_str().unwrap();
    timeout(Duration::from_secs(5), async {
        loop {
            let status = runtime.poll_task(WORKSPACE, id, 0).unwrap();
            if status["data"]["status"] != "running" {
                assert_eq!(status["data"]["status"], "succeeded", "{status}");
                break;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    let document = LegacyDocument::from_value(crate::conversation_db::Database::open(db_path).unwrap().read(CONVERSATION).unwrap().unwrap()).unwrap();
    assert_eq!(document.raw()["metadata"]["frozen_system_prompt"], FROZEN);
    assert_eq!(document.raw()["metadata"]["model_key"], "local-alias");
    assert_eq!(document.raw()["messages"][0]["_frozen_system_prompt"], true);
    assert_eq!(document.raw()["messages"][2]["content"], NEW_USER);
    assert!(
        document.raw()["messages"][2]
            .get("_frozen_system_prompt")
            .is_none()
    );
    assert_eq!(document.raw()["messages"][3]["content"], "done");
    server.abort();
}

#[tokio::test]
async fn selected_models_send_python_reasoning_options_to_each_protocol() {
    use std::time::Duration;

    use axum::{Json, Router, http::header, routing::post};
    use tokio::{net::TcpListener, sync::mpsc, time::timeout};

    use crate::runtime::ModelConfig;

    let fixture = Fixture::new();
    let (send, mut requests) = mpsc::unbounded_channel::<(&str, Value)>();
    let chat_send = send.clone();
    let router = Router::new()
        .route("/v1/chat/completions", post(move |Json(body): Json<Value>| async move {
            chat_send.send(("chat", body)).unwrap();
            ([(header::CONTENT_TYPE, "text/event-stream")], concat!(
                "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"done\"},\"finish_reason\":\"stop\"}]}\n\n",
                "data: [DONE]\n\n"
            ))
        }))
        .route("/v1/responses", post(move |Json(body): Json<Value>| async move {
            send.send(("responses", body)).unwrap();
            ([(header::CONTENT_TYPE, "text/event-stream")], concat!(
                "data: {\"type\":\"response.output_text.delta\",\"delta\":\"done\"}\n\n",
                "data: {\"type\":\"response.completed\"}\n\n"
            ))
        }));
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}/v1", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    let make_custom = |key, upstream| {
        let mut item = custom(key, upstream);
        item["url"] = json!(base);
        item["reasoning_capability"] = json!("fast,thinking");
        item
    };
    let mut explicit = make_custom("explicit", "qwen-test");
    explicit["extra_parameter"] = json!({"enable_thinking":true,"reasoning_effort":"low"});
    store(
        fixture.root().join("config/custom_models.json"),
        json!([make_custom("alias", "vendor/QWEN-test"), explicit, make_custom("plain", "plain-model")]),
    );
    store(
        fixture.repo.join("config/providers_catalog.json"),
        json!({"providers":[{"id":"relay","protocol_map":{"vendor/qwen-responses":"responses"}}]}),
    );
    store(
        fixture.data().join("providers.json"),
        json!({"providers":{"relay":{"catalog_id":"relay","base_url":base,"api_key":"fixture-token",
            "models":[{"id":"vendor/qwen-responses"}]}}}),
    );
    let catalog = fixture.source(&[]).load();
    let cases = [
        ("alias", false, Some("high"), "chat", json!(false), Value::Null),
        ("alias", true, Some("high"), "chat", Value::Null, json!("high")),
        ("explicit", false, Some("high"), "chat", json!(true), json!("low")),
        ("explicit", true, Some("high"), "chat", json!(true), json!("high")),
        ("plain", false, None, "chat", Value::Null, Value::Null),
        ("relay/vendor/qwen-responses", false, Some("xhigh"), "responses", Value::Null,
            json!({"effort":"xhigh","summary":"detailed"})),
    ];
    for (key, thinking, effort, expected_protocol, enable_thinking, reasoning) in cases {
        let selected = catalog.select_for_turn(Some(key), thinking, effort).unwrap();
        let config = ModelConfig::from_catalog(selected).unwrap();
        let completion = timeout(Duration::from_secs(5), config.complete(
            &[json!({"role":"user","content":"fixture"})], &[], |_| {},
        )).await.unwrap().unwrap();
        assert_eq!(completion.message["content"], "done");
        let (protocol, request) = requests.recv().await.unwrap();
        assert_eq!(protocol, expected_protocol);
        assert_eq!(request["enable_thinking"], enable_thinking, "{key}");
        if protocol == "responses" {
            assert_eq!(request["reasoning"], reasoning, "{key}");
            assert!(request.get("reasoning_effort").is_none(), "{key}");
        } else {
            assert_eq!(request["reasoning_effort"], reasoning, "{key}");
            assert!(request.get("reasoning").is_none(), "{key}");
        }
    }
    server.abort();
}
