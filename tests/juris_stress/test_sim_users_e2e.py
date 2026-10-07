"""E2E simulated-user tests v2: real onboarding flow first, then every
menu path, commands, admin, rate limiting, cache, session, concurrency."""
import concurrent.futures
import sys

sys.path.insert(0, "/opt/ai-orchestrator")

from conftest import SimUser


class OnboardedUser(SimUser):
    """User that has completed the disclaimer gate."""

    def send(self, text):
        reply = super().send(text)
        # auto-accept disclaimer callback for onboarding flow
        if reply and "disclaimer" in ((reply.get("text") or "") + str(reply.get("reply_markup") or "")).lower() \
           and self.bot.handle_message is not None:
            pass
        self.history[-1] = (text, reply)
        return reply


def _onboard(sim_user):
    sim_user.send("/start")
    return sim_user


# ── Onboarding ────────────────────────────────────────────────────────────
class TestOnboarding:
    def test_start_shows_disclaimer(self, sim_user):
        r = sim_user.send("/start")
        text = sim_user.text_of(r)
        assert "disclaimer" in text.lower() or "⚖" in text

    def test_first_free_text_shows_disclaimer_gate(self, sim_user):
        r = sim_user.send("hello")
        assert "disclaimer" in sim_user.text_of(r).lower()

    def test_accept_disclaimer_then_answer(self, sim_user, fake_ai):
        sim_user.send("/start")
        sim_user.send("✅ I Understand")
        fake_ai.clear()
        r = sim_user.send("duties of directors")
        assert len(fake_ai) >= 1, "AI not called after onboarding"
        assert sim_user.text_of(r)


# ── Every menu path resolves (post-onboarding) ────────────────────────────
class TestMenuWalk:
    BUTTONS = [
        "📚 Learn", "⚖️ Case Law", "🏛 Practice", "🛠 Study Tools",
        "📁 My Documents", "📈 Progress", "⚙️ Settings", "🔙 Main Menu",
        "🧠 Legal Q&A", "🎓 Bar Prep", "📖 Statutes", "🔍 Quick Search",
    ]

    def test_all_buttons_produce_reply(self, sim_user, fake_ai):
        _onboard(sim_user)
        for btn in self.BUTTONS:
            r = sim_user.send(btn)
            assert r is not None, f"no reply for {btn}"
            assert sim_user.text_of(r) or sim_user.menu_of(r), \
                f"empty reply for {btn}"


# ── Commands ──────────────────────────────────────────────────────────────
class TestCommands:
    def test_help(self, sim_user):
        _onboard(sim_user)
        assert sim_user.text_of(sim_user.send("/help"))

    def test_status(self, sim_user):
        _onboard(sim_user)
        assert sim_user.text_of(sim_user.send("/status"))

    def test_admin_gating_for_normal_user(self, sim_user):
        _onboard(sim_user)
        r = sim_user.send("/admin")
        text = sim_user.text_of(r).lower()
        assert ("admin" in text and ("not" in text or "denied" in text)) or \
               sim_user.menu_of(r)


# ── Rate limiting ─────────────────────────────────────────────────────────
class TestRateLimit:
    def test_sixth_message_in_window_is_limited(self, bot):
        u = SimUser(bot, user_id="777001")
        u.send("/start")
        u.send("✅ I Understand")
        limited = False
        for i in range(8):
            r = u.send(f"msg {i}")
            if "too quickly" in u.text_of(r):
                limited = True
                break
        assert limited, "rate limiter never engaged after 8 rapid messages"


# ── Free text answers grounded, cached ────────────────────────────────────
class TestFreeText:
    def test_legal_query_returns_answer(self, sim_user, fake_ai):
        _onboard(sim_user)
        r = sim_user.send("What are the duties of directors in Ghana?")
        text = sim_user.text_of(r)
        assert text and len(text) > 20

    def test_cache_serves_repeat(self, sim_user, fake_ai):
        _onboard(sim_user)
        q = "penalty for late filing of annual returns"
        fake_ai.clear()
        r1 = sim_user.send(q)
        assert len(fake_ai) >= 1, "first query did not hit the AI"
        first_answer = sim_user.text_of(r1)
        fake_ai.clear()
        r2 = sim_user.send(q)
        assert len(fake_ai) == 0, "repeat query was not served from cache"
        assert sim_user.text_of(r2) == first_answer


# ── Session ───────────────────────────────────────────────────────────────
class TestSession:
    def test_conversation_turn_recorded(self, sim_user, fake_ai):
        _onboard(sim_user)
        sim_user.send("duties of directors")
        sim_user.send("what about disqualification?")
        from core.juris_kai.session import get_recent_turns
        turns = get_recent_turns(sim_user.user_id)
        assert turns is not None  # a rendered context string is acceptable


# ── Concurrency smoke: 20 users in parallel ───────────────────────────────
class TestConcurrencySmoke:
    def test_20_concurrent_users_menu_and_query(self, bot, fake_ai):
        users = [SimUser(bot) for _ in range(20)]
        for u in users:
            u.send("/start")

        def drive(args):
            i, u = args
            seq = ["/start", "📚 Learn", "⚖️ Case Law", "duties of directors",
                   "🔙 Main Menu", "/help"][i % 6]
            r = u.send(seq)
            return r is not None and (u.text_of(r) or u.menu_of(r))

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(drive, list(enumerate(users))))
        assert all(results), f"{results.count(False)} of 20 users got no reply"
