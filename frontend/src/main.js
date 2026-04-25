import './style.css';

// DOM Elements
const goalInput = document.getElementById('goal-input');
const submitBtn = document.getElementById('submit-btn');
const heroSection = document.getElementById('hero-section');
const dynamicContent = document.getElementById('dynamic-content');
const strategiesView = document.getElementById('strategies-view');
const terminalView = document.getElementById('terminal-view');
const resultView = document.getElementById('result-view');
const cardsContainer = document.getElementById('cards-container');
const terminalOutput = document.getElementById('terminal-output');
const terminalTitle = document.getElementById('terminal-title');
const statusBadge = document.getElementById('status-badge');
const finalCostList = document.getElementById('final-cost-list');
const resetBtn = document.getElementById('reset-btn');

// Mock data
const strategies = [
  { id: 1, name: 'Fast & Dirty', desc: 'Maximizes throughput using 384-well format, minimal incubations.', time: 45, tips: 120, tipCost: 21.60, reagents: 1200, reagentCost: 4.80, machine: 45, machineCost: 15.75, total: 42.15, rec: false },
  { id: 2, name: 'Balanced', desc: 'Optimal cost/time tradeoff using 96-well format and multi-channel pipettes.', time: 80, tips: 96, tipCost: 17.28, reagents: 2400, reagentCost: 9.60, machine: 80, machineCost: 28.00, total: 54.88, rec: true },
  { id: 3, name: 'Precision', desc: 'Minimizes risk with single-channel transfers and individual tip changes to prevent contamination.', time: 150, tips: 384, tipCost: 69.12, reagents: 2400, reagentCost: 9.60, machine: 150, machineCost: 52.50, total: 131.22, rec: false }
];

function setStatus(text, type = '') {
  statusBadge.textContent = text;
  statusBadge.className = `status-badge ${type}`;
}

function handleSubmission() {
  if (goalInput.value.trim() === '') return;
  
  // Transition UI
  heroSection.classList.add('minimized');
  goalInput.blur();
  
  setStatus('Querying Market...', 'active');
  dynamicContent.classList.remove('hidden');
  
  // Clear views
  strategiesView.classList.add('hidden');
  terminalView.classList.add('hidden');
  resultView.classList.add('hidden');
  
  // Mock generation delay
  setTimeout(() => {
    renderStrategies();
    strategiesView.classList.remove('hidden');
    setStatus('Awaiting Selection');
  }, 1200);
}

goalInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') handleSubmission();
});

submitBtn.addEventListener('click', handleSubmission);

// Quick links
document.querySelectorAll('.pill-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    goalInput.value = btn.textContent;
    handleSubmission();
  });
});

function renderStrategies() {
  cardsContainer.innerHTML = '';
  strategies.forEach((s) => {
    const card = document.createElement('div');
    card.className = 'strategy-card';
    card.innerHTML = `
      ${s.rec ? '<div class="tag">RECOMMENDED</div>' : ''}
      <h3 class="card-title">${s.name}</h3>
      <p class="card-desc">${s.desc}</p>
      <div class="card-stats">
        <div class="stat-row"><span class="stat-label">Duration</span><span class="stat-val">${s.time} min</span></div>
        <div class="stat-row"><span class="stat-label">Tips</span><span class="stat-val">${s.tips}× ($${s.tipCost.toFixed(2)})</span></div>
        <div class="stat-row"><span class="stat-label">Reagents</span><span class="stat-val">${s.reagents}µL ($${s.reagentCost.toFixed(2)})</span></div>
        <div class="total-row stat-row"><span class="stat-label">Estimated Total</span><span class="stat-val">$${s.total.toFixed(2)}</span></div>
      </div>
    `;
    
    card.addEventListener('click', () => {
      startCompilation(s);
    });
    
    cardsContainer.appendChild(card);
  });
}

function appendTerm(html) {
  const div = document.createElement('div');
  div.innerHTML = html;
  terminalOutput.appendChild(div);
  terminalOutput.scrollTop = terminalOutput.scrollHeight;
}

function startCompilation(strategy) {
  strategiesView.classList.add('hidden');
  terminalView.classList.remove('hidden');
  terminalOutput.innerHTML = '';
  
  setStatus('Compiling...', 'active');
  terminalTitle.textContent = `Compiling: ${strategy.name}`;
  
  appendTerm(`<span class="log-info">➔ Generating Opentrons Python API v2 script...</span>`);
  
  setTimeout(() => {
    appendTerm(`from opentrons import protocol_api`);
    appendTerm(`metadata = {"apiLevel": "2.14"}`);
    appendTerm(`def run(protocol: protocol_api.ProtocolContext):`);
    appendTerm(`    plate = protocol.load_labware("corning_96_wellplate_360ul_flat", "1")`);
    appendTerm(`    tiprack = protocol.load_labware("opentrons_96_tiprack_300ul", "2")`);
    appendTerm(`    p300 = protocol.load_instrument("p300_multi_gen2", "right", tip_racks=[tiprack])`);
    
    setTimeout(() => {
      runSimulation(strategy);
    }, 1000);
  }, 1000);
}

function runSimulation(strategy) {
  setStatus('Simulating...', 'active');
  terminalTitle.textContent = `Running Simulation Loop`;
  appendTerm(`<br><span class="log-info">➔ Running opentrons_simulate...</span>`);
  
  setTimeout(() => {
    appendTerm(`<span class="log-error">✗ Attempt 1 FAILED</span>`);
    appendTerm(`<span class="log-error">APIError: time module is deprecated in v2. Use protocol.delay()</span>`);
    appendTerm(`<br>Diff:`);
    appendTerm(`<span class="log-diff-sub">- import time</span>`);
    appendTerm(`<span class="log-diff-sub">- time.sleep(30)</span>`);
    appendTerm(`<span class="log-diff-add">+ protocol.delay(seconds=30)</span>`);
    appendTerm(`<br><span class="log-info">➔ Re-compiling...</span>`);
    
    setTimeout(() => {
      appendTerm(`<span class="log-success">✓ Attempt 2 PASSED (Zero errors)</span>`);
      showResult(strategy);
    }, 1500);
  }, 1500);
}

function showResult(strategy) {
  setTimeout(() => {
    terminalView.classList.add('hidden');
    resultView.classList.remove('hidden');
    setStatus('Verified', 'success');
    
    finalCostList.innerHTML = `
      <li><span>Tips (${strategy.tips})</span><span>$${strategy.tipCost.toFixed(2)}</span></li>
      <li><span>Reagents (${strategy.reagents}µL)</span><span>$${strategy.reagentCost.toFixed(2)}</span></li>
      <li><span>Machine Time (${strategy.machine}m)</span><span>$${strategy.machineCost.toFixed(2)}</span></li>
      <li><span>Total Cost</span><span>$${strategy.total.toFixed(2)}</span></li>
    `;
  }, 1000);
}

resetBtn.addEventListener('click', () => {
  goalInput.value = '';
  heroSection.classList.remove('minimized');
  dynamicContent.classList.add('hidden');
  resultView.classList.add('hidden');
  setStatus('Idle');
});
