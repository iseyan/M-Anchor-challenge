'use strict';
// node tests/node_page_sim.js <python> <serve.py> <campaign> <shard> <split> <responses.json> <port> <seed> <kill_prob> [cases]
// Runs runner/page_loop.js in a simulated page against runner/serve.py with a mock Ollama. With
// probability kill_prob per handled attempt, either the service is killed and restarted, or the page
// is "reloaded" (old loop abandoned mid-attempt, a new loop with a new client id is started).
const { spawn } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const [python, serve, campaign, shard, split, respPath, port, seed, killProb, cases] = process.argv.slice(2);
const responses = JSON.parse(fs.readFileSync(respPath, 'utf8'));
const digests = JSON.parse(process.env.EXPECTED_DIGESTS || '{}');
const svc = 'http://127.0.0.1:' + port;
let s = Number(seed) >>> 0;
const rand = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
const sha = (t) => crypto.createHash('sha256').update(t, 'utf8').digest('hex');
const h = (text, salt) => parseInt(sha(salt + text).slice(0, 12), 16);
function injected(d, attempt) {
  const v = h(d, 'transport');
  if (v % 97 === 0) return { ok: false, status: 500, body: { error: 'prediction aborted, token repeat limit reached (mock)' } };
  if (attempt === 0 && v % 23 === 0) return 'timeout';
  return null;
}
const sent = {};                                    // request sha -> number of chat requests received
let server = null, serverExit = null, restarts = 0, reloads = 0;
function startServer() {
  const args = ['-B', '-I', serve, '--campaign', campaign, '--shard', shard, '--split', split, '--port', port].concat(cases ? ['--cases', cases] : []);
  server = spawn(python, args, { stdio: ['ignore', 'pipe', 'pipe'], env: Object.assign({}, process.env, { FS_LEASE_S: '1', FS_POLL_S: '1' }) });
  const me = server;
  me.stdout.setEncoding('utf8');
  me.on('exit', (code) => { if (me === server) serverExit = code; });
}
const attemptCounter = {};
async function mockFetch(url, init) {
  if (url === '/api/version') return resp(200, { version: '0.40.16' });
  if (url === '/api/tags') return resp(200, { models: Object.entries(digests).map(([name, digest]) => ({ name, digest })) });
  if (url === '/api/show') return resp(200, {});
  if (url === '/api/chat') {
    const d = sha(init.body);
    sent[d] = (sent[d] || 0) + 1;
    const a = attemptCounter[d] = (attemptCounter[d] === undefined ? 0 : attemptCounter[d] + 1);
    const bad = injected(d, a);
    await new Promise((r) => setTimeout(r, 2));
    if (bad === 'timeout') { const e = new Error('aborted'); e.name = 'AbortError'; throw e; }
    if (bad) return resp(bad.status, bad.body);
    if (!(d in responses) || responses[d] === null) return resp(500, { error: 'mock has no response' });
    return resp(200, responses[d]);
  }
  return fetch(url, init);                          // the local service (real HTTP)
}
function resp(status, obj) { const txt = JSON.stringify(obj); return { ok: status < 400, status, text: async () => txt, json: async () => JSON.parse(txt) }; }
function newPage() {
  const win = {};
  const ctx = { window: win, fetch: mockFetch, crypto: { randomUUID: () => crypto.randomUUID() }, performance, navigator: { userAgent: 'node-sim' },
                setTimeout, clearTimeout, AbortController, console, JSON, Math, Date, String, Object, Promise };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'runner', 'page_loop.js'), 'utf8'), ctx);
  return ctx;
}
(async () => {
  startServer();
  await new Promise((r) => setTimeout(r, 1500));
  let page = newPage();
  await page.startFullStudyLoop({ service: svc, models: Object.keys(digests), locked_wait_ms: 300 });
  let lastHandled = 0, guard = 0;
  while (guard++ < 200000) {
    await new Promise((r) => setTimeout(r, 5));
    const S = page.window.__fsrun;
    if (S.final && ['done', 'fatal', 'stopped'].includes(S.final.type)) break;
    if (serverExit !== null) {                       // service finished or died
      if (serverExit === 0 && S.final) break;
      serverExit = null; restarts++; startServer(); await new Promise((r) => setTimeout(r, 1200));
      continue;
    }
    if (S.handled !== lastHandled) {
      lastHandled = S.handled;
      if (rand() < Number(killProb)) {
        if (rand() < 0.5) { const old = server; serverExit = null; server = null; old.kill('SIGKILL'); restarts++; await new Promise((r) => setTimeout(r, 300)); startServer(); await new Promise((r) => setTimeout(r, 1200)); }
        else { S.running = false; reloads++; page = newPage(); await page.startFullStudyLoop({ service: svc, models: Object.keys(digests), locked_wait_ms: 300 }); lastHandled = 0; }
      }
    }
  }
  const S = page.window.__fsrun;
  if (server) { try { server.kill('SIGKILL'); } catch (e) {} }
  process.stdout.write(JSON.stringify({ restarts, reloads, final: S.final, chat_requests: Object.values(sent).reduce((a, b) => a + b, 0) }));
  process.exit(0);
})();
