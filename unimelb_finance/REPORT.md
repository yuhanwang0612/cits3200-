# University of Melbourne Finance scraping feasibility report

Harvest date: 2026-08-09

## Result

The task is technically feasible.

- The FBE Finance research page is an overview page rather than a researcher dataset.
- Its official academic-staff link leads to a server-rendered directory that can be extracted with a headless browser.
- Its Recent Publications link leads to a DSpace collection with a public REST API, so publication metadata should be harvested through the API rather than scraped from rendered pages.

## Extracted datasets

### Current Finance staff directory

- 47 records labelled `Department of Finance`
- 27 records link to a Find an Expert profile
- 24 records are automatically flagged for inclusion review
- Extracted fields: displayed name, role, disciplines, interests, department, email, phone and profile URL

The flagged group includes education-focused or teaching roles, professional/administrative roles, emeritus or postdoctoral appointments, and people without a linked Find an Expert profile. These flags are prompts for client review, not automatic exclusions.

### Finance - Research Publications collection

- 254 of 254 archived collection items extracted
- 236 Journal Articles
- 227 records with a DOI
- issued-year range in the current collection: 2003-2026
- 247 records containing Melbourne internal author identifiers

Extracted metadata includes title, authors, Melbourne authors, internal author IDs/ORCIDs where supplied, year, item type, DOI, ISSN/eISSN, journal, volume, issue, pages, publisher, department, faculty, citation text, licence and open-access information.

## Data-quality interpretation

The two datasets have different meanings:

- The staff directory is the best source for deciding who currently belongs to the Department of Finance.
- The Minerva collection is a department-level repository collection. It may include former staff and collaborators and may omit publications belonging to current staff.

Therefore, the collection should not be described as a guaranteed complete historical publication record for every current Finance researcher. A researcher-publication reconciliation stage is still required. Internal IDs and ORCIDs should be used first, followed by carefully reviewed name matching.

## Recommended project approach

1. Use the staff directory to create the current candidate researcher list.
2. Ask the client which appointment categories are included in rankings.
3. Use OpenAlex as the main publication/citation source after matching each approved researcher.
4. Use Minerva metadata to validate OpenAlex results and fill identified gaps.
5. Record match confidence and route ambiguous researchers or publications for manual review.

This is substantially more realistic than writing a separate full-publication HTML scraper for every university.
