// Runs static/app.js against a tiny fake DOM and checks the "days this job should NOT happen" picker.
// Run:  node core/tests/js/skip_dates.test.js
const fs = require('fs'), path = require('path'), assert = require('assert');
const src = fs.readFileSync(path.join(__dirname, '..', '..', '..', 'static', 'app.js'), 'utf8');

function el(tag = 'div', props = {}) {
  const e = { tag, children: [], listeners: {}, hidden: false, dataset: {}, value: '', className: '', textContent: '', attrs: {}, ...props };
  e.addEventListener = (t, f) => { (e.listeners[t] = e.listeners[t] || []).push(f); };
  e.appendChild = c => { e.children.push(c); return c; };
  e.replaceChildren = () => { e.children = []; };
  e.setAttribute = (k, v) => { e.attrs[k] = v; };
  e.focus = () => { e.focused = true; };
  e.fire = (t, ev = {}) => (e.listeners[t] || []).forEach(f => f({ target: e, preventDefault() { ev.prevented = true; }, ...ev }));
  return e;
}
function build(initial) {
  const text = el('input', { value: initial }), box = el(), picker = el('input'), addBtn = el('button'), chips = el(), empty = el('small');
  const jf = { querySelector: s => s === 'input[name=skip_dates]' ? text : null, querySelectorAll: () => [] };
  const byId = { jobform: jf, 'skip-widget': box, 'skip-picker': picker, 'skip-add': addBtn, 'skip-chips': chips, 'skip-empty': empty };
  box.hidden = true;
  global.document = { getElementById: id => byId[id] || null, createElement: t => el(t), addEventListener() {} };
  global.window = { confirm: () => true };
  new Function(src)();
  const labels = () => chips.children.map(c => c.children[0].textContent);
  const pick = v => { picker.value = v; addBtn.fire('click'); };
  return { text, box, picker, addBtn, chips, empty, labels, pick };
}

// 1. progressive enhancement: the widget appears, the plain text field is hidden
let f = build('');
assert.strictEqual(f.box.hidden, false); assert.strictEqual(f.text.hidden, true);
assert.strictEqual(f.empty.hidden, false); assert.strictEqual(f.chips.children.length, 0);

// 2. add separate (non-consecutive) days, in any order -> sorted, one chip each, value is what gets submitted
f.pick('2026-11-10'); f.pick('2026-11-03'); f.pick('2026-12-25');
assert.strictEqual(f.text.value, '2026-11-03, 2026-11-10, 2026-12-25');
assert.strictEqual(f.chips.children.length, 3); assert.strictEqual(f.empty.hidden, true);
assert.match(f.labels()[0], /Tue.*03.*Nov.*2026/);

// 3. duplicates are ignored, junk is ignored, picker is cleared after a good add
f.pick('2026-11-03'); assert.strictEqual(f.chips.children.length, 3);
f.pick(''); f.pick('not-a-date'); f.pick('2026-02-30'); assert.strictEqual(f.chips.children.length, 3);
f.pick('2027-01-01'); assert.strictEqual(f.picker.value, '');

// 4. removing a chip with its x button
const x = f.chips.children[1].children[1];            // 2026-11-10
assert.strictEqual(x.dataset.day, '2026-11-10'); assert.match(x.attrs['aria-label'], /Remove/);
f.chips.fire('click', { target: x });
assert.strictEqual(f.text.value, '2026-11-03, 2026-12-25, 2027-01-01');
f.chips.fire('click', { target: f.chips });             // clicking the empty area does nothing
assert.strictEqual(f.chips.children.length, 3);

// 5. Enter in the picker adds the day and must NOT submit the job form
f.picker.value = '2027-02-14'; const ev = {}; f.picker.fire('keydown', Object.assign(ev, { key: 'Enter' }));
assert.strictEqual(ev.prevented, true); assert.match(f.text.value, /2027-02-14$/);
const ev2 = {}; f.picker.fire('keydown', Object.assign(ev2, { key: 'a' })); assert.strictEqual(ev2.prevented, undefined);

// 6. editing an existing job: the stored days come back as chips; garbage in the field is dropped
f = build('2026-12-01, 2026-11-05;2026-11-05  junk 2026-13-40');
assert.strictEqual(f.text.value, '2026-11-05, 2026-12-01'); assert.strictEqual(f.chips.children.length, 2);

// 7. remove everything -> empty hint is back and the submitted value is empty
f.chips.fire('click', { target: f.chips.children[0].children[1] }); f.chips.fire('click', { target: f.chips.children[0].children[1] });
assert.strictEqual(f.text.value, ''); assert.strictEqual(f.empty.hidden, false);

// 8. pages without the widget (project task form, other pages) must not throw
global.document = { getElementById: () => null, createElement: () => el(), addEventListener() {} };
new Function(src)();
console.log('skip_dates: 8 groups of checks passed');
