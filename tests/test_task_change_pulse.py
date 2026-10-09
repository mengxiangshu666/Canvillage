import asyncio
import threading

from novelvideo.task_change_pulse import TaskChangePulse


def test_notification_wakes_waiter_from_writer_thread():
    async def scenario():
        pulse = TaskChangePulse()
        observed = pulse.version("project")
        thread = threading.Thread(target=lambda: pulse.notify("project"))
        thread.start()
        thread.join()
        assert await pulse.wait("project", observed, 0.2) > observed

    asyncio.run(scenario())


def test_timeout_and_cancellation_cleanup_waiters():
    async def scenario():
        pulse = TaskChangePulse()
        observed = pulse.version("project")
        assert await pulse.wait("project", observed, 0.001) == observed
        task = asyncio.create_task(pulse.wait("project", observed, 5))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert not pulse._waiters

    asyncio.run(scenario())


def test_project_keys_are_bounded():
    pulse = TaskChangePulse()
    for index in range(600):
        pulse.version(f"project-{index}")
    assert len(pulse._versions) <= 512


def test_registered_waiter_cross_thread_and_project_isolation():
    async def scenario():
        pulse = TaskChangePulse()
        observed = pulse.version("project-a")
        waiter = asyncio.create_task(pulse.wait("project-a", observed, 1))
        await asyncio.sleep(0)
        pulse.notify("project-b")
        await asyncio.sleep(0)
        assert not waiter.done()
        thread = threading.Thread(target=lambda: pulse.notify("project-a"))
        thread.start()
        thread.join()
        assert await waiter > observed
        assert not pulse._waiters

    asyncio.run(scenario())


def test_evicted_version_still_detects_commit_before_wait_registration():
    async def scenario():
        pulse = TaskChangePulse()
        observed = pulse.version("project-a")
        for index in range(600):
            pulse.notify(f"project-{index}")
        assert await pulse.wait("project-a", observed, 0.1) > observed

    asyncio.run(scenario())
