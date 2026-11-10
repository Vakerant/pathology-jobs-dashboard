"""Scope tests: the tracker keeps ONLY MD Pathology Senior Resident / fellowship
notices (no network).

"high"   = SR / fellowship notice naming a pathology discipline
"medium" = SR / fellowship notice whose department is unnamed or "various"
           (the PDF decides; see test_document_discovery)
None     = everything else

Most cases below are real titles taken from the live dashboard (Nov 2026),
which showed 1800 rows of which only a handful were actually applicable.
"""
from datetime import date

import pytest

import scraper
import scope
from scraper import relevance


HIGH = [
    "Senior Resident Pathology vacancy at AIIMS",
    "Walk in interview for four posts of Senior Resident in the Dept of Histopathology",
    "Engagement of Senior Resident in the department of Pathology, Govt. Medical College Anantnag.",
    "Senior Resident in the Department of Onco- Pathology",
    "Fellowship in Histopathology / Cytopathology – TMC Kolkata",
    "Fellowship: Hematopathology & Molecular Hemato-Oncology – TMH",
    "Walk-in for Histopathology Senior Demonstrator",
    "Walk-in interview for Senior Resident Pathology",
    "Corrigendum: Pathology SR",
    "विकृति विज्ञान विभाग में सीनियर रेजिडेंट के पद हेतु वॉक-इन",
]

MEDIUM = [
    "Advertisement for recruitment to the post of Senior Residents in various Departments of AIIMS Nagpur",
    "Advertisement For Recruitment To The Post Of Senior Residents (Non-Academic) Purely On Temporary Basis",
    "Recruitment of Senior Residents / Senior Demonstrators at AIIMS Mangalagiri",
    "Recruitment of Senior Residents in Various Specialties - 22 November 2026",
    "Walk in interview for the Post of Senior Resident/ Junior Resident in Govt. Medical College Pithoragarh",
    "Senior Resident vacancy",
]

NONE = [
    # other jobs at a pathology department
    "Applications are invited for the post of Project Nurse by the Dept. of Clinical Hematology and Medical Oncology",
    "Advertisement Notice for Lab Technician on contract basis in the Department of Immunopathology",
    "Applications are invited for the post of Junior Research Fellow (JRF) on project basis in the Dept of Immunopathology",
    "Applications are invited for the Student Internship by the Dept. of Hematology.",
    "Assistant Professor (Histopathology)",
    "Recruitment of Assistant Professor Pathology",
    "Walk-in-Interview for the post of Assistant Professors in various Departments on contract basis",
    # SR posts in a different speciality
    "Walk-in Interview for the (02)posts of Non Academic Senior Resident on adhoc basis by the Dept. of Clinical Hematology",
    "Walk in interview for two posts of Senior Resident on adhoc in the Dept of Transfusion Medicine",
    "Blood Bank / Transfusion Medicine Senior Resident",
    "Senior Resident vacancy General Surgery",
    "Senior Resident Biochemistry",
    "Fellow - Surgical Oncology",
    "Fellow - Clinical Haematology & Cellular Therapies",
    "FELLOWSHIP Fellow - GI-HPB Surgery View View 30/11/2026 Download Apply",
    "Oral Pathology Senior Resident",
    # after the vacancy closed
    "Selection list for the tenure post of Senior Resident (Pathology) in Government Medical College Anantnag.",
    "Selection of Candidate for the Post of Senior Resident in the Discipline of Pathology.",
    "Regarding Senior Resident Selection Order 8.7.26",
    "Offer of Appointment to post of SR.",
    "List of Rejected Candidates for the post of Senior Resident(Regular) Sep-2026.",
    "Final Eligibility List of candidates for the post of Faculty (Group-A)",
    # navigation, admin and department pages
    "Admission Notice Regarding MD/MS/Diploma/DNB Courses",
    "Orientation Session for the Fresh Batch of Interns, AIIMS Guwahati - Click here",
    "Pathology Academic Contact Departmental Administration Faculty General Info Notice Board",
    "Pathology Faculty", "Details Of Faculty", "Eligibility Notice",
    "Senior Residents", "Fellowships", "Junior Residents and Senior Residents",
    "Advertisement (English) ( 450.16 KB) View",
    "RRs of JS, LT & LA",
    "Blood Bank", "Pathology Services", "About Pathology", "Transfusion Medicine Event",
    "GEM tender for slide scanner by Department of Pathology",
    "Limited Tender enquiry for consumables Dept of Pathology",
    "Home", "President notice pathology", "Radiology recruitment", "Microbiology vacancy",
    "Gosumec Alumni Association - Fellowship for travelling abroad",
    # leaks seen in the first end-to-end run (PGIMER / RML / Punjab / GCRI)
    "Applications are invited for the post of Senior Resident on adhoc basis by the Dept. of Oral Health Sciences",
    "Walk-in Interview for the two posts of Senior Residents on adhoc basis by the Dept. of Radiadiagnosis",
    "Walk-in interview for the post of Senior Residents (Adhoc) in the Dept. of Opthalmology.",
    "Vacancy Notice for the post of Senior Demonstrator on adhoc basis in the Department of Experimental Medicine",
    "Notice for walk in interview for the post of Senior Resident and Senior Demonstrator (Non-Medical)",
    "Walk in interview for the two posts of Sr. Medical Officer on ad-hoc basis at PGI Satellite Centre",
    "Applications are invited for two posts each of Junior Administrative Officer(Assistant) and Sr. Assistant",
    "walk-in Interview for the post of MedTech Fellow at ICMR-Centre for Innovation BioDesign",
    "Payment of Stipend / Salary to UG Interns, Post-Graduate Residents and Senior Residents",
    "( Addendum ) Two additional candidates are permitted to appear in the written examination for the post of SR",
    "Detail of Applications Received for the Post of Reader",
    "Home Recruitment Junior Residents and Senior Residents",
]


