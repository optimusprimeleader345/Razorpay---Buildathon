/**
 * Bounded Recovery AI Subscription Platform - Frontend API Controller
 */

document.addEventListener('DOMContentLoaded', () => {
  // DOM Elements
  const chargesGrid = document.getElementById('charges-grid');
  const btnRefreshCharges = document.getElementById('btn-refresh-charges');
  const btnResetDb = document.getElementById('btn-reset-db');
  const formSimulate = document.getElementById('form-simulate-charge');
  
  const logModal = document.getElementById('log-modal');
  const btnCloseModal = document.getElementById('btn-close-modal');
  const modalChargeTitle = document.getElementById('modal-charge-title');
  const modalChargeSubtitle = document.getElementById('modal-charge-subtitle');
  const logTimelineContainer = document.getElementById('log-timeline-container');

  const statFailedVolume = document.getElementById('stat-failed-volume');
  const statRecoveredVolume = document.getElementById('stat-recovered-volume');
  const statRecoveryRate = document.getElementById('stat-recovery-rate');
  const statAiRatio = document.getElementById('stat-ai-ratio');
  const statSafetyOverrides = document.getElementById('stat-safety-overrides');

  // State
  let currentCharges = [];
  let activeTab = 'all';

  // Initial Load
  loadDashboard();

  // Event Listeners
  btnRefreshCharges.addEventListener('click', loadDashboard);
  btnResetDb.addEventListener('click', handleResetDatabase);
  formSimulate.addEventListener('submit', handleSimulateCharge);
  btnCloseModal.addEventListener('click', closeModal);
  
  logModal.addEventListener('click', (e) => {
    if (e.target === logModal) closeModal();
  });

  // Tab Filtering
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeTab = btn.dataset.tab;
      renderCharges(currentCharges);
    });
  });

  // FAQ Accordion Toggles
  document.querySelectorAll('.faq-item').forEach(item => {
    item.addEventListener('click', () => {
      item.classList.toggle('active');
    });
  });

  async function loadDashboard() {
    await Promise.all([fetchAnalytics(), fetchCharges()]);
  }

  // 1. Fetch Analytics Summary & Update FSM Node Counters
  async function fetchAnalytics() {
    try {
      const res = await fetch('/analytics/summary');
      if (!res.ok) throw new Error('Failed to fetch analytics summary');
      const data = await res.json();

      statFailedVolume.textContent = `$${data.total_failed_volume.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
      statRecoveredVolume.textContent = `$${data.total_recovered_volume.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})} Secured`;
      statRecoveryRate.textContent = `${data.recovery_rate_percentage.toFixed(2)}%`;
      
      const statBaselineRate = document.getElementById('stat-baseline-rate');
      const statBaselineCount = document.getElementById('stat-baseline-count');
      if (statBaselineRate) statBaselineRate.textContent = `${data.baseline_recovery_rate_percentage.toFixed(2)}%`;
      if (statBaselineCount) statBaselineCount.textContent = `${data.recovered_on_first_attempt_count} charges recovered on Attempt 1`;

      statAiRatio.textContent = `${data.ai_decision_count} / ${data.rule_fallback_count}`;
      statSafetyOverrides.textContent = data.safety_overrides_count;

      // Update FSM Node Counters & Batch Evidence Card
      const byState = data.charges_by_state || {};
      document.getElementById('node-count-detected').textContent = byState['DETECTED'] || 0;
      document.getElementById('node-count-scheduled').textContent = byState['RETRY_SCHEDULED'] || 0;
      document.getElementById('node-count-retrying').textContent = byState['RETRYING'] || 0;
      document.getElementById('node-count-recovered').textContent = byState['RECOVERED'] || 0;
      document.getElementById('node-count-escalated').textContent = byState['ESCALATED'] || 0;

      const batchFailedVol = document.getElementById('batch-failed-volume');
      const batchRecVol = document.getElementById('batch-recovered-volume');
      const batchRecRate = document.getElementById('batch-recovery-rate');
      const batchAiDec = document.getElementById('batch-ai-decisions');
      const batchRuleFall = document.getElementById('batch-rule-fallbacks');
      const batchSafety = document.getElementById('batch-safety-overrides');
      const batchRecCnt = document.getElementById('batch-recovered-count');
      const batchEsclCnt = document.getElementById('batch-escalated-count');
      const batchProgBar = document.getElementById('batch-progress-bar');

      if (batchFailedVol) batchFailedVol.textContent = `$${data.total_failed_volume.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
      if (batchRecVol) batchRecVol.textContent = `$${data.total_recovered_volume.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
      if (batchRecRate) batchRecRate.textContent = `${data.recovery_rate_percentage.toFixed(2)}%`;
      if (batchAiDec) batchAiDec.textContent = data.ai_decision_count;
      if (batchRuleFall) batchRuleFall.textContent = data.rule_fallback_count;
      if (batchSafety) batchSafety.textContent = `${data.safety_overrides_count} Enforced`;
      if (batchRecCnt) batchRecCnt.textContent = `${byState['RECOVERED'] || 0} RECOVERED`;
      if (batchEsclCnt) batchEsclCnt.textContent = `${byState['ESCALATED'] || 0} ESCALATED`;
      if (batchProgBar) batchProgBar.style.width = `${Math.min(data.recovery_rate_percentage, 100)}%`;
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // 2. Fetch Charges & Render Cards
  async function fetchCharges() {
    try {
      const res = await fetch('/charges');
      if (!res.ok) throw new Error('Failed to fetch charges');
      currentCharges = await res.json();
      renderCharges(currentCharges);
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function renderCharges(charges) {
    chargesGrid.innerHTML = '';
    
    // Filter charges by activeTab
    let filtered = charges;
    if (activeTab === 'active') {
      filtered = charges.filter(c => ['DETECTED', 'RETRY_SCHEDULED', 'RETRYING'].includes(c.state));
    } else if (activeTab !== 'all') {
      filtered = charges.filter(c => c.state === activeTab);
    }

    if (!filtered || filtered.length === 0) {
      chargesGrid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 3rem; color: var(--text-subtle);">No subscription charges found for filter '${activeTab}'.</div>`;
      return;
    }

    filtered.forEach(charge => {
      const isTerminal = ['RECOVERED', 'ESCALATED', 'ABANDONED'].includes(charge.state);
      const isDetected = charge.state === 'DETECTED';

      const card = document.createElement('div');
      card.className = 'charge-card';

      card.innerHTML = `
        <div>
          <div class="charge-header">
            <div>
              <div class="charge-id">${charge.id}</div>
              <div style="font-size: 0.75rem; color: var(--text-subtle);">Customer: ${charge.customer_id}</div>
            </div>
            <div class="badge badge-${charge.state}">${charge.state}</div>
          </div>

          <div class="charge-amount">$${charge.amount.toFixed(2)} <span style="font-size: 0.8rem; font-weight: 500; color: var(--text-subtle);">${charge.currency}</span></div>

          <div style="margin-top: 1rem;">
            <div class="detail-row">
              <span class="detail-key">Failure Reason</span>
              <span class="detail-val" style="color: var(--amber-warning);">${charge.failure_reason}</span>
            </div>
            <div class="detail-row">
              <span class="detail-key">Customer Attempts</span>
              <span class="detail-val">${charge.customer_attempt_count} / 3</span>
            </div>
            <div class="detail-row">
              <span class="detail-key">Infra Attempts</span>
              <span class="detail-val">${charge.infra_attempt_count} / 5</span>
            </div>
            <div class="detail-row">
              <span class="detail-key">Backoff Delay</span>
              <span class="detail-val">${charge.next_retry_delay_seconds ? charge.next_retry_delay_seconds + 's' : 'Immediate / N/A'}</span>
            </div>
          </div>
        </div>

        <div>
          <div class="charge-actions">
            ${isDetected ? `
              <button class="btn btn-secondary btn-sm btn-process" data-id="${charge.id}">
                <span>🧠</span> Process AI
              </button>
            ` : ''}
            
            ${!isTerminal ? `
              <button class="btn btn-primary btn-sm btn-retry" data-id="${charge.id}">
                <span>⚡</span> Retry
              </button>
            ` : `
              <button class="btn btn-secondary btn-sm" disabled style="opacity: 0.5; cursor: not-allowed;">
                Terminal
              </button>
            `}
            
            <button class="btn btn-secondary btn-sm btn-logs" data-id="${charge.id}">
              <span>📜</span> Logs
            </button>
          </div>
        </div>
      `;

      chargesGrid.appendChild(card);
    });

    // Attach Event Listeners
    document.querySelectorAll('.btn-process').forEach(btn => {
      btn.addEventListener('click', () => handleProcessCharge(btn.dataset.id));
    });
    document.querySelectorAll('.btn-retry').forEach(btn => {
      btn.addEventListener('click', () => handleRetryCharge(btn.dataset.id));
    });
    document.querySelectorAll('.btn-logs').forEach(btn => {
      btn.addEventListener('click', () => openAuditLogModal(btn.dataset.id));
    });
  }

  // Process Charge Endpoint Call
  async function handleProcessCharge(chargeId) {
    try {
      const res = await fetch(`/charges/${chargeId}/process`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Process failed');

      showToast(`Processed ${chargeId} -> ${data.decision.next_state} (${data.decision.decision_source})`, 'success');
      await loadDashboard();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // Idempotent Retry Endpoint Call
  async function handleRetryCharge(chargeId) {
    try {
      const idempKey = `idemp_ui_${chargeId}_${Date.now()}`;
      const res = await fetch(`/charges/${chargeId}/retry`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempKey
        }
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Retry execution failed');

      const isReplay = data.idempotent_replay;
      const status = data.result.status;
      showToast(`Retry executed for ${chargeId} -> ${status}${isReplay ? ' (Idempotent Replay)' : ''}`, 'success');
      await loadDashboard();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // Open Audit Log Modal Inspector
  async function openAuditLogModal(chargeId) {
    try {
      modalChargeTitle.textContent = `Audit Trail History: ${chargeId}`;
      modalChargeSubtitle.textContent = `Loading structured log history...`;
      logTimelineContainer.innerHTML = `<div style="text-align:center; color: var(--text-subtle);">Loading logs...</div>`;
      logModal.classList.add('active');

      const res = await fetch(`/charges/${chargeId}/logs`);
      if (!res.ok) throw new Error('Failed to fetch audit logs');
      const logs = await res.json();

      modalChargeSubtitle.textContent = `${logs.length} Structured Transitions Recorded`;
      renderLogTimeline(logs);
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function renderLogTimeline(logs) {
    logTimelineContainer.innerHTML = '';
    if (!logs || logs.length === 0) {
      logTimelineContainer.innerHTML = `<div style="text-align:center; color: var(--text-subtle);">No audit log entries recorded yet.</div>`;
      return;
    }

    logs.forEach(log => {
      const isAI = log.decision_source === 'ai_decision';
      const isSafetyOverride = log.decision_source_reason && log.decision_source_reason.includes('AI recommended RETRY but rule engine enforced ESCALATED');

      const item = document.createElement('div');
      item.className = 'log-item';
      item.style.background = 'rgba(15, 22, 35, 0.6)';
      item.style.border = '1px solid var(--border-color)';
      item.style.borderRadius = '12px';
      item.style.padding = '1rem';

      item.innerHTML = `
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem;">
          <div>
            <span class="badge ${isAI ? 'badge-RETRYING' : 'badge-DETECTED'}">
              ${isAI ? '🧠 AI Strategy' : '🛡️ Rule Guard'}
            </span>
            ${isSafetyOverride ? `<span class="badge badge-RETRY_SCHEDULED">⚠️ Safety Override</span>` : ''}
          </div>
          <div style="font-size: 0.75rem; color: var(--text-subtle); font-family: var(--font-mono);">${new Date(log.timestamp).toLocaleString()}</div>
        </div>

        <div style="display: flex; align-items: center; gap: 0.5rem; margin: 0.5rem 0;">
          <span class="badge badge-${log.previous_state}">${log.previous_state}</span>
          <span style="color: var(--text-subtle);">➔</span>
          <span class="badge badge-${log.new_state}">${log.new_state}</span>
        </div>

        <div style="font-size: 0.85rem; font-weight: 600; color: var(--text-main); margin-bottom: 0.25rem;">
          Event: <span style="font-family: var(--font-mono); color: var(--razorpay-blue);">${log.event}</span>
        </div>

        <div style="font-size: 0.825rem; color: var(--text-muted); margin-bottom: 0.5rem;">
          Reason: ${log.reason}
        </div>

        ${log.decision_source_reason ? `
          <div style="font-size: 0.75rem; color: var(--text-subtle); background: rgba(0,0,0,0.3); padding: 0.4rem 0.75rem; border-radius: 6px;">
            Rationale: ${log.decision_source_reason}
          </div>
        ` : ''}
      `;
      logTimelineContainer.appendChild(item);
    });
  }

  function closeModal() {
    logModal.classList.remove('active');
  }

  // Handle Inject Simulated Charge Form
  async function handleSimulateCharge(e) {
    e.preventDefault();
    const amount = parseFloat(document.getElementById('sim-amount').value);
    const failure_reason = document.getElementById('sim-reason').value;
    const customer_attempt_count = parseInt(document.getElementById('sim-customer-cnt').value);
    const infra_attempt_count = parseInt(document.getElementById('sim-infra-cnt').value);

    try {
      const res = await fetch('/charges/simulate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          amount,
          failure_reason,
          customer_attempt_count,
          infra_attempt_count,
          state: 'DETECTED'
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error('Simulation failed');

      showToast(`Injected charge ${data.id} ($${data.amount})`, 'success');
      await loadDashboard();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // Handle Reset Database
  async function handleResetDatabase() {
    if (!confirm('Are you sure you want to reset the database to initial mock state?')) return;
    try {
      const res = await fetch('/mock-data/reset', { method: 'POST' });
      if (!res.ok) throw new Error('Reset failed');
      showToast('Database reset to initial pristine state.', 'success');
      await loadDashboard();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // Toast Notification Helper
  function showToast(message, type = 'info') {
    const toastContainer = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.innerHTML = `
      <span>${type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️'}</span>
      <span>${message}</span>
    `;
    toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }
});
