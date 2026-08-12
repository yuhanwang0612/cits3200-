import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the ranking explorer shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>Research Ranking Explorer \| CITS3200 Team 20<\/title>/i);
  assert.match(html, /Preparing ranking data/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|react-loading-skeleton/i);
});

test("ships source-backed demo data and controls", async () => {
  const [page, layout, css, packageJson, data] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../public/rankings.json", import.meta.url), "utf8").then(JSON.parse),
  ]);
  assert.match(page, /Ranking controls/);
  assert.match(page, /Include profiles requiring review/);
  assert.match(page, /A\* ×/);
  assert.match(layout, /Research Ranking Explorer/);
  assert.match(css, /\.coverage-grid/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  assert.equal(data.coverage.length, 4);
  assert.equal(data.researchers.length, 143);
});
