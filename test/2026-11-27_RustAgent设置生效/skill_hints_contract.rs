use super::*;

#[test]
fn matches_case_insensitively_and_deduplicates_each_rule() {
    let rules = json!({
        "one":{"keywords":["workFLOW","工作流"],"hint":format!("{MARKER}read workflow")},
        "two":{"keywords":["并行"],"hint":"read parallel"},
        "empty":{"keywords":[""],"hint":"do not inject"},
    });
    assert_eq!(combined_hint(&rules, "WORKFLOW / 工作流 / 并行"),
        Some(format!("{MARKER}read workflow\nread parallel")));
    assert_eq!(combined_hint(&rules, "普通请求"), None);
}
