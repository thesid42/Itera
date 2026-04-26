/**
 * Itera Web Frontend
 *
 * Drives the three-pane UI by consuming Server-Sent Events from
 * POST /api/marketplace and POST /api/compile.
 *
 * SSE over POST is handled via fetch() + ReadableStream (EventSource only
 * supports GET, so we read the stream manually).
 */

// ── DOM refs ────────────────────────────────────────────────────────────────
const chatLog    = document.getElementById('chat-log');
const marketContent = document.getElementById('market-content');
const simContent = document.getElementById('sim-content');
const goalInput  = document.getElementById('goal-input');
const pipeline   = document.getElementById('pipeline');

// ── State ───────────────────────────────────────────────────────────────────
let awaitingSelection = false;
let currentStrategies = [];

// ── Pipeline stage indicator ─────────────────────────────────────────────────
function setStage(n) {
  pipeline.querySelectorAll('.stage').forEach(el => {
    el.classList.toggle('active', Number(el.dataset.stage) === n);
  });
}

// ── Scroll helpers ───────────────────────────────────────────────────────────
const scrollBottom = el => { el.scrollTop = el.scrollHeight; };

// ── Chat log helpers ─────────────────────────────────────────────────────────
function chatLine(html, className = '') {
  const p = document.createElement('p');
  p.className = 'chat-line ' + className;
  p.innerHTML = html;
  chatLog.appendChild(p);
  scrollBottom(chatLog);
  return p;
}

// ── POST → SSE stream reader ─────────────────────────────────────────────────
async function* sseStream(url, body) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const dec    = new TextDecoder();
  let   buf    = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();          // keep incomplete line
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try { yield JSON.parse(line.slice(6)); } catch (_) {}
      }
    }
  }
}

// ── Spinner helpers ──────────────────────────────────────────────────────────
function makeSpinner(msg) {
  const wrap = document.createElement('p');
  wrap.innerHTML = `<span class="spinner"></span><span class="dim">${msg}</span>`;
  return wrap;
}

// ── Risk bar renderer ────────────────────────────────────────────────────────
function riskColor(score) {
  if (score < 0.3) return '#00ff88';
  if (score < 0.6) return '#ffd060';
  return '#ff5060';
}

// ── Strategy card renderer ───────────────────────────────────────────────────
function renderStrategyCards(strategies) {
  const colors = ['c1', 'c2', 'c3'];
  const wrap = document.createElement('div');
  wrap.className = 'strategy-cards';

  strategies.forEach((s, i) => {
    const c = s.cost;
    const risk = c.failure_risk.score;
    const col  = colors[i % 3];
    const rec  = s.recommended ? '<span class="card-rec">★ RECOMMENDED</span>' : '';
    const pct  = Math.round(risk * 100);

    const card = document.createElement('div');
    card.className = 'strategy-card';
    card.dataset.index = i;
    card.innerHTML = `
      <div class="card-title ${col}">[${i+1}] ${s.name}${rec}</div>
      <div class="card-desc">${s.description}</div>
      <div class="card-labware dim">Labware: <span class="yellow">${s.labware}</span></div>
      <div class="card-labware dim">Duration: <span class="text">${s.estimated_duration_min} min</span></div>
      <div class="cost-row"><span>Tips (${c.tips.count}×)</span><span class="val">$${c.tips.usd.toFixed(2)}</span></div>
      <div class="cost-row"><span>Reagents (${c.reagents.total_ul.toFixed(0)} µL)</span><span class="val">$${c.reagents.usd.toFixed(2)}</span></div>
      <div class="cost-row"><span>Machine (${c.machine_time.minutes.toFixed(0)} min)</span><span class="val">$${c.machine_time.usd.toFixed(2)}</span></div>
      <div class="cost-total"><span>Total</span><span class="val">$${c.total_estimated_usd.toFixed(2)}</span></div>
      <div class="risk-bar-wrap">
        <div class="risk-label">Risk: ${pct}%${c.failure_risk.flags.length ? ' ⚠ ' + c.failure_risk.flags[0] : ''}</div>
        <div class="risk-bar-bg">
          <div class="risk-bar-fill" style="width:${pct}%;background:${riskColor(risk)};"></div>
        </div>
      </div>
    `;
    card.addEventListener('click', () => onStrategySelected(i));
    wrap.appendChild(card);
  });

  return wrap;
}

