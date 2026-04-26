import './style.css';
import { LiveGraph } from './graph.js';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const goalInput       = document.getElementById('goal-input');
const submitBtn       = document.getElementById('submit-btn');
const heroSection     = document.getElementById('hero-section');
const dynamicContent  = document.getElementById('dynamic-content');
const loaderView      = document.getElementById('loader-view');
const loaderMsg       = document.getElementById('loader-msg');
const strategiesView  = document.getElementById('strategies-view');
const execPanel       = document.getElementById('exec-panel');
const terminalPane    = document.getElementById('terminal-pane');
const resultPane      = document.getElementById('result-pane');
const cardsContainer  = document.getElementById('cards-container');
const terminalOutput  = document.getElementById('terminal-output');
const terminalTitle   = document.getElementById('terminal-title');
const terminalTab     = document.getElementById('terminal-tab');
const terminalSpinner = document.getElementById('terminal-spinner');
const tsLabel         = document.getElementById('ts-label');
const statusPill      = document.getElementById('status-pill');
const statusText      = document.getElementById('status-text');
const finalCostList   = document.getElementById('final-cost-list');
const resetBtn        = document.getElementById('reset-btn');
const downloadBtn     = document.getElementById('download-btn');
const headerProgress  = document.getElementById('header-progress');
const resultBanner    = document.getElementById('result-banner');
const bannerIcon      = document.getElementById('banner-icon');
const resultTitle     = document.getElementById('result-title');
const resultSubtitle  = document.getElementById('result-subtitle');
const graphCanvas     = document.getElementById('graph-canvas');
const graphBadge      = document.getElementById('graph-badge');
const graphHint       = document.getElementById('graph-hint');

// ── State ─────────────────────────────────────────────────────────────────────
let finalCode = '';
let awaitingSelection = false;
let graph = null;
let simAttempt = 0;
let recompileCount = 0;

// ── Pipeline ──────────────────────────────────────────────────────────────────
const PIPE_STEPS = ['idle','market','compile','simulate','verified'];

function setPipeline(active) {
  document.querySelectorAll('.pipe-step').forEach((el, i) => {
    const step = PIPE_STEPS[i];
    const idx  = PIPE_STEPS.indexOf(active);
    el.classList.remove('pulsing', ...PIPE_STEPS.map(s => `active-${s}`), 'done');
    if (i < idx) el.classList.add('done');
    else if (i === idx && active !== 'idle' && active !== 'verified')
      el.classList.add(`active-${step}`, 'pulsing');
    else if (active === 'verified' && i === idx)
      el.classList.add('active-verified');
  });
  headerProgress.className = (active !== 'idle' && active !== 'verified')
    ? 'header-progress running' : 'header-progress';
}

// ── Status pill ───────────────────────────────────────────────────────────────
function setStatus(text, type = '') {
  statusText.textContent = text;
  statusPill.className = `status-pill${type ? ' ' + type : ''}`;
}

// ── View manager ──────────────────────────────────────────────────────────────
function showStandalone(id) {
  // Show loader or strategies — hide exec panel
  execPanel.classList.add('hidden');
  loaderView.classList.add('hidden');
  strategiesView.classList.add('hidden');
  if (id) document.getElementById(id).classList.remove('hidden');
}

function showExec(pane) {
  // Show exec panel (graph stays visible); switch left pane
  loaderView.classList.add('hidden');
  strategiesView.classList.add('hidden');
  execPanel.classList.remove('hidden');
  terminalPane.classList.add('hidden');
  resultPane.classList.add('hidden');
  if (pane === 'terminal') terminalPane.classList.remove('hidden');
  if (pane === 'result')   resultPane.classList.remove('hidden');
}

// ── Graph badge ───────────────────────────────────────────────────────────────
function setGraphBadge(text, type) {
  graphBadge.textContent = text;
  graphBadge.className = `graph-topbar-badge ${type}`;
}

// ── Terminal helpers ──────────────────────────────────────────────────────────
function appendTerm(html) {
  const div = document.createElement('div');
  div.innerHTML = html;
  terminalOutput.appendChild(div);
  terminalOutput.scrollTop = terminalOutput.scrollHeight;
}

