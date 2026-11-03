// No inline scripts are allowed by the Content-Security-Policy, so all behaviour lives here.
// "Mark done" dialog: shows this job's checkpoints, and whether a photo/file (or a reason, for a late entry) is compulsory.
function prepareDone(dlg, b) {
  let items = [];
  try { items = JSON.parse(b.dataset.checkpoints || '[]'); } catch (err) { items = []; }
  if (!Array.isArray(items)) items = [];
  const box = dlg.querySelector('[data-checklist]'), list = dlg.querySelector('[data-checklist-items]');
  if (box && list) {
    list.replaceChildren();
    items.forEach((text, i) => {
      const lab = document.createElement('label'); lab.className = 'chk';
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.name = 'checks'; cb.value = String(i); cb.required = true;
      const span = document.createElement('span'); span.textContent = String(text);       // text, never HTML
      lab.appendChild(cb); lab.appendChild(span); list.appendChild(lab);
    });
    box.hidden = items.length === 0;
  }
  const need = b.dataset.evidence_required === '1', late = b.dataset.late === '1';
  const file = dlg.querySelector('input[name=evidence]'), note = dlg.querySelector('[name=note]');
  if (file) { file.required = need; file.value = ''; }
  if (note) note.required = late;
  const show = (sel, on) => dlg.querySelectorAll(sel).forEach(x => { x.hidden = !on; });
  show('[data-evidence-needed]', need); show('[data-evidence-optional]', !need);
  show('[data-note-needed]', late); show('[data-note-optional]', !late);
  show('[data-when-late]', late); show('[data-when-normal]', !late);
}

document.addEventListener('click', e => {
  const b = e.target.closest('[data-open]');
  if (b) {                                              // open a dialog and pre-fill it from data-* attributes
    const dlg = document.getElementById(b.dataset.open);
    if (!dlg) return;
    for (const [k, v] of Object.entries(b.dataset)) {
      if (k === 'open') continue;
      dlg.querySelectorAll('[name="' + k + '"]').forEach(f => { f.value = v; });
      dlg.querySelectorAll('[data-fill="' + k + '"]').forEach(t => { t.textContent = v; });
      dlg.querySelectorAll('select[data-value-from="' + k + '"]').forEach(s => { s.value = v; });     // e.g. pre-select the current role
      dlg.querySelectorAll('[data-action-from="' + k + '"]').forEach(f => { f.setAttribute('action', v); f.hidden = !v; });   // per-row form URLs
    }
    if (b.dataset.open === 'dlg-done') prepareDone(dlg, b);
    dlg.showModal();
    return;
  }
  if (e.target.matches('[data-close]')) { e.target.closest('dialog').close(); return; }
  if (e.target.matches('dialog')) {                     // click on the dimmed backdrop closes it
    const r = e.target.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) e.target.close();
  }
});

document.addEventListener('change', e => {              // toggle switches submit their form straight away
  if (e.target.matches('[data-autosubmit]')) e.target.form.submit();
});

document.addEventListener('submit', e => {              // "Are you sure?" on dangerous buttons
  const msg = e.target.dataset && e.target.dataset.confirm;
  if (msg && !window.confirm(msg)) e.preventDefault();
});

// keep the start date from running past the end date in "skip days" forms
document.addEventListener('change', e => {
  if (!e.target.matches('input[name=start_date]')) return;
  const end = e.target.form.querySelector('input[name=end_date]');
  if (end && (!end.value || end.value < e.target.value)) end.value = e.target.value;
});

// One time / Repeats switch on the job form.
// Shows the due-date box for "one time" and the schedule box for "repeats". Safe if anything is missing.
(function () {
  const jf = document.getElementById('jobform');
  const kinds = jf ? Array.from(jf.querySelectorAll('input[name=kind]')) : [];
  if (!kinds.length) return;
  const field = n => jf.querySelector('[name=' + n + ']');
  const isRepeat = () => { const c = kinds.find(k => k.checked); return !!c && c.value === 'repeat'; };
  const summary = () => {
    const el = document.getElementById('summary');
    if (!el) return;
    if (!isRepeat()) { el.textContent = 'This job happens one time.'; return; }
    const n = parseInt(field('every') && field('every').value, 10) || 1;
    const u = (field('unit') && field('unit').value) || 'week';
    const end = field('end_date') && field('end_date').value;
    el.textContent = 'Repeats every ' + (n === 1 ? '' : n + ' ') + u + (n === 1 ? '' : 's') +
      (end ? ', until ' + end : ', with no end date') + '. Admins can skip several days or move single dates later.';
  };
  const sync = () => {
    const once = document.getElementById('once-box'), rep = document.getElementById('repeat-box');
    if (once) once.hidden = isRepeat();
    if (rep) rep.hidden = !isRepeat();
    summary();
  };
  if (!kinds.some(k => k.checked)) kinds[0].checked = true;      // never leave the choice empty
  kinds.forEach(r => r.addEventListener('change', sync));
  ['every', 'unit', 'end_date'].forEach(n => { const f = field(n); if (f) f.addEventListener('input', summary); });
  jf.querySelectorAll('[data-preset]').forEach(b => b.addEventListener('click', () => {
    const p = b.dataset.preset.split(':');
    if (field('every')) field('every').value = p[0];
    if (field('unit')) field('unit').value = p[1];
    summary();
  }));
  sync();
})();

