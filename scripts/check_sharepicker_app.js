// Parses app/sharepicker.html's scripts, then checks its in-browser re-ranking against the
// Python scores in the embedded demo (written by scripts/build_sharepicker_demo.py).
const fs = require("fs");
const path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "app", "sharepicker.html"), "utf8");
const scripts = [...html.matchAll(/<script(?![^>]*application\/json)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
scripts.forEach((s, i) => { try { new Function(s); } catch (e) { console.error(`script ${i} doesn't parse: ${e.message}`); process.exit(1); } });
const engine = html.match(/<script id="engine">([\s\S]*?)<\/script>/)[1];
const api = new Function(`${engine}\nreturn { rerank, contributions, PRESETS };`)();
const demo = JSON.parse(html.match(/<script id="demo-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
if (!demo.shares || !demo.shares.length) { console.error("no demo data embedded"); process.exit(1); }
let bad = 0;
const ranked = api.rerank(demo.shares, demo.weights);
for (const s of ranked) {
  if (Math.abs(s.score - s.composite) > 1e-3 || s.newRank !== s.rank) {
    console.log(`FAIL ${s.ticker}: app ${s.score?.toFixed(3)} #${s.newRank} vs python ${s.composite?.toFixed(3)} #${s.rank}`);
    bad++;
  }
  const c = api.contributions(s.groups, demo.weights);
  for (const [g, v] of Object.entries(s.contributions)) if (Math.abs(c[g] - v) > 1e-3) { console.log(`FAIL ${s.ticker} ${g} contribution`); bad++; }
}
console.log(bad ? `${bad} mismatches` : `ok   ${ranked.length} shares match the Python scores`);
process.exit(bad ? 1 : 0);
