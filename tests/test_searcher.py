import pytest
from searcher import WebSearcher

def test_extract_content():
    searcher = WebSearcher()
    html = "<html><head><title>Test Title</title><script>var x = 1;</script></head><body><h1>Hello</h1><p>World</p></body></html>"
    cleaned = searcher._extract_content(html)
    assert "var x = 1" not in cleaned
    assert "Hello" in cleaned
    assert "World" in cleaned

def test_get_title():
    searcher = WebSearcher()
    html = "<html><head><title>Arch Linux Security Guide</title></head><body></body></html>"
    assert searcher._get_title(html) == "Arch Linux Security Guide"

def test_display_results_no_crash():
    searcher = WebSearcher()
    # Should format table without NameError on box
    searcher.display_results({
        "query": "arch linux",
        "sources": [{"title": "ArchWiki", "url": "https://wiki.archlinux.org"}],
        "summary": "Arch Linux is a rolling-release distribution."
    })
