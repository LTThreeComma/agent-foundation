"""Pure validation of prepared Run, Thread, and checkpoint relationships."""

from pydantic import ValidationError

from a13n_service.digests import digest_request

from .control_domain import WaitingRunContinueInput, WaitingRunFeedback
from .domain import Run, RunInputKind, RunLineageKind, RunStatus, Session, Thread, ThreadOriginKind, ThreadRole
from .errors import RunAcceptanceError
from .inheritance import inherited_run_fields
from .input import AcceptedAgentInput
from .models import RunRecord, ThreadRecord
from .state import RunCheckpoint, RunPayloadEnvelope


def validate_prepared_run(run: Run, state: RunCheckpoint) -> None:
    if run.status is not RunStatus.accepted or run.version != 1:
        raise ValueError("prepared acceptance requires a version-one accepted Run")
    if state.checkpoint_kind != "initial" or state.checkpoint_seq != 0:
        raise ValueError("prepared acceptance requires initial Run state")
    validate_run_state_selection(run, state)


def validate_run_state_selection(run: Run, state: RunCheckpoint) -> None:
    """Validate immutable Run selection facts against any retained checkpoint."""

    if (state.run_id, state.thread_id) != (run.id, run.thread_id):
        raise ValueError("Run and state identities do not match")
    if (state.agent_id, state.agent_revision_id) != (run.agent_id, run.agent_revision_id):
        raise ValueError("Run and state Agent selection do not match")
    effective = state.effective_agent_config
    effective_payload = effective.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
    if digest_request(effective_payload) != effective.content_digest:
        raise ValueError("Run effective configuration digest is invalid")
    if effective.content_digest != run.effective_agent_config_digest:
        raise ValueError("Run effective configuration digest does not match state")
    if effective.resolved_model.execution.observation() != run.model_execution_observation:
        raise ValueError("Run model observation does not match state")


def validate_new_thread(thread: Thread, run: Run, session: Session | None) -> None:
    _validate_new_thread_identity(thread, run)
    _validate_new_thread_session(thread, run, session)
    _validate_new_thread_origin(thread, run)


def _validate_new_thread_identity(thread: Thread, run: Run) -> None:
    if thread.version != 1 or thread.queue_version != 0 or thread.head_run_id is not None:
        raise ValueError("new Thread must start at version one with an empty head and queue")
    if thread.current_run_id != run.id:
        raise ValueError("new Thread must select its first Run")
    if (thread.organization_id, thread.session_id, thread.id) != (run.organization_id, run.session_id, run.thread_id):
        raise ValueError("new Thread and first Run scope do not match")
    if run.retry_of_run_id is not None:
        raise ValueError("the first Run of a new Thread cannot retry another Run")


def _validate_new_thread_session(thread: Thread, run: Run, session: Session | None) -> None:
    if session is not None and (session.id, session.organization_id) != (thread.session_id, thread.organization_id):
        raise ValueError("new Session and root Thread scope do not match")
    if session is not None and thread.role is not ThreadRole.root:
        raise ValueError("a new Session must begin with its root Thread")
    if session is None and thread.role is not ThreadRole.child:
        raise ValueError("an existing Session can accept only a child Thread")


def _validate_new_thread_origin(thread: Thread, run: Run) -> None:
    if thread.origin_kind is ThreadOriginKind.fork and run.lineage_kind is not RunLineageKind.fork:
        raise ValueError("fork Thread requires fork Run lineage")
    if thread.origin_kind is ThreadOriginKind.child and run.lineage_kind is not RunLineageKind.root:
        raise ValueError("independent child Thread requires root Run lineage")
    if thread.origin_kind is ThreadOriginKind.new and run.lineage_kind is not RunLineageKind.root:
        raise ValueError("new root Thread requires root Run lineage")


def validate_queued_run_input(
    run: Run,
    payload: RunPayloadEnvelope | None,
    accepted_input: AcceptedAgentInput,
) -> None:
    if run.input_kind is not RunInputKind.agent_input or run.retry_of_run_id is not None:
        raise ValueError("queue consumption requires ordinary non-retry Agent input")
    expected = accepted_input.model_dump(mode="json", by_alias=True, exclude_none=True)
    actual = run.input if payload is None else payload.payload
    if actual != expected:
        raise ValueError("prepared queued Run input does not match its accepted submission")


