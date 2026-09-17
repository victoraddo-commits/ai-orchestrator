from services.kai_directory.models import Record
from services.kai_directory.index import render_index


def test_render_lists_services_and_links():
    recs = [Record(id="money", name="money", display_name="Money Center",
                   category="finances", internal_url="https://money.tail82a9ca.ts.net/",
                   health_status="up")]
    html = render_index(recs)
    assert "Money Center" in html
    assert "https://money.tail82a9ca.ts.net/" in html
    assert "up" in html.lower()


def test_render_escapes_html():
    recs = [Record(id="x", name="x", display_name="<script>bad</script>")]
    html = render_index(recs)
    assert "<script>bad</script>" not in html
    assert "&lt;script&gt;" in html
