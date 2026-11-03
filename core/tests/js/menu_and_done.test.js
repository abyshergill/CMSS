// Runs static/app.js against a tiny fake DOM: the hide/show menu buttons and the "Mark done" dialog (checkpoints, required file).
// Run:  node core/tests/js/menu_and_done.test.js
const fs = require('fs'), path = require('path'), assert = require('assert');
const src = fs.readFileSync(path.join(__dirname, '..', '..', '..', 'static', 'app.js'), 'utf8');

function el(tag = 'div', props = {}) {
  const e = { tag, children: [], hidden: false, dataset: {}, value: '', className: '', textContent: '', attrs: {}, required: false, ...props };
  e.appendChild = c => { e.children.push(c); return c; };
  e.replaceChildren = () => { e.children = []; };
  e.setAttribute = (k, v) => { e.attrs[k] = v; };
  e.querySelectorAll = () => [];
  return e;
}
function classList(set) {
  return { contains: c => set.has(c), toggle(c, on) { (on === undefined ? !set.has(c) : on) ? set.add(c) : set.delete(c); }, _s: set };
}

const listeners = {};
const toggles = [el('button'), el('button')];
const classes = new Set();
const body = { classList: classList(classes) };
const doc = {
  body, cookie: '',
  addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); },
  getElementById: () => null,
  createElement: t => el(t),
  querySelectorAll: s => (s === '[data-side-toggle]' ? toggles : []),
};
global.document = doc; global.window = { confirm: () => true };
new Function(src)();
const click = target => (listeners.click || []).forEach(f => f({ target }));
const button = b => ({ matches: () => false, closest: s => (s === '[data-side-toggle]' ? b : null) });

// 1. menu: open at first; the buttons say so
assert.strictEqual(classes.has('side-hidden'), false);
assert.strictEqual(toggles[0].attrs['aria-expanded'], 'true');

// 2. clicking either button hides it, remembers it in a cookie, and updates both buttons
click(button(toggles[1]));
assert.strictEqual(classes.has('side-hidden'), true);
assert.match(doc.cookie, /^sidebar=hidden; path=\/; max-age=\d+; SameSite=Lax$/);
assert.deepStrictEqual(toggles.map(t => t.attrs['aria-expanded']), ['false', 'false']);

// 3. clicking again brings it back
click(button(toggles[0]));
assert.strictEqual(classes.has('side-hidden'), false);
assert.match(doc.cookie, /^sidebar=shown;/);
assert.deepStrictEqual(toggles.map(t => t.attrs['aria-expanded']), ['true', 'true']);

// 4. other clicks do nothing
const before = doc.cookie; click({ matches: () => false, closest: () => null }); assert.strictEqual(doc.cookie, before);

// 5. the page can be drawn hidden by the server: the first state is respected
classes.add('side-hidden');
(listeners.click = []);
new Function(src)();
assert.deepStrictEqual(toggles.map(t => t.attrs['aria-expanded']), ['false', 'false']);

// ---- "Mark done" dialog -------------------------------------------------------------------------------------------
const opener = (dataset) => ({ matches: () => false, closest: s => (s === '[data-open]' ? { dataset: { open: 'dlg-done', ...dataset } } : null) });
function dialog() {
  const checklist = el('fieldset'), items = el('div'), file = el('input'), note = el('textarea');
  const marks = { '[data-evidence-needed]': [el('small')], '[data-evidence-optional]': [el('small')], '[data-note-needed]': [el('small')],
                  '[data-note-optional]': [el('small')], '[data-when-late]': [el('span')], '[data-when-normal]': [el('span')] };
  const dlg = {
    shown: false, showModal() { this.shown = true; },
    querySelector: s => ({ '[data-checklist]': checklist, '[data-checklist-items]': items, 'input[name=evidence]': file, '[name=note]': note })[s] || null,
    querySelectorAll: s => marks[s] || [],
  };
  return { dlg, checklist, items, file, note, marks };
}
function open(dataset, d = dialog()) {
  const frame = listeners.click.slice();
  document.getElementById = id => (id === 'dlg-done' ? d.dlg : null);
  frame.forEach(f => f({ target: opener(dataset) }));
  return d;
}
classes.delete('side-hidden');

// 6. job with checkpoints and a compulsory file
let d = open({ checkpoints: JSON.stringify(['Oil level OK', 'No leaks']), evidence_required: '1' });
assert.strictEqual(d.dlg.shown, true);
assert.strictEqual(d.items.children.length, 2); assert.strictEqual(d.checklist.hidden, false);
const first = d.items.children[0];                                    // <label><input type=checkbox name=checks value=0 required><span>text</span></label>
assert.strictEqual(first.children[0].type, 'checkbox'); assert.strictEqual(first.children[0].name, 'checks');
assert.strictEqual(first.children[0].value, '0'); assert.strictEqual(first.children[0].required, true);
assert.strictEqual(first.children[1].textContent, 'Oil level OK');
assert.strictEqual(d.items.children[1].children[0].value, '1');
assert.strictEqual(d.file.required, true);
assert.strictEqual(d.marks['[data-evidence-needed]'][0].hidden, false); assert.strictEqual(d.marks['[data-evidence-optional]'][0].hidden, true);
assert.strictEqual(d.note.required, false); assert.strictEqual(d.marks['[data-when-late]'][0].hidden, true);

// 7. checkpoint text is only ever inserted as text, never as HTML
d = open({ checkpoints: JSON.stringify(['<img src=x onerror=alert(1)>']) });
assert.strictEqual(d.items.children[0].children[1].textContent, '<img src=x onerror=alert(1)>');
assert.ok(!('innerHTML' in d.items.children[0]));

// 8. a plain job: no checklist, file optional; old checkpoints from the last job are cleared
open({ checkpoints: JSON.stringify(['a']) }, d);
d = open({ checkpoints: '[]' }, d);
assert.strictEqual(d.items.children.length, 0); assert.strictEqual(d.checklist.hidden, true); assert.strictEqual(d.file.required, false);
assert.strictEqual(d.marks['[data-evidence-optional]'][0].hidden, false);

// 9. a late entry needs a reason
d = open({ late: '1' });
assert.strictEqual(d.note.required, true);
assert.strictEqual(d.marks['[data-note-needed]'][0].hidden, false); assert.strictEqual(d.marks['[data-when-late]'][0].hidden, false); assert.strictEqual(d.marks['[data-when-normal]'][0].hidden, true);

// 10. broken or hostile data attributes do not break the dialog
for (const bad of ['not json', '{"a":1}', 'null', '"text"', '']) {
  d = open({ checkpoints: bad });
  assert.strictEqual(d.items.children.length, 0, bad); assert.strictEqual(d.dlg.shown, true);
}
console.log('menu_and_done: all checks passed');
