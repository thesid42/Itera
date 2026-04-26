/**
 * LiveGraph — animated execution flow visualizer
 * Renders a vertical node graph with live edge animations,
 * pulsing active states, and hover/click tooltips for failures.
 */
export class LiveGraph {
  constructor(container) {
    this.el = container;
    this.el.className = 'live-graph';
    this.steps = [];          // { id, type, label, status, error }
    this._openTooltipId = null;
    this._tooltip = this._makeTooltip();
  }

  reset() {
    this.steps = [];
    this.el.innerHTML = '';
    this._closeTooltip();
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  addStep(id, type, label) {
    const step = { id, type, label, status: 'idle', error: null };
    this.steps.push(step);
    this._mount(step);
    return step;
  }

  setActive(id)              { this._set(id, 'active'); }
  setPass(id)                { this._set(id, 'pass'); }
  setFail(id, error = null)  { this._set(id, 'fail', error); }
  setEnd(id, success)        { this._set(id, success ? 'verified' : 'effort'); }

  // ── Internal state ──────────────────────────────────────────────────────────

  _set(id, status, error = null) {
    const step = this.steps.find(s => s.id === id);
    if (!step) return;
    step.status = status;
    if (error !== null) step.error = error;
    this._rerender(step);
    this._rerenderEdge(step);
  }

  // ── DOM building ────────────────────────────────────────────────────────────

  _mount(step) {
    const idx = this.steps.indexOf(step);
    if (idx > 0) this.el.appendChild(this._edgeEl(step));
    const node = this._nodeEl(step);
    this.el.appendChild(node);
    requestAnimationFrame(() => requestAnimationFrame(() => node.classList.add('gn-in')));
  }

  _edgeEl(step) {
    const el = document.createElement('div');
    el.className = 'gn-edge ge-idle';
    el.dataset.edgeFor = step.id;
    el.innerHTML = `<div class="ge-track"><div class="ge-particle"></div></div>`;
    return el;
  }

  _nodeEl(step) {
    const el = document.createElement('div');
    el.dataset.nodeId = step.id;
    this._fillNode(el, step);
    return el;
  }

  _rerender(step) {
    const el = this.el.querySelector(`[data-node-id="${step.id}"]`);
    if (el) this._fillNode(el, step);
  }

  _rerenderEdge(step) {
    const el = this.el.querySelector(`[data-edge-for="${step.id}"]`);
    if (!el) return;
    const map = { active: 'ge-active', pass: 'ge-pass', verified: 'ge-pass', fail: 'ge-fail', effort: 'ge-fail' };
    el.className = `gn-edge ${map[step.status] || 'ge-idle'}`;
  }

  _fillNode(el, step) {
    const clickable = step.status === 'fail' && step.error;
    el.className = `gn-node gnt-${step.type} gns-${step.status}${clickable ? ' gn-clickable' : ''}`;

    el.innerHTML = `
      <div class="gn-icon-wrap">
        <div class="gn-icon">${this._icon(step.type, step.status)}</div>
        ${step.status === 'active' ? '<div class="gn-ring"></div>' : ''}
      </div>
      <div class="gn-info">
        <div class="gn-name">${step.label}</div>
        <div class="gn-sub">${this._subLabel(step.status)}</div>
      </div>
      <div class="gn-trail">
        ${step.status === 'active' ? `<div class="gn-dots"><span></span><span></span><span></span></div>` : ''}
        ${step.status === 'pass' || step.status === 'verified' ? `
          <div class="gn-badge gn-badge-pass">
            <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 6l3 3 5-5"/></svg>
          </div>` : ''}
        ${step.status === 'fail' ? `
          <div class="gn-badge gn-badge-fail" title="Click to see error details">
            <svg viewBox="0 0 12 12" fill="currentColor"><path d="M6 1a5 5 0 100 10A5 5 0 006 1zm0 3a.6.6 0 01.6.6v2.4a.6.6 0 01-1.2 0V4.6A.6.6 0 016 4zm0 5a.75.75 0 110-1.5.75.75 0 010 1.5z"/></svg>
          </div>` : ''}
        ${step.status === 'effort' ? `
          <div class="gn-badge gn-badge-effort">
            <svg viewBox="0 0 12 12" fill="currentColor"><path d="M6 1a5 5 0 100 10A5 5 0 006 1zm0 3a.6.6 0 01.6.6v2.4a.6.6 0 01-1.2 0V4.6A.6.6 0 016 4zm0 5a.75.75 0 110-1.5.75.75 0 010 1.5z"/></svg>
          </div>` : ''}
      </div>
    `;

    if (clickable) {
      el.addEventListener('mouseenter', () => this._openTooltip(step, el));
      el.addEventListener('mouseleave', e => {
        if (!e.relatedTarget?.closest?.('.graph-tooltip')) this._closeTooltip();
      });
      el.addEventListener('click', e => {
        e.stopPropagation();
        if (this._openTooltipId === step.id) this._closeTooltip();
        else this._openTooltip(step, el);
      });
    }
  }

  // ── Icons ───────────────────────────────────────────────────────────────────

  _icon(type, status) {
    if (status === 'pass' || status === 'verified')
      return `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8l4 4 6-6"/></svg>`;
    if (status === 'fail')
      return `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/></svg>`;
    if (status === 'effort')
      return `<svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 2a6 6 0 100 12A6 6 0 008 2zm0 3.5a.75.75 0 01.75.75v3a.75.75 0 01-1.5 0v-3A.75.75 0 018 5.5zm0 7a1 1 0 110-2 1 1 0 010 2z"/></svg>`;
    if (type === 'start' || type === 'end')
      return `<svg viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="8" r="3.5"/></svg>`;
    if (type === 'compile' || type === 'recompile')
      return `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><polyline points="5,6 3,8 5,10"/><polyline points="11,6 13,8 11,10"/><line x1="9.5" y1="4" x2="6.5" y2="12"/></svg>`;
    if (type === 'simulate')
      return `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3.5l7 4.5-7 4.5V3.5z"/></svg>`;
    return `<svg viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="8" r="3"/></svg>`;
  }

  _subLabel(status) {
    return { idle: 'Waiting', active: 'Running…', pass: 'Passed', fail: 'Failed', verified: 'Verified', effort: 'Best effort' }[status] ?? '';
  }

  // ── Tooltip ─────────────────────────────────────────────────────────────────

  _makeTooltip() {
    const el = document.createElement('div');
    el.className = 'graph-tooltip';
    el.addEventListener('mouseleave', () => this._closeTooltip());
    document.addEventListener('click', e => {
      if (!e.target.closest('.gn-clickable') && !e.target.closest('.graph-tooltip')) this._closeTooltip();
    });
    document.body.appendChild(el);
    return el;
  }

  _openTooltip(step, anchor) {
    this._openTooltipId = step.id;
    const err = step.error || {};

    let html = `
      <div class="tt-head">
        <span class="tt-icon">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="8" cy="8" r="6"/><path d="M8 5.5v3M8 10v.5"/></svg>
        </span>
        <span class="tt-title">${step.label} — Failure</span>
      </div>`;

    if (err.stderr) {
      const lines = err.stderr.trim().split('\n').slice(0, 10).join('\n');
      html += `
        <div class="tt-block">
          <div class="tt-label">Error Output</div>
          <pre class="tt-pre tt-pre-err">${this._esc(lines)}</pre>
        </div>`;
    }

    if (err.diff) {
      const lines = err.diff.split('\n').slice(0, 18);
      html += `
        <div class="tt-block">
          <div class="tt-label">Code Changes (diff)</div>
          <pre class="tt-pre tt-pre-diff">${lines.map(l => {
            const c = l.startsWith('+') ? 'td-add' : l.startsWith('-') ? 'td-sub' : l.startsWith('@') ? 'td-hunk' : 'td-ctx';
            return `<span class="${c}">${this._esc(l)}</span>`;
          }).join('\n')}</pre>
        </div>`;
    }

    html += `
      <div class="tt-next">
        <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M7 2v10M2 7l5 5 5-5"/></svg>
        Re-compiling with automatic fixes
      </div>`;

    this._tooltip.innerHTML = html;

    // Measure height before committing position (tooltip is fixed, parked off-screen)
    this._tooltip.style.cssText = 'left:-9999px;top:-9999px;';
    this._tooltip.classList.add('tt-show');
    const ttH = this._tooltip.offsetHeight;
    const ttW = 330;

    const r = anchor.getBoundingClientRect();
    let left = r.right + 10;
    if (left + ttW > window.innerWidth - 8) left = r.left - ttW - 10;

    // Align with anchor top, clamp so it never clips the viewport bottom or top
    let top = r.top;
    if (top + ttH > window.innerHeight - 8) top = window.innerHeight - ttH - 8;
    top = Math.max(8, top);

    this._tooltip.style.cssText = `left:${Math.max(8, left)}px;top:${top}px;`;
  }

  _closeTooltip() {
    this._openTooltipId = null;
    this._tooltip.classList.remove('tt-show');
  }

  _esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
}
