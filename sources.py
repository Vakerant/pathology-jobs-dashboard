"""
Source registry for the Pathology Jobs & Senior-Resident Exam tracker.

Each source is a public recruitment / careers page that is polled daily.
The scraper is generic: it fetches the page, scans every link + heading, and
keeps anything that looks like a pathology / senior-resident / fellowship notice.

`region` groups cards in the dashboard. `category` is a coarse tag.
Add or remove dictionaries freely -- the scraper adapts automatically.
"""

# Words that make an item RELEVANT (medium relevance) -- recruitment-ish notices.
RECRUIT_KEYWORDS = [
    "senior resident", "sr ", "resident", "recruit", "vacancy", "vacancies",
    "walk-in", "walk in", "advertisement", "advt", "appointment", "engagement",
    "fellowship", "demonstrator", "tutor", "faculty", "notice", "career",
    "job", "opening", "post ", "posts", "hiring", "consultant",
]

# Words that make an item HIGH relevance -- clearly PATHOLOGY (not other specialities).
# NOTE: microbiology / clinical oncology / general SR are deliberately excluded.
PATHOLOGY_KEYWORDS = [
    "patholog",        # pathology, pathologist, histopathology, cytopathology, neuropathology
    "histopath", "cytopath", "haematopath", "hematopath",
    "haematolog", "hematolog",          # lab/hemato-pathology
    "cytolog", "histolog", "immunohisto",
    "molecular patho", "surgical patho", "onco patho", "oncopath",
    "clinical pathology", "lab medicine", "laboratory medicine",
    "transfusion", "blood bank", "fnac",
]

# Junk anchor text -> the scraper uses the row context as the title instead,
# and any listing still titled like this is dropped (it's a bare button).
JUNK_TITLES = {
    "view details", "view detail", "click here", "read more", "download", "apply",
    "apply now", "details", "more", "view", "pdf", "click", "here", "link",
    "notification", "read", "open", "advertisement", "advt", "notice", "new",
    "corrigendum", "result", "results",
    # Admin / navigation pages that are not job notices
    "non-faculty", "faculty", "fellowship", "recruitment rules",
    "presidency", "president",
    # Short category label links (bare navigation)
    "non-faculty group a", "non-faculty group b", "non-faculty group c",
    "group a", "group b", "group c",
}

# Junk link text we never want (nav / social / boilerplate).
STOPWORDS = [
    "home", "about us", "contact", "sitemap", "login", "facebook", "twitter",
    "instagram", "youtube", "privacy", "disclaimer", "feedback", "screen reader",
    "skip to", "rti", "tender", "gallery", "photo", "search", "menu",
]

