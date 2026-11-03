// Runs static/app.js against a tiny fake DOM and checks the One time / Repeats switch.
// Run:  node core/tests/js/kind_toggle.test.js
const fs = require('fs'), path = require('path'), assert = require('assert');
const src = fs.readFileSync(path.join(__dirname, '..', '..', '..', 'static', 'app.js'), 'utf8');

function el(name, attrs = {}) {
  const e = { name, ...attrs, hidden: false, listeners: {}, textContent: '', dataset: attrs.dataset || {} };
  e.addEventListener = (t, f) => { (e.listeners[t] = e.listeners[t] || []).push(f); };
  e.fire = t => (e.listeners[t] || []).forEach(f => f({ target: e }));
  return e;
}
function build({ checked }) {
  const once = el('once-box'), rep = el('repeat-box'), summary = el('summary');
  const rOnce = el('kind', { value: 'once', checked: checked === 'once' }), rRep = el('kind', { value: 'repeat', checked: checked === 'repeat' });
  const every = el('every', { value: '' }), unit = el('unit', { value: '' }), end = el('end_date', { value: '' });
  const preset = el('btn', { dataset: { preset: '2:week' } });
  const fields = { every, unit, end_date: end };
  const jf = {
    querySelectorAll: s => s === 'input[name=kind]' ? [rOnce, rRep] : s === '[data-preset]' ? [preset] : [],
    querySelector: s => { const m = s.match(/^\[name=(\w+)\]$/); return m ? fields[m[1]] || null : null; },
  };
  const byId = { jobform: jf, 'once-box': once, 'repeat-box': rep, summary };
  global.document = { getElementById: id => byId[id] || null, addEventListener() {} };
  global.window = { confirm: () => true };
  new Function(src)();
  return { once, rep, summary, rOnce, rRep, every, unit, end, preset };
}

// 1. new job form: "one time" is selected -> due-date box visible, schedule box hidden
let f = build({ checked: 'once' });
assert.strictEqual(f.once.hidden, false); assert.strictEqual(f.rep.hidden, true);
assert.match(f.summary.textContent, /one time/);

// 2. user picks "Repeats" -> boxes swap
f.rOnce.checked = false; f.rRep.checked = true; f.rRep.fire('change');
assert.strictEqual(f.once.hidden, true); assert.strictEqual(f.rep.hidden, false);
assert.match(f.summary.textContent, /Repeats every week/);

// 3. preset chip "every 2 weeks"
f.preset.listeners.click[0]({});
assert.strictEqual(f.every.value, '2'); assert.strictEqual(f.unit.value, 'week');
assert.match(f.summary.textContent, /every 2 weeks/);

// 4. end date shows in the summary
f.end.value = '2027-01-31'; f.end.fire('input');
assert.match(f.summary.textContent, /until 2027-01-31/);

// 5. back to one time
f.rRep.checked = false; f.rOnce.checked = true; f.rOnce.fire('change');
assert.strictEqual(f.once.hidden, false); assert.strictEqual(f.rep.hidden, true);

// 6. editing an existing repeating job (form re-rendered with kind=repeat) opens on the schedule box
f = build({ checked: 'repeat' });
assert.strictEqual(f.once.hidden, true); assert.strictEqual(f.rep.hidden, false);

// 7. nothing selected at all -> falls back to "one time" instead of crashing
f = build({ checked: null });
assert.strictEqual(f.rOnce.checked, true); assert.strictEqual(f.once.hidden, false);

// 8. page without the job form -> script must not throw
global.document = { getElementById: () => null, addEventListener() {} };
new Function(src)();
console.log('kind_toggle: 8 checks passed');