// ── POST → SSE generator ─────────────────────────────────────────────────────
async function* sseStream(url, body) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try { yield JSON.parse(line.slice(6)); } catch (_) {}
      }
    }
  }
}

// ── Strategy cards ────────────────────────────────────────────────────────────
function renderStrategies(strategies) {
  cardsContainer.innerHTML = '';
  strategies.forEach(s => {
    const c = s.cost;
    const card = document.createElement('div');
    card.className = 'strategy-card';
    card.innerHTML = `
      <div class="card-top">
        ${s.recommended ? '<div class="tag">Recommended</div>' : '<div></div>'}
        <div class="duration-chip">${s.estimated_duration_min} min</div>
      </div>
      <h3 class="card-title">${s.name}</h3>
      <p class="card-desc">${s.description}</p>
      <div class="card-stats">
        <div class="stat-row"><span class="stat-label">Tips</span><span class="stat-val">${c.tips.count}× · $${c.tips.usd.toFixed(2)}</span></div>
        <div class="stat-row"><span class="stat-label">Reagents</span><span class="stat-val">${c.reagents.total_ul.toFixed(0)} µL · $${c.reagents.usd.toFixed(2)}</span></div>
        <div class="stat-row total-row"><span class="stat-label">Estimated Total</span><span class="stat-val">$${c.total_estimated_usd.toFixed(2)}</span></div>
      </div>
      <div class="card-action">Compile and simulate</div>`;
    card.addEventListener('click', () => {
      if (!awaitingSelection) return;
      awaitingSelection = false;
      startCompilation(s);
    });
    cardsContainer.appendChild(card);
  });
}

// ── Marketplace ───────────────────────────────────────────────────────────────
async function handleSubmission() {
  const goal = goalInput.value.trim();
  if (!goal) return;

  heroSection.classList.add('minimized');
  goalInput.disabled = true;
  submitBtn.disabled = true;
  goalInput.blur();

  dynamicContent.classList.remove('hidden');
  showStandalone('loader-view');
  loaderMsg.textContent = 'Querying marketplace…';
  setPipeline('market');
  setStatus('Querying Market', 'active');
  awaitingSelection = false;
  finalCode = '';

  try {
    for await (const evt of sseStream('/api/marketplace', { goal })) {
      if (evt.type === 'status') {
        loaderMsg.textContent = evt.data;
        setStatus(evt.data, 'active');
      } else if (evt.type === 'strategies') {
        renderStrategies(evt.data);
        showStandalone('strategies-view');
        setPipeline('idle');
        setStatus('Select a Strategy', 'warning');
        awaitingSelection = true;
      } else if (evt.type === 'error') {
        showStandalone(null);
        setPipeline('idle');
        setStatus('Error', 'error');
      }
    }
  } catch (err) {
    showStandalone(null);
    setPipeline('idle');
    setStatus('Connection Error', 'error');
  } finally {
    goalInput.disabled = false;
    submitBtn.disabled = false;
  }
}

