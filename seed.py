"""
Seed the database with concrete, verified pathology opportunities found during
research (June 2026). These give the dashboard immediate value before the first
live scrape. They are tagged is_seed=1 and carry a helpful snippet.

Re-running is safe: upsert is idempotent on (source_id, url, title).
"""
import db

SEEDS = [
    # ---- Chandigarh ----
    {"source_id": "gmch", "source_name": "GMCH-32 Chandigarh", "region": "Chandigarh",
     "category": "Govt – Senior Resident", "relevance": "high",
     "title": "Walk-in: Senior Resident / Resident Pathologist (Pathology) – ~₹67,700 + 20% NPA",
     "url": "https://gmch.gov.in/job-opportunities",
     "snippet": "GMCH-32 holds frequent walk-in interviews for SR & Resident Pathologist. Level-11 (7th CPC) + NPA. Check site for the next dated notice."},
    {"source_id": "pgimer", "source_name": "PGIMER Chandigarh", "region": "Chandigarh / INI",
     "category": "INI – Senior Resident", "relevance": "high",
     "title": "Senior Resident (Pathology / Immunopathology) – Walk-in Interview",
     "url": "https://pgimer.edu.in/PGIMER_PORTAL/PGIMERPORTAL/Vacancies/JSP/VIEW_CALL.jsp?search=0&countt=0&aflag=1",
     "snippet": "PGIMER recruits SR Pathology via walk-in. MD Pathology required; some calls want Immunopathology experience."},

    # ---- Delhi ----
    {"source_id": "icmr_nip", "source_name": "ICMR National Institute of Pathology", "region": "Delhi / INI",
     "category": "Research – Senior Resident", "relevance": "high",
     "title": "Senior Resident (Pathology) – ICMR-NIP, Safdarjung campus",
     "url": "https://www.icmr.gov.in/whats-new",
     "snippet": "ICMR-NIP invites SR (Pathology) with MD/DNB/DCP completed within last 3 yrs. Located on Safdarjung Hospital campus, New Delhi."},
    {"source_id": "safdarjung", "source_name": "VMMC & Safdarjung Hospital", "region": "Delhi",
     "category": "Govt – Senior Resident", "relevance": "high",
     "title": "Senior Resident (incl. Pathology) – VMMC & Safdarjung",
     "url": "https://vmmc-sjh.mohfw.gov.in/recruitment",
     "snippet": "Large SR intake across departments including Pathology. Watch the Recruitment page for dated notices/walk-ins."},
    {"source_id": "aiims_delhi", "source_name": "AIIMS New Delhi", "region": "Delhi / INI",
     "category": "INI – Senior Resident", "relevance": "high",
     "title": "Senior Resident / Senior Demonstrator (Pathology) – AIIMS New Delhi",
     "url": "https://www.aiimsexams.ac.in/",
     "snippet": "AIIMS holds an SR recruitment exam (Residency Scheme, ad-hoc tenure). Pathology among specialties. Apply via aiimsexams portal."},

    # ---- Himachal ----
    {"source_id": "igmc_shimla", "source_name": "IGMC Shimla", "region": "Himachal Pradesh",
     "category": "Govt – Senior Resident", "relevance": "high",
     "title": "Senior Resident / Tutor Specialist (Pathology) – IGMC Shimla",
     "url": "http://www.igmcshimla.edu.in/recruitments.jsp",
     "snippet": "Direct recruitment of SR/Tutor Specialists. Non-clinical (Pathology) per HP Medical Education Service rules. Bond/field-posting rules may apply."},

    # ---- Punjab ----
    {"source_id": "bfuhs", "source_name": "Baba Farid University (BFUHS)", "region": "Punjab",
     "category": "Govt – Senior Resident", "relevance": "high",
     "title": "Senior Resident (Pathology) – BFUHS / GGS Medical College Faridkot",
     "url": "https://bfuhs.ggsmch.org/university-recruitment/",
     "snippet": "BFUHS & affiliated Punjab govt colleges recruit SR (MD Pathology) via direct interview. Watch University Recruitment page."},
    {"source_id": "gmc_patiala", "source_name": "Govt Medical College Patiala", "region": "Punjab",
     "category": "Govt – Senior Resident", "relevance": "medium",
     "title": "Senior Resident recruitment – GMC Patiala",
     "url": "https://gmcpatiala.edu.in/recruitments/",
     "snippet": "Periodic SR recruitment across departments. Check for Pathology in the latest advertisement."},

    # ---- Haryana ----
    {"source_id": "kcgmc", "source_name": "Kalpana Chawla GMC, Karnal", "region": "Haryana",
     "category": "Govt – Senior Resident", "relevance": "high",
     "title": "Senior Resident (Pathology) – KCGMC Karnal",
     "url": "https://www.kcgmc.edu.in/Tender/Advertisement",
     "snippet": "KCGMC (affiliated PGIMS Rohtak) recruits SR/Tutor-Demonstrator incl. Pathology. MD/MS/DNB; diploma/3-yr PG experience accepted if MD unavailable."},
    {"source_id": "uhsr_rohtak", "source_name": "PGIMS / UHS Rohtak", "region": "Haryana",
     "category": "Govt – Senior Resident", "relevance": "high",
     "title": "Senior Resident (Pathology) – PGIMS Rohtak / UHS",
     "url": "https://uhsr.ac.in/",
     "snippet": "Haryana faculty + SR recruitment runs through UHS Rohtak. Pathology posts appear in the omnibus medical-college notifications."},

    # ---- INIs ----
    {"source_id": "aiims_bhopal", "source_name": "AIIMS Bhopal", "region": "INI – Other",
     "category": "INI – Senior Resident", "relevance": "high",
     "title": "Senior Resident (Pathology) – AIIMS Bhopal (online application cycles)",
     "url": "https://www.aiimsbhopal.edu.in/index1.php?lang=1&level=1&sublinkid=222&lid=259",
     "snippet": "AIIMS Bhopal runs large SR online-application drives (e.g. 128 posts). Pathology among departments. MD/MS/DNB."},
    {"source_id": "jipmer", "source_name": "JIPMER Puducherry", "region": "INI – Other",
     "category": "INI – Senior Resident", "relevance": "high",
     "title": "Senior Resident (Pathology) – JIPMER",
     "url": "https://jipmer.edu.in/recruitment",
     "snippet": "JIPMER recruits SR across specialties incl. Pathology. INI; competitive."},
    {"source_id": "nimhans", "source_name": "NIMHANS Bengaluru", "region": "INI – Other",
     "category": "INI – Senior Resident / Neuropath", "relevance": "high",
     "title": "Senior Resident – NIMHANS (Neuropathology / Lab Medicine)",
     "url": "https://nimhans.ac.in/career/",
     "snippet": "NIMHANS recruits SR including Neuropathology & Lab Medicine. Strong subspecialty exposure."},

    # ---- Metros ----
    {"source_id": "tmc_mumbai", "source_name": "Tata Memorial Centre, Mumbai", "region": "Mumbai",
     "category": "Cancer Centre – SR / Fellowship", "relevance": "high",
     "title": "Senior Resident (Pathology) – TMC Mumbai (~₹1.01–1.10 L/month)",
     "url": "https://tmc.gov.in/m_events/events/jobvacancies",
     "snippet": "TMC recruits SR Pathology; pay ~₹1.01–1.10 L/month. MD Pathology. Onco-pathology exposure. Watch job-vacancies page."},
    {"source_id": "kem_mumbai", "source_name": "KEM Hospital / GSMC Mumbai", "region": "Mumbai",
     "category": "Govt – Senior Resident", "relevance": "high",
     "title": "Senior Resident / Resident Pathologist – KEM-GSMC Mumbai",
     "url": "https://www.kem.edu/recruitment/",
     "snippet": "KEM (GSMC) Dept of Pathology recruits SR / Resident Pathologist. High-volume teaching hospital."},
    {"source_id": "bjmc_ahmedabad", "source_name": "B.J. Medical College, Ahmedabad", "region": "Ahmedabad / Gujarat",
     "category": "Govt – Senior Resident", "relevance": "high",
     "title": "Senior Resident (Pathology) – B.J. Medical College, Ahmedabad",
     "url": "https://bjmcabd.edu.in/",
     "snippet": "Large PG institution; SR Pathology posts via Gujarat govt / college notices."},
    {"source_id": "gcri_ahmedabad", "source_name": "Gujarat Cancer Research Institute", "region": "Ahmedabad / Gujarat",
     "category": "Cancer Centre – SR / Onco-path", "relevance": "high",
     "title": "Senior Resident / Onco-Pathology – GCRI Ahmedabad",
     "url": "https://gcriindia.org/recruitment/",
     "snippet": "GCRI recruits SR & onco-pathology roles. Cancer-focused histopathology exposure."},

    # ---- Fellowships (paid post-MD) ----
    {"source_id": "tmc_fellowship", "source_name": "Tata Memorial Hospital", "region": "Fellowship",
     "category": "Fellowship – Paid", "relevance": "high",
     "title": "Fellowship: Hematopathology & Molecular Hemato-Oncology – TMH (paid, ~₹1.21 L gross)",
     "url": "https://tmc.gov.in/m_events/events/jobvacancies",
     "snippet": "1-yr TMH fellowship in Hematopathology + Molecular Hemato-Oncology, then peripheral posting. Gross pay ~₹1,21,200/month. Also Surgical Path & Cytopathology fellowships."},
    {"source_id": "actrec", "source_name": "ACTREC – Tata Memorial", "region": "Fellowship",
     "category": "Fellowship – Paid", "relevance": "high",
     "title": "Fellowship / SR – Hematopathology & Pathology, ACTREC (Navi Mumbai)",
     "url": "https://actrec.gov.in/careers",
     "snippet": "ACTREC offers paid SR-fellowship posts in Hematopathology & Pathology (molecular onco). Research-heavy."},
    {"source_id": "tmc_kolkata", "source_name": "Tata Medical Center, Kolkata", "region": "Fellowship",
     "category": "Fellowship – Paid", "relevance": "high",
     "title": "Fellowship in Histopathology / Cytopathology – TMC Kolkata",
     "url": "https://tmckolkata.com/in/careers/",
     "snippet": "Post-MD fellowships in Histopathology & Cytopathology at a dedicated cancer centre."},
    {"source_id": "natboard", "source_name": "National Board of Examinations", "region": "Fellowship",
     "category": "Fellowship – DrNB/FNB", "relevance": "medium",
     "title": "DrNB / FNB super-specialty pathology fellowships (NBE accredited)",
     "url": "https://natboard.edu.in/",
     "snippet": "NBE accredits DrNB/FNB fellowships (e.g. Hemato-pathology, Molecular). Stipend paid by host hospital. Apply via NBE counselling cycles."},

    # ---- Private chains ----
    {"source_id": "lalpathlabs", "source_name": "Dr Lal PathLabs", "region": "Private / Metro",
     "category": "Private – Consultant Pathologist", "relevance": "high",
     "title": "Consultant Pathologist – Dr Lal PathLabs (multiple cities)",
     "url": "https://www.lalpathlabs.com/career/job-opening/consultant-pathologist",
     "snippet": "Consultant Pathologist roles across Delhi-NCR and other metros. Typical consultant pay band ₹15–35 LPA in the diagnostics sector."},
    {"source_id": "metropolis", "source_name": "Metropolis Healthcare", "region": "Private / Metro",
     "category": "Private – Consultant Pathologist", "relevance": "high",
     "title": "Consultant Pathologist – Metropolis Healthcare (Mumbai & pan-India)",
     "url": "https://www.metropolisindia.com/careers",
     "snippet": "Consultant Pathologist openings (Mumbai HQ + regional labs). MD Pathology, 0–5 yrs. Also reporting/region-pathologist roles."},
    {"source_id": "agilus", "source_name": "Agilus Diagnostics (SRL)", "region": "Private / Metro",
     "category": "Private – Consultant Pathologist", "relevance": "high",
     "title": "Consultant / Reporting Pathologist – Agilus (SRL) Diagnostics",
     "url": "https://www.agilus.in/careers",
     "snippet": "Pan-India diagnostic chain; consultant & reporting pathologist roles, histopath/cytopath."},
]


def run():
    db.init_db()
    for s in SEEDS:
        s = dict(s)
        s["is_seed"] = 1
        db.upsert_listing(s)
    print(f"Seeded {len(SEEDS)} verified opportunities.")


if __name__ == "__main__":
    run()
