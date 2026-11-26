use super::*;

#[test]
fn accumulates_chunked_loopback_urls_and_rejects_external_hosts() {
    let mut text = ModelText::default();
    assert!(text.feed("地址 http://127.0.0.1:81", false).is_empty());
    let targets = text.feed(
        "23/docs，然后 http://localhost.evil.example:1234/ 和 http://0.0.0.0:5123/",
        false,
    );
    assert_eq!(targets.len(), 1);
    assert_eq!(targets[0]["url"], "http://127.0.0.1:8123/docs");
    let targets = text.feed(
        "next http://127.0.0.1:8123/docs https://[::1]:444/settings",
        true,
    );
    assert!(
        targets
            .iter()
            .all(|target| target["url"] != "http://127.0.0.1:8123/docs")
    );
    assert!(
        targets
            .iter()
            .any(|target| target["url"] == "https://[::1]:444/settings")
    );
    assert!(
        text.feed(
            "http://localhost/ http://localhost:8888/favicon.ico http://127.0.0.1:99999/",
            true
        )
        .is_empty()
    );
}

#[test]
fn normalizes_unspecified_host_for_an_existing_server_target() {
    let mut text = ModelText::default();
    let targets = text.feed("打开 http://0.0.0.0:5123/second", true);
    assert_eq!(targets.len(), 1);
    assert_eq!(targets[0]["url"], "http://127.0.0.1:5123/second");
}
