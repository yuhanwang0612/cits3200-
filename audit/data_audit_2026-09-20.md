# UniMelb / UWA data audit — 20 September 2026

This is a review of the current final CSVs. The 11 high-confidence identity collisions, 2 clear dissertation records and 6 additional records confirmed by manual review were excluded through the existing evidence-backed exclusion list. All other potentially wrong records are listed in [`manual_review_candidates.csv`](manual_review_candidates.csv) so that identity, scope and inclusion decisions can be made separately.

## Current dataset checks

| Dataset | Staff | Staff with publications | Zero-publication staff | Publications | Missing DOI | Missing citation percentile |
|---|---:|---:|---:|---:|---:|---:|
| UniMelb | 99 | 75 | 24 | 1,000 | 72 | 98 |
| UWA | 43 | 40 | 3 | 1,054 | 119 | 134 |

- Every publication row currently has a non-empty `name`, `title`, `year`, `journal_name`, `article_url` and `source`.
- UniMelb staff fields are complete except for 2 missing `job_title` values and 22 missing derived `academic_level` values. These are not automatically errors: the source profile can omit a title or use a title that cannot be mapped safely to A–E.
- UWA currently has `publication_type = Journal Article` for every row because that is the agreed export schema. This does not prove that every record is an original research article; book reviews still need the policy decision listed below.
- The missing DOI and citation-percentile values are source-coverage gaps, not evidence that the publication is false. They are retained rather than guessed.

## What was externally checked

### Strong false-positive / source-collision candidates

These were the safest records to remove because their venue/topic/affiliation conflicts with the official staff identity:

- **Demi Wang:** three GaN semiconductor papers. The official [Demi Wang profile](https://fbe.unimelb.edu.au/our-people/staff/accounting/demi-wang) describes an accounting lecturer, not a semiconductor researcher.
- **Like Jiang:** three materials/engineering papers. The official [Like Jiang profile](https://fbe.unimelb.edu.au/our-people/staff/accounting/like-jiang) describes an accounting/auditing scholar.
- **Bo Qin:** the transport bibliometrics, cybersecurity, electronic-auction and industrial-technology records are Chinese computer-science/engineering records and should not be attributed to the Melbourne accounting researcher. The ESG Sustainability paper is kept as a manual identity check because its topic could still be accounting-related.
- **Jon Gauntlett:** the Philadelphia Museum of Art Bulletin record is a source collision with the genuine RBA article. Keep the [RBA Real-time Gross Settlement article](https://www.rba.gov.au/publications/bulletin/2010/sep/8.html), remove the Philadelphia-venue duplicate.

### Non-journal / inclusion-policy candidates

- **Hoonsuk Park — “Three Essays in Household Finance”** was excluded because the OhioLINK record is a doctoral dissertation, not a journal article. See the [OhioLINK record](https://etd.ohiolink.edu/acprod/odb_etd/etd/r/1501/10?clear=10&p10_accession_num=osu1492511169579377).
- **Hae Won (Henny) Jung — “Essays on Financial Structure…”** was excluded because the GSU record is a dissertation, not a journal article. See the [GSU archive record](https://scholarworks.gsu.edu/rmi_diss/27).
- **Gary Biddle** has several book-review/book records; **Jonathan Black** has a repository/conference-style record; **Stuart Black** has practitioner-magazine records. They are listed in the CSV rather than silently removed.
- **UWA David Gilchrist** has nine outputs whose titles begin with `Book Review`. They are journal-published reviews, so whether they count depends on whether “journal articles” means any journal output or only original research articles.

### Records requiring manual judgement

- **Patrick Ferguson:** two medicine/materials records are likely namesakes and remain pending confirmation.
- **Attila Balogh and Eduard Inozemtsev:** their records require different treatment. Attila’s Superapps record remains pending identity confirmation; Eduard’s mathematics paper was retained because the author identity is plausible, with only its subject scope remaining as a policy question.
- **Jonathan Black:** the repository/conference-style record is retained because there is no proof of wrong identity and its subject is consistent with accounting.
- The normalized title/year scan found seven duplicate groups in UniMelb (14 rows), including repository `.v1` variants and DOI variants. They are listed as `duplicate_review`; keep one canonical publication per work after checking publisher metadata.

## Zero-publication staff

UniMelb currently has 24 staff with no publication retrieved: Rosemary Addis, Danny Burton, Wayne Coetzee, Bob Cornick, Greg Cusack, Lisa Greig, Warren Lee, Joana Linggo Liong, Tasneem Mohammed, Michelle Sabe, Michael Taouk, Di (Demi) Wang, Sarah Yang Spencer, Elizabeth Bowman, Shuang Chen, Assaf Dekel, Juan Pablo Franco, Allan Horsfall, Natalie (Nhung) Le, Tania Lee, Bob Li, Gaby Nardari, Linh Nguyen and Bryan Tan.

UWA currently has 3: Kevin Na, Yeok-Fun Mah and Zhengge Zhou.

These should not be filled with guessed publications. They are either source-coverage gaps, new/teaching-focused staff, or people for whom no matching official output was found. If the client requires every staff member to have a non-empty publication list, these names need targeted manual searches.

## Recommended decision order

1. The 19 confirmed exclusions are now applied; the remaining 36 CSV rows are the unresolved manual queue.
2. Decide whether dissertations, book reviews, practitioner pieces and conference/repository records count. This affects the remaining policy-review rows, not the scraper’s identity matching.
3. Deduplicate the remaining duplicate candidates by DOI/title/year, retaining the publisher journal record over a repository or `.v1` copy. Jun Yu is an exception: verify author identity before deduplication.
4. Leave missing DOI, citation percentile, ABDC, JIF and SJR values as null unless a source can verify them; do not invent values.

After the confirmed exclusions, the current counts are **UniMelb 1,000** and **UWA 1,054**. The remaining manual decisions can change those counts. The audit passed the existing test suite: **280 tests passed**.
