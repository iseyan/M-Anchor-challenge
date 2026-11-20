// Page loop for runner/serve.py. Paste or evaluate this inside the Ollama page (http://localhost:11434):
//   startFullStudyLoop({ service: 'http://127.0.0.1:8765', models: ['qwen3:4b-q4_K_M', 'gemma3:4b'] })
// With persist: true the loop keeps waiting after a shard is done, for the next shard's service (stopped/fatal still end it).
// It asks the local service for the next message, sends chat attempts with the page's own
// fetch('/api/chat') exactly as the V1 loop did, and posts the result. State: window.__fsrun.
// The loop never retries by itself and never decides the order; the service (driver) does both.
async function startFullStudyLoop(opts) {
  if (window.__fsrun && window.__fsrun.running) return 'ALREADY_RUNNING ' + window.__fsrun.client;
  const S = window.__fsrun = { running: true, client: (crypto.randomUUID ? crypto.randomUUID() : String(Math.random())).slice(0, 12),
    started_at: new Date().toISOString(), handled: 0, attempts: 0, polls: 0, service_errors: 0, last: null, final: null };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const attempt = async ({ body, timeout_ms }) => {        // identical request and result fields to the V1 call()
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
  const env = async () => {
    const e = {};
    e.version = await (await fetch('/api/version')).json();
    e.tags = await (await fetch('/api/tags')).json();
    e.user_agent = navigator.userAgent;
    for (const name of opts.models) {
      try { const j = await (await fetch('/api/show', { method: 'POST', body: JSON.stringify({ model: name }) })).json();
            e['show_' + name] = { details: j.details, parameters: j.parameters, modified_at: j.modified_at }; }
      catch (x) { e['show_' + name] = { error: String(x) }; }
    }
    return e;
  };
  const reply = async (msg) => {
    for (let k = 0; k < 5; k++) {
      try { const r = await fetch(opts.service + '/reply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(msg) });
            return r.status; }
      catch (e) { S.service_errors++; await sleep(2000); }
    }
    return 0;
  };
  (async () => {
    while (S.running) {
      let m;
      try { m = await (await fetch(opts.service + '/next?client=' + S.client)).json(); S.polls++; }
      catch (e) { S.service_errors++; await sleep(3000); continue; }      // service restarting
      S.last = m.type;
      if (m.type === 'wait') continue;
      if (m.type === 'locked') { S.locked_waits = (S.locked_waits || 0) + 1; await sleep(opts.locked_wait_ms || 10000); continue; }  // another page holds the lease
      if (m.type === 'env_request') { await reply({ type: 'env', token: m.token, env: await env() }); S.handled++; continue; }
      if (m.type === 'attempt') {
        S.attempts++;
        const r = await attempt(m);
        await reply(Object.assign({ type: 'attempt_result', token: m.token, call_id: m.call_id, attempt: m.attempt }, r));
        S.handled++;
        continue;
      }
      S.final = m;                                                     // done / stopped / fatal
      if (opts.persist && m.type === 'done') {                         // next shard's service starts shortly
        S.finals = (S.finals || []).concat([m]);
        await sleep(5000);
        continue;
      }
      break;
    }
    S.running = false;
    S.finished_at = new Date().toISOString();
  })();
  return 'STARTED ' + S.client;
}