@pytest.mark.parametrize("text", HIGH)
def test_pathology_sr_and_fellowship_notices_are_high(text):
    assert relevance(text) == "high"


@pytest.mark.parametrize("text", MEDIUM)
def test_department_unconfirmed_sr_notices_are_medium(text):
    assert relevance(text) == "medium"


@pytest.mark.parametrize("text", NONE)
def test_everything_else_is_dropped(text):
    assert relevance(text) is None


def test_a_document_cannot_overrule_a_title_naming_another_department():
    body = "Fellowship programme. Rotations include Pathology and Radiology."
    assert scope.judge_document("Fellow - Surgical Oncology", body) is None
    assert scope.judge_document("Fellow - Uro-Oncosurgery", body) is None
    assert scope.judge_document("Courses (Medical & Paramedical)", body) is None
    assert scope.judge_document("Fellowship advertisement", body) == "high"


def test_href_can_carry_the_department():
    assert relevance("Advertisement for Senior Residents", "/docs/sr-pathology-2026.pdf") == "high"


def test_document_body_decides_a_multi_department_notice():
    title = "Advertisement for Senior Residents in various departments"
    assert scope.judge_document(title, "Vacancy table: Pathology 2, Medicine 3") == "high"
    assert scope.judge_document(title, "Vacancy table: Medicine 3, Surgery 2") is None
    # A bare document link: the post comes from the body.
    assert scope.judge_document("View Document",
                                "Walk-in for Senior Residents. Department of Pathology: 1 post") == "high"
    assert scope.judge_document("View Document", "Department of Pathology annual report") is None


@pytest.mark.parametrize("title,stale", [
    ("Walk in interview for SR to be held on 06.09.2019 at LBSH", True),
    ("Reg. Extension in joining time as SR (28.02.2017)", True),
    ("Recruitment of Senior Residents in Various Specialties - 22 November 2026", False),
    ("Advertisement for Senior Residents", False),          # no year: not judged
])
def test_archived_notices_are_stale(title, stale):
    assert scope.is_stale(title, date(2026, 11, 10)) is stale


def test_last_years_notice_still_counts_early_in_the_year():
    assert scope.is_stale("SR walk-in 15.12.2025", date(2026, 1, 20)) is False
    assert scope.is_stale("SR walk-in 15.12.2025", date(2026, 6, 1)) is True


def test_mojibake_is_detected():
    garbled = "जीबीटी वित्तपोषित".encode().decode("latin-1")
    assert scope.is_mojibake(garbled)
    assert not scope.is_mojibake("जीबीटी वित्तपोषित")
    assert not scope.is_mojibake("Café résumé")


def test_cleanup_stored_applies_the_current_rules(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    base = dict(source_id="s", source_name="S", region="R", category="C", snippet="")
    garbled = "जीबीटी वित्तपोषित परियोजना में पद हेतु".encode().decode("latin-1")
    db.upsert_listings([
        {**base, "title": "Blood Bank", "url": "https://x/blood-bank", "relevance": "high"},
        {**base, "title": "Walk-in interview Senior Resident Pathology", "url": "https://x/a", "relevance": "medium"},
        {**base, "title": "Advertisement for Senior Residents in various departments",
         "url": "https://x/b.pdf", "relevance": "high"},
        # Promoted by an earlier PDF read: the stored snippet is the body's evidence.
        {**base, "title": "Recruitment of Senior Residents (Non-Academic)", "url": "https://x/c.pdf",
         "relevance": "high", "snippet": "Department of Pathology – 2 posts (UR 1, OBC 1)"},
        {**base, "title": "Lab Technician, Dept of Pathology", "url": "https://x/d", "relevance": "high"},
        {**base, "title": garbled, "url": "https://x/e", "relevance": "high"},
        {**base, "title": "Walk in interview for SR on 06.09.2019", "url": "https://x/f", "relevance": "medium"},
        {**base, "title": "Senior Resident (Pathology) – placeholder", "url": "https://x/g",
         "relevance": "high", "is_seed": 1},
        {**base, "title": "Starred by the user: Blood Bank", "url": "https://x/h", "relevance": "high"},
    ])
    conn = db.get_conn()
    conn.execute("UPDATE listings SET starred=1 WHERE title LIKE 'Starred by the user%'")
    conn.commit()
    conn.close()
    today = date(2026, 11, 10)
    db.upsert_listings([{**base, "source_id": "gone", "title": "Walk-in interview Senior Resident Pathology (old source)",
                         "url": "https://x/z", "relevance": "high"}])
    deleted, changed = scraper.cleanup_stored(today, source_ids={"s"})
    got = {r["title"]: r["relevance"] for r in db.fetch_listings()}
    assert got == {
        "Walk-in interview Senior Resident Pathology": "high",
        "Advertisement for Senior Residents in various departments": "medium",
        "Recruitment of Senior Residents (Non-Academic)": "high",
        "Starred by the user: Blood Bank": "high",
    }
    assert (deleted, changed) == (6, 2)
    # Idempotent.
    assert scraper.cleanup_stored(today, source_ids={"s"}) == (0, 0)
