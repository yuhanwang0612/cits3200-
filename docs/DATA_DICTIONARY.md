# Data dictionary

What every column in `final output/<uni>/` means, where its value comes from,
and how to read it. Each university folder has the same four tables, as both
CSV and JSON (identical content):

| File | One row per |
|---|---|
| `<uni>_staff` | researcher in scope |
| `<uni>_publications` | researcher–publication pair |
| `<uni>_journals` | journal used by at least one publication |
| `<uni>_harvest` | data source used in the run |

Some folders also have `<uni>_screened_out.csv` (publications removed as
probably belonging to someone else; see the end of this page).

## Reading the data

- **A blank means "not available", never zero.** A blank `quality_rank` means
  the journal is not on the ABDC list, not that it is ranked low. A blank
  `impact_factor` means the journal has no Clarivate impact factor, which is
  normal for journals outside Web of Science.
- **A co-authored paper appears once per researcher.** If two staff members at
  the same university wrote a paper together, it is one row for each of them.
- **Citation figures change between runs.** `cited_by_count`, `fwci` and
  `citation_percentile` are live values from OpenAlex, so they drift every
  time the pipeline runs.
- **Only journal articles are exported.** Books, chapters, conference papers
  and working papers are collected but dropped at export.

## Staff (`<uni>_staff`)

| Column | Meaning |
|---|---|
| `name` | The researcher's name with titles removed ("Dr", "Prof"). |
| `job_title` | The position as the university lists it, e.g. "Senior Lecturer in Finance". |
| `academic_level` | The Australian academic level derived from `job_title` (below). Blank when the title does not map to one, e.g. an administrative role such as "Deputy Head of School". |
| `university` | The university's name. |
| `field_of_research` | `Accounting` or `Finance`, from the department the university lists the person under. |
| `source_id` | The person's ID in the university's own research system (e.g. their Pure profile slug). Blank for universities that don't publish one. |
| `orcid` | The person's ORCID iD, a permanent researcher identifier (`0000-0000-0000-0000`), usually taken from their university profile. |
| `profile_url` | Link to their official university profile. |

Academic levels:

| Level | Typical titles |
|---|---|
| A | Associate Lecturer |
| B | Lecturer, Research Fellow |
| C | Senior Lecturer, Senior Research Fellow |
| D | Associate Professor, Reader |
| E | Professor, Emeritus Professor |

Staff with no publications are left out by default (`run.py --keep-empty-staff`
keeps them).

## Publications (`<uni>_publications`)

### Who and what

| Column | Meaning |
|---|---|
| `name` | The researcher this row counts towards; matches `name` in the staff table. |
| `orcid` | That researcher's ORCID iD, repeated for convenience. |
| `source_id` | That researcher's university system ID, where available. |
| `title` | The article title. |
| `year` | Year of publication. |
| `authors` | All authors, separated by `; `. |
| `author_count` | How many authors the paper has, including external co-authors. |
| `journal_name` | The journal, using its ABDC title where it is on the ABDC list so that spellings such as "&" and "and" line up. Joins to `journal_name` in the journals table. |
| `publication_status` | `published` or `forthcoming` (accepted but not yet in an issue). UWA rows carry the university's own wording instead, e.g. "Published - Jun 2024". |

### Links

| Column | Meaning |
|---|---|
| `doi` | The Digital Object Identifier, a permanent ID for the article (e.g. `10.1016/j.jbef.2020.100384`). |
| `article_url` | The best link to the article: the DOI link when there is a DOI, otherwise `link`. |
| `link` | The link given by the source the record came from, such as a repository page. |

### Journal ranking (copied from the journal)

| Column | Meaning |
|---|---|
| `quality_rank` | The journal's ABDC rating: `A*`, `A`, `B` or `C`. Blank when the journal is not on the ABDC list. |
| `sjr_quartile` | The journal's Scimago quartile, `Q1`–`Q4`. |

Both are explained under **Journal metrics** below.

### Citation impact (about this article)

| Column | Meaning |
|---|---|
| `cited_by_count` | How many times the article has been cited, according to OpenAlex. |
| `fwci` | Field-Weighted Citation Impact, from OpenAlex (below). |
| `citation_percentile` | The article's citation percentile, from OpenAlex, as a fraction from 0 to 1 (below). |

### Open access

| Column | Meaning |
|---|---|
| `oa_status` | Whether and how the article is free to read, from OpenAlex (below). |
| `oa_url` | A link to a free copy, where one exists. |

### Provenance

| Column | Meaning |
|---|---|
| `source` | Where this record came from. The university's own system (`UQ eSpace`, `UWA Pure`, `Monash Pure`, `UniMelb Minerva`, `ANU staff profile`, `UNSW staff profile`, `Sydney Profiles`), or one of the open indexes the pipeline also searches (`ORCID`, `Crossref`, `OpenAlex`). University-system rows are treated as the official record. |

## Journals (`<uni>_journals`)

