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

# STRICT SCOPE GUARD -- this tracker is for MD (Human) Pathology ONLY.
# These strings are pathology-ADJACENT but belong to a different degree or
# discipline. They must be tested BEFORE PATHOLOGY_KEYWORDS because
# "oral pathologist" and "veterinary pathologist" both contain the substring
# "patholog", so a positive-only test would happily keep them.
OFF_SCOPE_PATHS = [
    # --- Dental / oral (MDS; Oral Medicine & Radiology / Oral & Maxillofacial Surgery) ---
    "oral patho", "oral patholog", "oral medicin", "oral surg",
    "oral & maxillofacial", "oral and maxillofacial", "maxillofacial",
    "dental", "dentist", "periodont", "endodont", "orthodont",
    "prosthodont", "oral cytolog",
    # --- Non-human / non-medical pathology ---
    "veterinar", "animal patholog", "plant patholog", "comparative patho",
    "fish patholog", "poultry patholog",
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
    # Uninformative document stubs. Many govt sites render every document link
    # with identical boilerplate text ("View Document"), which is why the real
    # title has to come from the document body — see scraper._pdf_subject().
    "view document", "open document", "view doc", "open pdf", "document",
    "documents", "download pdf", "click to view", "click here to view",
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
    {"id": "delhi_health_vacancy", "name": "Delhi Health & Family Welfare (Vacancy Page)", "region": "Delhi",
     "category": "Govt – Vacancies", "url": "https://health.delhi.gov.in/health/vacancy"},

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
     "category": "Govt – Senior Resident", "url": "https://uhsr.ac.in/recruitment-advertisement"},

    # ---------------- INSTITUTES OF NATIONAL IMPORTANCE ----------------
    {"id": "aiims_bhopal", "name": "AIIMS Bhopal", "region": "INI – Other",
     "category": "INI – Senior Resident", "url": "https://www.aiimsbhopal.edu.in/"},
     {"id": "aiims_jodhpur", "name": "AIIMS Jodhpur", "region": "INI – Other",
      "category": "INI – Senior Resident", "url": "https://aiimsjodhpur.edu.in/"},
    {"id": "aiims_rishikesh", "name": "AIIMS Rishikesh", "region": "INI – Other",
     "category": "INI – Senior Resident", "url": "https://aiimsrishikesh.edu.in/job-new.php"},
    {"id": "aiims_raipur", "name": "AIIMS Raipur (Chhattisgarh)", "region": "INI – Other",
     "category": "INI – Senior Resident", "url": "https://aiimsraipur.edu.in/"},
    {"id": "aiims_nagpur", "name": "AIIMS Nagpur", "region": "INI – Other",
     "category": "INI – Senior Resident", "url": "https://aiimsnagpur.edu.in/recruitment"},
    {"id": "aiims_guwahati", "name": "AIIMS Guwahati", "region": "INI – Other",
     "category": "INI – Senior Resident", "url": "https://aiimsguwahati.ac.in/"},
    {"id": "aiims_raebareli", "name": "AIIMS Raebareli", "region": "INI – Other",
     "category": "INI – Senior Resident", "url": "https://aiimsrbl.edu.in/"},
    {"id": "aiims_deoghar", "name": "AIIMS Deoghar (Jharkhand)", "region": "INI – Other",
     "category": "INI – Senior Resident", "url": "https://www.aiimsdeoghar.edu.in/notice/list"},
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
    {"id": "nib_genomics", "name": "NIB – National Institute of Biomedical Genomics", "region": "Fellowship",
     "category": "Fellowship – Paid", "url": "https://www.nib.gov.in/"},
    # www.icmr.gov.in serves an incomplete TLS cert chain. It is listed in
    # config.INSECURE_HOSTS so the scraper uses verify=False -- still HTTPS, just
    # not chain-verified. Verified: https + verify=False -> 200, http -> 200.
    {"id": "icmr", "name": "ICMR – Indian Council of Medical Research", "region": "Fellowship",
     "category": "Fellowship – JRF / Research", "url": "https://www.icmr.gov.in/"},

    # ---------------- OTHER STATES: top govt medical colleges ----------------
    # Recruitment for a state's govt colleges is usually consolidated on the
    # state's Directorate of Medical Education / Health Department portal rather
    # than on each college's own site, so one portal per state is used here.
    {"id": "uk_medical_education", "name": "Uttarakhand Dept of Medical Education (All GMC SR/JR)",
     "region": "Uttarakhand", "category": "Govt – Senior Resident",
     "url": "https://medicaleducation.uk.gov.in/"},
    {"id": "uk_health", "name": "Uttarakhand Dept of Medical Health & Family Welfare",
     "region": "Uttarakhand", "category": "Govt – Vacancies", "url": "https://health.uk.gov.in/"},
    {"id": "punjab_health", "name": "Punjab Dept of Health & Family Welfare",
     "region": "Punjab", "category": "Govt – Vacancies", "url": "https://health.punjab.gov.in/"},
    {"id": "gmc_anantnag", "name": "GMC Anantnag (J&K)", "region": "Jammu & Kashmir",
     "category": "Govt – Senior Resident", "url": "https://gmcanantnag.ac.in/"},
    {"id": "sgpgims_lucknow", "name": "SGPGI Lucknow (SR / JR / Fellowship)",
     "region": "Uttar Pradesh", "category": "Govt – Senior Resident",
     "url": "https://sgpgims.org.in/Home/Recruitment.html"},
    {"id": "gmc_thiruvananthapuram", "name": "GMC Thiruvananthapuram, Kerala",
     "region": "Kerala", "category": "Govt – Senior Resident", "url": "https://gmc.edu.in/"},

]
