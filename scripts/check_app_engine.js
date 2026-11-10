// Parses app/kiwiwealth.html's scripts, then runs its in-browser engine on the shared
// cases in tests/parity_cases.json. Expected values there come from the Python engine
// (scripts/update_parity.py), so a pass means the two engines agree.
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "app", "kiwiwealth.html"), "utf8");
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
scripts.forEach((s, i) => { try { new Function(s); } catch (e) { console.error(`script ${i} doesn't parse: ${e.message}`); process.exit(1); } });

const engine = html.match(/<script id="engine">([\s\S]*?)<\/script>/)[1];
const api = new Function(`${engine}\nreturn { analyse, migrate };`)();
const cases = JSON.parse(fs.readFileSync(path.join(root, "tests", "parity_cases.json"), "utf8"));
let failed = 0;
const near = (a, b) => a == null || b == null ? a == b : Math.abs(a - b) <= 1;
for (const c of cases) {
  const A = api.analyse(api.migrate(JSON.parse(JSON.stringify(c.state))), null, new Date(c.on + "T12:00:00"));
  const r = A.ret, e = c.expected;
  const got = { net_worth: Math.round(A.sheet.net), ks: r.ksAt, wp: r.wpAt, acc: r.invAt, sustainable: r.sustainable,
    extra: r.extra, short_year: r.shortYear, locked_problem: r.lockedProblem };
  const problems = Object.keys(got).filter(k => typeof got[k] === "boolean" ? got[k] !== e[k] : !near(got[k], e[k]))
    .map(k => `${k}: app ${got[k]} vs python ${e[k]}`);
  const titles = A.recs.map(x => `${x.priority} ${x.title}`).sort();
  for (const t of e.titles) if (!titles.includes(t)) problems.push(`missing in app: ${t}`);
  for (const t of titles) if (!e.titles.includes(t)) problems.push(`only in app: ${t}`);
  console.log(`${problems.length ? "FAIL" : "ok  "} ${c.name}`);
  problems.forEach(p => console.log("     " + p));
  failed += problems.length ? 1 : 0;
}
process.exit(failed ? 1 : 0);
