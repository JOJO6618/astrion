//! In-memory tool approvals. Keep the store under the same lock as task state.

mod http;
pub(crate) mod review;
mod types;

use std::{
    collections::HashMap,
    time::{Duration, Instant},
};

use types::{clean_reason, epoch_seconds};
use uuid::Uuid;

pub use http::{ApprovalAccess, ApprovalBackend, router};
pub use types::{
    Approval, ApprovalError, ApprovalPrincipal, ApprovalRequirements, ApprovalType,
    AutoReviewStatus, Decision, ExecutionAccess, ExecutionGrant, ExecutionMode, ExecutionScope,
    PermissionMode, ReviewTicket, Status, TaskScope, ToolRequest, resolve_approval_requirements,
};

const RESOLVED_TTL: Duration = Duration::from_secs(3600);

/// Build the same narrow capability for an exempt workspace-file call. The
/// runtime selects the exemption from admitted task settings with its lock held.
/// Path authorization still happens in the capability-bound workspace writer.
pub(crate) fn exempt_workspace_write(
    scope: &TaskScope,
    call: &ToolRequest,
) -> Result<ExecutionScope, ApprovalError> {
    call.validate(scope.execution_mode())?;
    if !matches!(call.approval_type, ApprovalType::SandboxWrite | ApprovalType::DirectExecution)
        || !matches!(call.tool_name.as_str(), "write_file" | "edit_file")
    {
        return Err(ApprovalError::ScopeMismatch);
    }
    Ok(ExecutionScope {
        task: scope.clone(),
        call: call.clone(),
    })
}

/// Call mutation methods only while holding the live task's lock. Before creating,
/// claiming or consuming, the runtime must verify the task is active and supports
/// this operation. Task cancellation and completion must call `expire_task`.
#[derive(Default)]
pub struct ApprovalStore {
    records: HashMap<String, Record>,
}

struct Record {
    item: Approval,
    review: Option<ReviewAttempt>,
    resolved_at: Option<Instant>,
    revoked: bool,
}

struct ReviewAttempt {
    id: Uuid,
    fingerprint: String,
}

impl ApprovalStore {
    pub fn create(
        &mut self,
        scope: &TaskScope,
        request: &ToolRequest,
        permission: PermissionMode,
        allow_auto_approve_full_access: bool,
    ) -> Result<Approval, ApprovalError> {
        request.validate(scope.execution_mode)?;
        let requirements = resolve_approval_requirements(
            permission,
            request.approval_type,
            allow_auto_approve_full_access,
        )?;
        self.prune_resolved();
        let item = Approval {
            approval_id: format!("approval_{}", Uuid::new_v4().simple()),
            scope: scope.clone(),
            request: request.clone(),
            requirements,
            status: Status::Pending,
            decision: None,
            created_at: epoch_seconds(),
            decided_at: None,
            auto_review_status: (requirements.auto_review_required
                || request.approval_type == ApprovalType::FullAccess)
                .then_some(AutoReviewStatus::Pending),
            auto_review_started_at: None,
            auto_reviewed_at: None,
            human_decided_at: None,
            human_decision: None,
            human_reason: None,
            auto_review_reason: None,
            reason: None,
            review_error_code: None,
            decider: None,
            remember_site: (request.approval_type == ApprovalType::BrowserAction).then_some(false),
            execution_claimed_at: None,
        };
        self.records.insert(
            item.approval_id.clone(),
            Record {
                item: item.clone(),
                review: None,
                resolved_at: None,
                revoked: false,
            },
        );
        Ok(item)
    }

    pub fn get(
        &mut self,
        principal: &ApprovalPrincipal,
        id: &str,
    ) -> Result<Approval, ApprovalError> {
        let record = self.record(id)?;
        record.check_principal(principal)?;
        Ok(record.item.clone())
    }

    pub fn list_pending(
        &mut self,
        principal: &ApprovalPrincipal,
        conversation_id: Option<&str>,
    ) -> Result<Vec<Approval>, ApprovalError> {
        self.prune_resolved();
        let mut rows = self
            .records
            .values()
            .map(|record| &record.item)
            .filter(|item| {
                item.status == Status::Pending
                    && &item.scope.principal == principal
                    && conversation_id.is_none_or(|id| id == item.scope.conversation_id)
            })
            .cloned()
            .collect::<Vec<_>>();
        rows.sort_by(|a, b| {
            a.created_at
                .total_cmp(&b.created_at)
                .then_with(|| a.approval_id.cmp(&b.approval_id))
        });
        Ok(rows)
    }

