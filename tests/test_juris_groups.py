"""TDD tests for legal group management + group document audit."""

from __future__ import annotations

import uuid

import pytest

from core.juris_kai import accounts as accts
from core.juris_kai import groups as groups_api


def _acct(mgr, name="User"):
    return mgr.get_or_create(str(uuid.uuid4())[:8].replace("-", "0"), name)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(accts, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(accts, "DB_PATH", str(tmp_path / "juris.db"))
    accts._account_manager = None
    yield
    accts._account_manager = None


def _mgr():
    return accts.get_account_manager()


class TestGroupLifecycle:
    def test_create_group_makes_creator_owner(self):
        mgr = _mgr()
        owner = _acct(mgr, "Owner")
        res = groups_api.create_group("Accra Chambers", owner["account_id"])
        assert res["success"] is True
        gid = res["group"]["group_id"]
        members = mgr.list_members(gid)
        assert members[0]["account_id"] == owner["account_id"]
        assert members[0]["role"] == "owner"
        assert res["group"]["invite_code"].startswith("GRP-")

    def test_create_requires_name(self):
        mgr = _mgr()
        owner = _acct(mgr)
        assert groups_api.create_group("x", owner["account_id"])["success"] is False

    def test_join_leave(self):
        mgr = _mgr()
        owner = _acct(mgr, "Owner")
        joiner = _acct(mgr, "Joiner")
        code = groups_api.create_group("Group", owner["account_id"])["group"]["invite_code"]
        joined = groups_api.join_group(code, joiner["account_id"])
        assert joined["success"] and joined["added"]
        assert any(m["account_id"] == joiner["account_id"]
                   for m in mgr.list_members(joined["group"]["group_id"]))
        left = groups_api.leave_group(joined["group"]["group_id"], joiner["account_id"])
        assert left["removed"] is True

    def test_bad_invite_code(self):
        mgr = _mgr()
        u = _acct(mgr)
        assert groups_api.join_group("GRP-NOPE", u["account_id"])["success"] is False

    def test_rename_and_archive(self):
        mgr = _mgr()
        owner = _acct(mgr)
        gid = groups_api.create_group("Old", owner["account_id"])["group"]["group_id"]
        assert mgr.rename_group(gid, "New", actor="op")["group"]["name"] == "New"
        assert mgr.archive_group(gid, actor="op")["group"]["status"] == "archived"
        assert mgr.list_groups() == []  # archived hidden by default
        assert len(mgr.list_groups(include_archived=True)) == 1


class TestGroupMembers:
    def test_roles_and_bulk_add(self):
        mgr = _mgr()
        owner = _acct(mgr, "Owner")
        a, b, c = _acct(mgr), _acct(mgr), _acct(mgr)
        gid = groups_api.create_group("Team", owner["account_id"])["group"]["group_id"]
        assert mgr.add_member(gid, a["account_id"], role="admin", actor="op")["added"]
        assert mgr.set_member_role(gid, a["account_id"], "member", actor="op")["success"]
        bulk = mgr.bulk_add_members(gid, [b["account_id"], c["account_id"]],
                                    role="member", actor="op")
        assert bulk["added"] == 2
        members = mgr.list_members(gid)
        roles = {m["account_id"]: m["role"] for m in members}
        assert roles[owner["account_id"]] == "owner"
        assert roles[b["account_id"]] == "member"
        assert mgr.remove_member(gid, c["account_id"], actor="op")["removed"]
        assert mgr.set_member_role(gid, "ghost", "admin")["success"] is False

    def test_groups_for_account(self):
        mgr = _mgr()
        owner = _acct(mgr)
        other = _acct(mgr)
        gid = groups_api.create_group("Mine", owner["account_id"])["group"]["group_id"]
        assert [g["group_id"] for g in groups_api.groups_for_account(owner["account_id"])] == [gid]
        assert groups_api.groups_for_account(other["account_id"]) == []

    def test_mutations_are_audited(self):
        mgr = _mgr()
        owner = _acct(mgr)
        member = _acct(mgr)
        gid = groups_api.create_group("Audited", owner["account_id"])["group"]["group_id"]
        mgr.add_member(gid, member["account_id"], actor="op")
        mgr.remove_member(gid, member["account_id"], actor="op")
        actions = [e["action"] for e in mgr.get_group_audit_log(gid)]
        assert "create" in actions
        assert "member_add" in actions
        assert "member_remove" in actions


class TestGroupDocumentAudit:
    def _seed_doc(self, mgr, account_id, name):
        mgr.db.execute(
            "INSERT INTO juris_document_analyses "
            "(analysis_id, account_id, document_name, page_count, cost_ghs, status) "
            "VALUES (?, ?, ?, 1, 2.0, 'completed')",
            (str(uuid.uuid4())[:12], account_id, name))
        mgr.db.commit()

    def test_audit_classifies_and_stores_report(self):
        mgr = _mgr()
        owner = _acct(mgr, "Owner")
        gid = groups_api.create_group("Docs", owner["account_id"])["group"]["group_id"]
        self._seed_doc(mgr, owner["account_id"], "Companies Act 2019")
        self._seed_doc(mgr, owner["account_id"], "Unknown Lease Deed")

        def searcher(query, limit=3):
            if "Companies Act" in query:
                return [{"title": "Companies Act 2019", "citation": "Act 992"}]
            return []

        out = groups_api.audit_group_documents(
            gid, requested_by="op", searcher=searcher)
        assert out["success"] is True
        assert out["verified_count"] == 1
        assert out["flagged_count"] == 1
        report = out["report"]
        assert report["document_count"] == 2
        stored = mgr.list_group_reports(gid)
        assert stored and stored[0]["report_id"] == report["report_id"]
        assert stored[0]["findings"]["group"] == "Docs"
        # The audit itself is logged.
        actions = [e["action"] for e in mgr.get_group_audit_log(gid)]
        assert "document_audit" in actions

    def test_audit_missing_group(self):
        assert groups_api.audit_group_documents("nope")["success"] is False

    def test_audit_empty_group_is_clean(self):
        mgr = _mgr()
        owner = _acct(mgr)
        gid = groups_api.create_group("Empty", owner["account_id"])["group"]["group_id"]
        out = groups_api.audit_group_documents(
            gid, searcher=lambda q, limit=3: [])
        assert out["success"] is True
        assert out["report"]["document_count"] == 0


class TestTelegramGroupCommands:
    def _cmd(self, args, account, is_admin=False):
        from core.juris_kai.commands import handle_group
        return handle_group(args, account, is_admin)

    def test_create_list_join_leave(self):
        mgr = _mgr()
        owner = _acct(mgr, "Owner")
        joiner = _acct(mgr, "Joiner")
        created = self._cmd("create Kumasi Bar", owner)
        assert "Created" in created
        invite = None
        for g in groups_api.groups_for_account(owner["account_id"]):
            invite = g["invite_code"]
        assert invite
        assert "not in any" in self._cmd("", joiner)
        joined = self._cmd(f"join {invite}", joiner)
        assert "Joined" in joined
        gid = groups_api.groups_for_account(joiner["account_id"])[0]["group_id"]
        assert "Left" in self._cmd(f"leave {gid}", joiner)

    def test_non_admin_cannot_manage(self):
        mgr = _mgr()
        owner = _acct(mgr)
        outsider = _acct(mgr)
        self._cmd("create Secure", owner)
        gid = groups_api.groups_for_account(owner["account_id"])[0]["group_id"]
        msg = self._cmd(f"add {gid} {outsider['account_id']}", outsider,
                        is_admin=False)
        assert "Unknown group action" in msg

    def test_admin_can_manage_members(self):
        mgr = _mgr()
        owner = _acct(mgr)
        target = _acct(mgr)
        self._cmd("create AdminGroup", owner)
        gid = groups_api.groups_for_account(owner["account_id"])[0]["group_id"]
        assert "Added" in self._cmd(
            f"add {gid} {target['account_id']} admin", owner, is_admin=True)
        roles = {m["account_id"]: m["role"] for m in mgr.list_members(gid)}
        assert roles[target["account_id"]] == "admin"
        assert "Role updated" in self._cmd(
            f"role {gid} {target['account_id']} member", owner, is_admin=True)
        assert "Removed" in self._cmd(
            f"remove {gid} {target['account_id']}", owner, is_admin=True)
        assert "Archived" in self._cmd(f"archive {gid}", owner, is_admin=True)

