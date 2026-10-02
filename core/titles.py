"""Turning a job title into an academic level.

Two problems this solves. Some people's rank is not in their job title at
all — an endowed chair name carries no rank word, and a convenorship is a
role rather than a level. And "Dr" is a qualification, not a rank, so it
must not be read as one.
"""

import re

PREFIX = re.compile(
    r"^(Associate Professor|Emeritus Professor|Professor|Dr|Mr|Mrs|Ms|Miss"
    r"|A/Prof|APrf|Prof|Assoc\.? Prof\.?)\.?\s+",
    re.IGNORECASE,
)

# Trailing parenthetical, e.g. "Associate Lecturer (Finance)".
SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")

# Order matters: compound titles must be tested before their components,
# or "Associate Lecturer" matches the "Lecturer" pattern first.
LADDER = [
    ("Emeritus Professor",     r"emeritus prof|professor emeritus"),
    ("Associate Professor",    r"associate prof|a/prof|aprof|aprf"),
    ("Associate Lecturer",     r"associate lecturer"),
    ("Senior Lecturer",        r"senior lecturer"),
    ("Senior Research Fellow", r"senior research fellow"),
    ("Research Fellow",        r"research fellow"),
    ("Teaching Associate",     r"teaching associate"),
    # US-style title for an Australian Lecturer (Level B). Must sit above the
    # Professor rule, whose bare "professor" would otherwise read it as Level E.
    ("Lecturer",               r"assistant prof"),
    ("Professor",              r"\bprofessor\b|chair in|\bdean\b"),
    ("Lecturer",               r"\blecturer\b"),
]

# Australian academic levels. Teaching-only and casual appointments are
# deliberately absent — they sit outside this ladder and map to None.
LEVEL = {
    "Associate Lecturer":     "A",
    "Lecturer":               "B",
    "Fellow":                 "B",
    "Research Fellow":        "B",
    "Senior Lecturer":        "C",
    "Senior Fellow":          "C",
    "Senior Research Fellow": "C",
    "Associate Professor":    "D",
    "Reader":                 "D",
    "Professor":              "E",
    "Professorial Fellow":    "E",
    "Professor Emeritus":     "E",
    "Emeritus Professor":     "E",
}

# Honorifics that say nothing about rank.
_QUALIFICATIONS = {"dr", "mr", "mrs", "ms", "miss"}

# Shorthands that appear in name prefixes → canonical ladder label.
_PREFIX_NORM = {
    "aprof": "Associate Professor",
    "aprf":  "Associate Professor",
    "a/prof": "Associate Professor",
    "assoc. prof": "Associate Professor",
    "assoc prof": "Associate Professor",
    "prof": "Professor",
    "emeritus professor": "Emeritus Professor",
}


def split_prefix(name):
    """('Associate Professor Jane Doe') -> ('Jane Doe', 'Associate Professor')"""
    m = PREFIX.match(name or "")
    return PREFIX.sub("", name or "").strip(), (m.group(1) if m else None)


def rank(title, prefix=None):
    """Normalise a job title onto the ladder.

    Falls back to the name prefix when the title carries no rank word, which
    is how an endowed-chair title still resolves to Professor. A prefix that
    is only a qualification is ignored.
    """
    for label, pat in LADDER:
        if title and re.search(pat, title, re.I):
            return label
    if prefix and prefix.lower() not in _QUALIFICATIONS:
        return _PREFIX_NORM.get(prefix.lower(), prefix)
    return None


def level(rank_label):
    """Academic level A–E, or None for roles outside the ladder."""
    return LEVEL.get(rank_label)


# Reverse of the common case in LEVEL: the plain rank word a level code
# most often stands for. Not a full inverse of LEVEL (several labels share
# one level, e.g. "Reader" and "Associate Professor" are both D) — this
# picks the generic ladder rank, which is what a job title reads as when
# the only thing known is the level itself, not the exact word used on the
# source page.
_RANK_OF_LEVEL = {
    "A": "Associate Lecturer",
    "B": "Lecturer",
    "C": "Senior Lecturer",
    "D": "Associate Professor",
    "E": "Professor",
}


