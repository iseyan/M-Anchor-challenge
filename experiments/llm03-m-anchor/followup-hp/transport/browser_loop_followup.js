(P) => {
  // Follow-up loop (followup-hp), derived from harness/transport/browser_loop.js; same transport as V1:
  // a page on http://localhost:11434 fetches /api/chat. Stage 1 is NOT called: each trial carries the stored
  // stage-1 output of the source run (s1_content). Order per trial: S2 for C3R, C4R, H, P -> RX for each
  // sendable handoff in the same condition order. Stage-2 and receiver bodies are built here by a port of
  // lib/anchor.py, lib/prompts.py and followup-hp/fu.py; the ingest step rebuilds every body in Python and
  // compares SHA-256. State lives in window.__llm03fu so it never overwrites the main run's window.__llm03.
  if (window.__llm03fu && window.__llm03fu.running) { return 'ALREADY_RUNNING ' + window.__llm03fu.run_id; }
  const S = window.__llm03fu = { run_id: P.run_id, payload_sha256: P.payload_sha256, running: true, aborted: false,
    started_at: new Date().toISOString(), finished_at: null, total_trials: P.trials.length, done_trials: 0,
    done_calls: 0, env: {}, env_check: null, records: [], fatal: null };
  const T = P.templates;
  const isDict = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);
  const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
  const STATUSES = ['confirmed', 'unconfirmed', 'refuted'];
  const hex = (buf) => Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
  const sha = async (s) => hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)));
  const rep = (s, k, v) => s.split(k).join(v);

  function bodyStr(model, messages, seed) {
    const b = { model: model.name, messages: messages, stream: P.backend.stream, format: P.backend.format,
      keep_alive: P.backend.keep_alive, options: Object.assign({}, P.options, { seed: seed }) };
    if (model.think !== null && model.think !== undefined) { b.think = model.think; }
    return JSON.stringify(b);
  }
  function parseJson(content) {                       // V1 rule
    if (content === null || content === undefined) { return null; }
    const s = content.trim().replace(/^```[a-zA-Z]*\s*|\s*```$/g, '').trim();
    try { return JSON.parse(s); } catch (e) {}
    const i = s.indexOf('{'), j = s.lastIndexOf('}');
    if (i !== -1 && j > i) { try { return JSON.parse(s.slice(i, j + 1)); } catch (e) {} }
    return null;
  }
  const normStatus = (v) => (typeof v === 'string' ? v.trim().toLowerCase() : null);
  function buildState(c, obj) {                       // port of lib/anchor.py build_state
    const kinds = c.doc_kinds;
    const claimsIn = isDict(obj) ? obj.claims : null;
    const out = [];
    for (const cl of c.claims) {
      const item = isDict(claimsIn) ? (has(claimsIn, cl.key) ? claimsIn[cl.key] : null) : null;
      const stated = isDict(item) ? normStatus(item.status) : null;
      const ev = isDict(item) ? item.evidence : null;
      const cited = Array.isArray(ev) ? ev.filter(e => typeof e === 'string' && has(kinds, e)) : [];
      const admitted = cited.filter(e => cl.admissible_kinds.includes(kinds[e]));
      let status, reason;
      if (!STATUSES.includes(stated)) { status = 'unconfirmed'; reason = '評価なし'; }
      else if (stated === 'unconfirmed') { status = 'unconfirmed'; reason = '前工程で未決'; }
      else if (admitted.length) { status = stated; reason = '根拠 ' + admitted.join('・'); }
      else {
        status = 'unconfirmed';
        const need = cl.admissible_kinds.map(k => T.kind_label[k]).join('・') || '確定に使える資料';
        const got = cited.map(e => e + '〔' + T.kind_label[kinds[e]] + '〕').join('・') || '根拠の指定なし';
        reason = cl.admissible_kinds.length ? (got + 'は' + need + 'ではない') : (need + 'がない');
      }
      out.push([cl.key, status, reason]);
    }
    return out;
  }
  const renderBlock = (state) => [T.c4_header].concat(state.map(([k, s, r]) => '- ' + k + ': ' + s + '（' + r + '）')).join('\n');
  function stage2Base(c) {                            // port of lib/prompts.py stage2_base
    const keys = c.claims.map(cl => cl.key);
    const example = '{' + keys.map(k => '"' + k + '": "状態"').join(', ') + '}';
    return rep(rep(T.stage2_base, '{claim_keys}', keys.join('、')), '{claims_example}', example);
  }
  function stage2Instruction(cond, state, c) {            // port of followup-hp/fu.py instruction
    const kind = P.stage2_block[cond];
    const base = stage2Base(c);
    if (kind === null) { return base; }
    if (kind === 'anchor') { return renderBlock(state) + '\n\n' + base; }
    if (kind === 'anchor_header') { return renderBlock([]) + '\n\n' + base; }
    if (kind === 'anchor_permit') { return renderBlock(state) + '\n' + T.permit_sentence + '\n\n' + base; }
    throw new Error('unknown stage-2 block ' + kind);
  }
  function handoffClaims(rec) {
    if (!rec || rec.call_status !== 'OK' || rec.content === null || rec.content === undefined) { return null; }
    const obj = parseJson(rec.content);
    const h = isDict(obj) ? obj.handoff : null;
    const c = isDict(h) ? h.claims : null;
    return isDict(c) ? c : null;
  }
  function renderHandoff(c, claims) {
    return c.claims.map(cl => {
      let shown;
      if (!has(claims, cl.key)) { shown = T.missing; }
      else if (typeof claims[cl.key] === 'string') { shown = claims[cl.key]; }
      else { shown = T.invalid; }
      return '- ' + cl.key + ': ' + shown;
    }).join('\n');
  }
  function receiverMessages(c, claims) {
    let u = T.receiver_user_template;
    u = rep(u, '{request_summary}', c.request_summary);
    u = rep(u, '{handoff}', renderHandoff(c, claims));
    u = rep(u, '{claims_block}', c.claims_block);
    return [{ role: 'system', content: T.receiver_system }, { role: 'user', content: u.trim() }];
  }
  async function call(bodyStr) {
    const attempts = [];
    for (let a = 0; a <= P.max_retries; a++) {
      const ac = new AbortController();
      const timer = setTimeout(() => ac.abort(), P.timeout_ms);
      const t0 = performance.now();
      try {
        const r = await fetch(P.endpoint, { method: 'POST', body: bodyStr, signal: ac.signal });
        const txt = await r.text();
        clearTimeout(timer);
        let j = null; try { j = JSON.parse(txt); } catch (e) {}
        if (!r.ok || !j || j.error) {
          attempts.push({ attempt: a, status: 'HTTP_ERROR', http: r.status, error: String((j && j.error) || txt).slice(0, 500), ms: Math.round(performance.now() - t0) });
          continue;
        }
        attempts.push({ attempt: a, status: 'OK', ms: Math.round(performance.now() - t0) });
        return { call_status: 'OK', attempts, content: j.message ? j.message.content : null,
          thinking: j.message ? (j.message.thinking || null) : null, done_reason: j.done_reason,
          eval_count: j.eval_count, prompt_eval_count: j.prompt_eval_count, total_duration_ns: j.total_duration,
          load_duration_ns: j.load_duration, response_model: j.model };
      } catch (e) {
        clearTimeout(timer);
        attempts.push({ attempt: a, status: e.name === 'AbortError' ? 'TIMEOUT' : 'TRANSPORT_ERROR', error: String(e), ms: Math.round(performance.now() - t0) });
      }
    }
    return { call_status: attempts[attempts.length - 1].status, attempts, content: null };
  }
  async function send(meta, model, messages, seed) {
    if (S.aborted) { throw new Error('aborted by operator'); }
    const b = bodyStr(model, messages, seed);
    const ts = new Date().toISOString();
    const r = await call(b);
    const rec = Object.assign({ call_id: meta.call_id, trial_id: meta.trial_id, stage: meta.stage, condition: meta.condition,
      model: model.name, seed: seed, ts: ts, request_sha256: await sha(b) }, r);
    S.records.push(rec);
    S.done_calls++;
    return rec;
  }
  (async () => {
    try {
      S.env.version = await (await fetch('/api/version')).json();
      S.env.tags = await (await fetch('/api/tags')).json();
      S.env.user_agent = navigator.userAgent;
      for (const name of Object.keys(P.expected_env.digests)) {
        try { const j = await (await fetch('/api/show', { method: 'POST', body: JSON.stringify({ model: name }) })).json();
              S.env['show_' + name] = { details: j.details, parameters: j.parameters, modified_at: j.modified_at }; }
        catch (e) { S.env['show_' + name] = { error: String(e) }; }
      }
      const found = {}; for (const m of (S.env.tags.models || [])) { found[m.name] = m.digest; }
      const mism = [];
      if (S.env.version.version !== P.expected_env.version) { mism.push('version ' + S.env.version.version); }
      for (const [n, d] of Object.entries(P.expected_env.digests)) { if (found[n] !== d) { mism.push(n + ' digest ' + found[n]); } }
      S.env_check = { matches: mism.length === 0, mismatches: mism };
      if (mism.length) { throw new Error('ENV_MISMATCH ' + mism.join('; ')); }
      for (const t of P.trials) {
        const c = t.case;
        const s1 = t.s1_content;                          // stored stage-1 output of the source run
        const state = buildState(c, parseJson(s1));
        const s2 = {};
        for (const cond of P.conditions) {
          const msgs = t.s1_messages.concat([{ role: 'assistant', content: s1 }, { role: 'user', content: stage2Instruction(cond, state, c) }]);
          s2[cond] = await send({ call_id: 'S2|' + t.trial_id + '|' + cond, trial_id: t.trial_id, stage: 2, condition: cond }, t.sender, msgs, t.seed);
        }
        for (const cond of P.conditions) {
          const claims = handoffClaims(s2[cond]);
          if (claims !== null) {
            await send({ call_id: 'RX|' + t.trial_id + '|' + cond, trial_id: t.trial_id, stage: 3, condition: cond }, P.receiver, receiverMessages(c, claims), t.seed);
          }
        }
        S.done_trials++;
      }
    } catch (e) { S.fatal = String(e); }
    S.running = false; S.finished_at = new Date().toISOString();
  })();
  return 'STARTED ' + P.run_id + ' trials=' + P.trials.length;
}
