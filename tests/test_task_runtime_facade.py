from __future__ import annotations

from novelvideo import services


def test_task_runtime_facade_exports_cancellation_contract() -> None:
    assert issubclass(services.TaskCancelled, Exception)
    assert issubclass(services.TaskTimedOut, Exception)
    assert callable(services.run_project_subprocess)
