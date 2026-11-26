use super::*;
use crate::usage::test_support::{MockSource, Scratch, put, read};
use axum::http::StatusCode;

fn identity(provider: &str) -> PriceIdentity {
    PriceIdentity {
        provider_id: Some("account".into()),
        modelsdev_key: Some(provider.into()),
    }
}

#[tokio::test]
async fn stale_registry_upgrades_once_and_freezes_exact_provider_price() {
    let fixture = Scratch::new();
    let path = fixture.path("modelsdev_cache.json");
    put(
        &path,
        json!({"version":2, "fetched_at":"2000-01-01T00:00:00Z", "providers":{"openai":{"models":{"same":{"cost":{"input":93}}}}}}),
    );
    put(
        &fixture.path("providers_catalog.json"),
        json!({"providers":[{"modelsdev_key":"other"}]}),
    );
    let server = MockSource::start(StatusCode::OK, json!({
        "openai":{"npm":"@ai-sdk/openai","models":{"same":{"provider":{"npm":"per-model"},"reasoning":true,"modalities":{"input":["text","image"]},"limit":{"input":90,"output":60,"context":256},"cost":{"input":2,"cache_read":0,"output":15,"cache_write":1}}}},
        "other":{"models":{"same":{"limit":{"input":61.5},"cost":{"input":5,"cache_read":-1,"output":true}}}},
        "uncatalogued":{"models":{"same":{"cost":{"input":7}}}}
    })).await;
    let refresh = PriceRefresh::with_source(fixture.root(), fixture.root(), &server.url);
    let openai = identity("openai");
    let other = identity("other");
    let (first, second) = tokio::join!(
        refresh.capture("account/same", "same", &openai),
        refresh.capture("account/same", "same", &other)
    );
    assert_eq!(server.calls(), 1);
    assert_eq!(first.provider_id.as_deref(), Some("account"));
    assert_eq!(first.source.as_deref(), Some(server.url.as_str()));
    assert_eq!(first.rates.input.as_ref().unwrap().as_u64(), Some(2));
    assert_eq!(second.rates.input.unwrap().as_u64(), Some(5));
    assert!(second.rates.cache_read.is_none());
    assert!(second.rates.output.is_none());
    let cache = read(&path);
    assert_eq!(cache["version"], 3);
    let fetched = cache["fetched_at"].as_str().unwrap();
    assert_eq!(fetched.len(), 20);
    assert_eq!(first.fetched_at.as_deref(), Some(fetched));
    assert!(cache["providers"].get("uncatalogued").is_none());
    let stored = &cache["providers"]["openai"]["models"]["same"];
    assert_eq!(stored["npm"], "per-model");
    assert_eq!(stored["reasoning"], true);
    assert_eq!(stored["context"], 256);
    assert_eq!(stored["max_input"], 90);
    assert_eq!(stored["max_output"], 60);
    assert_eq!(stored["modalities_input"], json!(["text", "image"]));
    assert!(stored["cost"].get("cache_write").is_none());
    assert!(
        cache["providers"]["other"]["models"]["same"]
            .get("max_input")
            .is_none()
    );
    let absent = refresh.capture("account/SAME", "SAME", &openai).await;
    assert!(absent.rates.input.is_none());
    assert_eq!(server.calls(), 1);
}

#[tokio::test]
async fn failed_refresh_backs_off_and_uses_existing_or_bundled_price() {
    let fixture = Scratch::new();
    let old = json!({"version":2,"source":"old source", "fetched_at":"2000-01-01T00:00:00Z", "providers":{"openai":{"models":{"exact":{"cost":{"input":3}}}}}});
    let cache = fixture.path("modelsdev_cache.json");
    put(&cache, old.clone());
    put(
        &fixture.path("modelsdev_snapshot.json"),
        json!({"version":3,"source":"bundled", "providers":{"openai":{"models":{"exact":{"cost":{"input":11}}}}}}),
    );
    let server = MockSource::start(StatusCode::BAD_GATEWAY, json!({"error":"synthetic"})).await;
    let refresh = PriceRefresh::with_source(fixture.root(), fixture.root(), &server.url);
    let provider = identity("openai");
    let snapshot = refresh.capture("account/exact", "exact", &provider).await;
    assert_eq!(snapshot.rates.input.unwrap().as_u64(), Some(3));
    assert_eq!(snapshot.source.as_deref(), Some("old source"));
    assert_eq!(read(&cache), old);
    fs::remove_file(&cache).unwrap();
    let bundled = refresh.capture("account/exact", "exact", &provider).await;
    assert_eq!(bundled.rates.input.unwrap().as_u64(), Some(11));
    assert_eq!(bundled.source.as_deref(), Some("bundled"));
    assert_eq!(server.calls(), 1);
}