// ── Compile + Simulate ────────────────────────────────────────────────────────
async function startCompilation(strategy) {
  terminalOutput.innerHTML = '';
  terminalTitle.textContent = `Compiling: ${strategy.name}`;
  terminalTab.textContent = 'protocol.py';
  terminalSpinner.classList.remove('hidden');
  tsLabel.textContent = 'Compiling';

  // Init graph
  graph = new LiveGraph(graphCanvas);
  graphHint.classList.remove('hidden');
  setGraphBadge('Running', 'running');

  graph.addStep('start',   'start',   'Start');
  graph.setPass('start');
  graph.addStep('compile', 'compile', 'Compile');
  graph.setActive('compile');

  showExec('terminal');
  setPipeline('compile');
  setStatus('Compiling', 'active');
  simAttempt = 0;
  recompileCount = 0;

  appendTerm(`<span class="log-info">➔ Generating Opentrons Python API v2 script…</span>`);

  let codeAccum = '';
  let recompileMode = false;
  let recompileNodeId = null;

  try {
    for await (const evt of sseStream('/api/compile', { strategy })) {

      if (evt.type === 'status') {
        // Backend sends exactly one "Running simulation loop…" event before the loop starts
        if (evt.data.toLowerCase().startsWith('running')) {
          graph.setPass('compile');
          simAttempt = 1;
          graph.addStep('sim_1', 'simulate', 'Simulate #1');
          graph.setActive('sim_1');
          setPipeline('simulate');
          setStatus('Simulating #1', 'active');
          terminalTitle.textContent = 'Simulation — Attempt 1';
          tsLabel.textContent = 'Attempt 1';
          setGraphBadge('Running', 'running');
        }
        appendTerm(`<span class="log-info">➔ ${evt.data}</span>`);

      } else if (evt.type === 'code') {
        codeAccum += evt.data;
        let el = terminalOutput.querySelector('pre.live-code');
        if (!el) { el = document.createElement('pre'); el.className = 'live-code'; terminalOutput.appendChild(el); }
        el.textContent = codeAccum;
        terminalOutput.scrollTop = terminalOutput.scrollHeight;

      } else if (evt.type === 'recompile') {
        if (!recompileMode) {
          recompileMode = true;
          recompileCount++;
          recompileNodeId = `recompile_${recompileCount}`;
          codeAccum = '';
          const oldPre = terminalOutput.querySelector('pre.recompile-code');
          if (oldPre) oldPre.remove();
          graph.addStep(recompileNodeId, 'recompile', `Re-compile ${recompileCount}`);
          graph.setActive(recompileNodeId);
          setPipeline('compile');
          setStatus('Re-compiling', 'active');
          tsLabel.textContent = 'Re-compiling';
          appendTerm(`<span class="log-info">➔ Re-compiling with fixes…</span>`);
        }
        codeAccum += evt.data;
        let el = terminalOutput.querySelector('pre.recompile-code');
        if (!el) { el = document.createElement('pre'); el.className = 'recompile-code'; terminalOutput.appendChild(el); }
        el.textContent = codeAccum;
        terminalOutput.scrollTop = terminalOutput.scrollHeight;

      } else if (evt.type === 'attempt') {
        const a = evt.data;

        // For attempt N>1: the preceding recompile just finished — close it and open new sim node
        if (a.attempt > 1 && recompileNodeId) {
          graph.setPass(recompileNodeId);
          recompileNodeId = null;
          recompileMode = false;
          simAttempt = a.attempt;
          const simId = `sim_${simAttempt}`;
          graph.addStep(simId, 'simulate', `Simulate #${simAttempt}`);
          graph.setActive(simId);
          setPipeline('simulate');
          setStatus(`Simulating #${simAttempt}`, 'active');
          terminalTitle.textContent = `Simulation — Attempt ${simAttempt}`;
          tsLabel.textContent = `Attempt ${simAttempt}`;
          setGraphBadge('Running', 'running');
        }

        const simId = `sim_${simAttempt}`;
        if (a.passed) {
          graph.setPass(simId);
          appendTerm(`<span class="log-success">✓ Attempt ${a.attempt} — PASSED</span>`);
          setGraphBadge('Passed', 'pass');
        } else {
          graph.setFail(simId, { stderr: a.stderr, diff: a.diff });
          appendTerm(`<span class="log-error">✗ Attempt ${a.attempt} — FAILED</span>`);
          if (a.stderr) a.stderr.split('\n').slice(0, 6).forEach(l =>
            appendTerm(`<span class="log-error">&nbsp;&nbsp;${l}</span>`)
          );
          if (a.diff) {
            appendTerm(`<span class="log-info">── diff ──</span>`);
            a.diff.split('\n').slice(0, 20).forEach(l => {
              const cls = l.startsWith('+') ? 'log-diff-add' : l.startsWith('-') ? 'log-diff-sub' : 'log-info';
              appendTerm(`<span class="${cls}">${l}</span>`);
            });
          }
          setGraphBadge('Failed', 'fail');
          recompileMode = false;
        }

      } else if (evt.type === 'result') {
        finalCode = evt.data.final_code || codeAccum;
        terminalSpinner.classList.add('hidden');

        // Final graph node
        const endId = 'end';
        graph.addStep(endId, 'end', evt.data.success ? 'Verified ✓' : 'Best Effort');
        graph.setEnd(endId, evt.data.success);
        setGraphBadge(evt.data.success ? 'Passed' : 'Best Effort', evt.data.success ? 'pass' : 'fail');

        showResult(evt.data);

      } else if (evt.type === 'error') {
        appendTerm(`<span class="log-error">✗ ${evt.data}</span>`);
        terminalSpinner.classList.add('hidden');
        setGraphBadge('Error', 'fail');
        setPipeline('idle');
        setStatus('Error', 'error');
      }
    }
  } catch (err) {
    appendTerm(`<span class="log-error">✗ Connection error: ${err.message}</span>`);
    terminalSpinner.classList.add('hidden');
    setGraphBadge('Error', 'fail');
    setPipeline('idle');
    setStatus('Error', 'error');
  }
}

