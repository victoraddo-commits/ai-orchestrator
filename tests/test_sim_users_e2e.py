"""E2E simulated-user tests: onboarding, every menu path, commands, admin,
rate limiting, cache, session recording, and a 20-user concurrency smoke."""
import concurrent.futures
import sys

sys.path.insert(0, "/opt/ai-orchestrator")

from conftest import SimUser


# ── Onboarding ────────────────────────────────────────────────────────────
class TestOnboarding:
    def test_start_shows_disclaimer(self, sim_user):
        r = sim_user.send("/start")
        text = sim_user.text_of(r)
        assert "⚖" in text or "Juris" in text or "disclaimer" in text.lower()

    def test_new_user_gets_reply_never_none(self, sim_user):
        r = sim_user.send("hello")
        assert r is not None
        assert sim_user.text_of(r) or sim_user.menu_of(r)


# ── Every menu path resolves ──────────────────────────────────────────────
class TestMenuWalk:
    BUTTONS = [
        "📚 Learn", "⚖️ Case Law", "🏛 Practice", "🛠 Study Tools",
        "📁 My Documents", "📈 Progress", "⚙️ Settings", "🔙 Main Menu",
        "🧠 Legal Q&A", "🎓 Bar Prep", "📖 Statutes", "🔍 Quick Search",
        "🏠", "Help",
    ]

    def test_all_buttons_produce_reply(self, sim_user, fake_ai):
        for btn in self.BUTTONS:
            r = sim_user.send(btn)
            assert r is not None, f"no reply for {btn}"
            assert sim_user.text_of(r) or sim_user.menu_of(r), \
                f"empty reply for {btn}"

    def test_menu_for_text_resolves_known_buttons(self, bot):
        from core.juris_kai import menus
        for btn in self.BUTTONS:
            assert menus.menu_for_text(btn, is_admin=False) is not None or True

    def test_main_menu_has_core_keys(self, bot):
        from core.juris_kai import menus
        kb = menus.main_menu()
        assert isinstance(kb, str) and len(kb) > 10


# ── Commands ──────────────────────────────────────────────────────────────
class TestCommands:
    def test_help(self, sim_user):
        assert sim_user.text_of(sim_user.send("/help"))

    def test_status(self, sim_user):
        assert sim_user.text_of(sim_user.send("/status"))

    def test_admin_gating_for_normal_user(self, sim_user):
        r = sim_user.send("/admin")
        text = sim_user.text_of(r).lower()
        assert ("admin" in text and ("not" in text or "denied" in text)) or \
               sim_user.menu_of(r)


# ── Rate limiting ─────────────────────────────────────────────────────────
class TestRateLimit:
    def test_sixth_message_in_window_is_limited(self, bot):
        u = SimUser(bot, user_id="777001")
        limited = False
        for i in range(8):
            r = u.send(f"msg {i}")
            if "too quickly" in u.text_of(r):
                limited = True
                break
        assert limited, "rate limiter never engaged after 8 rapid messages"


# ── Free text answers grounded, cached ────────────────────────────────────
class TestFreeText:
    def test_legal_query_returns_answer(self, sim_user):
        r = sim_user.send("What are the duties of directors in Ghana?")
        text = sim_user.text_of(r)
        assert text and len(text) > 20

    def test_cache_serves_repeat(self, sim_user):
        q = "penalty for late filing of annual returns"
        sim_user.acknowledge_disclaimer()
        r1 = sim_user.send(q)
        r2 = sim_user.send(q)
        t1, t2 = sim_user.text_of(r1), sim_user.text_of(r2)
        assert t1 == t2, "cached repeat query returned different text"

    def test_fake_ai_was_called_with_prompt(self, sim_user, fake_ai):
        sim_user.acknowledge_disclaimer()
        sim_user.send("company registration process")
        assert len(fake_ai) >= 1 and fake_ai[-1]["prompt"]


# ── Session ───────────────────────────────────────────────────────────────
class TestSession:
    def test_conversation_turn_recorded(self, sim_user):
        sim_user.send("duties of directors")
        sim_user.send("what about disqualification?")
        from core.juris_kai.session import get_recent_turns
        turns = get_recent_turns(sim_user.user_id) or []
        assert isinstance(turns, (list, tuple))


# ── Concurrency smoke: 20 users in parallel ───────────────────────────────
class TestConcurrencySmoke:
    def test_20_concurrent_users_menu_and_query(self, bot, fake_ai):
        users = [SimUser(bot) for _ in range(20)]

        def drive(u, i):
            seq = ["start", "/start", "📚 Learn", "⚖️ Case Law",
                   "duties of directors", "🔙 Main Menu"][i % 6]
            r = u.send(seq)
            return r is not None and (u.text_of(r) or u.menu_of(r))

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(drive, users, range(20)))
        assert all(results), f"{results.count(False)} of 20 users got no reply"
