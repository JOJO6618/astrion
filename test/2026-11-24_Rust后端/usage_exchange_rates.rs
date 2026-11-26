use std::time::Duration;

use super::*;
use crate::usage::test_support::{MockSource, Scratch, put, read};
use axum::http::StatusCode;

#[tokio::test]
async fn stale_exchange_rate_refreshes_once_and_keeps_publication_date() {
    let fixture = Scratch::new();
    let path = fixture.path(CACHE_FILENAME);
    put(
        &path,
        json!({"usd_cny":7.0, "fetched_at":"2000-01-01T00:00:00Z", "rate_date":"1999-12-31", "source":"old"}),
    );
    let before = cached_rate(Some(fixture.root()), Timestamp::now());
    assert_eq!(before["stale"], true);
    let server = MockSource::start(
        StatusCode::OK,
        json!({"base":"USD", "rates":{"CNY":7.19}, "date":"2000-01-03"}),
    )
    .await;
    let refresh = ExchangeRateRefresh::with_source(fixture.root(), &server.url);
    for _ in 0..8 {
        refresh.refresh_if_stale(&before);
    }
    let fresh = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let payload = cached_rate(Some(fixture.root()), Timestamp::now());
            if payload["stale"] == false {
                break payload;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(fresh["usd_cny"].as_f64(), Some(7.19));
    assert_eq!(fresh["rate_date"], "2000-01-03");
    assert_eq!(fresh["source"], server.url);
    assert_eq!(server.calls(), 1);
    assert!(read(&path).get("stale").is_none());
}

#[tokio::test]
async fn invalid_exchange_response_preserves_last_good_rate() {
    let fixture = Scratch::new();
    let path = fixture.path(CACHE_FILENAME);
    let old =
        json!({"usd_cny":6.99, "fetched_at":"2000-01-01T00:00:00Z", "rate_date":"1999-12-31"});
    put(&path, old.clone());
    let server = MockSource::start(
        StatusCode::OK,
        json!({"base":"EUR", "rates":{"CNY":8.03}, "date":"2000-01-03"}),
    )
    .await;
    let refresh = ExchangeRateRefresh::with_source(fixture.root(), &server.url);
    assert_eq!(
        refresh.fetch_and_store().await.unwrap_err().kind(),
        io::ErrorKind::InvalidData
    );
    assert_eq!(read(&path), old);
    assert_eq!(
        cached_rate(Some(fixture.root()), Timestamp::now())["stale"],
        true
    );
    assert_eq!(cached_rate(None, Timestamp::now()), json!({"stale":true}));
}