# Administrative roles, moved out of job_title into admin_title with the
# detail around them dropped: "Head of School, School of Finance" ->
# "Head of School". A title can hold several ("Professor & Convenor of HDR,
# Co-Director of ANCAAR"); every one is kept, in title order. Where patterns
# overlap the match that starts first wins, then the longest, so "Deputy
# Program Director" is not reported as "Program Director" or "Director".
_ROLES = [
    ("Deputy Head of School",      r"deputy head of school"),
    ("Head of School",             r"\bhead of school"),
    ("Deputy Head of Department",  r"deputy head of department"),
    ("Head of Department",         r"\bhead of department"),
    ("Associate Dean",             r"associate dean"),
    ("Assistant Dean",             r"assistant dean"),
    ("Deputy Dean",                r"deputy dean"),
    ("Dean",                       r"\bdean\b"),
    ("Deputy Program Director",    r"deputy program director"),
    ("PhD Program Director",       r"phd program director"),
    ("Program Director",           r"program director"),
    ("Deputy Director",            r"\bdeputy director\b"),
    ("Co-Director",                r"\bco-?director\b"),
    ("HDR Director",               r"director of hdr|\bhdr director"),
    ("Director",                   r"\bdirector\b"),
    ("HDR Convenor",               r"convenor of hdr|\bhdr convenor"),
    ("Program Convenor",           r"program convenor"),
    ("Discipline Convenor",        r"discipline convenor"),
    ("Major Convenor",             r"major convenor"),
    ("Course Convenor",            r"course(?:\s*work)? convenor"),
    ("Deputy Honours Coordinator", r"deputy honours coordinator"),
    ("Honours Coordinator",        r"honours coordinator"),
    ("PhD Coordinator",            r"phd coordinator"),
    ("Major Coordinator",          r"major coordinator"),
    ("Program Coordinator",        r"program coordinator"),
    ("Working Paper Series Coordinator", r"working paper series coordinator"),
    ("Research Hub Co-Leader",     r"research hub co.?leader"),
    ("CPA Liaison Officer",        r"cpa liaison officer"),
    ("Online Course Facilitator",  r"online course facilitator"),
]


def _find_roles(title):
    """(labels in title order, title with the role text removed)."""
    hits = sorted(
        ((m.start(), -(m.end() - m.start()), m.end(), label)
         for label, pattern in _ROLES
         for m in re.finditer(pattern, title, re.I)),
    )
    labels, spans, taken_until = [], [], -1
    for start, _neg_len, end, label in hits:
        if start < taken_until:
            continue                  # inside a role already taken
        spans.append((start, end))
        taken_until = end
        if label not in labels:
            labels.append(label)
    rest = title
    for start, end in reversed(spans):
        rest = rest[:start] + " " + rest[end:]
    return labels, rest


# Appointment-type words dropped from job_title, which carries the rank alone:
# "Adjunct Associate Professor" -> "Associate Professor", "Casual Teaching
# Lecturer" -> "Lecturer", "Honorary Principal Fellow" -> "Principal Fellow".
# Emeritus is not one of them: "Emeritus Professor" is a title of its own.
_QUALIFIERS = [
    r"\badjunct\b",
    r"\bhonorary\b",
    r"\bcasual\b",
    r"\bp/t\b|\bpart[- ]time\b",
]

# Titles that are neither a rank nor a role, written out plainly.
_RENAME = {
    # the closing bracket may already be trimmed by the time this is checked
    r"^employee\s*\(prof\.?\s*staff\)?$": "Professional Staff",
}

# Values a scraper has put in the title field that are not titles at all.
_NOT_A_TITLE = {"research and executive education"}


def split_job_title(title, level_code=None):
    """Split a listed title into (job_title, admin_title) for the staff export.

    job_title is the academic rank only: Emeritus Professor, an Adjunct or
    Honorary rank with its qualifier, the rank word found anywhere in the
    title (any case), else the rank implied by the academic level. A teaching
    role such as "Teaching Fellow" with no rank and no level is kept as is.

    admin_title is the administrative role, if any, e.g. "Dean".

    A pure admin title with no level ("Deputy Head of School" at USyd) gives
    job_title None: the academic rank is genuinely unknown, and the role must
    not be passed off as one.
    """
    if not title or not title.strip():
        return title, None
    t = " ".join(title.split())
    if t.lower() in _NOT_A_TITLE:
        return (rank_from_level(level_code) if level_code else None), None

    # Look for a rank only in what is left once the roles are removed, so a
    # role's own words ("dean") are not read as one.
    labels, rest = _find_roles(t)
    admin = "; ".join(labels) or None
    rest = " ".join(rest.split()).strip(" ,-&()")

    r = rank(rest) if rest else None
    if r == "Emeritus Professor":
        return r, admin
    for pattern in _QUALIFIERS:
        rest = " ".join(re.sub(pattern, " ", rest, flags=re.I).split())
    for pattern, plain in _RENAME.items():
        if re.match(pattern, rest, re.I):
            rest = plain
    if r:
        return r, admin
    if level_code:
        return (rank_from_level(level_code) or (None if admin else _bare(rest) or None)), admin
    return (None if admin else _bare(rest) or None), admin


def _bare(title):
    """A title with no rank, trimmed the way ranked titles are: the field and
    qualifiers after it go, as "Lecturer in Finance" becomes "Lecturer".
    "Enterprise Fellow in data, analytics, disruption and innovation" ->
    "Enterprise Fellow"; "Tutor - Education Focussed" -> "Tutor"."""
    t = re.split(r"\s+(?:in|of)\s+|\s+[-–—]\s+|\s*\(", title, maxsplit=1)[0]
    return t.strip(" ,") or title


def rank_from_level(level_code):
    """Canonical rank label for a bare level code, for when a caller has
    already resolved A-E some other way (e.g. from a name-prefix fallback)
    but has no literal rank text left to normalise with `rank()` itself."""
    return _RANK_OF_LEVEL.get(level_code)
