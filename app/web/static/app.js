/* ============================================================
   Trading Bot Dashboard — Frontend Application
   ============================================================ */

(function () {
  'use strict';

  // ── Auth ──────────────────────────────────────────────────
  const Auth = {
    getToken() { return localStorage.getItem('auth_token'); },
    setToken(t) { localStorage.setItem('auth_token', t); },
    clearToken() { localStorage.removeItem('auth_token'); },
    isAuthenticated() { return !!this.getToken(); },
  };

  async function apiFetch(url, opts = {}) {
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    const token = Auth.getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(url, { ...opts, headers });
    if (res.status === 401) { Auth.clearToken(); showLogin(); throw new Error('Session expired'); }
    return res;
  }

  // ── Utilities ─────────────────────────────────────────────
  function $(sel) { return document.querySelector(sel); }
  function $$(sel) { return document.querySelectorAll(sel); }

  function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function formatTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return d.toLocaleDateString();
  }

  function formatPnl(v) {
    if (v == null) return '—';
    const n = parseFloat(v);
    const cls = n >= 0 ? 'pnl-positive' : 'pnl-negative';
    const sign = n >= 0 ? '+' : '';
    return `<span class="${cls}">${sign}${n.toFixed(2)}</span>`;
  }

  function formatPct(v) {
    if (v == null) return '—';
    return (parseFloat(v) * 100).toFixed(2) + '%';
  }

  // ── Navigation ────────────────────────────────────────────
  function showLogin() {
    $('#login-view').style.display = 'flex';
    $('#app').style.display = 'none';
    $('#login-msg').className = 'message';
    $('#login-msg').textContent = '';
    $('#otp-step').style.display = 'none';
    $('#request-step').style.display = 'block';
  }

  function showApp() {
    $('#login-view').style.display = 'none';
    $('#app').style.display = 'block';
    showTab('dashboard');
    startDashboardPolling();
    connectWebSocket();
    loadAllowedCommands();
  }

  function showTab(name) {
    $$('.tab-content').forEach(el => el.style.display = 'none');
    $$('.nav-tab').forEach(el => el.classList.remove('active'));
    const panel = $(`#${name}-view`);
    const tab = $(`.nav-tab[data-tab="${name}"]`);
    if (panel) panel.style.display = 'block';
    if (tab) tab.classList.add('active');

    if (name === 'dashboard') refreshDashboard();
    if (name === 'commands') refreshCommandStatus();
    if (name === 'history') refreshHistory();
  }

  // ── Login Flow ────────────────────────────────────────────
  function initLogin() {
    $('#btn-request-otp').addEventListener('click', async () => {
      const btn = $('#btn-request-otp');
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Sending...';
      try {
        const res = await fetch('/api/auth/request-otp', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        });
        const data = await res.json();
        if (res.ok) {
          $('#login-msg').className = 'message success';
          $('#login-msg').textContent = data.message || 'OTP sent! Check Telegram.';
          $('#request-step').style.display = 'none';
          $('#otp-step').style.display = 'block';
          $('#otp-code').focus();
        } else {
          $('#login-msg').className = 'message error';
          $('#login-msg').textContent = data.detail || 'Failed to send OTP';
        }
      } catch (e) {
        $('#login-msg').className = 'message error';
        $('#login-msg').textContent = 'Network error. Is the server running?';
      }
      btn.disabled = false;
      btn.innerHTML = '🔐 Request Login Code';
    });

    $('#btn-verify-otp').addEventListener('click', async () => {
      const code = $('#otp-code').value.trim();
      if (!code) return;
      const btn = $('#btn-verify-otp');
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Verifying...';
      try {
        const res = await fetch('/api/auth/verify-otp', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code }),
        });
        const data = await res.json();
        if (res.ok && data.token) {
          Auth.setToken(data.token);
          showApp();
        } else {
          $('#login-msg').className = 'message error';
          $('#login-msg').textContent = data.detail || 'Invalid code';
        }
      } catch (e) {
        $('#login-msg').className = 'message error';
        $('#login-msg').textContent = 'Network error';
      }
      btn.disabled = false;
      btn.innerHTML = 'Verify &amp; Login';
    });

    $('#otp-code').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') $('#btn-verify-otp').click();
    });
  }

  // ── Dashboard ─────────────────────────────────────────────
  let dashboardInterval = null;

  function startDashboardPolling() {
    if (dashboardInterval) clearInterval(dashboardInterval);
    refreshDashboard();
    dashboardInterval = setInterval(refreshDashboard, 10000);
  }

  async function refreshDashboard() {
    try {
      const [statusRes, tradesRes, approvedRes, winnerRes, equityRes] = await Promise.all([
        apiFetch('/api/dashboard/status'),
        apiFetch('/api/dashboard/trades?limit=20'),
        apiFetch('/api/dashboard/approved'),
        apiFetch('/api/dashboard/winner'),
        apiFetch('/api/dashboard/equity'),
      ]);

      const status = await statusRes.json();
      const trades = await tradesRes.json();
      const approved = await approvedRes.json();
      const winner = await winnerRes.json();
      const equity = await equityRes.json();

      renderStatus(status);
      renderTrades(trades.trades || []);
      renderApproved(approved.combinations || []);
      renderWinner(winner.winner);
      renderEquityChart(equity.equity_curve || []);
    } catch (e) { /* silently retry on next interval */ }
  }

  function renderStatus(s) {
    const modeClass = s.trading_mode === 'live' ? 'badge-error' : 'badge-success';
    const killClass = s.kill_switch ? 'badge-error' : 'badge-success';
    $('#status-cards').innerHTML = `
      <div class="stat-card"><div class="stat-label">Mode</div>
        <div class="stat-value"><span class="badge ${modeClass}">${s.trading_mode}</span></div></div>
      <div class="stat-card"><div class="stat-label">Live Trading</div>
        <div class="stat-value"><span class="badge ${s.live_enabled ? 'badge-error' : 'badge-success'}">${s.live_enabled ? 'ENABLED' : 'disabled'}</span></div></div>
      <div class="stat-card"><div class="stat-label">Kill Switch</div>
        <div class="stat-value"><span class="badge ${killClass}">${s.kill_switch ? 'ACTIVE' : 'off'}</span></div></div>
      <div class="stat-card"><div class="stat-label">Symbols</div>
        <div class="stat-value accent" style="font-size:1rem">${(s.symbols || []).join(', ')}</div></div>
      <div class="stat-card"><div class="stat-label">Interval</div>
        <div class="stat-value accent">${s.interval}</div></div>
      <div class="stat-card"><div class="stat-label">Risk / Trade</div>
        <div class="stat-value">${formatPct(s.max_risk_per_trade)}</div></div>
      <div class="stat-card"><div class="stat-label">Max Daily Loss</div>
        <div class="stat-value">${formatPct(s.max_daily_loss_pct)}</div></div>
      <div class="stat-card"><div class="stat-label">Stop Loss</div>
        <div class="stat-value">${formatPct(s.stop_loss_pct)}</div></div>
    `;
  }

  function renderTrades(trades) {
    if (!trades.length) {
      $('#trades-table-body').innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted)">No trades yet</td></tr>';
      return;
    }
    $('#trades-table-body').innerHTML = trades.map(t => `
      <tr>
        <td>${escapeHtml(t.symbol)}</td>
        <td><span class="badge ${t.side === 'BUY' ? 'badge-success' : 'badge-error'}">${t.side}</span></td>
        <td>${parseFloat(t.quantity || 0).toFixed(6)}</td>
        <td>${parseFloat(t.price || 0).toFixed(2)}</td>
        <td>${formatPnl(t.pnl)}</td>
        <td>${escapeHtml(t.strategy_name || '—')}</td>
        <td>${formatTime(t.timestamp)}</td>
      </tr>
    `).join('');
  }

  function renderApproved(combos) {
    const approved = combos.filter(c => c.approved);
    if (!approved.length) {
      $('#approved-grid').innerHTML = '<div class="empty-state"><div class="icon">📊</div><p>No approved combinations. Run optimize first.</p></div>';
      return;
    }
    $('#approved-grid').innerHTML = approved.map(c => `
      <div class="combo-card fade-in">
        <div class="combo-header">
          <span class="combo-pair">${escapeHtml(c.symbol)}/${escapeHtml(c.interval)}</span>
          <span class="badge badge-success">approved</span>
        </div>
        <div class="combo-stats">
          <span>Strategy <span class="val">${escapeHtml(c.strategy_name)}</span></span>
          <span>Robustness <span class="val">${(c.robustness_score || 0).toFixed(3)}</span></span>
          <span>Pass Rate <span class="val">${formatPct(c.pass_rate)}</span></span>
          <span>Regime <span class="val">${c.regime_tradable ? '✓' : '✗'}</span></span>
        </div>
      </div>
    `).join('');
  }

  function renderWinner(w) {
    if (!w) {
      $('#winner-section').innerHTML = '<div class="empty-state"><div class="icon">🏆</div><p>No winner selected. Run a backtest.</p></div>';
      return;
    }
    $('#winner-section').innerHTML = `
      <div class="winner-card fade-in">
        <div class="winner-name">🏆 ${escapeHtml(w.strategy_name)}</div>
        <div class="winner-metrics">
          <div class="metric-item"><span class="metric-label">Return</span><span class="metric-val">${formatPct(w.total_return_pct)}</span></div>
          <div class="metric-item"><span class="metric-label">Sharpe</span><span class="metric-val">${(w.sharpe_ratio || 0).toFixed(2)}</span></div>
          <div class="metric-item"><span class="metric-label">Max DD</span><span class="metric-val">${formatPct(w.max_drawdown_pct)}</span></div>
          <div class="metric-item"><span class="metric-label">Win Rate</span><span class="metric-val">${formatPct(w.win_rate)}</span></div>
          <div class="metric-item"><span class="metric-label">Trades</span><span class="metric-val">${w.total_trades}</span></div>
          <div class="metric-item"><span class="metric-label">Qualified</span><span class="metric-val">${w.qualified ? '✅' : '❌'}</span></div>
        </div>
      </div>
    `;
  }

  // ── Equity Chart (Canvas) ──────────────────────────────────
  function renderEquityChart(data) {
    const canvas = $('#equity-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.parentElement.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = rect.width + 'px';
    canvas.style.height = rect.height + 'px';
    ctx.scale(dpr, dpr);
    const W = rect.width, H = rect.height;

    ctx.fillStyle = '#161b22';
    ctx.fillRect(0, 0, W, H);

    if (!data.length) {
      ctx.fillStyle = '#6e7681';
      ctx.font = '14px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('No equity data yet', W / 2, H / 2);
      return;
    }

    const values = data.map(d => d.total_equity);
    const minV = Math.min(...values) * 0.998;
    const maxV = Math.max(...values) * 1.002;
    const range = maxV - minV || 1;
    const pad = { top: 30, right: 60, bottom: 30, left: 16 };
    const cW = W - pad.left - pad.right;
    const cH = H - pad.top - pad.bottom;

    // Grid lines
    ctx.strokeStyle = '#21262d';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + (cH / 4) * i;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(W - pad.right, y); ctx.stroke();
      const val = maxV - (range / 4) * i;
      ctx.fillStyle = '#6e7681';
      ctx.font = '11px Inter, sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText('$' + val.toFixed(0), W - pad.right + 6, y + 4);
    }

    // Equity line
    const grad = ctx.createLinearGradient(0, 0, W, 0);
    grad.addColorStop(0, '#00d4aa');
    grad.addColorStop(1, '#00bcd4');
    ctx.strokeStyle = grad;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    for (let i = 0; i < values.length; i++) {
      const x = pad.left + (i / (values.length - 1)) * cW;
      const y = pad.top + (1 - (values[i] - minV) / range) * cH;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Area fill
    const lastX = pad.left + cW;
    const lastY = pad.top + (1 - (values[values.length - 1] - minV) / range) * cH;
    ctx.lineTo(lastX, pad.top + cH);
    ctx.lineTo(pad.left, pad.top + cH);
    ctx.closePath();
    const areaGrad = ctx.createLinearGradient(0, pad.top, 0, pad.top + cH);
    areaGrad.addColorStop(0, 'rgba(0,212,170,.15)');
    areaGrad.addColorStop(1, 'rgba(0,212,170,.0)');
    ctx.fillStyle = areaGrad;
    ctx.fill();

    // Current value label
    ctx.fillStyle = '#e6edf3';
    ctx.font = 'bold 13px Inter, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText('$' + values[values.length - 1].toFixed(2), W - pad.right, pad.top - 10);
  }

  // ── Commands ──────────────────────────────────────────────
  let ws = null;
  let wsReconnectDelay = 1000;

  async function loadAllowedCommands() {
    try {
      const res = await apiFetch('/api/commands/allowed');
      const data = await res.json();
      const sel = $('#cmd-select');
      sel.innerHTML = '<option value="">— Select command —</option>';
      (data.commands || []).forEach(cmd => {
        const opt = document.createElement('option');
        opt.value = cmd;
        opt.textContent = cmd;
        sel.appendChild(opt);
      });
    } catch (e) { /* retry later */ }
  }

  function initCommands() {
    $('#btn-run-cmd').addEventListener('click', async () => {
      const cmd = $('#cmd-select').value;
      if (!cmd) return;
      const argsStr = $('#cmd-args').value.trim();
      const args = argsStr ? argsStr.split(/\s+/) : [];

      $('#btn-run-cmd').disabled = true;
      clearTerminal();
      appendTerminalLine(`$ python -m app.cli ${cmd} ${argsStr}`, 'system');

      try {
        const res = await apiFetch('/api/commands/run', {
          method: 'POST',
          body: JSON.stringify({ command: cmd, args }),
        });
        const data = await res.json();
        if (!res.ok) {
          appendTerminalLine(`[ERROR] ${data.detail || 'Failed to start'}`, 'error');
          $('#btn-run-cmd').disabled = false;
        } else {
          updateCommandUI(true, cmd);
        }
      } catch (e) {
        appendTerminalLine(`[ERROR] ${e.message}`, 'error');
        $('#btn-run-cmd').disabled = false;
      }
    });

    $('#btn-stop-cmd').addEventListener('click', async () => {
      try {
        await apiFetch('/api/commands/stop', { method: 'POST' });
        appendTerminalLine('[SYSTEM] Stop signal sent...', 'system');
      } catch (e) { /* ignore */ }
    });
  }

  async function refreshCommandStatus() {
    try {
      const res = await apiFetch('/api/commands/status');
      const data = await res.json();
      if (data.running) {
        updateCommandUI(true, data.command);
        // Show existing output for a currently-running command
        if (data.output && data.output.length && !$('#terminal-output').children.length) {
          data.output.forEach(line => appendTerminalLine(line));
        }
      } else {
        // Command is not running — reset UI to idle
        // Don't repopulate terminal with stale output from a finished command
        updateCommandUI(false);
      }
    } catch (e) { /* ignore */ }
  }

  function updateCommandUI(running, cmdName = '') {
    $('#btn-run-cmd').disabled = running;
    $('#btn-stop-cmd').style.display = running ? 'inline-flex' : 'none';
    $('#cmd-select').disabled = running;
    $('#cmd-args').disabled = running;

    const indicator = $('#running-indicator');
    if (running) {
      indicator.style.display = 'flex';
      indicator.innerHTML = `<span class="status-dot running"></span> Running: <strong>${escapeHtml(cmdName)}</strong>`;
    } else {
      indicator.style.display = 'none';
    }

    // Update nav status
    const navDot = $('#nav-cmd-dot');
    if (navDot) {
      navDot.className = running ? 'status-dot running' : 'status-dot';
      $('#nav-cmd-text').textContent = running ? 'Command running' : 'Idle';
    }
  }

  function clearTerminal() {
    $('#terminal-output').innerHTML = '';
  }

  function appendTerminalLine(text, type = '') {
    const el = document.createElement('span');
    el.className = 'terminal-line' + (type ? ` ${type}` : '');
    el.textContent = text;
    const container = $('#terminal-output');
    container.appendChild(el);
    container.appendChild(document.createTextNode('\n'));
    container.scrollTop = container.scrollHeight;
  }

  // ── WebSocket ─────────────────────────────────────────────
  function connectWebSocket() {
    if (ws && ws.readyState <= 1) return; // already open or connecting

    const token = Auth.getToken();
    if (!token) return;

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/api/commands/ws?token=${encodeURIComponent(token)}`;

    ws = new WebSocket(url);

    ws.onopen = () => {
      wsReconnectDelay = 1000;
      updateWsIndicator(true);
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'replay' && msg.lines) {
          clearTerminal();
          msg.lines.forEach(line => appendTerminalLine(line));
        } else if (msg.type === 'output' && msg.line != null) {
          const line = msg.line;
          let type = '';
          if (line.startsWith('[SYSTEM') || line.startsWith('[COMMAND')) type = 'system';
          else if (line.includes('[ERR]') || line.includes('error')) type = 'error';
          appendTerminalLine(line, type);
        } else if (msg.type === 'status' && msg.data) {
          const d = msg.data;
          updateCommandUI(d.running, d.command || '');
          // If command just finished, show exit code badge
          if (d.finished && !d.running && d.exit_code != null) {
            const exitClass = d.exit_code === 0 ? 'success' : 'error';
            appendTerminalLine(`\n✓ Command completed (exit code: ${d.exit_code})`, exitClass);
          }
        } else if (msg.type === 'pong') {
          /* heartbeat ack */
        }
      } catch (e) { /* ignore parse errors */ }
    };

    ws.onclose = () => {
      updateWsIndicator(false);
      setTimeout(() => {
        wsReconnectDelay = Math.min(wsReconnectDelay * 2, 30000);
        if (Auth.isAuthenticated()) connectWebSocket();
      }, wsReconnectDelay);
    };

    ws.onerror = () => { ws.close(); };

    // Heartbeat ping every 30s
    const pingInterval = setInterval(() => {
      if (ws.readyState === 1) ws.send('ping');
      else clearInterval(pingInterval);
    }, 30000);
  }

  function updateWsIndicator(connected) {
    const dot = $('#ws-dot');
    const text = $('#ws-text');
    if (dot) dot.className = connected ? 'status-dot connected' : 'status-dot disconnected';
    if (text) text.textContent = connected ? 'Connected' : 'Disconnected';
  }

  // ── History ───────────────────────────────────────────────
  async function refreshHistory() {
    try {
      const res = await apiFetch('/api/commands/history');
      const data = await res.json();
      const list = $('#history-list');
      if (!(data.history || []).length) {
        list.innerHTML = '<div class="empty-state"><div class="icon">📜</div><p>No command history yet</p></div>';
        return;
      }
      list.innerHTML = data.history.map((h, i) => {
        const exitClass = h.exit_code === 0 ? 'badge-success' : (h.exit_code == null ? 'badge-warning' : 'badge-error');
        const exitLabel = h.exit_code === 0 ? 'OK' : (h.exit_code == null ? '?' : `Exit ${h.exit_code}`);
        return `
          <div class="history-item fade-in">
            <div class="history-item-header" onclick="this.nextElementSibling.classList.toggle('open')">
              <span class="cmd-name">${escapeHtml(h.command)} ${(h.args || []).map(escapeHtml).join(' ')}</span>
              <span class="cmd-meta">
                <span class="badge ${exitClass}">${exitLabel}</span>
                <span>${h.duration_seconds}s</span>
                <span>${formatTime(h.started_at)}</span>
              </span>
            </div>
            <div class="history-item-body">
              <pre>${(h.output_tail || []).map(escapeHtml).join('\n')}</pre>
            </div>
          </div>
        `;
      }).join('');
    } catch (e) { /* ignore */ }
  }

  // ── Init ──────────────────────────────────────────────────
  function init() {
    initLogin();
    initCommands();

    // Nav tabs
    $$('.nav-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const t = tab.dataset.tab;
        if (t === 'logout') {
          Auth.clearToken();
          if (ws) ws.close();
          showLogin();
        } else {
          showTab(t);
        }
      });
    });

    // Route on load
    if (Auth.isAuthenticated()) {
      showApp();
    } else {
      showLogin();
    }

    // Resize chart
    window.addEventListener('resize', () => {
      if ($('#app').style.display !== 'none') {
        // Re-render equity chart on resize
        const canvas = $('#equity-canvas');
        if (canvas && canvas._lastData) renderEquityChart(canvas._lastData);
      }
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