SOURCES = [
    # ---------------- DELHI ----------------
    {"id": "safdarjung", "name": "VMMC & Safdarjung Hospital", "region": "Delhi",
     "category": "Govt – Senior Resident", "url": "https://vmmc-sjh.mohfw.gov.in/recruitment"},
    {"id": "aiims_delhi", "name": "AIIMS New Delhi (Recruitment)", "region": "Delhi / INI",
     "category": "INI – Senior Resident", "url": "https://www.aiims.edu/index.php/en/notices/recruitment/aiims-recruitment"},
    {"id": "aiims_exams", "name": "AIIMS Exams Portal", "region": "Delhi / INI",
     "category": "INI – Senior Resident", "url": "https://www.aiimsexams.ac.in/"},
    {"id": "rml", "name": "RML Hospital, Delhi", "region": "Delhi",
     "category": "Govt – Senior Resident", "url": "https://rmlh.nic.in/index1.aspx?lsid=201&lev=2&lid=181&langid=1"},
    {"id": "delhi_health", "name": "Delhi Health & Family Welfare", "region": "Delhi",
     "category": "Govt – Vacancies", "url": "https://health.delhi.gov.in/vacancy"},
    {"id": "icmr_nip", "name": "ICMR National Institute of Pathology", "region": "Delhi / INI",
     "category": "Research – Senior Resident", "url": "https://www.icmr.gov.in/whats-new"},

    # ---------------- CHANDIGARH ----------------
    {"id": "pgimer", "name": "PGIMER Chandigarh (Vacancies)", "region": "Chandigarh / INI",
     "category": "INI – Senior Resident", "url": "https://pgimer.edu.in/PGIMER_PORTAL/PGIMERPORTAL/Vacancies/JSP/VIEW_CALL.jsp?search=0&countt=0&aflag=1"},
    {"id": "gmch", "name": "GMCH-32 Chandigarh (Jobs)", "region": "Chandigarh",
     "category": "Govt – Senior Resident", "url": "https://gmch.gov.in/"},

    # ---------------- HIMACHAL PRADESH ----------------
    {"id": "igmc_shimla", "name": "IGMC Shimla", "region": "Himachal Pradesh",
     "category": "Govt – Senior Resident", "url": "http://www.igmcshimla.edu.in/recruitments.jsp"},
    {"id": "aimss_chamiana", "name": "AIMSS Chamiana, Shimla", "region": "Himachal Pradesh",
     "category": "Govt – Senior Resident", "url": "https://aimsschamiana.edu.in/recruitments.php"},

    # ---------------- PUNJAB ----------------
    {"id": "bfuhs", "name": "Baba Farid University (BFUHS)", "region": "Punjab",
     "category": "Govt – Senior Resident", "url": "https://bfuhs.ggsmch.org/university-recruitment/"},
    {"id": "gmc_patiala", "name": "Govt Medical College Patiala", "region": "Punjab",
     "category": "Govt – Senior Resident", "url": "https://gmcpatiala.edu.in/recruitments/"},
    {"id": "punjab_med_edu", "name": "Punjab Medical Education & Research", "region": "Punjab",
     "category": "Govt – Recruitment", "url": "https://www.punjabmedicaleducation.org/rect.html"},

    # ---------------- HARYANA ----------------
    {"id": "kcgmc", "name": "Kalpana Chawla GMC, Karnal", "region": "Haryana",
     "category": "Govt – Senior Resident", "url": "https://www.kcgmc.edu.in/Tender/Advertisement"},
    {"id": "haryana_health", "name": "Haryana Health Department", "region": "Haryana",
     "category": "Govt – Recruitment", "url": "https://haryanahealth.gov.in/notice-category/recruitments/"},
    {"id": "uhsr_rohtak", "name": "PGIMS / UHS Rohtak", "region": "Haryana",
     "category": "Govt – Senior Resident", "url": "https://uhsr.ac.in/detailsleft.aspx?artid=27"},

    # ---------------- INSTITUTES OF NATIONAL IMPORTANCE ----------------
    {"id": "aiims_bhopal", "name": "AIIMS Bhopal", "region": "INI – Other",
     "category": "INI – Senior Resident", "url": "https://www.aiimsbhopal.edu.in/"},
     {"id": "aiims_jodhpur", "name": "AIIMS Jodhpur", "region": "INI – Other",
      "category": "INI – Senior Resident", "url": "https://aiimsjodhpur.edu.in/"},
    {"id": "aiims_rishikesh", "name": "AIIMS Rishikesh", "region": "INI – Other",
     "category": "INI – Senior Resident", "url": "https://aiimsrishikesh.edu.in/aiims/en/recruitment.html"},
    {"id": "aiims_bbsr", "name": "AIIMS Bhubaneswar", "region": "INI – Other",
     "category": "INI – Senior Resident", "url": "https://aiimsbhubaneswar.nic.in/recruitment-notice/"},
    {"id": "aiims_bilaspur", "name": "AIIMS Bilaspur (HP)", "region": "INI – Other",
     "category": "INI – Senior Resident", "url": "https://www.aiimsbilaspur.edu.in/recruitment"},
    {"id": "jipmer", "name": "JIPMER Puducherry", "region": "INI – Other",
     "category": "INI – Senior Resident", "url": "https://jipmer.edu.in/"},
    {"id": "nimhans", "name": "NIMHANS Bengaluru", "region": "INI – Other",
     "category": "INI – Senior Resident / Neuropath", "url": "https://nimhans.ac.in/"},

    # ---------------- TOP METROS / STATE COLLEGES ----------------
    {"id": "kem_mumbai", "name": "KEM Hospital / GSMC Mumbai", "region": "Mumbai",
     "category": "Govt – Senior Resident", "url": "https://www.kem.edu/"},
    {"id": "tmc_mumbai", "name": "Tata Memorial Centre (Jobs)", "region": "Mumbai",
     "category": "Cancer Centre – SR / Fellowship", "url": "https://tmc.gov.in/m_events/events/jobvacancies"},
    {"id": "bjmc_ahmedabad", "name": "B.J. Medical College, Ahmedabad", "region": "Ahmedabad / Gujarat",
     "category": "Govt – Senior Resident", "url": "https://bjmcabd.edu.in/"},
    {"id": "gcri_ahmedabad", "name": "Gujarat Cancer Research Institute", "region": "Ahmedabad / Gujarat",
     "category": "Cancer Centre – SR / Onco-path", "url": "https://gcriindia.org/"},

    # ---------------- FELLOWSHIPS (paid post-MD training) ----------------
    {"id": "actrec", "name": "ACTREC – Tata (Hemato/Path Fellowship)", "region": "Fellowship",
     "category": "Fellowship – Paid", "url": "https://actrec.gov.in/"},
    {"id": "tmc_fellowship", "name": "Tata Memorial Fellowships", "region": "Fellowship",
     "category": "Fellowship – Paid", "url": "https://tmc.gov.in/m_events/events/jobvacancies"},
    {"id": "tmc_kolkata", "name": "Tata Medical Center, Kolkata", "region": "Fellowship",
     "category": "Fellowship – Paid", "url": "https://tmckolkata.com/"},
    {"id": "natboard", "name": "National Board (DrNB / FNB)", "region": "Fellowship",
     "category": "Fellowship – DrNB/FNB", "url": "https://natboard.edu.in/"},

    # ---------------- PRIVATE DIAGNOSTIC CHAINS (Consultant Pathologist) ----------------
    {"id": "lalpathlabs", "name": "Dr Lal PathLabs – Careers", "region": "Private / Metro",
     "category": "Private – Consultant Pathologist", "url": "https://www.lalpathlabs.com/career/job-opening-list"},
    {"id": "metropolis", "name": "Metropolis Healthcare – Careers", "region": "Private / Metro",
     "category": "Private – Consultant Pathologist", "url": "https://www.metropolisindia.com/careers"},
    {"id": "agilus", "name": "Agilus Diagnostics (SRL) – Careers", "region": "Private / Metro",
     "category": "Private – Consultant Pathologist", "url": "https://www.agilusdiagnostics.com/agilus-careers"},
]
