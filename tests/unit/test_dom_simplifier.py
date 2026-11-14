from packages.inspector.dom_simplifier import simplify_dom, visible_text


def test_default_snapshots_are_bounded_for_llm_context():
    repeated = "<p>" + ("useful crawler text " * 4000) + "</p>"
    raw_html = f"<html><body>{repeated}</body></html>"

    assert len(visible_text(raw_html)) <= 6_000
    assert len(simplify_dom(raw_html)) <= 16_000
