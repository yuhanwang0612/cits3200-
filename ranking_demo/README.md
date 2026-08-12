# CITS3200 research ranking demo

This local client-discussion prototype combines the normalized researcher summaries from:

- UWA Accounting;
- UWA Finance;
- University of Melbourne Accounting; and
- University of Melbourne Finance.

It supports university, discipline, academic-level and name filters; optional inclusion of profiles requiring review; adjustable A*/A/B/C weights; and sorting by illustrative score or publication counts.

## Important interpretation

The score is a demonstration, not a final ranking methodology. Default weights are A*=8, A=4, B=2 and C=1. The four repository collections have different coverage and are not guaranteed to represent complete researcher career histories. Inclusion rules and the final weighting method require client approval.

## Run locally

From `ranking_demo/`:

```bash
pnpm install
pnpm run dev
```

Open the local URL printed in the terminal, normally `http://localhost:3000/`.

`pnpm run dev` regenerates `public/rankings.json` from the four sibling module outputs before starting the site. To rebuild only the combined dataset, run:

```bash
pnpm run data
```

To verify the production build:

```bash
pnpm run build
```
