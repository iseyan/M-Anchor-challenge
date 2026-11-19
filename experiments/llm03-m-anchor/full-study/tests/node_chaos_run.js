'use strict';
// node tests/node_chaos_run.js <python> <driver.py> <campaign> <shard> <split> <responses.json> <kill_prob> <seed> [cases]
// Runs a shard through runner/transport.js with a mock Ollama; kills the driver at random points with
// probability kill_prob per point and restarts until the shard is done. Prints a JSON summary.
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { runShard } = require(path.join(__dirname, '..', 'runner', 'transport.js'));

const [python, driver, campaign, shard, split, respPath, killProb, seed, cases] = process.argv.slice(2);
const responses = JSON.parse(fs.readFileSync(respPath, 'utf8'));
let s = Number(seed) >>> 0;
const rand = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
const sha = (t) => crypto.createHash('sha256').update(t, 'utf8').digest('hex');
const h = (text, salt) => parseInt(sha(salt + text).slice(0, 12), 16);
function injected(digest, attempt) {               // same rule as runner/mock.py attempt_status
  const v = h(digest, 'transport');
  if (v % 97 === 0) return { status: 'HTTP_ERROR', http: 500, error: 'prediction aborted, token repeat limit reached (mock)', ms: 5 };
  if (attempt === 0 && v % 23 === 0) return { status: 'TIMEOUT', error: 'AbortError (mock)', ms: 120000 };
  return null;
}
const fetches = {};
const env = { version: { version: '0.40.16' }, tags: { models: [] }, user_agent: 'node-mock' };
(async () => {
  const cfgDigests = JSON.parse(process.env.EXPECTED_DIGESTS || '{}');
  env.tags.models = Object.entries(cfgDigests).map(([name, digest]) => ({ name, digest }));
  let runs = 0, kills = 0, final = null;
  const points = {};
  while (runs < 500) {
    runs++;
    const args = ['--campaign', campaign, '--shard', shard, '--split', split].concat(cases ? ['--cases', cases] : []);
    const r = await runShard({
      python, driver, args,
      fetchEnv: async () => env,
      fetchAttempt: async ({ body, attempt }) => {
        const d = sha(body);
        fetches[d] = (fetches[d] || 0) + 1;
        const bad = injected(d, attempt);
        if (bad) return bad;
        if (!(d in responses) || responses[d] === null) return { status: 'HTTP_ERROR', http: 500, error: 'mock has no response', ms: 1 };
        return { status: 'OK', ms: 1, response: responses[d] };
      },
      chaos: (point) => { if (rand() < Number(killProb)) { points[point] = (points[point] || 0) + 1; return 'kill'; } },
    });
    if (r.killed) { kills++; continue; }
    final = r.final;
    if (!final || final.type !== 'stopped') break;
  }
  process.stdout.write(JSON.stringify({ runs, kills, kill_points: points, final, fetch_total: Object.values(fetches).reduce((a, b) => a + b, 0), fetches }));
})();
