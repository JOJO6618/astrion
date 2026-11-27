use super::ApprovalDisplay;
use serde_json::json;

#[test]
fn projects_live_and_resolved_reviews_without_reopening_them() {
    let mut display = ApprovalDisplay::default();
    let approval = json!({"approval_id":"manual","tool_name":"write_file","status":"pending"});
    display.apply(1, "tool_approval_required", &json!({"approval":approval}));
    display.apply(2, "auto_approval_progress", &json!({"approval_id":"manual","progress":{"stage":"start"}}));
    let mut pending = approval.clone();
    display.decorate_pending(&mut pending);
    assert_eq!(pending["auto_review_status"], "reviewing");
    assert_eq!(pending["auto_review_progress"][0]["stage"], "start");

    display.apply(3, "goal_review_progress", &json!({"progress":{"stage":"start"}}));
    display.apply(4, "goal_review_progress", &json!({"progress":{"stage":"done","decision":"approved"}}));
    display.apply(5, "workflow_review_progress", &json!({"progress":{"stage":"start"}}));
    display.apply(6, "tool_approval_resolved", &json!({"approval_id":"manual","decision":"rejected","reason":"fixture"}));
    display.apply(7, "tool_approval_required", &json!({"approval":approval}));
    display.apply(8, "auto_approval_progress", &json!({"approval_id":"manual","progress":{"stage":"done","decision":"approved"}}));
    display.apply(9, "tool_approval_resolved", &json!({"approval_id":"manual","decision":"approved"}));

    let records = display.records();
    assert_eq!(display.resolved(), ["manual"]);
    assert_eq!(records.len(), 3);
    assert_eq!(records[0]["kind"], "goal");
    assert_eq!(records[0]["decision"], "approved");
    assert_eq!(records[1]["kind"], "workflow");
    assert_eq!(records[2]["approval"]["status"], "rejected");
    assert_eq!(records[2]["final_decision"], "rejected");
    assert_eq!(records[2]["progress"].as_array().unwrap().len(), 1);
}

#[test]
fn bounds_review_history_independently_of_the_task_event_window() {
    let mut display = ApprovalDisplay::default();
    for idx in 0..35 {
        display.apply(idx, "goal_review_progress", &json!({"progress":{"stage":"start"}}));
    }
    for idx in 35..150 {
        display.apply(idx, "goal_review_progress", &json!({"progress":{"stage":"model_call","message":idx}}));
    }
    let records = display.records();
    assert_eq!(records.len(), 30);
    assert_eq!(records[0]["id"], "goal-5");
    assert_eq!(records[29]["progress"].as_array().unwrap().len(), 100);
    assert_eq!(records[29]["progress"][0]["message"], 50);
}