// "Days this job should NOT happen": a date picker that builds a list of separate days (they need not be consecutive).
// The plain text field stays in the page as a fallback and is the value that gets submitted.
(function () {
  const box = document.getElementById('skip-widget');
  const jf = document.getElementById('jobform');
  const text = jf && jf.querySelector('input[name=skip_dates]');
  const picker = document.getElementById('skip-picker'), addBtn = document.getElementById('skip-add');
  const chips = document.getElementById('skip-chips'), empty = document.getElementById('skip-empty');
  if (!box || !text || !picker || !addBtn || !chips) return;

  const ISO = /^\d{4}-\d{2}-\d{2}$/;
  const valid = s => {
    if (!ISO.test(s)) return false;
    const p = s.split('-').map(Number), d = new Date(p[0], p[1] - 1, p[2]);
    return d.getFullYear() === p[0] && d.getMonth() === p[1] - 1 && d.getDate() === p[2];
  };
  const pretty = s => {
    const p = s.split('-').map(Number);
    return new Date(p[0], p[1] - 1, p[2]).toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
  };
  let days = (text.value || '').split(/[\s,;]+/).filter(valid);
  days = Array.from(new Set(days)).sort();

  const render = () => {
    text.value = days.join(', ');
    chips.replaceChildren();
    days.forEach(day => {
      const chip = document.createElement('span'); chip.className = 'chip on';
      const label = document.createElement('span'); label.textContent = pretty(day);
      const x = document.createElement('button'); x.type = 'button'; x.className = 'x'; x.textContent = '\u00d7';
      x.dataset.day = day; x.setAttribute('aria-label', 'Remove ' + pretty(day));
      chip.appendChild(label); chip.appendChild(x); chips.appendChild(chip);
    });
    if (empty) empty.hidden = days.length > 0;
  };
  const add = () => {
    const v = picker.value;
    if (!valid(v)) { picker.focus(); return; }
    if (!days.includes(v)) { days.push(v); days.sort(); }
    picker.value = ''; render(); picker.focus();
  };
  addBtn.addEventListener('click', add);
  picker.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); add(); } });   // Enter adds the day, it does not save the job
  chips.addEventListener('click', e => {
    const day = e.target && e.target.dataset && e.target.dataset.day;
    if (!day) return;
    days = days.filter(d => d !== day); render();
  });
  text.hidden = true; box.hidden = false;
  render();
})();

// "Select all shown" tick box on the add-people list
document.addEventListener('change', e => {
  if (!e.target.matches('[data-check-all]')) return;
  const form = e.target.form;
  if (form) form.querySelectorAll('input[name=users]').forEach(c => { c.checked = e.target.checked; });
});

// Hide / show the left menu. The choice is kept in a cookie so the server draws the page the same way next time (no flicker).
(function () {
  const body = document.body;
  if (!body || !body.classList) return;
  const buttons = () => Array.from(document.querySelectorAll('[data-side-toggle]'));
  const apply = hidden => {
    body.classList.toggle('side-hidden', hidden);
    buttons().forEach(b => b.setAttribute('aria-expanded', hidden ? 'false' : 'true'));
  };
  apply(body.classList.contains('side-hidden'));
  document.addEventListener('click', e => {
    const t = e.target && e.target.closest && e.target.closest('[data-side-toggle]');
    if (!t) return;
    const hidden = !body.classList.contains('side-hidden');
    apply(hidden);
    document.cookie = 'sidebar=' + (hidden ? 'hidden' : 'shown') + '; path=/; max-age=31536000; SameSite=Lax';
  });
})();
