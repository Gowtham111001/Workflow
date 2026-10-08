from pathlib import Path

from jobpilot.apply.answers import AnswerResolver, DraftAnswer, FormField, match_option, queue_questions

from .conftest import FakeLLM

FILES = {"resume_pdf": Path("/tmp/r.pdf"), "cover_pdf": Path("/tmp/c.pdf")}


def resolver(profile, master, job, llm=None):
    return AnswerResolver(profile, master, job, FILES, "Dear team...", llm)


def test_profile_rules(profile, master, job):
    r = resolver(profile, master, job)
    assert r.resolve(FormField("k", "text", "First Name*", True)).value == "Jane"
    assert r.resolve(FormField("k", "email", "Email", True)).value == "jane@example.com"
    assert r.resolve(FormField("k", "url", "LinkedIn Profile", False)).value.endswith("janedoe")
    assert r.resolve(FormField("k", "file", "Resume/CV", True)).value == "/tmp/r.pdf"
    assert r.resolve(FormField("k", "file", "Cover Letter", False)).value == "/tmp/c.pdf"
    assert r.resolve(FormField("k", "textarea", "Cover letter", False)).value == "Dear team..."
    assert r.resolve(FormField("k", "text", "Total years of experience", False)).value == "4"


def test_specific_question_beats_generic_word(profile, master, job):
    r = resolver(profile, master, job)
    a = r.resolve(FormField("k", "select", "Are you willing to relocate to this location?", True, ["Yes", "No"]))
    assert a.value == "Yes"


def test_answer_bank_wins(profile, master, job):
    r = resolver(profile, master, job)
    a = r.resolve(FormField("k", "select", "How did you hear about us?", True,
                            ["LinkedIn", "Company careers page", "Referral"]))
    assert (a.value, a.source) == ("Company careers page", "bank")


def test_work_authorization_uses_job_country(profile, master, job):
    r = resolver(profile, master, job)
    q = FormField("k", "radio", "Are you legally authorized to work in the country this job is in?", True,
                  ["Yes", "No"])
    assert r.resolve(q).value == "Yes"  # job location says United States
    job.location = "London, UK"
    assert resolver(profile, master, job).resolve(q) is None  # never guessed


def test_sensitive_questions_never_go_to_llm(profile, master, job):
    llm = FakeLLM()  # would raise IndexError if called
    r = resolver(profile, master, job, llm)
    assert r.resolve(FormField("k", "checkbox", "I certify that the information is accurate", True)) is None
    assert r.resolve(FormField("k", "text", "Have you ever been convicted of a felony?", True)) is None
    assert r.resolve(FormField("k", "text", "Desired salary", True)) is None  # profile has none set
    assert not llm.calls


def test_demographics_default_to_decline(profile, master, job):
    r = resolver(profile, master, job)
    a = r.resolve(FormField("k", "select", "Gender", False, ["Male", "Female", "Non-binary", "I decline to self-identify"]))
    assert a.value == "I decline to self-identify"


def test_free_text_drafted_and_flagged(profile, master, job):
    llm = FakeLLM(DraftAnswer(answer="I built Redis caching at Acme...", confident=True))
    a = resolver(profile, master, job, llm).resolve(FormField("k", "textarea", "Why do you want to join Globex?", True))
    assert a.source == "llm" and a.needs_review


def test_unconfident_draft_is_missing(profile, master, job):
    llm = FakeLLM(DraftAnswer(answer="", confident=False))
    assert resolver(profile, master, job, llm).resolve(FormField("k", "text", "Your Kaggle rank?", True)) is None


def test_match_option():
    assert match_option("yes", ["Yes", "No"]) == "Yes"
    assert match_option("Yes", ["Yes, I am authorized", "No, I am not"]) == "Yes, I am authorized"
    assert match_option("Decline to self-identify", ["Male", "Female", "I don't wish to answer"]) == "I don't wish to answer"
    assert match_option("Banana", ["Yes", "No"]) is None


def test_queue_questions_dedupes(tmp_path, job):
    path = tmp_path / "pending.yaml"
    f = FormField("k", "text", "Expected graduation date?", True)
    queue_questions(path, job, [f])
    queue_questions(path, job, [f])
    text = path.read_text()
    assert text.count("Expected graduation date?") == 1 and text.count("Globex") == 1
