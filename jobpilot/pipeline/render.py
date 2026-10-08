"""Render tailored resume + cover letter to HTML and PDF.

Identity, employers, titles and dates always come from the master resume;
only the wording/selection comes from the tailored version. The template is
single-column with real text (no tables, icons or images) so ATS parsers read
it cleanly.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from ..models import MasterResume, TailoredResume

env = Environment(loader=PackageLoader("jobpilot", "templates"), autoescape=select_autoescape(["j2", "html"]))


def resume_view(master: MasterResume, tailored: TailoredResume) -> dict:
    by_id = {s.source_id: s for s in (*tailored.experience, *tailored.projects)}
    experience = [
        {"company": e.company, "title": e.title, "location": e.location, "start": e.start, "end": e.end,
         "bullets": [b.text for b in by_id[e.id].bullets]}
        for e in master.experience if e.id in by_id  # master order = reverse-chronological
    ]
    projects_by_id = {p.id: p for p in master.projects}
    projects = [
        {"name": projects_by_id[s.source_id].name, "link": projects_by_id[s.source_id].link,
         "bullets": [b.text for b in s.bullets]}
        for s in tailored.projects if s.source_id in projects_by_id
    ]
    return {
        "basics": master.basics,
        "headline": tailored.headline,
        "summary": tailored.summary,
        "experience": experience,
        "projects": projects,
        "education": master.education,
        "skills": tailored.skills,
        "certifications": master.certifications,
    }


def render_html(master: MasterResume, tailored: TailoredResume, company: str) -> tuple[str, str]:
    resume_html = env.get_template("resume.html.j2").render(**resume_view(master, tailored))
    letter_html = env.get_template("cover_letter.html.j2").render(
        basics=master.basics, company=company, today=f"{date.today():%B} {date.today().day}, {date.today().year}",
        paragraphs=[p.strip() for p in tailored.cover_letter.split("\n\n") if p.strip()],
    )
    return resume_html, letter_html


def html_to_pdf(pages: list[tuple[str, Path]], paper: str = "Letter") -> None:
    from playwright.sync_api import sync_playwright

    from ..chromium import launch

    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page()
        for html, out in pages:
            page.set_content(html, wait_until="load")
            page.pdf(path=str(out), format=paper, print_background=True,
                     margin={"top": "0.5in", "bottom": "0.5in", "left": "0.6in", "right": "0.6in"})
        browser.close()


def render(master: MasterResume, tailored: TailoredResume, company: str, out_dir: Path, paper: str = "Letter",
           file_stem: str | None = None) -> dict[str, Path]:
    stem = file_stem or master.basics.name.replace(" ", "_")
    resume_html, letter_html = render_html(master, tailored, company)
    paths = {
        "resume_html": out_dir / "resume.html",
        "cover_html": out_dir / "cover_letter.html",
        # Recruiters see the uploaded filename; make it yours, not "resume (3).pdf".
        "resume_pdf": out_dir / f"{stem}_Resume.pdf",
        "cover_pdf": out_dir / f"{stem}_Cover_Letter.pdf",
    }
    paths["resume_html"].write_text(resume_html)
    paths["cover_html"].write_text(letter_html)
    html_to_pdf([(resume_html, paths["resume_pdf"]), (letter_html, paths["cover_pdf"])], paper)
    return paths
