import fakeredis

from app.adapters.local.jobs import RqJobQueue
from app.adapters.local.notify import LogNotifier
from app.ports.jobs import JobState
from app.ports.notify import Notification


def test_job_runs_and_reports_progress():
    queue = RqJobQueue(fakeredis.FakeRedis(), is_async=False)  # runs inline
    job_id = queue.enqueue("app.jobs.ping.ping", "hello")
    status = queue.status(job_id)
    assert status.state == JobState.SUCCEEDED
    assert status.progress == 100
    assert status.message == "done"


def test_failed_job_reports_error():
    queue = RqJobQueue(fakeredis.FakeRedis(), is_async=False)
    job_id = queue.enqueue("app.jobs.does_not_exist.nope")
    status = queue.status(job_id)
    assert status.state == JobState.FAILED
    assert status.error


def test_unknown_job():
    assert RqJobQueue(fakeredis.FakeRedis()).status("missing").state == JobState.UNKNOWN


def test_log_notifier_records():
    n = LogNotifier()
    n.send(Notification(tenant_id="acme", recipients=("u1",), subject="Approval due", body="CAND-1042"))
    assert n.sent[0].subject == "Approval due"