| Column | Meaning |
|---|---|
| `journal_name` | The journal's name as used in the publications table (the ABDC title where there is one). |
| `journal_raw` | The name exactly as the source gave it, before standardising. |
| `publisher` | The publisher, where known. |
| `issn` | The journal's ISSN(s), its standard serial number; print and online editions often have one each, separated by `; `. |
| `quality_rank` | ABDC rating: `A*`, `A`, `B`, `C`, or blank if not listed. |
| `abdc_edition` | Which ABDC list the rating comes from (`2025`). |
| `impact_factor` | Clarivate Journal Impact Factor, 2-year (below). |
| `impact_factor_5yr` | Clarivate Journal Impact Factor, 5-year (below). |
| `jcr_year` | The Journal Citation Reports release the impact factors come from (`2025`). |
| `sjr` | SCImago Journal Rank score (below). |
| `sjr_quartile` | Scimago quartile, `Q1`–`Q4` (below). |
| `h_index` | The journal's h-index from Scimago (below). This is about the journal, not a researcher. |
| `cites_per_doc_2y` | Scimago's average citations per document over two years (below). |
| `scimago_year` | The Scimago release the Scimago figures come from (`2025`). |

## Harvest (`<uni>_harvest`)

A record of what each run collected, so it is visible when a source is stale.

| Column | Meaning |
|---|---|
| `source` | A data source used in the run (same values as `source` above). |
| `last_run` | When the pipeline ran (UTC). |
| `latest_year` | The most recent publication year among the records from that source. A low value can mean the source lags. |
| `record_count` | How many records that source supplied, before duplicates were merged and non-articles dropped, so it is larger than the final publication count. |

## Journal metrics explained

**ABDC rating (`quality_rank`).** The Australian Business Deans Council
Journal Quality List rates business journals in four tiers: `A*` (the
highest, roughly the top 7%), `A`, `B` and `C`. It is the main quality measure
for accounting and finance in Australia. The list only covers business-relevant
journals, so a blank is normal for, say, a medical or engineering journal.
Source: `data/ABDC-JQL-2025-v1-260326.xlsx`, matched by ISSN, or by exact title
when a record has no ISSN.

**Journal Impact Factor (`impact_factor`).** Clarivate's 2-year impact factor:
the citations a journal received in the JCR year to items it published in the
two previous years, divided by the number of citable items it published in
those two years. An impact factor of 5.0 means an average recent article was
cited five times that year. Only journals indexed in Web of Science have one.
Values vary widely between fields, so compare within a field.
Source: Clarivate Journal Citation Reports API, when `CLARIVATE_API_KEY` is set.

**5-year impact factor (`impact_factor_5yr`).** The same calculation over five
years, which smooths out year-to-year swings and suits slower-citing fields
such as accounting.

**SJR (`sjr`).** SCImago Journal Rank: citations per article, weighted by the
prestige of the journals the citations come from, so a citation from a top
journal counts for more. Higher is better. It covers the Scopus database,
which is broader than Web of Science.
Source: `data/scimagojr 2025.csv`, matched by ISSN.

**Scimago quartile (`sjr_quartile`).** Where the journal sits among journals
in its subject category by SJR: `Q1` is the top 25%, `Q4` the bottom 25%.
A journal in several categories gets its best quartile.

**Journal h-index (`h_index`).** A journal has h-index *h* if *h* of its
articles have each been cited at least *h* times. It reflects long-run impact
and grows with a journal's age and size.

**Citations per document, 2 years (`cites_per_doc_2y`).** Scimago's average
citations per document over the previous two years; similar in spirit to the
2-year impact factor, but based on Scopus.

## Article metrics explained

These come from OpenAlex, a free index of scholarly works, looked up by DOI,
so articles without a DOI have none.

**Citations (`cited_by_count`).** The total number of times the article has
been cited. Older articles have had longer to collect citations, so compare
articles of similar age, or use the next two metrics.

**Field-Weighted Citation Impact (`fwci`).** How often the article has been
cited compared with the average for similar articles: the same type, the same
year and the same subfield. **1.0 means exactly the world average**; 2.0 means
twice as many citations as expected; 0.5 means half. Because it is relative to
field and year, it is fair to compare across fields and ages. It is sensitive
to a single highly cited paper, which can reach values in the hundreds.

**Citation percentile (`citation_percentile`).** Where the article ranks by
citations among articles of the same type, year and subfield, as a fraction:
**0.91 means it is cited more than about 91% of comparable articles**. Unlike
FWCI it cannot be pulled up by one extreme value.

**Open-access status (`oa_status`):**

| Value | Meaning |
|---|---|
| `gold` | Published in a fully open-access journal. |
| `diamond` | Fully open-access journal that charges authors nothing. |
| `hybrid` | Open-access article in a subscription journal, with an open licence. |
| `bronze` | Free to read on the publisher's site, but without an open licence. |
| `green` | Free copy in a repository (e.g. a university repository); the journal version may be paywalled. |
| `closed` | No free copy found. |

## Screened-out publications (`<uni>_screened_out.csv`)

Publications the pipeline removed because they probably belong to a different
person with the same name. Only records from the open indexes (ORCID, Crossref,
OpenAlex) are ever screened; records from the university's own system are not.
Kept so that a person can check and overrule the decision.

| Column | Meaning |
|---|---|
| `name`, `title`, `year`, `journal`, `doi`, `source` | As in the publications table. |
| `screened_reason` | Why it was removed, e.g. "only 3% of this researcher's retrieved journal articles are in an ABDC-rated journal; the identifier probably belongs to a namesake". |

The rule lives in `screen.py`. To reinstate a wrongly removed publication, or
remove a missed one, use the files in `data/` (for example
`data/publication_exclusions.csv` and the per-university identity override
files) rather than editing the output, which the next run would overwrite.