def validate_retry_advancement(current: RunRecord, run: Run) -> None:
    if run.retry_of_run_id != current.id or current.status not in {
        RunStatus.failed.value,
        RunStatus.cancelled.value,
    }:
        raise RunAcceptanceError("run_retry_conflict", "Retry source is not the current failed or cancelled Run")
    _validate_retry_copy(current.to_resource(), run)


def validate_root_advancement(thread: ThreadRecord, current: RunRecord, run: Run) -> None:
    if run.lineage_kind is not RunLineageKind.root or thread.head_run_id is not None:
        raise RunAcceptanceError("run_lineage_invalid", "Root-like advancement requires a null Thread head")
    if current.status not in {RunStatus.failed.value, RunStatus.cancelled.value}:
        raise RunAcceptanceError("run_lineage_invalid", "Root-like advancement requires terminal current work")


def validate_parent_advancement(
    parent: RunRecord,
    thread: ThreadRecord,
    run: Run,
    candidate_payload: RunPayloadEnvelope | None,
) -> None:
    assert run.parent_run_id is not None
    if run.lineage_kind is RunLineageKind.fork:
        raise RunAcceptanceError("run_lineage_invalid", "Fork lineage can only be the first Run of a new Thread")
    expected_status = (
        RunStatus.waiting.value
        if run.input_kind in {RunInputKind.waiting_feedback, RunInputKind.waiting_continue}
        else RunStatus.completed.value
    )
    if parent.thread_id != thread.id or parent.status != expected_status:
        raise RunAcceptanceError("run_lineage_invalid", "Continuation parent is not an eligible same-Thread state")
    if expected_status == RunStatus.waiting.value:
        _validate_inherited_execution(parent.to_resource(), run)
        _validate_waiting_input(parent, run, candidate_payload)


def validate_selected_head(head: RunRecord, thread: ThreadRecord) -> None:
    if head.thread_id != thread.id or head.status not in {RunStatus.waiting.value, RunStatus.completed.value}:
        raise RunAcceptanceError("thread_head_invalid", "Selected Thread head is not a sealed continuation state")


def _validate_retry_copy(source: Run, candidate: Run) -> None:
    _validate_inherited_execution(source, candidate)
    object_backed = source.input_object is not None
    immutable_intent = (
        source.parent_run_id,
        source.lineage_kind,
        source.input_kind,
        object_backed,
        source.input_text,
        None if object_backed else source.input,
    )
    candidate_intent = (
        candidate.parent_run_id,
        candidate.lineage_kind,
        candidate.input_kind,
        candidate.input_object is not None,
        candidate.input_text,
        None if object_backed else candidate.input,
    )
    if candidate_intent != immutable_intent:
        raise RunAcceptanceError("run_retry_invalid", "Retry must copy the terminal Run's exact accepted intent")


def _validate_inherited_execution(source: Run, candidate: Run) -> None:
    if inherited_run_fields(candidate) != inherited_run_fields(source):
        raise RunAcceptanceError(
            "run_inherited_authority_invalid",
            "Run must preserve its source's accepted execution authority",
        )


def _validate_waiting_input(
    parent: RunRecord,
    candidate: Run,
    payload: RunPayloadEnvelope | None,
) -> None:
    raw = candidate.input if payload is None else payload.payload
    try:
        if candidate.input_kind is RunInputKind.waiting_feedback:
            accepted = WaitingRunFeedback.model_validate(raw)
        elif candidate.input_kind is RunInputKind.waiting_continue:
            accepted = WaitingRunContinueInput.model_validate(raw)
        else:
            raise RunAcceptanceError(
                "run_input_invalid",
                "Waiting continuation requires feedback or composite Continue input",
            )
    except ValidationError as error:
        raise RunAcceptanceError("run_input_invalid", "Waiting continuation input is invalid") from error
    if accepted.waiting_run_id != parent.id or accepted.sealed_state_digest_sha256 != parent.sealed_state_digest_sha256:
        raise RunAcceptanceError("run_waiting_state_conflict", "Waiting continuation state changed")
    pending = parent.to_resource().pending
    if pending is None:
        raise RunAcceptanceError("run_waiting_state_invalid", "Waiting Run has no pending summary")
    expected = tuple((call.call_id, call.kind) for call in pending.calls)
    actual = tuple((resolution.call_id, resolution.kind) for resolution in accepted.resolutions)
    if actual != expected:
        raise RunAcceptanceError(
            "run_feedback_invalid",
            "Waiting continuation does not exactly cover the frozen pending set",
        )
