//! Question and plan approval state shared by the runtime and authenticated API.

mod http;
mod store;
mod tools;

pub use http::router;
pub use store::InteractionStore;
pub use tools::{ASK_USER, SUBMIT_PLAN, is_tool};

pub(crate) use store::{Event, Kind, Wait};
pub(crate) use tools::{Plan, Question, QuestionAnswer, question_result, schemas};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Error {
    Invalid(&'static str),
    Missing,
    Forbidden,
    Unavailable,
    RateLimited,
}

#[cfg(test)]
#[path = "../../../test/2026-11-26_Rust提问计划审批/interactions_store.rs"]
mod tests;
