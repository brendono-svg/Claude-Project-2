// Parses app/kiwiwealth.html's scripts and checks its in-browser engine against
// figures from the Python engine (kiwiwealth/) for the example household.
const fs = require("fs");
const path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "app", "kiwiwealth.html"), "utf8");
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
scripts.forEach((s, i) => { try { new Function(s); } catch (e) { console.error(`script ${i} doesn't parse: ${e.message}`); process.exit(1); } });

const engine = html.match(/<script id="engine">([\s\S]*?)<\/script>/)[1];
const example = html.match(/const EXAMPLE = (\{[\s\S]*?\n\});/)[1];
const api = new Function(`${engine}\nreturn { analyse, EXAMPLE: ${example} };`)();
const A = api.analyse(api.EXAMPLE, null, new Date(2026, 10, 8));

// Expected values come from: python -m kiwiwealth report examples/profile.example.yaml
const expected = { ksAt: 717773, wpAt: 354401, invAt: 1073195 };
let failed = false;
for (const [k, v] of Object.entries(expected)) {
  const got = Math.round(A.ret[k]);
  const ok = Math.abs(got - v) <= 1;
  console.log(`${ok ? "ok  " : "FAIL"} ${k}: ${got} (python ${v})`);
  failed ||= !ok;
}
process.exit(failed ? 1 : 0);
