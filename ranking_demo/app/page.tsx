"use client";

import { useEffect, useMemo, useState } from "react";

type Researcher = {
  id: string;
  name: string;
  university: string;
  universityShort: string;
  discipline: string;
  academicTitle: string;
  academicLevel: string;
  inclusionReviewRequired: boolean;
  publicationCount: number;
  rankedPublicationCount: number;
  aStarCount: number;
  aCount: number;
  bCount: number;
  cCount: number;
  unrankedCount: number;
  profileUrl: string;
};

type Coverage = {
  key: string;
  universityShort: string;
  discipline: string;
  researcherCount: number;
  organisationOutputCount: number;
  rankedOutputCount: number;
  researcherPublicationLinks: number;
  sourceCountReconciles: boolean | null;
  warnings: string[];
};

type Dataset = {
  generatedAt: string;
  scoringNote: string;
  researchers: Researcher[];
  coverage: Coverage[];
};

type Weights = { aStar: number; a: number; b: number; c: number };
type SortKey = "score" | "ranked" | "publications" | "name";

const defaultWeights: Weights = { aStar: 8, a: 4, b: 2, c: 1 };

function score(row: Researcher, weights: Weights) {
  return row.aStarCount * weights.aStar + row.aCount * weights.a + row.bCount * weights.b + row.cCount * weights.c;
}

function number(value: number) {
  return new Intl.NumberFormat("en-AU").format(value);
}