// ── Marketplace pipeline ─────────────────────────────────────────────────────
async function runMarketplace(goal) {
  setStage(1);
  goalInput.disabled = true;

  chatLine(`<span class="cyan bold">→ Goal:</span> ${goal}`);
  chatLine(`<span class="dim">⟳ Querying OpenRouter (Qwen3)…</span>`);

  marketContent.innerHTML = '';
  const spinner = makeSpinner('Qwen3 is generating strategies');
  marketContent.appendChild(spinner);

  simContent.innerHTML = '<p class="dim">Simulation output will appear here.</p>';

  try {
    for await (const evt of sseStream('/api/marketplace', { goal })) {
      if (evt.type === 'status') {
        spinner.querySelector('.dim').textContent = evt.data;
      } else if (evt.type === 'strategies') {
        currentStrategies = evt.data;
        marketContent.innerHTML = '';
        marketContent.appendChild(renderStrategyCards(evt.data));
        chatLine(`<span class="green dim">✓ Strategies received — click one to compile</span>`);
        awaitingSelection = true;
        setStage(0);
      } else if (evt.type === 'error') {
        marketContent.innerHTML = `<p class="red">✗ ${evt.data}</p>`;
        chatLine(`<span class="red bold">✗ Marketplace error:</span> ${evt.data}`);
        setStage(0);
      }
    }
  } catch (err) {
    marketContent.innerHTML = `<p class="red">✗ ${err.message}</p>`;
    chatLine(`<span class="red bold">✗ Connection error:</span> ${err.message}`);
    setStage(0);
  } finally {
    goalInput.disabled = false;
    goalInput.focus();
  }
}

