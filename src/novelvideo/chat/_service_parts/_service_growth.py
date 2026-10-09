"""Internal implementation part for the chat service facade."""

from __future__ import annotations

from ._service_shared import *  # noqa: F401,F403

# Definitions in this module share the facade namespace at runtime.
# ruff: noqa: F401,F403,F405,F821

def _collect_provider_task_ids(value: object, sink: set[str]) -> None:
    """Collect bounded provider task identities from nested ACP receipts."""

    if len(sink) >= 32:
        return
    if isinstance(value, dict):
        for key in (
            "provider_task_id",
            "providerTaskId",
            "provider_task_ids",
            "providerTaskIds",
        ):
            candidate = value.get(key)
            values = candidate if isinstance(candidate, (list, tuple, set)) else [candidate]
            for item in values:
                normalized = redact_secrets(str(item or "")).strip()
                if normalized:
                    sink.add(normalized[:240])
                    if len(sink) >= 32:
                        return
        for child in value.values():
            _collect_provider_task_ids(child, sink)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            _collect_provider_task_ids(child, sink)


def _schedule_growth_task(coro) -> None:
    """Track a best-effort growth task without losing task exceptions."""

    task = asyncio.create_task(coro)
    _GROWTH_TASKS.add(task)

    def done(completed: asyncio.Task[object]) -> None:
        _GROWTH_TASKS.discard(completed)
        try:
            completed.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 - outbox replay stays non-blocking
            logger.warning(
                "Xiaoshu growth background task failed: %s",
                type(exc).__name__,
            )

    task.add_done_callback(done)


def _growth_distiller_lease_seconds() -> int:
    """Keep the SQLite claim lease longer than the provider request window."""

    try:
        timeout = float(os.environ.get("GROWTH_DISTILLER_TIMEOUT_SECONDS", "180"))
    except ValueError:
        timeout = 180.0
    try:
        margin = float(os.environ.get("GROWTH_DISTILLER_LEASE_MARGIN_SECONDS", "30"))
    except ValueError:
        margin = 30.0
    return max(30, int(timeout + max(5.0, margin) + 0.999))


async def shutdown_growth_distillation(*, timeout_seconds: float = 2.0) -> None:
    """Bounded shutdown drain for tracked growth tasks.

    SQLite claims remain durable when cancellation wins; the lease expiry lets
    the next process resume without losing the teaching event.
    """

    pending = [task for task in tuple(_GROWTH_TASKS) if not task.done()]
    if not pending:
        return
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=max(0.0, float(timeout_seconds)),
        )
    except TimeoutError:
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


def schedule_growth_distillation_drain(
    username: str = "local",
    *,
    project: str = "",
    limit: int = 2,
) -> None:
    """Schedule one bounded drain from lifecycle or configuration hooks."""

    _schedule_growth_task(
        _drain_growth_distillation_events(username, project=project, limit=limit)
    )