// ── Result ────────────────────────────────────────────────────────────────────
function showResult(r) {
  const c = r.cost;
  finalCostList.innerHTML = `
    <li><span>Tips (${c.tips.count}×)</span><span>$${c.tips.usd.toFixed(2)}</span></li>
    <li><span>Reagents (${c.reagents.total_ul.toFixed(0)} µL)</span><span>$${c.reagents.usd.toFixed(2)}</span></li>
    <li><span>Machine Time (${c.machine_time.minutes.toFixed(0)} min)</span><span>$${c.machine_time.usd.toFixed(2)}</span></li>
    <li><span>Total Estimated</span><span>$${c.total_estimated_usd.toFixed(2)}</span></li>
  `;

  if (r.success) {
    resultBanner.className = 'result-banner';
    bannerIcon.className = 'banner-icon';
    bannerIcon.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`;
    resultTitle.textContent = 'Protocol Verified';
    resultSubtitle.textContent = `Zero simulation errors · ${r.total_attempts} attempt(s)`;
    setPipeline('verified');
    setStatus('Verified', 'success');
  } else {
    resultBanner.className = 'result-banner effort';
    bannerIcon.className = 'banner-icon effort';
    bannerIcon.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17v.5"/><path d="M10.3 3.7L2 20h20L13.7 3.7a2 2 0 00-3.4 0z"/></svg>`;
    resultTitle.textContent = 'Best Effort Result';
    resultSubtitle.textContent = `Could not fully pass after ${r.total_attempts} attempts`;
    setPipeline('idle');
    setStatus('Best Effort', 'warning');
  }

  showExec('result');
}

// ── Download ──────────────────────────────────────────────────────────────────
downloadBtn.addEventListener('click', () => {
  if (!finalCode) return;
  const blob = new Blob([finalCode], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'protocol.py'; a.click();
  URL.revokeObjectURL(url);
});

// ── Reset ─────────────────────────────────────────────────────────────────────
resetBtn.addEventListener('click', () => {
  goalInput.value = '';
  heroSection.classList.remove('minimized');
  dynamicContent.classList.add('hidden');
  showStandalone(null);
  execPanel.classList.add('hidden');
  setPipeline('idle');
  setStatus('Ready');
  awaitingSelection = false;
  finalCode = '';
  if (graph) { graph.reset(); graph = null; }
  goalInput.focus();
});

// ── Floating header on scroll ────────────────────────────────────────────────
const siteHeader = document.getElementById('site-header');
const onScroll = () => siteHeader.classList.toggle('scrolled', window.scrollY > 24);
window.addEventListener('scroll', onScroll, { passive: true });

// ── Input ─────────────────────────────────────────────────────────────────────
goalInput.addEventListener('keydown', e => { if (e.key === 'Enter') handleSubmission(); });
submitBtn.addEventListener('click', handleSubmission);
document.querySelectorAll('.pill-btn').forEach(btn => {
  btn.addEventListener('click', () => { goalInput.value = btn.textContent; handleSubmission(); });
});
