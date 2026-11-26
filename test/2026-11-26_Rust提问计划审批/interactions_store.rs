use super::*;
use crate::approval::{ApprovalPrincipal, ExecutionMode, TaskScope};
use serde_json::json;
use std::time::Duration;

fn scope(user: &str, workspace: &str) -> TaskScope {
    TaskScope::new(
        ApprovalPrincipal::new(user, workspace).unwrap(),
        "conversation", "task", "executor",
        std::env::temp_dir(), ExecutionMode::Sandbox,
    ).unwrap()
}

#[tokio::test]
async fn question_batch_preserves_order_and_first_valid_answers() {
    let mut store = InteractionStore::default();
    let scope = scope("host", "workspace");
    let questions = vec![
        Question::parse("first", &json!({"question":"Choose the file", "options":[
            {"id":"one","label":"One"}, {"id":"two","label":"Two"}
        ]})).unwrap(),
        Question::parse("second", &json!({"question":"Enter the label"})).unwrap(),
    ];
    let (wait, events) = store.open_questions(scope.clone(), questions);
    let principal = scope.principal();
    let pending = store.pending(principal, Kind::Question, Some("conversation"));
    assert_eq!(events.last().unwrap().name, "user_questions_required");
    assert_eq!(pending[0]["tool_call_id"], "first");
    assert_eq!(pending[1]["tool_call_id"], "second");
    let first = pending[0]["question_id"].as_str().unwrap();
    let second = pending[1]["question_id"].as_str().unwrap();
    let foreign = ApprovalPrincipal::new("host", "different-workspace").unwrap();
    assert!(store.pending(&foreign, Kind::Question, None).is_empty());
    assert_eq!(store.answer_question(&foreign, first, &QuestionAnswer::default()).unwrap_err(), Error::Forbidden);
    let invalid = QuestionAnswer { selected_option_id: Some("missing".into()), ..Default::default() };
    assert!(matches!(store.answer_question(principal, first, &invalid), Err(Error::Invalid(_))));
    let supplement = QuestionAnswer { selected_option_id: Some("two".into()), text: Some("additional detail".into()), ..Default::default() };
    let (answered, event) = store.answer_question(principal, first, &supplement).unwrap();
    assert_eq!(answered["answer"]["type"], "option_with_text");
    assert_eq!(answered["answer"]["text"], "additional detail");
    assert!(event.is_none());
    assert!(store.resolved(&wait).unwrap().is_none());
    let skip = QuestionAnswer { dismissed: Some(true), ..Default::default() };
    let (repeated, _) = store.answer_question(principal, first, &skip).unwrap();
    assert_eq!(repeated, answered);
    let (_, event) = store.answer_question(principal, second, &skip).unwrap();
    assert_eq!(event.unwrap().name, "user_questions_resolved");
    tokio::time::timeout(Duration::from_millis(200), wait.notify.notified()).await.unwrap();
    let rows = store.resolved(&wait).unwrap().unwrap();
    assert_eq!(rows[0], answered);
    assert_eq!(question_result(&rows[1])["status"], "dismissed");
}

#[test]
fn plan_cancellation_and_stale_answers_preserve_terminal_state() {
    let mut store = InteractionStore::default();
    let scope = scope("host", "workspace");
    let plan = Plan::new("plan", ".astrion/plan/test.md", "fixture plan", &json!("test"));
    let (wait, events) = store.open_plan(scope.clone(), plan);
    let pending = store.pending(scope.principal(), Kind::Plan, None);
    let id = pending[0]["approval_id"].as_str().unwrap();
    assert_eq!(events[1].name, "plan_approval_required");
    assert!(store.expire_task("other-task").is_empty());
    assert_eq!(store.expire_task("task")[0].name, "plan_approval_resolved");
    assert!(store.expire_wait(&wait, true).is_none());
    let (row, event) = store.answer_plan(scope.principal(), id, true, "late").unwrap();
    assert_eq!(row["status"], "expired");
    assert!(event.is_none());
    assert!(store.pending(scope.principal(), Kind::Plan, None).is_empty());
}