async def _distill_growth_episode(
    username: str,
    *,
    project: str,
    turn_id: str,
    conversation_id: str = "main",
    user_prompt: str,
    assistant_output: str,
    feedback: str,
    execution_result: dict[str, Any],
    project_context: str,
    task_family_hint: str,
    preceding_episode: dict[str, Any] | None = None,
    event_id: int = 0,
    worker_id: str = "",
    attempt_count: int = 0,
    materialized_result: Any = None,
) -> None:
    """Run the optional growth model off the response path.

    The model returns only a candidate recipe. Persistence is deliberately
    separate and keeps the candidate out of the executable recall pool.
    """

    worker_id = str(worker_id or "").strip() or (
        f"growth-direct:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        if event_id
        else ""
    )
    try:
        max_attempts = max(1, int(os.environ.get("GROWTH_DISTILLER_MAX_ATTEMPTS", "4")))
    except ValueError:
        max_attempts = 4
    enabled = str(os.environ.get("GROWTH_DISTILLER_ENABLED", "1")).strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        if event_id:
            await asyncio.to_thread(
                requeue_growth_distillation_event,
                username,
                event_id,
                reason="growth_distiller_disabled",
                worker_id=worker_id,
            )
        return
    try:
        from novelvideo.chat.growth_distiller import distill_growth_memory
        result = materialized_result
        if result is None:
            result = await distill_growth_memory(
                {
                    "raw_user_prompt": user_prompt,
                    "assistant_output": assistant_output,
                    "user_feedback": feedback,
                    "execution_result": execution_result,
                    "project_context": project_context,
                    "task_family_hint": task_family_hint,
                    "preceding_episode": preceding_episode or {},
                }
            )
        decision = str(getattr(result, "decision", "") or "needs_review").strip().lower()
        result_payload = (
            result.model_dump(mode="json") if hasattr(result, "model_dump") else {}
        )
        if event_id and result_payload and not str(materialized_result or ""):
            if not await asyncio.to_thread(
                materialize_growth_distillation_result,
                username,
                event_id,
                worker_id=worker_id,
                result=result_payload,
            ):
                return
        if decision == "candidate":
            saved = await asyncio.to_thread(
                save_growth_distillation_candidate,
                username,
                project=project,
                turn_id=turn_id,
                conversation_id=conversation_id,
                result=result,
                event_id=event_id,
            )
            if event_id:
                if saved is not None:
                    # A verifier can commit before the growth model finishes.
                    # Once the candidate exists, replay the bounded WorkflowRun
                    # queue so that late evidence is attached immediately,
                    # without waiting for process restart.
                    if project and event_id:
                        try:
                            from novelvideo.workflow_runtime.store import (
                                WorkflowRunStore,
                            )

                            replayed = await WorkflowRunStore(
                                _project_state_dir(username, project)
                            ).replay_verifier_learning(
                                project_id=project,
                                limit=32,
                            )
                            if replayed.get("failed"):
                                logger.warning(
                                    "growth candidate verifier replay incomplete "
                                    "project=%s failed=%s",
                                    project,
                                    replayed["failed"],
                                )
                        except Exception:  # noqa: BLE001 - growth never blocks delivery
                            logger.warning(
                                "growth candidate verifier replay failed "
                                "project=%s",
                                project,
                                exc_info=True,
                            )
                    await asyncio.to_thread(
                        finalize_growth_distillation_event,
                        username,
                        event_id,
                        worker_id=worker_id,
                        decision="add",
                        memory_id=int(saved.id),
                        reason="growth_candidate_persisted",
                        result=result_payload,
                    )
                else:
                    await asyncio.to_thread(
                        requeue_growth_distillation_event,
                        username,
                        event_id,
                        reason="growth_candidate_persist_failed",
                        worker_id=worker_id,
                    )
        elif event_id and decision == "noop":
            await asyncio.to_thread(
                finalize_growth_distillation_event,
                username,
                event_id,
                worker_id=worker_id,
                decision="evidence_only",
                reason=str(getattr(result, "reason", "growth_noop"))[:500],
            )
        elif event_id:
            review_reason = str(
                getattr(result, "reason", "growth_needs_review")
            )[:500]
            if review_reason.startswith("growth_distiller_failed:"):
                if attempt_count >= max_attempts:
                    await asyncio.to_thread(
                        finalize_growth_distillation_event,
                        username,
                        event_id,
                        worker_id=worker_id,
                        decision="needs_review",
                        reason=f"retry_exhausted:{review_reason}",
                    )
                else:
                    await asyncio.to_thread(
                        requeue_growth_distillation_event,
                        username,
                        event_id,
                        reason=review_reason,
                        delay_seconds=GROWTH_DISTILLATION_RETRY_DELAYS[
                            min(max(0, attempt_count - 1), len(GROWTH_DISTILLATION_RETRY_DELAYS) - 1)
                        ],
                        worker_id=worker_id,
                    )
            else:
                await asyncio.to_thread(
                    finalize_growth_distillation_event,
                    username,
                    event_id,
                    worker_id=worker_id,
                    decision="needs_review",
                    reason=review_reason,
                )
    except Exception:  # noqa: BLE001 - growth must never block delivery
        if event_id:
            try:
                if attempt_count >= max_attempts:
                    await asyncio.to_thread(
                        finalize_growth_distillation_event,
                        username,
                        event_id,
                        worker_id=worker_id,
                        decision="needs_review",
                        reason="retry_exhausted:growth_distiller_exception",
                    )
                else:
                    await asyncio.to_thread(
                        requeue_growth_distillation_event,
                        username,
                        event_id,
                        reason="growth_distiller_exception",
                        delay_seconds=GROWTH_DISTILLATION_RETRY_DELAYS[
                            min(max(0, attempt_count - 1), len(GROWTH_DISTILLATION_RETRY_DELAYS) - 1)
                        ],
                        worker_id=worker_id,
                    )
            except Exception:  # noqa: BLE001 - replay remains durable
                logger.warning(
                    "failed to requeue growth event user=%s event=%s",
                    username,
                    event_id,
                    exc_info=True,
                )
        logger.warning(
            "Xiaoshu growth distillation skipped user=%s project=%s",
            username,
            project,
            exc_info=True,
        )


