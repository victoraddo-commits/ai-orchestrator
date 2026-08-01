from datetime import datetime, timedelta

from core.build_manager import save_builds
from core.approval_watchdog import check_stale_approvals, STALE_THRESHOLD_SECONDS, REMINDER_REPEAT_SECONDS


def _build(build_id, status, entered_at, name="17X"):
    return {
        "id": build_id,
        "name": name,
        "status": status,
        "history": [
            {"status": "REQUESTED", "timestamp": (entered_at - timedelta(hours=1)).isoformat()},
            {"status": status, "timestamp": entered_at.isoformat()},
        ],
    }


def test_no_reminder_for_a_fresh_approval_wait():
    now = datetime.now()
    save_builds([_build("b1", "WAITING_FOR_ARCHITECTURE_APPROVAL", now - timedelta(minutes=5))])

    sent = []
    reminded = check_stale_approvals(now=now, send_message=sent.append)

    assert reminded == []
    assert sent == []


def test_reminder_sent_once_a_wait_crosses_the_stale_threshold():
    now = datetime.now()
    entered_at = now - timedelta(seconds=STALE_THRESHOLD_SECONDS + 60)
    save_builds([_build("b1", "WAITING_FOR_ARCHITECTURE_APPROVAL", entered_at, name="17A")])

    sent = []
    reminded = check_stale_approvals(now=now, send_message=sent.append)

    assert reminded == ["b1"]
    assert len(sent) == 1
    assert "17A" in sent[0]
    assert "architecture approval" in sent[0]


def test_does_not_re_remind_immediately_on_the_next_cycle():
    now = datetime.now()
    entered_at = now - timedelta(seconds=STALE_THRESHOLD_SECONDS + 60)
    save_builds([_build("b1", "WAITING_FOR_ARCHITECTURE_APPROVAL", entered_at)])

    sent = []
    check_stale_approvals(now=now, send_message=sent.append)

    # A second check a minute later (still well under the repeat window)
    # must not nag again.
    reminded_again = check_stale_approvals(now=now + timedelta(minutes=1), send_message=sent.append)

    assert reminded_again == []
    assert len(sent) == 1


def test_re_reminds_after_the_repeat_window_if_still_stuck():
    now = datetime.now()
    entered_at = now - timedelta(seconds=STALE_THRESHOLD_SECONDS + 60)
    save_builds([_build("b1", "WAITING_FOR_ARCHITECTURE_APPROVAL", entered_at)])

    sent = []
    check_stale_approvals(now=now, send_message=sent.append)

    later = now + timedelta(seconds=REMINDER_REPEAT_SECONDS + 60)
    reminded_again = check_stale_approvals(now=later, send_message=sent.append)

    assert reminded_again == ["b1"]
    assert len(sent) == 2


def test_reminds_again_when_a_build_moves_to_a_different_waiting_status():
    now = datetime.now()
    entered_at = now - timedelta(seconds=STALE_THRESHOLD_SECONDS + 60)
    save_builds([_build("b1", "WAITING_FOR_ARCHITECTURE_APPROVAL", entered_at)])

    sent = []
    check_stale_approvals(now=now, send_message=sent.append)

    # Same build, now stuck on deploy approval instead -- a fresh gate, not
    # the one already reminded about, so it should get its own notice even
    # though we're still inside the repeat window.
    later = now + timedelta(minutes=45)
    save_builds([_build("b1", "WAITING_FOR_DEPLOY_APPROVAL", later - timedelta(seconds=STALE_THRESHOLD_SECONDS + 60))])

    reminded_again = check_stale_approvals(now=later, send_message=sent.append)

    assert reminded_again == ["b1"]
    assert "deploy approval" in sent[1]


def test_no_reminder_once_the_build_leaves_the_waiting_state():
    now = datetime.now()
    entered_at = now - timedelta(seconds=STALE_THRESHOLD_SECONDS + 60)
    save_builds([_build("b1", "WAITING_FOR_ARCHITECTURE_APPROVAL", entered_at)])

    sent = []
    check_stale_approvals(now=now, send_message=sent.append)

    save_builds([{**_build("b1", "GENERATING", now), "status": "GENERATING"}])

    reminded_again = check_stale_approvals(now=now + timedelta(hours=3), send_message=sent.append)

    assert reminded_again == []
    assert len(sent) == 1


def test_waiting_for_user_input_gets_a_reminder_too():
    now = datetime.now()
    entered_at = now - timedelta(seconds=STALE_THRESHOLD_SECONDS + 60)
    save_builds([_build("b1", "WAITING_FOR_USER_INPUT", entered_at)])

    sent = []
    reminded = check_stale_approvals(now=now, send_message=sent.append)

    assert reminded == ["b1"]
    assert "clarifying question" in sent[0]


def test_multiple_stale_builds_each_get_their_own_reminder():
    now = datetime.now()
    entered_at = now - timedelta(seconds=STALE_THRESHOLD_SECONDS + 60)
    save_builds([
        _build("b1", "WAITING_FOR_ARCHITECTURE_APPROVAL", entered_at, name="17A"),
        _build("b2", "WAITING_FOR_DEPLOY_APPROVAL", entered_at, name="17K"),
    ])

    sent = []
    reminded = check_stale_approvals(now=now, send_message=sent.append)

    assert set(reminded) == {"b1", "b2"}
    assert len(sent) == 2
