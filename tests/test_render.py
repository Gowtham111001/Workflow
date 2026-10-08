import pytest
from pypdf import PdfReader

from jobpilot.pipeline import render

from .test_grounding import tailored

pytest.importorskip("playwright.sync_api")


def test_render_uses_master_identity_and_dates(master, tmp_path):
    t = tailored(headline="Backend Engineer, Distributed Systems")
    paths = render.render(master, t, "Globex", tmp_path, file_stem="Jane_Doe")
    html = paths["resume_html"].read_text()
    assert "Jane Doe" in html and "2022-01" in html and "Distributed Systems" in html
    # Master order (Acme, then Initech) regardless of tailored order
    assert html.index("Acme") < html.index("Initech")
    text = "".join(p.extract_text() for p in PdfReader(paths["resume_pdf"]).pages)
    assert "Jane Doe" in text and "Redis" in text
    assert paths["cover_pdf"].name == "Jane_Doe_Cover_Letter.pdf"