export default function Home() {
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [error, setError] = useState("");
  const [university, setUniversity] = useState("All");
  const [discipline, setDiscipline] = useState("All");
  const [level, setLevel] = useState("All");
  const [query, setQuery] = useState("");
  const [includeReview, setIncludeReview] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [weights, setWeights] = useState<Weights>(defaultWeights);

  useEffect(() => {
    fetch("/rankings.json")
      .then((response) => {
        if (!response.ok) throw new Error(`Dataset request failed (${response.status})`);
        return response.json();
      })
      .then(setDataset)
      .catch((reason) => setError(reason.message || "Unable to load ranking data"));
  }, []);

  const ranked = useMemo(() => {
    if (!dataset) return [];
    const normalizedQuery = query.trim().toLowerCase();
    return dataset.researchers
      .filter((row) => university === "All" || row.universityShort === university)
      .filter((row) => discipline === "All" || row.discipline === discipline)
      .filter((row) => level === "All" || (level === "Unmapped" ? !row.academicLevel : row.academicLevel === level))
      .filter((row) => includeReview || !row.inclusionReviewRequired)
      .filter((row) => !normalizedQuery || `${row.name} ${row.academicTitle}`.toLowerCase().includes(normalizedQuery))
      .map((row) => ({ ...row, score: score(row, weights) }))
      .sort((left, right) => {
        if (sortKey === "name") return left.name.localeCompare(right.name);
        if (sortKey === "ranked") return right.rankedPublicationCount - left.rankedPublicationCount || right.score - left.score;
        if (sortKey === "publications") return right.publicationCount - left.publicationCount || right.score - left.score;
        return right.score - left.score || right.rankedPublicationCount - left.rankedPublicationCount || left.name.localeCompare(right.name);
      });
  }, [dataset, discipline, includeReview, level, query, sortKey, university, weights]);

  if (error) {
    return <main className="state-message"><strong>Demo data could not be loaded.</strong><span>{error}</span></main>;
  }
  if (!dataset) {
    return <main className="state-message"><strong>Preparing ranking data…</strong><span>Loading four verified source collections.</span></main>;
  }

  const totals = dataset.coverage.reduce(
    (result, item) => ({
      researchers: result.researchers + item.researcherCount,
      outputs: result.outputs + item.organisationOutputCount,
      ranked: result.ranked + item.rankedOutputCount,
      links: result.links + item.researcherPublicationLinks,
    }),
    { researchers: 0, outputs: 0, ranked: 0, links: 0 },
  );

  return (
    <main>
      <header className="hero">
        <div className="hero-inner">
          <div className="eyebrow">CITS3200 · Team 20 · Working prototype</div>
          <div className="hero-grid">
            <div>
              <h1>Research ranking explorer</h1>
              <p className="hero-copy">A transparent preview of Accounting and Finance research data from UWA and the University of Melbourne.</p>
            </div>
            <div className="freshness"><span>Dataset refreshed</span><strong>{new Date(dataset.generatedAt).toLocaleString("en-AU", { dateStyle: "medium", timeStyle: "short" })}</strong></div>
          </div>
          <div className="metric-grid">
            <div className="metric"><span>Linked profiles</span><strong>{number(totals.researchers)}</strong></div>
            <div className="metric"><span>Source collections</span><strong>{dataset.coverage.length}</strong></div>
            <div className="metric"><span>Organisation outputs</span><strong>{number(totals.outputs)}</strong></div>
            <div className="metric"><span>ABDC-matched outputs</span><strong>{number(totals.ranked)}</strong></div>
          </div>
        </div>
      </header>

      <section className="content-shell">
        <div className="notice">
          <span className="notice-mark">Demo</span>
          <p><strong>This is not a final league table.</strong> Scores are illustrative, inclusion rules still require client confirmation, and each source collection has different coverage.</p>
        </div>

        <section className="control-panel" aria-label="Ranking controls">
          <div className="control-heading">
            <div><span className="section-kicker">Explore the data</span><h2>Ranking controls</h2></div>
            <button className="reset-button" onClick={() => { setUniversity("All"); setDiscipline("All"); setLevel("All"); setQuery(""); setIncludeReview(false); setSortKey("score"); setWeights(defaultWeights); }}>Reset</button>
          </div>
          <div className="filter-grid">
            <label>University<select value={university} onChange={(event) => setUniversity(event.target.value)}><option>All</option><option>UWA</option><option>UniMelb</option></select></label>
            <label>Discipline<select value={discipline} onChange={(event) => setDiscipline(event.target.value)}><option>All</option><option>Accounting</option><option>Finance</option></select></label>
            <label>Academic level<select value={level} onChange={(event) => setLevel(event.target.value)}><option>All</option><option>A</option><option>B</option><option>C</option><option>D</option><option>E</option><option>Unmapped</option></select></label>
            <label>Sort by<select value={sortKey} onChange={(event) => setSortKey(event.target.value as SortKey)}><option value="score">Illustrative score</option><option value="ranked">ABDC-ranked outputs</option><option value="publications">Linked outputs</option><option value="name">Researcher name</option></select></label>
            <label className="search-control">Researcher search<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Type a name or title" /></label>
          </div>
          <div className="weight-row">
            <span className="weight-label">Score weights</span>
            {(["aStar", "a", "b", "c"] as const).map((key) => (
              <label className="weight" key={key}><span>{key === "aStar" ? "A*" : key.toUpperCase()}</span><input aria-label={`${key} weight`} type="number" min="0" max="20" value={weights[key]} onChange={(event) => setWeights({ ...weights, [key]: Math.max(0, Number(event.target.value) || 0) })} /></label>
            ))}
            <label className="review-toggle"><input type="checkbox" checked={includeReview} onChange={(event) => setIncludeReview(event.target.checked)} /><span>Include profiles requiring review</span></label>
          </div>
        </section>

        <section className="ranking-section">
          <div className="ranking-heading">
            <div><span className="section-kicker">Illustrative results</span><h2>{number(ranked.length)} researchers shown</h2></div>
            <p>A* × {weights.aStar} · A × {weights.a} · B × {weights.b} · C × {weights.c}</p>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Rank</th><th>Researcher</th><th>Source</th><th>Level</th><th>Score</th><th>A*</th><th>A</th><th>B</th><th>C</th><th>ABDC</th><th>Linked outputs</th></tr></thead>
              <tbody>
                {ranked.slice(0, 50).map((row, index) => (
                  <tr key={row.id}>
                    <td className="rank-cell">{index + 1}</td>
                    <td><div className="researcher-name">{row.profileUrl ? <a href={row.profileUrl} target="_blank" rel="noreferrer">{row.name}</a> : row.name}{row.inclusionReviewRequired && <span className="review-badge">Review</span>}</div><span className="subtext">{row.academicTitle}</span></td>
                    <td><span className={`source-pill ${row.universityShort === "UWA" ? "uwa" : "unimelb"}`}>{row.universityShort}</span><span className="discipline">{row.discipline}</span></td>
                    <td>{row.academicLevel || <span className="muted">—</span>}</td>
                    <td className="score-cell">{row.score}</td>
                    <td>{row.aStarCount}</td><td>{row.aCount}</td><td>{row.bCount}</td><td>{row.cCount}</td>
                    <td>{row.rankedPublicationCount}</td><td>{row.publicationCount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {ranked.length === 0 && <div className="empty-state">No researchers match the current filters.</div>}
          </div>
          {ranked.length > 50 && <p className="table-note">Showing the first 50 of {number(ranked.length)} matching researchers.</p>}
        </section>

        <section className="coverage-section">
          <div className="coverage-heading"><span className="section-kicker">Evidence and caveats</span><h2>Source coverage</h2><p>Counts describe repository collections, not complete career histories.</p></div>
          <div className="coverage-grid">
            {dataset.coverage.map((item) => (
              <article className="coverage-card" key={item.key}>
                <div className="coverage-title"><div><strong>{item.universityShort}</strong><span>{item.discipline}</span></div><span className={`status ${item.sourceCountReconciles === false ? "warning" : "ok"}`}>{item.sourceCountReconciles === false ? "Count review" : "Reconciled"}</span></div>
                <dl><div><dt>Profiles</dt><dd>{number(item.researcherCount)}</dd></div><div><dt>Outputs</dt><dd>{number(item.organisationOutputCount)}</dd></div><div><dt>ABDC matched</dt><dd>{number(item.rankedOutputCount)}</dd></div><div><dt>Researcher links</dt><dd>{number(item.researcherPublicationLinks)}</dd></div></dl>
                <p>{item.warnings[0]}</p>
              </article>
            ))}
          </div>
        </section>
      </section>
      <footer><span>CITS3200 Team 20 · Client discussion prototype</span><span>ABDC 2025-v2 · Source-linked, reviewable data</span></footer>
    </main>
  );
}
