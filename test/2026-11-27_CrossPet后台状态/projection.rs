use super::{projection::Projection, *};
use std::sync::Mutex;

#[derive(Default)]
struct TestRuntime(Mutex<BTreeMap<TaskRef, TaskObservation>>);

impl CrossPetRuntime for TestRuntime {
    fn model_id(&self, _: &str) -> Option<String> { None }
    fn foreground(&self) -> io::Result<Option<Foreground>> { Ok(None) }
    fn observe(&self, _: &[TaskRef]) -> BTreeMap<TaskRef, TaskObservation> {
        self.0.lock().unwrap().clone()
    }
}

#[test]
fn crosspet_completion_waits_for_an_idle_sample_after_the_main_task_ends() {
    let mut projection = Projection::default();
    let runtime = TestRuntime::default();
    let active = TaskRef {
        session: SessionIdentity {
            workspace_id: "workspace".into(),
            conversation_id: "conv_fixture".into(),
        },
        task_id: "fixture".into(),
    };
    let event = |action| TaskEvent { task: active.clone(), model_key: String::new(), action };
    let instance = uuid::Uuid::new_v4();
    projection.apply(event(Action::Started), 100.0);
    runtime.0.lock().unwrap().insert(active.clone(), TaskObservation {
        terminal: None, background: Some(false),
    });
    projection.inspect(&runtime, 101.0);

    projection.apply(event(Action::Finished(TerminalStatus::Succeeded)), 102.0);
    let update = projection.snapshot(instance, 1, 102.0, None);
    assert_eq!(update["sessions"].as_object().unwrap().values().next().unwrap()["pose"], "delegating");
    for (index, background, pose) in [(2, true, "delegating"), (3, false, "happy")] {
        runtime.0.lock().unwrap().insert(active.clone(), TaskObservation {
            terminal: Some(TerminalStatus::Succeeded), background: Some(background),
        });
        projection.inspect(&runtime, 102.0 + index as f64);
        let update = projection.snapshot(instance, index, 102.0 + index as f64, None);
        let row = update["sessions"].as_object().unwrap().values().next().unwrap();
        assert_eq!(row["pose"], pose);
        assert_eq!(row["active"], background);
    }
}
