"""Drives a real headless Chromium against a local application form."""

from pathlib import Path

import pytest

from jobpilot.apply.answers import AnswerResolver, DraftAnswer
from jobpilot.apply.browser import _fill_one, extract_fields, fill_application
from jobpilot.chromium import launch

from .conftest import FakeLLM

FORM = (Path(__file__).parent / "fixtures" / "application_form.html").resolve().as_uri()
playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def files(tmp_path):
    resume = tmp_path / "Jane_Doe_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    cover = tmp_path / "Jane_Doe_Cover_Letter.pdf"
    cover.write_bytes(b"%PDF-1.4 test")
    return {"resume_pdf": resume, "cover_pdf": cover}


@pytest.fixture
def page():
    with playwright.sync_playwright() as p:
        browser = launch(p)
        pg = browser.new_page()
        pg.goto(FORM)
        yield pg
        browser.close()


def test_extract_fields_reads_labels(page):
    fields = {f.label: f for f in extract_fields(page)}
    assert fields["First Name *"].required and fields["First Name *"].kind == "text"
    assert fields["Resume/CV *"].kind == "file"
    assert fields["How did you hear about this job? *"].options == ["LinkedIn", "Company careers page", "Referral"]
    radio = fields["Will you now or in the future require visa sponsorship? *"]
    assert radio.kind == "radio" and radio.options == ["Yes", "No"] and radio.required
    assert fields["I have read the privacy notice *"].kind == "checkbox"
    assert not any("token" in label for label in fields)  # hidden inputs skipped
    assert fields["Are you open to relocation? *"].kind == "combobox"
    assert len([f for f in fields.values() if "relocation" in f.label]) == 1  # aria-hidden twin skipped
    assert fields["Cover Letter"].kind == "file" and fields["Cover Letter"].hint.startswith("cl_file")


def test_fill_values(page, profile, master, job, files):
    resolver = AnswerResolver(profile, master, job, files, "", None)
    for f in extract_fields(page):
        answer = resolver.resolve(f)
        if answer:
            _fill_one(page, f, answer)
    assert page.input_value("#first_name") == "Jane"
    assert page.input_value("#email") == "jane@example.com"
    assert page.locator("select[name=source]").evaluate("e => e.selectedOptions[0].text") == "Company careers page"
    assert page.is_checked("input[name=sponsorship][value='0']")  # requires_sponsorship: false -> "No"
    assert page.locator("#resume").evaluate("e => e.files[0].name") == "Jane_Doe_Resume.pdf"
    assert page.locator("#gender").evaluate("e => e.selectedOptions[0].text") == "I don't wish to answer"
    assert page.locator("#cl_file").evaluate("e => e.files[0].name") == "Jane_Doe_Cover_Letter.pdf"


def test_combobox_probe_and_select(page, profile, master, job, files):
    from jobpilot.apply.browser import _probe_combobox

    resolver = AnswerResolver(profile, master, job, files, "", None)
    f = next(f for f in extract_fields(page) if f.kind == "combobox")
    _probe_combobox(page, f)
    assert f.options == ["Yes", "No"]
    _fill_one(page, f, resolver.resolve(f))  # willing_to_relocate: true
    assert page.locator("#relocate_cb").evaluate("e => e.dataset.selected") == "Yes"


def test_auto_mode_falls_back_when_answer_was_drafted(profile, master, job, files, tmp_path):
    profile.answers["privacy"] = "Yes"
    llm = FakeLLM(DraftAnswer(answer="Because of your Redis-heavy stack...", confident=True))
    asked = []
    result = fill_application(FORM, AnswerResolver(profile, master, job, files, "", llm), tmp_path,
                              mode="auto", headless=True, confirm=lambda r: asked.append(r) or False, log=lambda m: None)
    assert asked, "human should be asked to review the drafted answer"
    assert not result.submitted and result.needs_review and not result.missing_required
    assert (tmp_path / "filled_form.png").exists() and (tmp_path / "fill_report.json").exists()


def test_auto_mode_submits_when_everything_is_from_profile(profile, master, job, files, tmp_path):
    profile.answers.update({"privacy": "Yes", "why do you want to work": "Your mission matches my experience."})
    result = fill_application(FORM, AnswerResolver(profile, master, job, files, "", None), tmp_path,
                              mode="auto", headless=True, confirm=lambda r: pytest.fail("should not ask"),
                              log=lambda m: None)
    assert result.submitted and "Thank you" in result.confirmation


def test_missing_required_is_reported(profile, master, job, files, tmp_path):
    # No answer bank entry for the privacy checkbox and no LLM for the essay question.
    result = fill_application(FORM, AnswerResolver(profile, master, job, files, "", None), tmp_path,
                              mode="assisted", headless=True, confirm=lambda r: False, log=lambda m: None)
    missing = {f.label for f in result.missing_required}
    assert missing == {"Why do you want to work at Globex? *", "I have read the privacy notice *"}