    pub fn decide(
        &mut self,
        principal: &ApprovalPrincipal,
        id: &str,
        decision: Decision,
        remember_site: bool,
    ) -> Result<Approval, ApprovalError> {
        let record = self.record(id)?;
        record.check_principal(principal)?;
        let item = &mut record.item;
        if item.status != Status::Pending {
            return Ok(item.clone());
        }
        if !item.requirements.human_approval_required {
            return Err(ApprovalError::Invalid(
                "This request requires automatic approval only",
            ));
        }
        if remember_site && decision == Decision::Approved {
            if item.request.approval_type != ApprovalType::BrowserAction
                || matches!(item.request.tool_name.as_str(), "browser_evaluate" | "browser_fill_saved_login")
            {
                return Err(ApprovalError::Invalid(
                    "Only a browser site approval may be remembered",
                ));
            }
            item.remember_site = Some(true);
        }
        if item.human_decision != Some(decision) {
            item.human_decision = Some(decision);
            item.human_decided_at = Some(epoch_seconds());
            item.decider = Some(principal.username().to_owned());
            record.resolve();
        }
        Ok(record.item.clone())
    }

    /// Start the review of a frozen input snapshot. Restarting a review replaces
    /// its ticket so an earlier reviewer cannot publish a stale result.
    pub fn start_auto_review(
        &mut self,
        scope: &TaskScope,
        id: &str,
        fingerprint: &str,
    ) -> Result<ReviewTicket, ApprovalError> {
        let record = self.record(id)?;
        record.check_task(scope)?;
        if !record.item.requirements.auto_review_required {
            return Err(ApprovalError::Invalid(
                "This request does not require automatic review",
            ));
        }
        if record.item.status != Status::Pending
            || record.item.auto_review_status == Some(AutoReviewStatus::Approved)
        {
            return Err(ApprovalError::NotApproved);
        }
        if fingerprint.trim().is_empty() {
            return Err(ApprovalError::Invalid(
                "Automatic review needs an input fingerprint",
            ));
        }
        let attempt = Uuid::new_v4();
        record.review = Some(ReviewAttempt {
            id: attempt,
            fingerprint: fingerprint.to_owned(),
        });
        record.item.auto_review_status = Some(AutoReviewStatus::Reviewing);
        record.item.auto_review_started_at = Some(epoch_seconds());
        Ok(ReviewTicket {
            approval_id: id.to_owned(),
            attempt,
        })
    }

    pub fn finish_auto_review(
        &mut self,
        ticket: &ReviewTicket,
        current_fingerprint: &str,
        decision: Decision,
        reason: Option<&str>,
    ) -> Result<Approval, ApprovalError> {
        let record = self.record(&ticket.approval_id)?;
        record.check_review(ticket, current_fingerprint)?;
        if record.item.status == Status::Pending
            && record.item.auto_review_status == Some(AutoReviewStatus::Reviewing)
        {
            record.item.auto_review_status = Some(match decision {
                Decision::Approved => AutoReviewStatus::Approved,
                Decision::Rejected => AutoReviewStatus::Rejected,
            });
            record.item.auto_reviewed_at = Some(epoch_seconds());
            record.item.auto_review_reason = clean_reason(reason);
            record.item.reason = record.item.auto_review_reason.clone();
            record.item.decider = Some("approval_agent".into());
            record.resolve();
        }
        Ok(record.item.clone())
    }

    pub fn fail_auto_review(
        &mut self,
        ticket: &ReviewTicket,
        current_fingerprint: &str,
        code: &str,
        reason: &str,
    ) -> Result<Approval, ApprovalError> {
        let record = self.record(&ticket.approval_id)?;
        record.check_review(ticket, current_fingerprint)?;
        if record.item.status == Status::Pending
            && record.item.auto_review_status == Some(AutoReviewStatus::Reviewing)
        {
            record.fail_review(code, reason);
        }
        Ok(record.item.clone())
    }

    /// Close errors detected by the trusted task before or around the review attempt.
    pub(crate) fn fail_pending_review(
        &mut self,
        scope: &TaskScope,
        id: &str,
        code: &str,
        reason: &str,
    ) -> Result<Approval, ApprovalError> {
        let record = self.record(id)?;
        record.check_task(scope)?;
        if record.item.status == Status::Pending
            && record.item.requirements.auto_review_required
            && matches!(
                record.item.auto_review_status,
                Some(AutoReviewStatus::Pending | AutoReviewStatus::Reviewing)
            )
        {
            record.fail_review(code, reason);
        }
        Ok(record.item.clone())
    }

    pub fn expire(&mut self, scope: &TaskScope, id: &str) -> Result<Approval, ApprovalError> {
        let record = self.record(id)?;
        record.check_task(scope)?;
        record.invalidate();
        Ok(record.item.clone())
    }

    /// `task_id` is the globally unique internal ID read from the locked runtime store.
    pub fn expire_task(&mut self, task_id: &str) {
        self.prune_resolved();
        for record in self
            .records
            .values_mut()
            .filter(|record| record.item.scope.task_id == task_id)
        {
            record.invalidate();
        }
    }

