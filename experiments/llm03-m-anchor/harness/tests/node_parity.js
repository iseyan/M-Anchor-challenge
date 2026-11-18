// Runs transport/browser_loop.js under Node with a stand-in fetch, for the parity test.
// usage: node node_parity.js <bundle.json> <responses.json> <out_state.json> [wrong-digest]
// responses.json maps request SHA-256 -> {content, done_reason, eval_count, prompt_eval_count, model}.
// A request whose body the Python side never produced has no entry and fails the call, so a single
// divergent byte in a JS-built body shows up as an HTTP error and as a hash mismatch at ingest.
const fs = require('fs');
const crypto = require('crypto');
const [bundlePath, respPath, outPath, wrong] = process.argv.slice(2);
const B = JSON.parse(fs.readFileSync(bundlePath, 'utf8'));
const R = JSON.parse(fs.readFileSync(respPath, 'utf8'));
let chatCalls = 0;
const res = (obj, ok = true, status = 200) => ({ ok, status, text: async () => JSON.stringify(obj), json: async () => obj });
globalThis.window = globalThis;
globalThis.fetch = async (url, init) => {
  if (url === '/api/version') { return res({ version: B.payload.expected_env.version }); }
  if (url === '/api/tags') {
    const models = Object.entries(B.payload.expected_env.digests).map(([name, digest]) => ({ name, digest: wrong ? 'x' + digest.slice(1) : digest }));
    return res({ models });
  }
  if (url === '/api/show') { return res({ details: {}, parameters: '' }); }
  if (url === '/api/chat') {
    chatCalls++;
    const h = crypto.createHash('sha256').update(init.body, 'utf8').digest('hex');
    const r = R[h];
    if (!r) { return res({ error: 'no response for body ' + h }, false, 500); }
    return res({ model: r.model, message: { role: 'assistant', content: r.content }, done_reason: r.done_reason,
                 eval_count: r.eval_count, prompt_eval_count: r.prompt_eval_count, total_duration: 1, load_duration: 0 });
  }
  return res({ error: 'unknown url ' + url }, false, 404);
};
const LOOP = (0, eval)('(' + B.loop_js + ')');
const started = LOOP(B.payload);
(function wait() {
  if (window.__llm03.running) { return setTimeout(wait, 5); }
  fs.writeFileSync(outPath, JSON.stringify(window.__llm03));
  console.log(JSON.stringify({ started, chat_calls: chatCalls, records: window.__llm03.records.length, fatal: window.__llm03.fatal }));
})();
