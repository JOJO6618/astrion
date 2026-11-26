use super::*;
use serde_json::json;

#[test]
fn quota_accepts_only_valid_numeric_windows_and_preserves_fractional_values() {
    let limits = normalize_usage(&json!({
        "plan_type": "synthetic-plan",
        "credits": {"balance": 900},
        "rate_limit": {
            "primary_window": {
                "used_percent": 25.5, "limit_window_seconds": 92, "reset_at": 1000.5,
                "internal": "synthetic-private-value"
            },
            "secondary_window": {
                "used_percent": true, "limit_window_seconds": 60, "reset_at": 1000
            }
        }
    }))
    .unwrap();
    assert_eq!(
        serde_json::to_value(limits).unwrap(),
        json!({"primary": {"usedPercent": 25.5, "windowDurationMins": 92.0 / 60.0, "resetsAt": 1000.5}})
    );
    for (percent, duration, reset) in [
        (json!(-1), json!(60), json!(1000)),
        (json!(101), json!(60), json!(1000)),
        (json!(54), json!(0), json!(1000)),
        (json!(54), json!(366 * 86400 + 1), json!(1000)),
        (json!(54), json!(60), json!(0)),
        (json!(54), json!(60), json!(253_402_300_800_u64)),
        (json!("54"), json!(60), json!(1000)),
    ] {
        assert!(
            normalize_usage(&json!({"rate_limit": {"primary_window": {
                "used_percent": percent, "limit_window_seconds": duration, "reset_at": reset
            }}}))
            .is_none()
        );
    }
}

#[test]
fn quota_keeps_idle_and_busy_query_intervals_after_failures() {
    let initial = Instant::now();
    let mut idle = Timing::default();
    assert!(idle.begin(initial, false, false));
    assert!(!idle.begin(initial + Duration::from_secs(119), false, false));
    assert!(idle.begin(initial + IDLE_INTERVAL, false, false));

    let mut busy = Timing::default();
    assert!(busy.begin(initial, true, false));
    assert!(!busy.begin(initial + Duration::from_secs(59), false, false));
    assert!(busy.begin(initial + BUSY_INTERVAL, true, false));

    let mut activity = Timing::default();
    assert!(activity.begin(initial, false, false));
    assert!(!activity.begin(initial + Duration::from_secs(59), true, true));
    assert!(activity.begin(initial + BUSY_INTERVAL, true, true));

    let mut first_failure = Timing::default();
    assert!(first_failure.begin(initial, false, false));
    assert!(!first_failure.begin(initial + BUSY_INTERVAL, true, false));
    assert!(first_failure.begin(initial + IDLE_INTERVAL, true, false));
}
