'use strict';
// Transport for runner/driver.py. One request at a time, strictly in the driver's order.
// In production `fetchAttempt` and `fetchEnv` run inside the Ollama page (http://localhost:11434) with
// page.evaluate, so every chat request is the same browser fetch('/api/chat') as the V1 loop; see
// playwright.js. In tests they are mocks. Retry policy and persistence live in the driver.
const { spawn } = require('child_process');

function runShard(opts) {
  // opts: python, driver, args[], fetchEnv() -> env, fetchAttempt({body, timeout_ms, call_id, attempt})
  //       -> {status, http?, error?, ms, response?}, chaos(point, info) -> 'kill' | undefined, log(msg)
  return new Promise((resolve) => {
    const p = spawn(opts.python, ['-B', '-I', opts.driver, ...opts.args], { stdio: ['pipe', 'pipe', 'pipe'] });
    const stderr = [];
    let buf = '';
    let busy = Promise.resolve();
    let final = null;
    let killed = false;
    const log = opts.log || (() => {});
    const kill = (why) => { if (!killed) { killed = true; log({ type: 'chaos_kill', why }); p.kill('SIGKILL'); } };
    const send = (msg) => { if (!killed) p.stdin.write(JSON.stringify(msg) + '\n'); };
    async function handle(msg) {
      if (killed) return;
      if (msg.type === 'env_request') { send({ type: 'env', env: await opts.fetchEnv() }); return; }
      if (msg.type === 'attempt') {
        if (opts.chaos && opts.chaos('before_fetch', msg) === 'kill') return kill('before_fetch');
        const r = await opts.fetchAttempt({ body: msg.body, timeout_ms: msg.timeout_ms, call_id: msg.call_id, attempt: msg.attempt });
        if (opts.chaos && opts.chaos('after_fetch', msg) === 'kill') return kill('after_fetch');
        send(Object.assign({ type: 'attempt_result', call_id: msg.call_id, attempt: msg.attempt }, r));
        if (opts.chaos && opts.chaos('after_result', msg) === 'kill') return kill('after_result');
        return;
      }
      final = msg;
      log(msg);
    }
    p.stdout.setEncoding('utf8');
    p.stdout.on('data', (chunk) => {
      buf += chunk;
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i);
        buf = buf.slice(i + 1);
        if (!line.trim()) continue;
        const msg = JSON.parse(line);
        busy = busy.then(() => handle(msg)).catch((e) => { final = { type: 'fatal', error: 'transport: ' + String(e) }; kill('transport_error'); });
      }
    });
    p.stderr.setEncoding('utf8');
    p.stderr.on('data', (d) => stderr.push(d));
    p.on('close', (code) => { busy.then(() => resolve({ code, killed, final, stderr: stderr.join('') })); });
  });
}

// The single-attempt fetch run INSIDE the Ollama page. Same request and result fields as the V1 call().
const PAGE_ATTEMPT = async ({ body, timeout_ms }) => {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeout_ms);
  const t0 = performance.now();
  try {
    const r = await fetch('/api/chat', { method: 'POST', body, signal: ac.signal });
    const txt = await r.text();
    clearTimeout(timer);
    let j = null; try { j = JSON.parse(txt); } catch (e) {}
    const ms = Math.round(performance.now() - t0);
    if (!r.ok || !j || j.error) return { status: 'HTTP_ERROR', http: r.status, error: String((j && j.error) || txt).slice(0, 500), ms };
    return { status: 'OK', ms, response: j };
  } catch (e) {
    clearTimeout(timer);
    return { status: e.name === 'AbortError' ? 'TIMEOUT' : 'TRANSPORT_ERROR', error: String(e), ms: Math.round(performance.now() - t0) };
  }
};

// Environment as the V1 loop collected it.
const PAGE_ENV = async (names) => {
  const env = {};
  env.version = await (await fetch('/api/version')).json();
  env.tags = await (await fetch('/api/tags')).json();
  env.user_agent = navigator.userAgent;
  for (const name of names) {
    try { const j = await (await fetch('/api/show', { method: 'POST', body: JSON.stringify({ model: name }) })).json();
          env['show_' + name] = { details: j.details, parameters: j.parameters, modified_at: j.modified_at }; }
    catch (e) { env['show_' + name] = { error: String(e) }; }
  }
  return env;
};

module.exports = { runShard, PAGE_ATTEMPT, PAGE_ENV };
