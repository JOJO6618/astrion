use super::*;

fn dates(rule: Value, after: &str) -> Vec<String> {
    let after = instant(after).unwrap();
    Rule::read(&rule, after, None).unwrap().0.next_dates(after)
}

#[test]
fn skips_dst_gap_and_uses_only_first_fold() {
    let rule = json!({"type":"daily","timezone":"America/New_York","times":["02:30"]});
    assert_eq!(
        dates(rule, "2026-03-08T00:00:00Z")[0],
        "2026-03-09T06:30:00.000000Z"
    );
    let rule = json!({"type":"daily","timezone":"America/New_York","times":["01:30"]});
    let after = instant("2026-11-01T00:00:00Z").unwrap();
    let (rule, _) = Rule::read(&rule, after, None).unwrap();
    assert_eq!(
        iso(rule.next(after).unwrap()),
        "2026-11-01T05:30:00.000000Z"
    );
    let between = instant("2026-11-01T06:00:00Z").unwrap();
    assert_eq!(
        iso(rule.next(between).unwrap()),
        "2026-11-02T06:30:00.000000Z"
    );
    assert_eq!(
        iso(rule.previous(between).unwrap()),
        "2026-11-01T05:30:00.000000Z"
    );
}

#[test]
fn supports_month_end_leap_year_and_cron_or() {
    assert_eq!(
        dates(
            json!({"type":"monthly","day":"last","timezone":"UTC","times":["09:00"]}),
            "2028-01-31T12:00:00Z"
        )[0],
        "2028-02-29T09:00:00.000000Z"
    );
    assert_eq!(
        dates(
            json!({"type":"monthly","day":31,"timezone":"UTC","times":["09:00"]}),
            "2026-01-31T12:00:00Z"
        )[0],
        "2026-03-31T09:00:00.000000Z"
    );
    let cron = json!({"type":"cron","timezone":"UTC","expression":"0 9 10 * 1"});
    assert_eq!(
        dates(cron, "2026-02-01T00:00:00Z"),
        [
            "2026-02-02T09:00:00.000000Z",
            "2026-02-09T09:00:00.000000Z",
            "2026-02-10T09:00:00.000000Z"
        ]
    );
    let sunday = json!({"type":"cron","timezone":"UTC","expression":"*/20 9 * * 7"});
    assert_eq!(
        dates(sunday, "2026-02-01T00:00:00Z")[2],
        "2026-02-01T09:40:00.000000Z"
    );
}

#[test]
fn interval_grid_and_timezone_aliases() {
    let after = instant("2026-02-01T02:23:00+02:00").unwrap();
    let interval = json!({"type":"interval","timezone":"Etc/UTC","every":10,"unit":"minutes","anchor":"2026-02-01T00:00:00Z"});
    let (clock, normalized) = Rule::read(&interval, after, None).unwrap();
    assert_eq!(
        iso(clock.previous(after).unwrap()),
        "2026-02-01T00:20:00.000000Z"
    );
    assert_eq!(
        iso(clock.next(after).unwrap()),
        "2026-02-01T00:30:00.000000Z"
    );
    let mut edited = interval;
    edited.as_object_mut().unwrap().remove("anchor");
    let (_, updated) = Rule::read(&edited, after, Some(&normalized)).unwrap();
    assert_eq!(updated["anchor"], normalized["anchor"]);
    assert!(zone_names().iter().any(|zone| zone == "America/New_York"));
    assert!(
        Rule::read(
            &json!({"type":"once","timezone":"Not/AZone","date":"2028-02-29","times":["09:00"]}),
            after,
            None
        )
        .is_err()
    );
}
