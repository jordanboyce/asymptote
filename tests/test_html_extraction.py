"""HTML extraction tests — .html/.htm reports become heading-chunked sections."""

import pytest

from services.document_extractor import DocumentExtractor


@pytest.fixture()
def extractor():
    return DocumentExtractor(enable_ocr=False)


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


REPORT = """<!DOCTYPE html>
<html><head>
  <title>Quarterly Ops Report</title>
  <style>body { color: red; }</style>
  <script>alert("nope");</script>
</head><body>
  <h1>Summary</h1>
  <p>Overall throughput improved by 12%.</p>
  <h2>Incidents</h2>
  <p>Two outages, both resolved within an hour.</p>
  <table>
    <tr><th>System</th><th>Uptime</th></tr>
    <tr><td>Ingest</td><td>99.7%</td></tr>
    <tr><td>Search</td><td>99.9%</td></tr>
  </table>
</body></html>"""


def test_html_is_supported_extension(extractor):
    assert ".html" in extractor.SUPPORTED_EXTENSIONS
    assert ".htm" in extractor.SUPPORTED_EXTENSIONS


def test_sections_split_on_headings(extractor, tmp_path):
    result = extractor.extract_text(_write(tmp_path, "report.html", REPORT))
    pages = result.page_texts
    assert len(pages) == 2
    assert "Summary" in pages[1]
    assert "throughput improved" in pages[1]
    assert "Incidents" in pages[2]
    assert "resolved within an hour" in pages[2]


def test_title_prepended_to_first_section(extractor, tmp_path):
    result = extractor.extract_text(_write(tmp_path, "report.html", REPORT))
    assert result.page_texts[1].startswith("Quarterly Ops Report")


def test_script_and_style_stripped(extractor, tmp_path):
    result = extractor.extract_text(_write(tmp_path, "report.html", REPORT))
    joined = "\n".join(result.page_texts.values())
    assert "alert(" not in joined
    assert "color: red" not in joined


def test_tables_flattened_to_rows(extractor, tmp_path):
    result = extractor.extract_text(_write(tmp_path, "report.html", REPORT))
    joined = "\n".join(result.page_texts.values())
    assert "System | Uptime" in joined
    assert "Ingest | 99.7%" in joined
    assert "Search | 99.9%" in joined


def test_htm_extension_routes_same(extractor, tmp_path):
    result = extractor.extract_text(_write(tmp_path, "old.htm", REPORT))
    assert "throughput improved" in result.page_texts[1]


def test_no_headings_yields_single_section(extractor, tmp_path):
    html = "<html><head><title>Note</title></head><body><p>Just one paragraph.</p></body></html>"
    result = extractor.extract_text(_write(tmp_path, "note.html", html))
    assert list(result.page_texts) == [1]
    assert "Just one paragraph." in result.page_texts[1]
    assert result.page_texts[1].startswith("Note")


def test_malformed_html_still_extracts(extractor, tmp_path):
    html = "<html><body><h1>Broken<p>Unclosed tags everywhere<div><span>but text survives"
    result = extractor.extract_text(_write(tmp_path, "broken.html", html))
    joined = "\n".join(result.page_texts.values())
    assert "Unclosed tags everywhere" in joined
    assert "text survives" in joined


def test_empty_html_returns_blank_page(extractor, tmp_path):
    result = extractor.extract_text(_write(tmp_path, "empty.html", "<html><body></body></html>"))
    assert result.page_texts == {1: ""}


def test_fragment_without_body_tag(extractor, tmp_path):
    # Many old report generators emit fragments with no <html>/<body> wrapper.
    html = "<h1>Fragment</h1><p>Content without a document skeleton.</p>"
    result = extractor.extract_text(_write(tmp_path, "fragment.html", html))
    assert "Content without a document skeleton." in result.page_texts[1]
