(P) => {
  // Browser-side loop, same transport as V1: a page on http://localhost:11434 fetches /api/chat.
  // P = {run_id, phase, payload_sha256, timeout_ms, max_retries, models_to_show, calls: [{call_id, body}]}.
  // Each body is the frozen request string built by the harness; it is sent verbatim and hashed.
  if (window.__llm03 && window.__llm03.running) { return 'ALREADY_RUNNING ' + window.__llm03.run_id; }
  const S = window.__llm03 = { run_id: P.run_id, phase: P.phase, payload_sha256: P.payload_sha256,
    running: true, aborted: false, started_at: new Date().toISOString(), finished_at: null,
    total_calls: P.calls.length, done_calls: 0, env: {}, records: [], fatal: null };
  const hex = (buf) => Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
  const sha = async (s) => hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)));
  async function call(bodyStr) {
    const attempts = [];
    for (let a = 0; a <= P.max_retries; a++) {
      const ac = new AbortController();
      const timer = setTimeout(() => ac.abort(), P.timeout_ms);
      const t0 = performance.now();
      try {
        const r = await fetch('/api/chat', { method: 'POST', body: bodyStr, signal: ac.signal });
        const txt = await r.text();
        clearTimeout(timer);
        let j = null; try { j = JSON.parse(txt); } catch (e) {}
        if (!r.ok || !j || j.error) {
          attempts.push({ attempt: a, status: 'HTTP_ERROR', http: r.status, error: (j && j.error) || txt.slice(0, 500), ms: Math.round(performance.now() - t0) });
          continue;
        }
        attempts.push({ attempt: a, status: 'OK', ms: Math.round(performance.now() - t0) });
        return { call_status: 'OK', request_sha256: await sha(bodyStr), attempts,
          content: j.message ? j.message.content : null, thinking: j.message ? (j.message.thinking || null) : null,
          done_reason: j.done_reason, eval_count: j.eval_count, prompt_eval_count: j.prompt_eval_count,
          total_duration_ns: j.total_duration, load_duration_ns: j.load_duration, response_model: j.model };
      } catch (e) {
        clearTimeout(timer);
        attempts.push({ attempt: a, status: e.name === 'AbortError' ? 'TIMEOUT' : 'TRANSPORT_ERROR', error: String(e), ms: Math.round(performance.now() - t0) });
      }
    }
    const last = attempts[attempts.length - 1];
    return { call_status: last.status, request_sha256: await sha(bodyStr), attempts, content: null };
  }
  (async () => {
    try {
      S.env.version = await (await fetch('/api/version')).json();
      S.env.tags = await (await fetch('/api/tags')).json();
      S.env.user_agent = navigator.userAgent;
      for (const name of P.models_to_show) {
        try { const j = await (await fetch('/api/show', { method: 'POST', body: JSON.stringify({ model: name }) })).json();
              S.env['show_' + name] = { details: j.details, parameters: j.parameters, modified_at: j.modified_at }; }
        catch (e) { S.env['show_' + name] = { error: String(e) }; }
      }
      for (const c of P.calls) {
        if (S.aborted) throw new Error('aborted by operator');
        const ts = new Date().toISOString();
        const r = await call(c.body);
        S.records.push(Object.assign({ call_id: c.call_id, ts }, r));
        S.done_calls++;
      }
    } catch (e) { S.fatal = String(e); }
    S.running = false; S.finished_at = new Date().toISOString();
  })();
  return 'STARTED ' + P.run_id + ' phase=' + P.phase + ' total_calls=' + S.total_calls;
}