async def _drain_growth_distillation_events(
    username: str,
    *,
    project: str = "",
    limit: int = 2,
) -> int:
    """Replay a bounded growth outbox batch at the next service entry.

    A SQLite transaction claims rows with a recoverable lease, making the
    provider-call boundary single-owner across workers. The process-local set
    is an additional same-worker guard.
    """

    from novelvideo.chat.growth_distiller import growth_distiller_model_ref

    if not growth_distiller_model_ref():
        # Keep the durable event pending when neither a dedicated model nor a
        # separately configured text model exists. Never spend the main Agent
        # model or a media model as an implicit fallback.
        return 0
    worker_id = f"growth:{os.getpid()}:{uuid.uuid4().hex[:12]}"
    rows = await asyncio.to_thread(
        claim_growth_distillation_events,
        username,
        project=project,
        limit=limit,
        worker_id=worker_id,
        lease_seconds=_growth_distiller_lease_seconds(),
    )
    processed = 0
    for row in rows:
        event_id = int(row.get("id") or 0)
        event_key = (str(username or "local"), event_id)
        if event_id <= 0 or event_key in _GROWTH_EVENTS_IN_FLIGHT:
            continue
        _GROWTH_EVENTS_IN_FLIGHT.add(event_key)
        try:
            payload = parse_growth_distillation_event(row)
            if payload is None:
                await asyncio.to_thread(
                    finalize_growth_distillation_event,
                    username,
                    event_id,
                    worker_id=worker_id,
                    decision="evidence_only",
                    reason="growth_event_payload_invalid",
                )
                processed += 1
                continue
            materialized_result = None
            stored_result = str(row.get("result_json") or "").strip()
            if stored_result:
                try:
                    from novelvideo.chat.growth_distiller import GrowthDistillationResult

                    materialized_result = GrowthDistillationResult.model_validate(
                        json.loads(stored_result)
                    )
                except Exception:
                    await asyncio.to_thread(
                        finalize_growth_distillation_event,
                        username,
                        event_id,
                        worker_id=worker_id,
                        decision="needs_review",
                        reason="growth_result_json_invalid",
                    )
                    processed += 1
                    continue
            await _distill_growth_episode(
                username,
                project=str(row.get("project_id") or project),
                turn_id=str(row.get("task_id") or ""),
                conversation_id=str(payload.get("conversation_id") or "main"),
                user_prompt=str(payload.get("raw_user_prompt") or ""),
                assistant_output=str(payload.get("assistant_output") or ""),
                feedback=str(payload.get("user_feedback") or ""),
                execution_result=(
                    payload.get("execution_result")
                    if isinstance(payload.get("execution_result"), dict)
                    else {}
                ),
                project_context=str(payload.get("project_context") or ""),
                task_family_hint=str(payload.get("task_family_hint") or ""),
                preceding_episode=(
                    payload.get("preceding_episode")
                    if isinstance(payload.get("preceding_episode"), dict)
                    else {}
                ),
                event_id=event_id,
                worker_id=worker_id,
                attempt_count=int(row.get("attempt_count") or 0),
                materialized_result=materialized_result,
            )
            processed += 1
        finally:
            _GROWTH_EVENTS_IN_FLIGHT.discard(event_key)
    return processed


@dataclass(slots=True)
class RecoverableChatTurnError(RuntimeError):
    """A turn stopped after losing its worker and can be resumed once."""

    message: str
    recovery_packet: dict[str, Any]

    def __str__(self) -> str:
        return self.message

