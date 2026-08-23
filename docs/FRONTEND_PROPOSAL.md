# Frontend — a starting proposal for Wednesday

Due at the 26 Aug meeting per the 19 Aug minutes ("begin discussion on
frontend design; review/plan improvements on last year's design"); nobody
had started it as of tonight. This is a conversation starter, not a spec —
based on the actual Scope of Work FRs and User Stories docs (read directly,
not from memory), three concrete layout proposals to react to on
Wednesday, not a design to approve.

## What the minimum product actually needs on screen

Straight from the FRs and user stories, not invented:

- **A ranked researcher table** (FR10): filterable by university, field,
  academic level and name; a selectable ranking metric; column sorting;
  paging. The public-user stories add: each publication's ABDC rank and
  citation percentile visible, academic levels shown on a standard A–E
  scale so they're comparable across universities.
- **A researcher detail page** (FR11): profile plus a searchable, sortable
  list of that researcher's publications, each appearing once (a user
  story specifically calls out no double-counting, which is also why
  DOI-first de-duplication matters upstream of the frontend).
- **A ranked university table** (FR12), with its own selectable ranking
  metric — a separate view, not a filter on the researcher table.
- **Source currency** (FR14 / a user story): when each source was last
  harvested and its latest year, visible somewhere a user would actually
  look, not buried in a footnote — the whole point of FR14 is that stale
  data should be visible, not just tracked internally.
- **A methodology page** (FR16 / a user story): sources, cleaning rules,
  metric definitions, known limitations — published, not internal-only.
  This is also where a methodology caveat like the one now in
  `ANU_DATA_SUMMARY.md` (profile-page vs. repository sourcing) belongs
  once the site exists, not just in an internal doc.

## Where the ABDC/Scimago/JIF selector sits

The client confirmed on 19 Aug: **ABDC is the landing default**, both
rankings supported, switch via a selector — not three separate pages.
Concretely, this reads as one control, visible on both the researcher
table and the university table (it changes what "top" means on both), not
duplicated per-page state. Whichever of the three proposals below wins,
this selector should be the same component in the same visual position on
every ranked view, so switching metric mid-session doesn't feel like
leaving and re-entering the tool.

## Where Excel export sits (FR15)

A user story asks for this generically ("export the data to Excel, so I
can run my own analysis") — not scoped to one specific table in the FRs.
Simplest reading: an export action on the researcher table, the university
table, and the detail page, each exporting what's currently on screen
(current filter/metric state), rather than one global "export everything"
button that ignores what the user was just looking at.

## How the separate refresh operations surface

Two client instructions converge here: 12 Aug ("split into smaller
operations... one button for staff scraping, a separate one for
publication scraping") and the administrator user stories ("re-run the
pipeline on specific or all sources... existing records updated in place,
so that data stays current without duplication or loss... failures logged
with source and reason"). This points to an **admin-only** screen, separate
from the public views entirely (the NFRs are explicit: "the refresh
process must not be triggerable by a public user") — a per-source list
(staff directory, each publication source, each ranking source) with its
own trigger and its own FR14 last-run/latest-year and last-failure state,
not one combined "refresh everything" button.

## Honest gap: I haven't seen last year's reference system

The Scope of Work (§2) says the client provided a previous team's "G8
Research Output Database" as a reference — filterable researcher rankings,
per-researcher publication lists, university rankings, a published
methodology page, ~539 researchers. I don't have access to it — no URL or
file for it exists anywhere I can currently reach in this repo. If it's
sitting somewhere (a link the client sent, a screenshot, an old team's
repo), that's the first thing to pull up Wednesday before picking between
the proposals below, since the Scope of Work explicitly frames it as "a
functional benchmark... to be evaluated and improved upon," not something
to design from scratch ignoring it.

## Three concrete proposals

**A. Tabbed single page.** One URL, two tabs (Researchers / Universities),
shared filter bar and metric selector above the tabs so switching between
them keeps your filters and chosen ranking metric. Detail view opens as an
expandable row or a slide-over panel rather than full navigation.
*Trade-off:* fastest to browse and compare, but a slide-over detail view is
more frontend work than a plain page, and doesn't give a shareable URL per
researcher without extra routing work.

**B. Separate pages per FR.** `/researchers`, `/universities`, and
`/researchers/:id` as three distinct routes, each with its own filter bar
and the same metric-selector component repeated in the same position.
*Trade-off:* maps most directly onto FR10/11/12 as written, gives every
researcher a real shareable URL for free, but filter/metric state doesn't
persist between pages unless that's built deliberately.

**C. Dashboard-first landing page.** Home page shows university-level
summary cards (top-ranked institutions, current metric) plus a small
"top researchers" preview, with a clear "browse all" action into the full
filterable table (option A or B underneath). *Trade-off:* best first
impression for a public user with no idea what they're looking for yet,
but it's an extra screen to build and maintain that doesn't map to any
single FR on its own — closest to "beyond" scope, not minimum product.

My lean, weakly: B, because it maps most literally onto FR10/11/12 and
gives researcher detail pages a real URL — but this is exactly the kind
of call that should get made as a team on Wednesday, not decided here.
