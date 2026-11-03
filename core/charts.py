"""Tiny dependency-free SVG charts (work under a strict Content-Security-Policy:
no scripts, no inline styles - colours come from CSS classes in style.css).
Every piece of text is HTML-escaped before it goes into the SVG."""
from html import escape
from math import isfinite

try:                                    # inside Django the result is marked safe for templates; every text is escaped below
    from django.utils.safestring import mark_safe as _safe
except ImportError:                     # plain Python (unit tests)
    def _safe(s):
        return s

PALETTE = ["c1", "c2", "c3", "c4", "c5", "c6"]


def _n(v):
    v = float(v)
    return v if isfinite(v) and v > 0 else 0.0


@(lambda f: (lambda *a, **k: _safe(f(*a, **k))))
def donut(segments, center_text="", size=140, label="chart"):
    """segments = [(label, value, css_class)]"""
    segs = [(str(l), _n(v), c) for l, v, c in segments]
    total = sum(v for _, v, _ in segs)
    out = [f'<svg class="chart donut" viewBox="0 0 42 42" width="{size}" height="{size}" role="img" aria-label="{escape(label)}">',
           '<circle class="ring-bg" cx="21" cy="21" r="15.9155" fill="none" stroke-width="5"/>']
    if total > 0:
        offset = 0.0
        for l, v, c in segs:
            if v <= 0:
                continue
            pct = 100 * v / total
            out.append(f'<circle class="seg {escape(c)}" cx="21" cy="21" r="15.9155" fill="none" stroke-width="5" '
                       f'pathLength="100" stroke-dasharray="{pct:.3f} {100 - pct:.3f}" stroke-dashoffset="{-offset:.3f}" '
                       f'transform="rotate(-90 21 21)"><title>{escape(l)}: {v:g}</title></circle>')
            offset += pct
    if center_text != "":
        out.append(f'<text class="ctext" x="21" y="23" text-anchor="middle">{escape(str(center_text))}</text>')
    out.append("</svg>")
    return "".join(out)


@(lambda f: (lambda *a, **k: _safe(f(*a, **k))))
def ring(percent, size=64, label="progress"):
    p = max(0.0, min(100.0, _n(percent)))
    return (f'<svg class="chart ring" viewBox="0 0 42 42" width="{size}" height="{size}" role="img" aria-label="{escape(label)} {p:.0f}%">'
            '<circle class="ring-bg" cx="21" cy="21" r="15.9155" fill="none" stroke-width="4.5"/>'
            f'<circle class="seg {"c-green" if p >= 100 else "c1"}" cx="21" cy="21" r="15.9155" fill="none" stroke-width="4.5" '
            f'pathLength="100" stroke-dasharray="{p:.2f} {100 - p:.2f}" stroke-linecap="round" transform="rotate(-90 21 21)"/>'
            f'<text class="ctext sm" x="21" y="23.5" text-anchor="middle">{p:.0f}%</text></svg>')


@(lambda f: (lambda *a, **k: _safe(f(*a, **k))))
def bars(items, width=420, height=170, label="bar chart", css="c1"):
    """items = [(label, value)] -> vertical bars."""
    items = [(str(l), _n(v)) for l, v in items]
    if not items:
        return f'<svg class="chart bars" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}"></svg>'
    top, bottom, left = 16, 24, 6
    mx = max([v for _, v in items] + [1])
    n = len(items)
    slot = (width - left * 2) / n
    bw = slot * 0.62
    ph = height - top - bottom
    out = [f'<svg class="chart bars" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">',
           f'<line class="axis" x1="{left}" y1="{height - bottom}" x2="{width - left}" y2="{height - bottom}"/>']
    for i, (l, v) in enumerate(items):
        h = ph * v / mx
        x = left + i * slot + (slot - bw) / 2
        y = height - bottom - h
        out.append(f'<rect class="bar {escape(css)}" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" rx="3"><title>{escape(l)}: {v:g}</title></rect>')
        if v > 0:
            out.append(f'<text class="val" x="{x + bw / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle">{v:g}</text>')
        out.append(f'<text class="lab" x="{x + bw / 2:.1f}" y="{height - 8}" text-anchor="middle">{escape(l)}</text>')
    out.append("</svg>")
    return "".join(out)


@(lambda f: (lambda *a, **k: _safe(f(*a, **k))))
def hbars(items, width=420, row=26, label="bar chart"):
    """items = [(label, value)] -> horizontal bars, longest first as given."""
    items = [(str(l)[:28], _n(v)) for l, v in items]
    height = max(row * len(items) + 6, 30)
    mx = max([v for _, v in items] + [1])
    lw, vw = 120, 34
    bw = width - lw - vw
    out = [f'<svg class="chart hbars" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">']
    for i, (l, v) in enumerate(items):
        y = 4 + i * row
        w = bw * v / mx
        out.append(f'<text class="lab" x="0" y="{y + 15}">{escape(l)}</text>'
                   f'<rect class="track" x="{lw}" y="{y + 3}" width="{bw}" height="14" rx="7"/>'
                   f'<rect class="bar {PALETTE[i % len(PALETTE)]}" x="{lw}" y="{y + 3}" width="{w:.1f}" height="14" rx="7"><title>{escape(l)}: {v:g}</title></rect>'
                   f'<text class="val" x="{lw + bw + 6}" y="{y + 15}">{v:g}</text>')
    out.append("</svg>")
    return "".join(out)