    /// Atomically claim the exact approved call. The caller supplies the current
    /// trusted task scope and review fingerprint under the runtime task lock.
    pub fn claim_execution(
        &mut self,
        id: &str,
        scope: &TaskScope,
        request: &ToolRequest,
        current_fingerprint: Option<&str>,
    ) -> Result<ExecutionGrant, ApprovalError> {
        let record = self.record(id)?;
        record.check_execution(scope, request, current_fingerprint)?;
        if record.item.execution_claimed_at.is_some() {
            return Err(ApprovalError::NotApproved);
        }
        record.item.execution_claimed_at = Some(epoch_seconds());
        Ok(ExecutionGrant(id.to_owned()))
    }

    /// Consume a claim immediately before tool startup with the live task lock
    /// held. Keep cancellation and context updates serialized until startup.
    pub fn consume_execution(
        &mut self,
        grant: ExecutionGrant,
        scope: &TaskScope,
        request: &ToolRequest,
        current_fingerprint: Option<&str>,
    ) -> Result<ExecutionScope, ApprovalError> {
        let record = self.record(&grant.0)?;
        record.check_execution(scope, request, current_fingerprint)?;
        if record.item.execution_claimed_at.is_none() {
            return Err(ApprovalError::NotApproved);
        }
        Ok(ExecutionScope {
            task: record.item.scope.clone(),
            call: record.item.request.clone(),
        })
    }

    fn record(&mut self, id: &str) -> Result<&mut Record, ApprovalError> {
        self.prune_resolved();
        self.records.get_mut(id).ok_or(ApprovalError::NotFound)
    }

    fn prune_resolved(&mut self) {
        self.records.retain(|_, record| {
            record
                .resolved_at
                .is_none_or(|at| at.elapsed() < RESOLVED_TTL)
        });
    }
}

impl Record {
    fn check_principal(&self, principal: &ApprovalPrincipal) -> Result<(), ApprovalError> {
        if &self.item.scope.principal != principal {
            return Err(ApprovalError::Forbidden);
        }
        Ok(())
    }

    fn check_task(&self, scope: &TaskScope) -> Result<(), ApprovalError> {
        if &self.item.scope != scope {
            return Err(ApprovalError::ScopeMismatch);
        }
        Ok(())
    }

    fn check_review(&self, ticket: &ReviewTicket, fingerprint: &str) -> Result<(), ApprovalError> {
        if self
            .review
            .as_ref()
            .is_none_or(|review| review.id != ticket.attempt || review.fingerprint != fingerprint)
        {
            return Err(ApprovalError::ReviewChanged);
        }
        Ok(())
    }

    fn check_execution(
        &self,
        scope: &TaskScope,
        request: &ToolRequest,
        fingerprint: Option<&str>,
    ) -> Result<(), ApprovalError> {
        self.check_task(scope)?;
        if !self.item.request.matches_execution(request) {
            return Err(ApprovalError::ScopeMismatch);
        }
        if self.revoked || self.item.status != Status::Approved || !self.gates_approved() {
            return Err(ApprovalError::NotApproved);
        }
        if self.item.requirements.auto_review_required
            && self
                .review
                .as_ref()
                .is_none_or(|review| Some(review.fingerprint.as_str()) != fingerprint)
        {
            return Err(ApprovalError::ReviewChanged);
        }
        Ok(())
    }

    fn gates_approved(&self) -> bool {
        (!self.item.requirements.human_approval_required
            || self.item.human_decision == Some(Decision::Approved))
            && (!self.item.requirements.auto_review_required
                || self.item.auto_review_status == Some(AutoReviewStatus::Approved))
    }

    fn resolve(&mut self) {
        if self.item.human_decision == Some(Decision::Rejected) {
            self.item.reason = self.item.human_reason.clone();
            self.complete(Status::Rejected, Some(Decision::Rejected));
        } else if self.item.auto_review_status == Some(AutoReviewStatus::Rejected) {
            self.complete(Status::Rejected, Some(Decision::Rejected));
        } else if self.gates_approved() {
            self.complete(Status::Approved, Some(Decision::Approved));
        }
    }

    fn fail_review(&mut self, code: &str, reason: &str) {
        self.item.auto_review_status = Some(AutoReviewStatus::Error);
        self.item.auto_reviewed_at = Some(epoch_seconds());
        self.item.review_error_code =
            Some(clean_reason(Some(code)).unwrap_or_else(|| "review_failed".into()));
        self.item.auto_review_reason = clean_reason(Some(reason));
        self.item.reason = self.item.auto_review_reason.clone();
        self.item.decider = Some("approval_agent".into());
        self.complete(Status::Expired, None);
    }

    fn complete(&mut self, status: Status, decision: Option<Decision>) {
        self.item.status = status;
        self.item.decision = decision;
        self.item.decided_at = Some(epoch_seconds());
        self.resolved_at = Some(Instant::now());
    }

    fn invalidate(&mut self) {
        self.revoked = true;
        if self.item.status == Status::Pending {
            self.complete(Status::Expired, None);
        }
    }
}

#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/approval_contract.rs"]
mod tests;