// ── Compile + simulate pipeline ──────────────────────────────────────────────
async function onStrategySelected(index) {
  if (!awaitingSelection) return;
  awaitingSelection = false;

  const strategy = currentStrategies[index];
  // Highlight selected card
  document.querySelectorAll('.strategy-card').forEach((c, i) => {
    c.classList.toggle('selected', i === index);
  });

  chatLine(`<span class="green bold">→ Compiling:</span> ${strategy.name}`);
  chatLine(`<span class="dim">⟳ Streaming Opentrons protocol from Qwen3…</span>`);

  setStage(2);
  simContent.innerHTML = '';

  // Code streaming block
  const codeHeader = document.createElement('p');
  codeHeader.className = 'dim';
  codeHeader.textContent = '── Protocol code (live stream) ──────────────────';
  simContent.appendChild(codeHeader);

  const codeBlock = document.createElement('pre');
  codeBlock.className = 'code-block';
  const cursor = document.createElement('span');
  cursor.className = 'cursor';
  codeBlock.appendChild(cursor);
  simContent.appendChild(codeBlock);

  let codeText = '';
  let recompileBlock = null;
  let recompileText  = '';

  function appendCode(t, el, textRef) {
    // insert before cursor
    el.insertBefore(document.createTextNode(t), cursor);
    scrollBottom(el);
  }

  try {
    for await (const evt of sseStream('/api/compile', { strategy })) {

      if (evt.type === 'status') {
        const s = document.createElement('p');
        s.className = 'status-line';
        s.textContent = '⟳ ' + evt.data;
        simContent.appendChild(s);
        scrollBottom(simContent);

        if (evt.data.startsWith('Running')) setStage(3);

      } else if (evt.type === 'code') {
        codeText += evt.data;
        appendCode(evt.data, codeBlock);

      } else if (evt.type === 'recompile') {
        if (!recompileBlock) {
          const rh = document.createElement('p');
          rh.className = 'dim';
          rh.textContent = '── Re-compiled code ──────────────────────────────';
          simContent.appendChild(rh);
          recompileBlock = document.createElement('pre');
          recompileBlock.className = 'code-block recompile-block';
          recompileBlock.appendChild(cursor);   // move cursor here
          simContent.appendChild(recompileBlock);
        }
        recompileText += evt.data;
        appendCode(evt.data, recompileBlock);

      } else if (evt.type === 'attempt') {
        cursor.remove();   // pull cursor out while we add fixed content
        const a = evt.data;
        const line = document.createElement('p');
        if (a.passed) {
          line.className = 'attempt-pass';
          line.textContent = `✓ Attempt ${a.attempt} — PASSED`;
        } else {
          line.className = 'attempt-fail';
          line.textContent = `✗ Attempt ${a.attempt} — FAILED`;
          if (a.stderr) {
            a.stderr.split('\n').slice(0, 6).forEach(l => {
              const e = document.createElement('p');
              e.className = 'attempt-err';
              e.textContent = l;
              simContent.appendChild(e);
            });
          }
          if (a.diff) {
            const dh = document.createElement('p');
            dh.className = 'dim';
            dh.textContent = '── diff ──────────────────';
            simContent.appendChild(dh);
            a.diff.split('\n').slice(0, 30).forEach(l => {
              const dl = document.createElement('p');
              dl.className = 'diff-line ' +
                (l.startsWith('+') ? 'diff-add' :
                 l.startsWith('-') ? 'diff-remove' :
                 l.startsWith('@') ? 'diff-hunk' : 'diff-ctx');
              dl.textContent = l;
              simContent.appendChild(dl);
            });
          }
        }
        simContent.appendChild(line);
        scrollBottom(simContent);

      } else if (evt.type === 'result') {
        cursor.remove();
        const r = evt.data;
        const c = r.cost;

        const banner = document.createElement('div');
        banner.className = r.success ? 'verified-banner' : 'effort-banner';
        banner.textContent = r.success
          ? '═══ VERIFIED — Zero simulation errors ═══'
          : `═══ BEST EFFORT after ${r.total_attempts} attempts ═══`;
        simContent.appendChild(banner);

        const card = document.createElement('div');
        card.className = 'cost-card';
        card.innerHTML = `
          <div class="cost-card-title">Final cost analysis</div>
          <div class="row"><span>Tips (${c.tips.count}×)</span><span class="v">$${c.tips.usd.toFixed(2)}</span></div>
          <div class="row"><span>Reagents (${c.reagents.total_ul.toFixed(0)} µL)</span><span class="v">$${c.reagents.usd.toFixed(2)}</span></div>
          <div class="row"><span>Machine (${c.machine_time.minutes.toFixed(1)} min)</span><span class="v">$${c.machine_time.usd.toFixed(2)}</span></div>
          <div class="total"><span>Total estimated</span><span class="v">$${c.total_estimated_usd.toFixed(2)}</span></div>
        `;
        simContent.appendChild(card);
        scrollBottom(simContent);

        chatLine(`<span class="green bold">✓ Protocol saved</span> — ${r.total_attempts} attempt(s) | $${c.total_estimated_usd.toFixed(2)}`);
        chatLine(`<span class="dim">Enter a new goal to start again.</span>`);
        setStage(r.success ? 4 : 0);
        awaitingSelection = false;

      } else if (evt.type === 'error') {
        cursor.remove();
        const e = document.createElement('p');
        e.className = 'red';
        e.textContent = '✗ ' + evt.data;
        simContent.appendChild(e);
        chatLine(`<span class="red bold">✗ Error:</span> ${evt.data}`);
        setStage(0);
      }
    }
  } catch (err) {
    cursor.remove();
    chatLine(`<span class="red bold">✗ Connection error:</span> ${err.message}`);
    setStage(0);
  } finally {
    goalInput.disabled = false;
    goalInput.focus();
  }
}

// ── Input handler ────────────────────────────────────────────────────────────
goalInput.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const val = goalInput.value.trim();
  if (!val) return;
  goalInput.value = '';
  runMarketplace(val);
});

// ── Init ─────────────────────────────────────────────────────────────────────
goalInput.focus();
setStage(0);
