// ============================================================
// Illusion Dashboard - AI-Powered Trade Assistant
// ============================================================

const API_BASE = '';
let _authUser = null;

function isAdminUser() {
    return (_authUser?.role || '') === 'admin';
}

function _toggleAuthOverlay(show) {
    const el = document.getElementById('auth-overlay');
    if (!el) return;
    el.style.display = show ? 'flex' : 'none';
}

function applyRoleAccess() {
    const badge = document.getElementById('auth-user-badge');
    const logoutBtn = document.getElementById('auth-logout-btn');
    if (badge) {
        badge.textContent = _authUser
            ? `${_authUser.username} (${_authUser.role})`
            : '';
    }
    if (logoutBtn) logoutBtn.style.display = _authUser ? 'inline-flex' : 'none';

    const settingsTab = document.getElementById('tab-settings');
    if (settingsTab) settingsTab.style.display = isAdminUser() ? '' : 'none';

    const adminButtons = [
        'btn-index-scan', 'btn-index-scan-positional',
        'btn-index-ce-buy', 'btn-index-pe-buy',
        'btn-index-manual-refresh', 'btn-scan-swing',
        'btn-scan-fno', 'btn-refresh-instruments', 'btn-value-refresh',
        'btn-refresh-sectors', 'btn-sector-telegram'
    ];
    adminButtons.forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.disabled = !isAdminUser();
    });
    const pnlInputs = ['pnl-max-profit', 'pnl-max-loss'];
    pnlInputs.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = !isAdminUser();
    });
}

async function initAuth() {
    try {
        const resp = await fetch(`${API_BASE}/api/auth/me`);
        const data = await resp.json();
        if (data?.authenticated && data?.user) {
            _authUser = data.user;
            _toggleAuthOverlay(false);
            applyRoleAccess();
            return true;
        }
    } catch (_) {}
    _authUser = null;
    _toggleAuthOverlay(true);
    applyRoleAccess();
    return false;
}

async function loginAppUser() {
    const u = document.getElementById('auth-username');
    const p = document.getElementById('auth-password');
    const err = document.getElementById('auth-error');
    const btn = document.getElementById('btn-auth-login');
    if (!u || !p || !err || !btn) return;
    err.textContent = '';
    btn.disabled = true;
    btn.textContent = 'Signing In...';
    try {
        const resp = await fetch(`${API_BASE}/api/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: u.value.trim(), password: p.value }),
        });
        const data = await resp.json();
        if (!data?.success) throw new Error(data?.message || 'Login failed');
        _authUser = data.user;
        _toggleAuthOverlay(false);
        applyRoleAccess();
        startApp();
    } catch (e) {
        err.textContent = e.message || 'Login failed';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Sign In';
    }
}

async function logoutAppUser() {
    try { await fetch(`${API_BASE}/api/auth/logout`, { method: 'POST' }); } catch (_) {}
    _authUser = null;
    _toggleAuthOverlay(true);
    applyRoleAccess();
}

// --- Sidebar Collapse ---

function toggleSidebar() {
    const sidebar = document.querySelector('.sidebar');
    sidebar.classList.toggle('collapsed');
    localStorage.setItem('sidebar-collapsed', sidebar.classList.contains('collapsed'));
}

function initSidebar() {
    const collapsed = localStorage.getItem('sidebar-collapsed') === 'true';
    if (collapsed) document.querySelector('.sidebar').classList.add('collapsed');
}

// --- Table Sorting ---

function makeTableSortable(tableEl) {
    if (!tableEl) return;
    const headers = tableEl.querySelectorAll('th');
    headers.forEach((th, colIdx) => {
        if (!th.textContent.trim()) return;
        th.classList.add('sortable');
        th.addEventListener('click', () => sortTable(tableEl, colIdx, th));
    });
}

function sortTable(tableEl, colIdx, th) {
    const tbody = tableEl.querySelector('tbody');
    if (!tbody) return;

    const rows = Array.from(tbody.querySelectorAll('tr'));
    const isAsc = th.classList.contains('sort-asc');
    const dir = isAsc ? -1 : 1;

    tableEl.querySelectorAll('th').forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
    th.classList.add(isAsc ? 'sort-desc' : 'sort-asc');

    rows.sort((a, b) => {
        const aCell = a.children[colIdx];
        const bCell = b.children[colIdx];
        if (!aCell || !bCell) return 0;

        let aVal = (aCell.textContent || '').trim();
        let bVal = (bCell.textContent || '').trim();

        const aNum = parseFloat(aVal.replace(/[^0-9.\-]/g, ''));
        const bNum = parseFloat(bVal.replace(/[^0-9.\-]/g, ''));

        if (!isNaN(aNum) && !isNaN(bNum)) return (aNum - bNum) * dir;
        return aVal.localeCompare(bVal) * dir;
    });

    rows.forEach(r => tbody.appendChild(r));
}

function applySortable(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const table = el.querySelector('.data-table');
    if (table) makeTableSortable(table);
}

// --- Filter Pills ---

function togglePill(pill, section) {
    const container = pill.parentElement;
    const isAllPill = pill.dataset.value === '';

    if (isAllPill) {
        container.querySelectorAll('.filter-pill').forEach(p => p.classList.remove('active'));
        pill.classList.add('active');
    } else {
        const allPill = container.querySelector('.filter-pill[data-value=""]');
        if (allPill) allPill.classList.remove('active');
        pill.classList.toggle('active');
        const anyActive = container.querySelectorAll('.filter-pill.active');
        if (anyActive.length === 0 && allPill) allPill.classList.add('active');
    }

    if (section === 'swing') loadSignals('swing');
    else if (section === 'fno') loadFnoSignals();
}

function getSelectedPills(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];
    const active = container.querySelectorAll('.filter-pill.active');
    const values = Array.from(active).map(p => p.dataset.value).filter(v => v !== '');
    return values;
}

// --- Theme ---

function initTheme() {
    const saved = localStorage.getItem('vcp-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    updateThemeIcons(saved);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('vcp-theme', next);
    updateThemeIcons(next);

    if (_selectedSector && window._lastSectors) {
        loadSectorChart(_selectedSector);
    }
}

function updateThemeIcons(theme) {
    const sun = document.getElementById('icon-sun');
    const moon = document.getElementById('icon-moon');
    if (!sun || !moon) return;
    if (theme === 'dark') {
        sun.classList.remove('hidden');
        moon.classList.add('hidden');
    } else {
        sun.classList.add('hidden');
        moon.classList.remove('hidden');
    }
}

function isDark() {
    return document.documentElement.getAttribute('data-theme') === 'dark';
}

// --- Daily P&L Guard (Dhan pnlExit) ---

function _setPnlGuardStatus(msg, isError = false) {
    const el = document.getElementById('pnl-guard-status');
    if (!el) return;
    el.textContent = msg || '';
    el.style.color = isError ? '#ef4444' : 'var(--text-muted)';
}

async function loadPnlGuardStatus() {
    const profitInput = document.getElementById('pnl-max-profit');
    const lossInput = document.getElementById('pnl-max-loss');
    if (!profitInput || !lossInput) return;
    _setPnlGuardStatus('Checking status...');
    try {
        const resp = await fetch(`${API_BASE}/api/settings/pnl-exit`);
        const result = await resp.json();
        if (!result?.success) {
            _setPnlGuardStatus(result?.message || 'Not configured');
            return;
        }

        const data = result.data || {};
        const status = String(data.pnlExitStatus || data.status || '').toUpperCase();
        const profit = data.profit ?? data.profitValue ?? '';
        const loss = data.loss ?? data.lossValue ?? '';
        if (profit !== '' && profit !== null && profit !== undefined) profitInput.value = Number(profit);
        if (loss !== '' && loss !== null && loss !== undefined) lossInput.value = Math.abs(Number(loss));

        // Dhan returns PENDING before it moves to ACTIVE; both should be treated
        // as configured for UI clarity.
        if (status === 'ACTIVE' || status === 'PENDING') {
            const profitLabel = (profit !== '' && profit !== null && profit !== undefined) ? Number(profit) : '-';
            const lossLabel = (loss !== '' && loss !== null && loss !== undefined) ? Math.abs(Number(loss)) : '-';
            _setPnlGuardStatus(`${status} · Profit ${profitLabel} · Loss ${lossLabel}`);
        } else {
            _setPnlGuardStatus('Inactive');
        }
    } catch (e) {
        _setPnlGuardStatus(`Status error: ${e.message}`, true);
    }
}

async function savePnlGuard() {
    if (!isAdminUser()) {
        _setPnlGuardStatus('Readonly user cannot modify P&L Guard', true);
        return;
    }
    const profitInput = document.getElementById('pnl-max-profit');
    const lossInput = document.getElementById('pnl-max-loss');
    if (!profitInput || !lossInput) return;

    const maxProfit = profitInput.value ? Number(profitInput.value) : null;
    const maxLoss = lossInput.value ? Number(lossInput.value) : null;
    if (maxProfit === null && maxLoss === null) {
        _setPnlGuardStatus('Enter max profit or max loss first', true);
        return;
    }

    _setPnlGuardStatus('Applying...');
    try {
        const resp = await fetch(`${API_BASE}/api/settings/pnl-exit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                max_profit: maxProfit,
                max_loss: maxLoss,
                product_type: ['INTRADAY', 'DELIVERY'],
                enable_kill_switch: false,
            }),
        });
        const result = await resp.json();
        if (!result?.success) {
            _setPnlGuardStatus(result?.message || 'Failed to apply', true);
            return;
        }
        _setPnlGuardStatus('Applied successfully');
        await loadPnlGuardStatus();
    } catch (e) {
        _setPnlGuardStatus(`Apply error: ${e.message}`, true);
    }
}

async function disablePnlGuard() {
    if (!isAdminUser()) {
        _setPnlGuardStatus('Readonly user cannot disable P&L Guard', true);
        return;
    }
    _setPnlGuardStatus('Disabling...');
    try {
        const resp = await fetch(`${API_BASE}/api/settings/pnl-exit`, { method: 'DELETE' });
        const result = await resp.json();
        if (!result?.success) {
            _setPnlGuardStatus(result?.message || 'Failed to disable', true);
            return;
        }
        _setPnlGuardStatus('Disabled');
    } catch (e) {
        _setPnlGuardStatus(`Disable error: ${e.message}`, true);
    }
}

// --- Tab Switching ---

function switchTab(tab) {
    if (tab === 'settings' && !isAdminUser()) return;
    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));

    document.getElementById(`content-${tab}`).classList.remove('hidden');
    document.getElementById(`tab-${tab}`).classList.add('active');

    if (tab === 'sectors') {
        loadSectorsTab();
        syncSectorTelegramToggle();
    }
    if (tab === 'market-direction') { loadMarketDirection(); }
    if (tab === 'swing') { loadSignals('swing'); showVcpInfo(); }
    if (tab === 'fno') { loadFnoSignals(); loadSchedulerStatus(); switchFnoSubTab(fnoSubTab); }
    if (tab === 'index') { loadIndexSignals(); }
    if (tab === 'value') { loadValueInvesting(); }
    if (tab === 'journal') loadTrades();
    if (tab === 'settings') { loadBackups(); loadSectorDisplay(); }
    if (tab !== 'index' && _indexManualMonitorTimer) {
        clearInterval(_indexManualMonitorTimer);
        _indexManualMonitorTimer = null;
    }
}

// --- Sector Analysis Tab ---

function _marketBiasColor(bias) {
    const b = String(bias || '').toUpperCase();
    if (b.includes('STRONGLY BULLISH')) return '#16a34a';
    if (b.includes('BULLISH')) return '#22c55e';
    if (b.includes('STRONGLY BEARISH')) return '#dc2626';
    if (b.includes('BEARISH')) return '#ef4444';
    return '#eab308';
}

function _marketBiasBadge(bias) {
    return `<span class="text-2xs px-2 py-1 rounded-full" style="background:rgba(99,102,241,0.12);color:${_marketBiasColor(bias)};font-weight:700">${bias || 'SIDEWAYS / RANGE-BOUND'}</span>`;
}

async function sendMarketDirectionHistoryToTelegram(predictionId, btnEl = null) {
    if (!predictionId) return;
    const statusEl = document.getElementById('market-direction-status');
    const oldText = btnEl ? btnEl.textContent : '';
    if (btnEl) {
        btnEl.disabled = true;
        btnEl.textContent = 'Sending...';
    }
    try {
        const resp = await fetch(`${API_BASE}/api/market-direction/history/${predictionId}/notify`, {
            method: 'POST',
        });
        const data = await resp.json();
        if (!resp.ok || !data?.ok) throw new Error(data?.message || data?.detail || 'Send failed');
        if (statusEl) {
            statusEl.textContent = 'AI prediction sent to Telegram';
            statusEl.style.color = '#22c55e';
        }
    } catch (err) {
        if (statusEl) {
            statusEl.textContent = `Telegram send failed: ${err.message}`;
            statusEl.style.color = '#ef4444';
        }
    } finally {
        if (btnEl) {
            btnEl.disabled = false;
            btnEl.textContent = oldText || 'Send';
        }
    }
}

let _marketDirectionSubTab = 'today';
let _marketDirectionDashboard = null;

function switchMarketDirectionSubTab(tab, btnEl = null) {
    _marketDirectionSubTab = tab || 'today';
    const wrap = document.getElementById('market-direction-subtab');
    if (wrap) {
        wrap.querySelectorAll('.filter-pill').forEach((el) => {
            el.classList.toggle('active', el.dataset.value === _marketDirectionSubTab);
        });
    } else if (btnEl) {
        document.querySelectorAll('#market-direction-subtab .filter-pill').forEach((el) => el.classList.remove('active'));
        btnEl.classList.add('active');
    }
    if (_marketDirectionDashboard) renderMarketDirection(_marketDirectionDashboard);
}

async function loadMarketDirection(force = false) {
    const statusEl = document.getElementById('market-direction-status');
    const contentEl = document.getElementById('market-direction-content');
    const btn = document.getElementById('btn-market-direction-refresh');
    if (!statusEl || !contentEl) return;
    if (btn) btn.disabled = true;
    statusEl.textContent = 'Analyzing NIFTY and SENSEX...';
    statusEl.style.color = 'var(--text-muted)';
    if (force) {
        contentEl.innerHTML = `<div class="p-8 text-center text-2xs" style="color:var(--text-muted)"><span class="spinner"></span> Refreshing prediction...</div>`;
    }

    try {
        const resp = await fetch(`${API_BASE}/api/market-direction/dashboard${force ? '?force_refresh=true' : ''}`);
        const data = await resp.json();
        if (!resp.ok) throw new Error(data?.detail || 'Prediction failed');
        _marketDirectionDashboard = data;
        renderMarketDirection(data);
        const ts = data.as_of ? new Date(data.as_of).toLocaleString() : 'n/a';
        statusEl.textContent = `Updated: ${ts}`;
        statusEl.style.color = '#22c55e';
    } catch (err) {
        contentEl.innerHTML = `<div class="p-6 text-center text-2xs" style="color:#ef4444">Market direction load failed: ${err.message}</div>`;
        statusEl.textContent = 'Prediction unavailable';
        statusEl.style.color = '#ef4444';
    } finally {
        if (btn) btn.disabled = false;
    }
}

function renderMarketDirection(data) {
    const contentEl = document.getElementById('market-direction-content');
    if (!contentEl) return;
    const section = _marketDirectionSubTab || 'today';
    const todayPred = data?.today || {};
    const tomorrowPred = data?.tomorrow || {};
    const history = Array.isArray(data?.history) ? data.history : [];
    const streak = data?.streak || {};
    const timing = data?.timing || {};

    const buildPredictionCard = (pred, heading, opts = {}) => {
        const showOpenType = !!opts.showOpenType;
        const probs = pred?.probabilities || {};
        const breadth = pred?.breadth || {};
        const indices = Array.isArray(pred?.indices) ? pred.indices : [];
        const targetDate = pred?.target_date ? new Date(pred.target_date).toLocaleDateString() : '-';
        const indexCards = indices.map((idx) => {
            const tf = idx.timeframe_scores || {};
            const notes = Array.isArray(idx.notes) ? idx.notes : [];
            const snap = idx.technical_snapshot || {};
            const session = idx.session_context || {};
            const tfText = Object.entries(tf).map(([k, v]) => `${k.toUpperCase()}: ${(Number(v) * 100).toFixed(0)}`).join(' · ') || '-';
            const unavailableNote = idx.analysis_ok ? '' : `<div class="text-2xs mb-1" style="color:#f59e0b">Data unavailable${idx.reason ? ` (${idx.reason})` : ''}</div>`;
            const openType = session.open_type || 'UNKNOWN';
            const gapText = Number.isFinite(Number(session.gap_pct)) ? ` (${Number(session.gap_pct).toFixed(2)}%)` : '';
            return `
                <div class="glass-card-sm p-2" style="min-width:280px">
                    <div class="flex items-center justify-between mb-1">
                        <span class="text-xs font-semibold" style="color:var(--text-heading)">${idx.index || '-'}</span>
                        ${_marketBiasBadge(idx.bias)}
                    </div>
                    ${unavailableNote}
                    ${showOpenType ? `<div class="text-2xs mb-1" style="color:var(--text-muted)">Open Type: <b style="color:var(--text-heading)">${openType}${gapText}</b></div>` : ''}
                    <div class="text-2xs mb-1" style="color:var(--text-muted)">Confidence: <b style="color:var(--text-heading)">${idx.confidence ?? 0}%</b></div>
                    <div class="text-2xs mb-1" style="color:var(--text-muted)">TF scores: ${tfText}</div>
                    <div class="text-2xs mb-1" style="color:var(--text-muted)">
                        EMA20 ${snap.ema20 ?? '-'} · EMA50 ${snap.ema50 ?? '-'} · EMA200 ${snap.ema200 ?? '-'} · RSI ${snap.rsi14 ?? '-'} · ADX ${snap.adx14 ?? '-'}
                    </div>
                    <div class="text-2xs" style="color:var(--text-muted)">
                        ${(notes || []).slice(0, 3).map(n => `• ${n}`).join('<br/>') || '-'}
                    </div>
                </div>
            `;
        }).join('');
        return `
            <div class="mb-3">
                <div class="flex items-center gap-2 mb-1">
                    <span class="text-xs font-semibold" style="color:var(--text-heading)">${heading} (${targetDate})</span>
                    ${_marketBiasBadge(pred.market_bias)}
                    <span class="text-2xs" style="color:var(--text-muted)">Confidence:</span>
                    <span class="text-xs font-bold" style="color:var(--text-heading)">${pred.confidence ?? 0}%</span>
                </div>
                <div class="text-2xs" style="color:var(--text-muted)">
                    Probabilities → Bullish: <b>${probs.bullish ?? '-' }%</b> · Sideways: <b>${probs.sideways ?? '-' }%</b> · Bearish: <b>${probs.bearish ?? '-' }%</b>
                </div>
                <div class="text-2xs mt-1" style="color:var(--text-muted)">
                    Sector breadth: <b>${breadth.strong_count ?? 0}/${breadth.total ?? 0}</b> strong sectors (${Math.round((Number(breadth.strong_share || 0)) * 100)}%)
                </div>
                <div class="text-2xs mt-1" style="color:var(--text-muted)">
                    ${timing.note || ''}
                </div>
                ${pred.model_note ? `<div class="text-2xs mt-1" style="color:var(--text-muted)">${pred.model_note}</div>` : ''}
                ${pred.note ? `<div class="text-2xs mt-1" style="color:#f59e0b">${pred.note}</div>` : ''}
            </div>
            <div class="grid md:grid-cols-2 gap-2">${indexCards || '<div class="text-2xs" style="color:var(--text-muted)">No index analysis data.</div>'}</div>
            <div class="text-2xs mt-3" style="color:var(--text-muted)">${pred.disclaimer || ''}</div>
        `;
    };

    const historyRows = history.map((row) => {
        const result = String(row.result || 'PENDING').toUpperCase();
        const resultColor = result === 'HIT' ? '#22c55e' : result === 'MISS' ? '#ef4444' : '#eab308';
        const sendBtn = row.id
            ? `<button class="btn-secondary px-2 py-1 rounded text-2xs" onclick="sendMarketDirectionHistoryToTelegram(${Number(row.id)}, this)">Send</button>`
            : '-';
        return `
            <tr>
                <td class="px-2 py-1">${row.target_date ? new Date(row.target_date).toLocaleDateString() : '-'}</td>
                <td class="px-2 py-1">${(row.horizon || '-').toUpperCase()}</td>
                <td class="px-2 py-1">${_marketBiasBadge(row.predicted_bias)}</td>
                <td class="px-2 py-1">${row.confidence ?? 0}%</td>
                <td class="px-2 py-1">${row.actual_bias ? _marketBiasBadge(row.actual_bias) : '<span class="text-2xs" style="color:var(--text-muted)">Pending</span>'}</td>
                <td class="px-2 py-1"><span class="text-2xs font-semibold" style="color:${resultColor}">${result}</span></td>
                <td class="px-2 py-1">${sendBtn}</td>
            </tr>
        `;
    }).join('');

    let sectionHtml = '';
    if (section === 'tomorrow') {
        sectionHtml = buildPredictionCard(tomorrowPred, 'Tomorrow', { showOpenType: false });
    } else if (section === 'history') {
        const streakValue = Number.isFinite(Number(streak.current)) ? Number(streak.current) : 0;
        const streakHorizon = String(streak.horizon || 'tomorrow').toUpperCase();
        sectionHtml = `
            <div class="text-2xs mb-2" style="color:var(--text-muted)">Algo prediction vs actual market outcome.</div>
            <div class="text-2xs mb-2" style="color:var(--text-heading)">Current HIT Streak (${streakHorizon}): <b style="color:#22c55e">${streakValue}</b> day(s)</div>
            <div class="overflow-auto">
                <table class="w-full text-2xs">
                    <thead>
                        <tr style="color:var(--text-muted);border-bottom:1px solid rgba(148,163,184,0.25)">
                            <th class="text-left px-2 py-1">Date</th>
                            <th class="text-left px-2 py-1">Type</th>
                            <th class="text-left px-2 py-1">Predicted</th>
                            <th class="text-left px-2 py-1">Confidence</th>
                            <th class="text-left px-2 py-1">Actual</th>
                            <th class="text-left px-2 py-1">Result</th>
                            <th class="text-left px-2 py-1">Telegram</th>
                        </tr>
                    </thead>
                    <tbody>${historyRows || '<tr><td colspan="7" class="px-2 py-3" style="color:var(--text-muted)">No history yet.</td></tr>'}</tbody>
                </table>
            </div>
        `;
    } else {
        sectionHtml = buildPredictionCard(todayPred, 'Today', { showOpenType: true });
    }

    contentEl.innerHTML = `
        ${sectionHtml}
        <div class="text-2xs mt-3" style="color:var(--text-muted)">Today prediction reliable after: <b>${timing.today_reliable_after_ist || '09:45'} IST</b></div>
    `;
}

// --- Sector Analysis Tab ---

const SECTOR_TELEGRAM_TOGGLE_KEY = 'sector-telegram-on-refresh';
let _sectorTelegramOnRefresh = localStorage.getItem(SECTOR_TELEGRAM_TOGGLE_KEY) === 'true';

function syncSectorTelegramToggle() {
    const btn = document.getElementById('btn-sector-telegram');
    if (!btn) return;
    btn.textContent = 'T';
    if (_sectorTelegramOnRefresh) {
        btn.style.setProperty('border', '1px solid #16a34a', 'important');
        btn.style.setProperty('background', '#16a34a', 'important');
        btn.style.setProperty('background-image', 'none', 'important');
        btn.style.setProperty('color', '#ffffff', 'important');
    } else {
        btn.style.setProperty('border', '1px solid rgba(107,114,128,0.45)', 'important');
        btn.style.setProperty('background', 'rgba(107,114,128,0.12)', 'important');
        btn.style.setProperty('background-image', 'none', 'important');
        btn.style.setProperty('color', 'var(--text-muted)', 'important');
    }
    btn.style.fontWeight = '700';
    btn.title = _sectorTelegramOnRefresh
        ? 'Telegram on refresh: ON (click to disable)'
        : 'Telegram on refresh: OFF (click to enable)';
}

function toggleSectorTelegramOnRefresh() {
    if (!isAdminUser()) return;
    _sectorTelegramOnRefresh = !_sectorTelegramOnRefresh;
    localStorage.setItem(SECTOR_TELEGRAM_TOGGLE_KEY, _sectorTelegramOnRefresh ? 'true' : 'false');
    syncSectorTelegramToggle();
    const statusEl = document.getElementById('sector-tab-status');
    if (statusEl) {
        statusEl.textContent = _sectorTelegramOnRefresh
            ? 'Telegram notify on refresh: ON'
            : 'Telegram notify on refresh: OFF';
        statusEl.style.color = _sectorTelegramOnRefresh ? '#22c55e' : 'var(--text-muted)';
    }
}

async function notifySectorReportToTelegram() {
    const response = await fetch(`${API_BASE}/api/settings/sectors/notify`, { method: 'POST' });
    const data = await response.json();
    if (!response.ok || !data?.ok) {
        throw new Error(data?.message || data?.error || 'Telegram notification failed');
    }
    return data;
}

async function loadSectorsTab() {
    try {
        const response = await fetch(`${API_BASE}/api/settings/sectors/all`);
        const sectors = await response.json();

        if (!sectors.length) {
            document.getElementById('sectors-table').innerHTML = `
                <div class="p-10 text-center" style="color:var(--text-muted)">
                    <p class="text-sm mb-1">No sector data yet</p>
                    <p class="text-xs">Click "Refresh Sectors" to run analysis.</p>
                </div>`;
            return;
        }

        window._lastSectors = sectors;
        renderSectorsTable(sectors);
    } catch (err) {
        console.error('Error loading sectors:', err);
    }
}

let _selectedSector = null;
let _scoreTooltipEl = null;

function showScoreTooltip(event, s) {
    hideScoreTooltip();

    const rs = Math.round((s.relative_strength || 0) * 100);
    const hrs = Math.round((s.hourly_rs || s.daily_rs || s.relative_strength || 0) * 100);
    const drs = Math.round((s.daily_rs || 0) * 100);
    const wrs = Math.round((s.weekly_rs || 0) * 100);
    const mrs = Math.round((s.monthly_rs || 0) * 100);
    const pa = Math.round((s.price_action_score || 0) * 100);
    const combined = Math.round((s.combined_score || 0) * 100);

    const check = v => v ? '✓' : '✗';
    const checkColor = v => v ? '#22c55e' : '#ef4444';

    const paChecks = [
        { label: 'Above 20 EMA', val: s.is_above_20ema },
        { label: 'Above 50 EMA', val: s.is_above_50ema },
        { label: 'Higher Highs (20d vs prior)', val: s.is_making_higher_highs },
        { label: 'Higher Lows (20d vs prior)', val: s.is_making_higher_lows },
        { label: 'EMA Aligned (20 > 50)', val: s.is_ema_aligned },
    ];
    const paCount = paChecks.filter(c => c.val).length;

    const tip = document.createElement('div');
    tip.className = 'score-tooltip';
    tip.innerHTML = `
        <div style="font-weight:700;font-size:0.65rem;margin-bottom:6px;color:var(--text-heading);border-bottom:1px solid var(--glass-border);padding-bottom:4px">
            Score Breakdown — ${combined}%
        </div>
        <div style="font-size:0.55rem;color:var(--text-secondary);margin-bottom:6px">
            <span style="color:var(--text-muted)">Formula:</span> 50% Relative Strength + 50% Price Action
        </div>
        <div style="display:flex;gap:12px;margin-bottom:8px">
            <div style="flex:1">
                <div style="font-weight:600;font-size:0.55rem;color:var(--text-heading);margin-bottom:4px">
                    Relative Strength: ${rs}%
                </div>
                <div style="font-size:0.5rem;color:var(--text-muted);margin-bottom:2px">
                    Sector return vs Nifty 50 return
                </div>
                <div style="font-size:0.5rem;display:flex;flex-direction:column;gap:2px">
                    <span>Hourly 75m (20% wt): <b style="color:${hrs >= 65 ? '#22c55e' : hrs >= 40 ? '#eab308' : '#ef4444'}">${hrs}%</b></span>
                    <span>Daily (30% wt): <b style="color:${drs >= 65 ? '#22c55e' : drs >= 40 ? '#eab308' : '#ef4444'}">${drs}%</b></span>
                    <span>Weekly (40% wt): <b style="color:${wrs >= 65 ? '#22c55e' : wrs >= 40 ? '#eab308' : '#ef4444'}">${wrs}%</b></span>
                    <span>Monthly (10% wt): <b style="color:${mrs >= 65 ? '#22c55e' : mrs >= 40 ? '#eab308' : '#ef4444'}">${mrs}%</b></span>
                </div>
            </div>
            <div style="flex:1">
                <div style="font-weight:600;font-size:0.55rem;color:var(--text-heading);margin-bottom:4px">
                    Price Action: ${pa}% <span style="color:var(--text-muted);font-weight:400">(${paCount}/5)</span>
                </div>
                <div style="font-size:0.5rem;display:flex;flex-direction:column;gap:2px">
                    ${paChecks.map(c => `<span><span style="color:${checkColor(c.val)}">${check(c.val)}</span> ${c.label}</span>`).join('')}
                </div>
            </div>
        </div>
    `;

    _positionTooltip(tip, event);
}

function hideScoreTooltip() {
    if (_scoreTooltipEl) {
        _scoreTooltipEl.remove();
        _scoreTooltipEl = null;
    }
}

function _positionTooltip(tip, event) {
    document.body.appendChild(tip);
    _scoreTooltipEl = tip;
    const rect = event.target.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - tipRect.width / 2;
    let top = rect.bottom + 8;
    if (left + tipRect.width > window.innerWidth - 10) left = window.innerWidth - tipRect.width - 10;
    if (left < 10) left = 10;
    if (top + tipRect.height > window.innerHeight - 10) top = rect.top - tipRect.height - 8;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
    tip.style.opacity = '1';
}

function getSignalDirection(signalType) {
    if (!signalType) return '';
    const t = String(signalType).toUpperCase();
    if (t.startsWith('CE')) return 'CE';
    if (t.startsWith('PE')) return 'PE';
    return signalType;
}

function isAdvDeclSignal(signalType) {
    return String(signalType || '').toUpperCase().includes('_AD');
}

function showFnoScoreTooltip(event, s) {
    hideScoreTooltip();

    const combined = Math.round((s.current_score || 0) * 100);
    const trend = Math.round((s.trend_score || 0) * 100);
    const consol = Math.round((s.consol_score || 0) * 100);
    const breakout = Math.round((s.breakout_score || 0) * 100);
    const dir = getSignalDirection(s.signal_type);
    const direction = dir === 'CE' ? 'Bullish (CE)' : 
                      dir === 'PE' ? 'Bearish (PE)' : 
                      s.signal_type === 'VCP' ? 'VCP Pattern' :
                      s.signal_type === 'Hybrid-VCP' ? 'Hybrid VCP' :
                      s.signal_type;
    const barColor = v => v >= 70 ? '#22c55e' : v >= 45 ? '#eab308' : '#ef4444';

    const bar = (label, val, weight) => `
        <div style="margin-bottom:6px">
            <div style="display:flex;justify-content:space-between;margin-bottom:2px">
                <span style="color:var(--text-secondary)">${label} <span style="color:var(--text-muted);font-size:0.45rem">(${weight}% wt)</span></span>
                <b style="color:${barColor(val)}">${val}%</b>
            </div>
            <div style="height:4px;border-radius:2px;background:var(--glass-border);overflow:hidden">
                <div style="height:100%;width:${val}%;border-radius:2px;background:${barColor(val)};transition:width 0.3s ease"></div>
            </div>
        </div>`;

    const hasSubs = s.trend_score != null && !isAdvDeclSignal(s.signal_type);

    const tip = document.createElement('div');
    tip.className = 'score-tooltip';
    tip.innerHTML = hasSubs ? `
        <div style="font-weight:700;font-size:0.65rem;margin-bottom:4px;color:var(--text-heading);border-bottom:1px solid var(--glass-border);padding-bottom:4px">
            ${s.symbol} — ${combined}% <span style="font-weight:400;font-size:0.5rem;color:var(--text-muted)">${direction}</span>
        </div>
        <div style="font-size:0.5rem;color:var(--text-muted);margin-bottom:8px">
            ${s.signal_type?.includes('VCP') ? 
                'Formula: Trend + Contraction + Volume + Position' : 
                'Formula: 35% Trend + 35% Consolidation + 30% Breakout Proximity'}
        </div>
        ${bar(s.signal_type?.includes('VCP') ? 'Trend Alignment' : 'Trend Strength', trend, 35)}
        <div style="font-size:0.45rem;color:var(--text-muted);margin-top:-4px;margin-bottom:6px">
            ${s.signal_type?.includes('VCP') ? 
                'Moving average alignment, price above key levels' : 
                'EMA alignment, higher highs/lows, slope'}
        </div>
        ${bar(s.signal_type?.includes('VCP') ? 'Contraction Quality' : 'Consolidation Quality', consol, 35)}
        <div style="font-size:0.45rem;color:var(--text-muted);margin-top:-4px;margin-bottom:6px">
            ${s.signal_type?.includes('VCP') ? 
                'Progressive tightening pattern, volume behavior' : 
                `Tight range at ${dir === 'CE' ? 'highs' : 'lows'}, volume dry-up`}
        </div>
        ${bar(s.signal_type?.includes('VCP') ? 'Volume Pattern' : 'Breakout Proximity', breakout, 30)}
        <div style="font-size:0.45rem;color:var(--text-muted);margin-top:-4px;margin-bottom:4px">
            ${s.signal_type?.includes('VCP') ? 
                'Volume dry-up during contraction, breakout surge potential' : 
                `Distance to consolidation ${dir === 'CE' ? 'ceiling' : 'floor'}`}
        </div>
        <div style="border-top:1px solid var(--glass-border);padding-top:4px;font-size:0.5rem;display:flex;justify-content:space-between">
            <span style="color:var(--text-muted)">Peak: ${Math.round((s.peak_score || 0) * 100)}%</span>
            <span style="color:var(--text-muted)">Detected: ${s.first_detected_date}</span>
        </div>
    ` : isAdvDeclSignal(s.signal_type) ? `
        <div style="font-weight:700;font-size:0.65rem;margin-bottom:4px;color:var(--text-heading)">
            ${s.symbol} — ${combined}% <span style="font-weight:400;font-size:0.5rem;color:var(--text-muted)">${direction}</span>
        </div>
        <div style="font-size:0.5rem;color:var(--text-muted)">
            Adv/Decl momentum setup: breadth + EMA/RSI/ADX/volume + OI boosts.
        </div>
    ` : `
        <div style="font-weight:700;font-size:0.65rem;margin-bottom:4px;color:var(--text-heading)">
            ${s.symbol} — ${combined}% <span style="font-weight:400;font-size:0.5rem;color:var(--text-muted)">${direction}</span>
        </div>
        <div style="font-size:0.5rem;color:var(--text-muted)">
            Run a fresh scan to populate sub-score breakdown.
        </div>
    `;

    _positionTooltip(tip, event);
}

function renderSectorsTable(sectors) {
    window._lastSectors = sectors;
    const rows = sectors.map(s => {
        const trendColor = {
            'Strong': '#22c55e',
            'Moderate': '#eab308',
            'Weak': 'var(--text-muted)',
            'Bearish': '#ef4444',
        }[s.trend || 'Weak'] || 'var(--text-muted)';

        const rsBar = (val) => {
            const pct = Math.round((val || 0) * 100);
            const color = pct >= 65 ? '#22c55e' : pct >= 40 ? '#eab308' : 'var(--text-muted)';
            return `<span style="font-size:0.55rem;color:${color};font-weight:600">${pct}%</span>`;
        };

        const badges = [
            s.is_above_20ema ? '<span style="font-size:0.5rem;padding:0 2px;border-radius:3px;background:rgba(34,197,94,0.12);color:#22c55e">20E</span>' : '',
            s.is_above_50ema ? '<span style="font-size:0.5rem;padding:0 2px;border-radius:3px;background:rgba(34,197,94,0.12);color:#22c55e">50E</span>' : '',
            s.is_making_higher_highs ? '<span style="font-size:0.5rem;padding:0 2px;border-radius:3px;background:rgba(59,130,246,0.12);color:#3b82f6">HH</span>' : '',
        ].filter(Boolean).join(' ');

        const signalBadge = s.active_signals > 0
            ? `<span class="text-2xs px-1.5 py-0.5 rounded-full font-medium" style="background:rgba(99,102,241,0.12);color:#818cf8">${s.active_signals}</span>`
            : '';

        const selected = _selectedSector === s.sector_name ? 'sector-selected' : '';

        return `<tr class="cursor-pointer ${selected}" onclick="expandSector('${s.sector_name}')" id="sector-row-${s.sector_name.replace(/\s+/g, '-')}">
            <td>
                <span class="font-medium" style="font-size:0.6rem;color:var(--text-primary)">${s.sector_name}</span>
                <span style="font-size:0.45rem;color:var(--text-muted);margin-left:2px">${s.stock_count}</span>
            </td>
            <td>${rsBar(s.hourly_rs || s.daily_rs || s.relative_strength)}</td>
            <td>${rsBar(s.daily_rs || s.relative_strength)}</td>
            <td>${rsBar(s.weekly_rs || s.relative_strength)}</td>
            <td>${rsBar(s.monthly_rs || s.relative_strength)}</td>
            <td><span style="font-size:0.5rem;padding:2px 4px;border-radius:3px;color:${trendColor};font-weight:500">${s.trend || '\u2014'}</span></td>
            <td><span style="display:flex;gap:2px">${badges}</span></td>
            <td style="font-weight:600;font-size:0.6rem;color:${(s.combined_score || 0) >= 0.6 ? '#22c55e' : (s.combined_score || 0) >= 0.4 ? '#eab308' : 'var(--text-muted)'}">
                <span class="score-tooltip-trigger" data-sector="${s.sector_name}"
                    onmouseenter="showScoreTooltip(event, ${JSON.stringify(s).replace(/"/g, '&quot;')})"
                    onmouseleave="hideScoreTooltip()"
                >${Math.round((s.combined_score || 0) * 100)}%</span>
            </td>
            <td>${signalBadge}</td>
        </tr>`;
    }).join('');

    document.getElementById('sectors-table').innerHTML = `
        <table class="data-table">
            <thead><tr>
                <th>Sector</th>
                <th>Hourly</th>
                <th>Daily</th>
                <th>Weekly</th>
                <th>Monthly</th>
                <th>Trend</th>
                <th></th>
                <th>Score</th>
                <th></th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
    applySortable('sectors-table');
}

async function expandSector(sectorName) {
    _selectedSector = sectorName;

    if (window._lastSectors) renderSectorsTable(window._lastSectors);

    const panel = document.getElementById('sector-stocks-panel');
    const titleEl = document.getElementById('sector-stocks-title');
    const listEl = document.getElementById('sector-stocks-list');

    titleEl.textContent = sectorName;
    panel.classList.remove('hidden');
    listEl.innerHTML = '<div class="p-2 text-center"><span class="spinner"></span></div>';

    loadSectorChart(sectorName);

    try {
        const response = await fetch(`${API_BASE}/api/settings/sectors/${encodeURIComponent(sectorName)}/stocks`);
        const stocks = await response.json();

        if (!stocks.length) {
            listEl.innerHTML = '<div class="p-6 text-center text-xs" style="color:var(--text-muted)">No stocks found for this sector.</div>';
            return;
        }

        const rows = stocks.map(s => {
            const signalBadge = s.has_signal
                ? `<span class="text-2xs px-1.5 py-0.5 rounded-full ${s.signal_status === 'exploded' ? 'status-exploded' : ''} font-medium" style="background:rgba(99,102,241,0.12);color:#818cf8">${s.signal_type === 'explosion' ? 'BREAKOUT' : 'VCP'} ${Math.round((s.signal_score || 0) * 100)}%</span>`
                : '';
            const occBadge = s.occurrence_number > 1
                ? `<span class="occurrence-${Math.min(s.occurrence_number, 3)} text-2xs px-1 py-0.5 rounded-full ml-1">${ordinal(s.occurrence_number)} time</span>`
                : '';
            const fnoBadge = s.is_fno
                ? '<span class="text-2xs px-1 py-0.5 rounded" style="background:rgba(168,85,247,0.12);color:#a78bfa">F&O</span>'
                : '';

            return `<tr>
                <td class="font-medium" style="color:var(--text-primary)">${s.symbol}${occBadge}</td>
                <td style="color:var(--text-secondary)">${s.display_name || ''}</td>
                <td>${fnoBadge}</td>
                <td>${s.is_fno ? s.lot_size : ''}</td>
                <td>${signalBadge}</td>
            </tr>`;
        }).join('');

        listEl.innerHTML = `
            <table class="data-table">
                <thead><tr>
                    <th>Symbol</th><th>Name</th><th>Type</th><th>Lot</th><th>Signal</th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
        makeTableSortable(listEl.querySelector('.data-table'));
    } catch (err) {
        listEl.innerHTML = `<div class="p-4 text-center text-red-400 text-xs">Error loading stocks: ${err.message}</div>`;
    }
}

function closeSectorStocks() {
    _selectedSector = null;
    document.getElementById('sector-stocks-panel').classList.add('hidden');
    if (window._lastSectors) renderSectorsTable(window._lastSectors);
}

async function loadSectorChart(sectorName) {
    const canvas = document.getElementById('sector-chart');
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;

    const rect = canvas.parentElement.getBoundingClientRect();
    canvas.width = (rect.width - 12) * dpr;
    canvas.height = (rect.height - 12) * dpr;
    canvas.style.width = (rect.width - 12) + 'px';
    canvas.style.height = (rect.height - 12) + 'px';
    ctx.scale(dpr, dpr);

    const cw = rect.width - 12;
    const ch = rect.height - 12;

    ctx.clearRect(0, 0, cw, ch);
    const cs = getComputedStyle(document.documentElement);
    ctx.fillStyle = cs.getPropertyValue('--text-muted').trim();
    ctx.font = '9px sans-serif';
    ctx.fillText('Loading weekly chart...', 10, ch / 2);

    try {
        const response = await fetch(`${API_BASE}/api/settings/sectors/${encodeURIComponent(sectorName)}/chart`);
        const data = await response.json();

        if (!data.candles || !data.candles.length) {
            ctx.clearRect(0, 0, cw, ch);
            ctx.fillText('No chart data', 10, ch / 2);
            return;
        }

        drawWeeklyChart(ctx, cw, ch, data.candles, data.ema20, data.ema50);
    } catch (err) {
        ctx.clearRect(0, 0, cw, ch);
        ctx.fillText('Chart error', 10, ch / 2);
    }
}

function drawWeeklyChart(ctx, w, h, candles, ema20, ema50) {
    const cs = getComputedStyle(document.documentElement);
    const chartBg = cs.getPropertyValue('--chart-bg').trim();
    const chartGrid = cs.getPropertyValue('--chart-grid').trim();
    const chartLabel = cs.getPropertyValue('--chart-label').trim();
    const bullFill = chartBg;

    ctx.clearRect(0, 0, w, h);

    const volH = Math.round(h * 0.1);
    const pad = { top: 6, bottom: 12, left: 2, right: 32 };
    const priceH = h - pad.top - pad.bottom - volH - 4;
    const chartW = w - pad.left - pad.right;

    const recentStart = Math.floor(candles.length * 0.4);
    const recentCandles = candles.slice(recentStart);
    const minPrice = Math.min(...recentCandles.map(c => c.l));
    const maxPrice = Math.max(...recentCandles.map(c => c.h));
    const rawRange = maxPrice - minPrice || 1;
    const pRange = rawRange * 1.15;
    const maxVol = Math.max(...candles.map(c => c.v || 0)) || 1;

    const step = chartW / candles.length;
    const candleW = Math.max(2, step * 0.75);

    const midPrice = (minPrice + maxPrice) / 2;
    const scaledMin = midPrice - pRange / 2;
    const yP = (price) => {
        const y = pad.top + priceH - ((price - scaledMin) / pRange) * priceH;
        return Math.max(pad.top - 2, Math.min(pad.top + priceH + 2, y));
    };
    const xC = (i) => pad.left + i * step + step / 2;
    const volTop = pad.top + priceH + 4;

    ctx.fillStyle = chartBg;
    ctx.fillRect(0, 0, w, h);

    ctx.strokeStyle = chartGrid;
    ctx.lineWidth = 0.5;
    ctx.fillStyle = chartLabel;
    ctx.font = '7px sans-serif';
    ctx.textAlign = 'right';
    const gridSteps = 5;
    for (let i = 0; i <= gridSteps; i++) {
        const price = minPrice + (pRange / gridSteps) * i;
        const y = yP(price);
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(w - pad.right, y);
        ctx.stroke();
        ctx.fillText(price.toFixed(0), w - 3, y + 3);
    }

    for (let i = 0; i < candles.length; i++) {
        const c = candles[i];
        const x = xC(i);
        const vH = ((c.v || 0) / maxVol) * volH;
        const bullish = c.c >= c.o;
        ctx.fillStyle = bullish ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)';
        ctx.fillRect(x - candleW / 2, volTop + volH - vH, candleW, vH);
    }

    if (ema50 && ema50.length) {
        ctx.strokeStyle = '#3b82f6';
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (let i = 0; i < ema50.length; i++) {
            const x = xC(i);
            const y = yP(ema50[i]);
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
    }

    if (ema20 && ema20.length) {
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (let i = 0; i < ema20.length; i++) {
            const x = xC(i);
            const y = yP(ema20[i]);
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
    }

    for (let i = 0; i < candles.length; i++) {
        const c = candles[i];
        const x = xC(i);
        const bullish = c.c >= c.o;
        const bodyTop = yP(Math.max(c.o, c.c));
        const bodyBot = yP(Math.min(c.o, c.c));
        const bodyH = Math.max(0.8, bodyBot - bodyTop);

        ctx.strokeStyle = bullish ? '#22c55e' : '#ef4444';
        ctx.lineWidth = 0.7;
        ctx.beginPath();
        ctx.moveTo(x, yP(c.h));
        ctx.lineTo(x, yP(c.l));
        ctx.stroke();

        if (bullish) {
            ctx.fillStyle = bullFill;
            ctx.fillRect(x - candleW / 2, bodyTop, candleW, bodyH);
            ctx.strokeStyle = '#22c55e';
            ctx.lineWidth = 0.7;
            ctx.strokeRect(x - candleW / 2, bodyTop, candleW, bodyH);
        } else {
            ctx.fillStyle = '#ef4444';
            ctx.fillRect(x - candleW / 2, bodyTop, candleW, bodyH);
        }
    }

    ctx.fillStyle = chartLabel;
    ctx.font = '7px sans-serif';
    ctx.textAlign = 'center';
    const labelInterval = Math.max(1, Math.floor(candles.length / 6));
    for (let i = 0; i < candles.length; i += labelInterval) {
        const d = candles[i].t;
        const parts = d.split('-');
        const months = ['', 'Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        const label = months[parseInt(parts[1])] + ' ' + parts[0].slice(2);
        ctx.fillText(label, xC(i), h - 1);
    }

    ctx.font = '7px sans-serif';
    ctx.textAlign = 'left';
    ctx.fillStyle = '#f59e0b';
    ctx.fillText('\u2014 20 EMA', pad.left + 4, pad.top + 8);
    ctx.fillStyle = '#3b82f6';
    ctx.fillText('\u2014 50 EMA', pad.left + 50, pad.top + 8);

    const lastC = candles[candles.length - 1];
    ctx.fillStyle = lastC.c >= lastC.o ? '#22c55e' : '#ef4444';
    ctx.font = 'bold 8px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(lastC.c.toFixed(2), w - 3, yP(lastC.c) - 3);
}

async function runSectorAnalysisFromTab() {
    const btn = document.getElementById('btn-refresh-sectors');
    const statusEl = document.getElementById('sector-tab-status');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Analyzing...';
    if (statusEl) {
        statusEl.textContent = 'Refreshing sectors...';
        statusEl.style.color = 'var(--text-muted)';
    }

    try {
        const response = await fetch(`${API_BASE}/api/settings/scan/sectors`, { method: 'POST' });
        const data = await response.json();
        if (data.sectors) {
            window._lastSectors = data.sectors;
            renderSectorsTable(data.sectors);
            if (statusEl) {
                statusEl.textContent = `Refreshed ${data.sectors.length} sectors`;
                statusEl.style.color = '#22c55e';
            }
        } else {
            loadSectorsTab();
            if (statusEl) {
                statusEl.textContent = 'Refresh complete';
                statusEl.style.color = '#22c55e';
            }
        }
        if (_sectorTelegramOnRefresh) {
            if (statusEl) {
                statusEl.textContent = 'Refreshed. Sending Telegram...';
                statusEl.style.color = 'var(--text-muted)';
            }
            try {
                const notify = await notifySectorReportToTelegram();
                if (statusEl) {
                    statusEl.textContent = `Refreshed + Telegram sent${notify?.telegram?.message_id ? ` (msg #${notify.telegram.message_id})` : ''}`;
                    statusEl.style.color = '#22c55e';
                }
            } catch (err) {
                if (statusEl) {
                    statusEl.textContent = `Refreshed, Telegram failed: ${err.message}`;
                    statusEl.style.color = '#ef4444';
                }
            }
        }
    } catch (err) {
        console.error('Sector analysis failed:', err);
        if (statusEl) {
            statusEl.textContent = `Refresh failed: ${err.message}`;
            statusEl.style.color = '#ef4444';
        }
    } finally {
        btn.disabled = false;
        btn.innerHTML = 'Refresh Sectors';
    }
}

async function sendSectorReportToTelegram() {
    if (!isAdminUser()) return;
    const statusEl = document.getElementById('sector-tab-status');
    if (statusEl) {
        statusEl.textContent = 'Sending Telegram report...';
        statusEl.style.color = 'var(--text-muted)';
    }

    try {
        const notify = await notifySectorReportToTelegram();
        if (statusEl) {
            statusEl.textContent = `Telegram sent${notify?.telegram?.message_id ? ` (msg #${notify.telegram.message_id})` : ''}`;
            statusEl.style.color = '#22c55e';
        }
        loadSectorsTab();
    } catch (err) {
        if (statusEl) {
            statusEl.textContent = `Telegram send failed: ${err.message}`;
            statusEl.style.color = '#ef4444';
        }
    }
}

// --- Signals ---

async function loadSignals(section) {
    const statusFilters = getSelectedPills(`${section}-status-filter`);
    const scoreFilters = getSelectedPills(`${section}-score-filter`);
    const containerId = `${section}-signals-table`;

    try {
        const response = await fetch(`${API_BASE}/api/signals/?section=${section}`);
        let signals = await response.json();

        // Apply status filters
        if (statusFilters.length) {
            signals = signals.filter(s => statusFilters.includes(s.status));
        }

        // Apply score filters
        if (scoreFilters.length) {
            signals = signals.filter(s => {
                const scorePercent = (s.current_score * 100);
                return scoreFilters.some(threshold => {
                    const minScore = parseInt(threshold);
                    return scorePercent >= minScore;
                });
            });
        }

        if (!signals.length) {
            const label = statusFilters.length ? statusFilters.join(', ') : 'any';
            document.getElementById(containerId).innerHTML = `
                <div class="p-10 text-center" style="color:var(--text-muted)">
                    <p class="text-sm mb-1">No ${label} signals</p>
                    <p class="text-xs">Try a different status filter or run a scan.</p>
                </div>`;
            return;
        }

        const secIds = signals.map(s => s.security_id);
        let cmpMap = {};
        try {
            const cmpRes = await fetch(`${API_BASE}/api/signals/cmp`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({security_ids: secIds}),
            });
            if (cmpRes.ok) {
                cmpMap = await cmpRes.json();
            } else {
                console.warn('CMP data unavailable (market closed or API issue)');
            }
        } catch (e) { 
            console.warn('Failed to fetch CMP data:', e.message);
        }

        const rows = signals.map(s => {
            const scoreClass = s.current_score >= 0.8 ? 'score-high' :
                               s.current_score >= 0.6 ? 'score-medium' : 'score-low';
            const occBadge = s.occurrence_number > 1
                ? `<span class="occurrence-${Math.min(s.occurrence_number, 3)} text-2xs px-1.5 py-0.5 rounded-full ml-1.5">${ordinal(s.occurrence_number)} time</span>`
                : '';
            
            // F&O indicator badge with lot size tooltip
            const fnoBadge = s.is_fno 
                ? `<span class="text-2xs px-1.5 py-0.5 rounded-full ml-1" style="background:rgba(168,85,247,0.15);color:#a78bfa;border:1px solid rgba(168,85,247,0.3)" title="F&O Available - Lot Size: ${s.lot_size || 'N/A'}">F&O</span>`
                : '';
            
            const statusClass = `status-${s.status}`;
            const cmp = cmpMap[s.security_id];
            const cmpDisplay = cmp ? cmp.toFixed(2) : (s.close_price_at_detection ? s.close_price_at_detection.toFixed(2) : '-');
            const pctChange = (cmp && s.close_price_at_detection)
                ? ((cmp - s.close_price_at_detection) / s.close_price_at_detection * 100).toFixed(1)
                : null;
            const pctClass = pctChange > 0 ? 'text-profit' : pctChange < 0 ? 'text-loss' : '';
            const pctDisplay = pctChange !== null ? `<span class="${pctClass}" style="font-size:0.5rem">${pctChange > 0 ? '+' : ''}${pctChange}%</span>` : '';

            // Tooltip data for score breakdown
            const tipData = JSON.stringify({
                symbol: s.symbol, 
                signal_type: s.signal_type || 'VCP', 
                current_score: s.current_score,
                peak_score: s.peak_score, 
                trend_score: s.trend_score, 
                consol_score: s.consol_score,
                breakout_score: s.breakout_score, 
                first_detected_date: s.first_detected_date,
            }).replace(/"/g, '&quot;');

            return `<tr class="cursor-pointer" onclick="viewSignalDetail(${s.id})">
                <td class="font-medium" style="color:var(--text-primary)">${s.symbol}${occBadge}${fnoBadge}</td>
                <td>${s.sector || '-'}</td>
                <td><span class="${scoreClass} text-2xs px-1.5 py-0.5 rounded-full score-tooltip-trigger"
                    onmouseenter="showFnoScoreTooltip(event, ${tipData})"
                    onmouseleave="hideScoreTooltip()"
                    >${(s.current_score * 100).toFixed(0)}%</span></td>
                <td>${s.close_price_at_detection || '-'}</td>
                <td>${cmpDisplay} ${pctDisplay}</td>
                <td>${s.first_detected_date}</td>
                <td>${daysSince(s.first_detected_date)}d</td>
                <td><span class="${statusClass} text-2xs px-1.5 py-0.5 rounded-full">${s.status}</span></td>
                <td>
                    ${s.status === 'active' ? `<button onclick="event.stopPropagation(); dismissSignal(${s.id})" class="text-2xs hover:text-red-400" style="color:var(--text-muted)">Dismiss</button>` : ''}
                </td>
            </tr>`;
        }).join('');

        document.getElementById(containerId).innerHTML = `
            <table class="data-table">
                <thead><tr>
                    <th>Symbol</th><th>Sector</th><th>Score</th>
                    <th>Entry Price</th><th>CMP</th><th>Detected</th><th>Days</th><th>Status</th><th></th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
        applySortable(containerId);
        
        // Update last refreshed time for swing trades
        if (section === 'swing') {
            const lastUpdatedEl = document.getElementById('swing-last-updated');
            if (lastUpdatedEl) {
                const now = new Date();
                const totalSignals = signals.length;
                const displayedSignals = rows.length;
                lastUpdatedEl.textContent = `${displayedSignals} of ${totalSignals} signals • Updated: ${now.toLocaleTimeString()}`;
            }
        }
    } catch (err) {
        console.error('Error loading signals:', err);
    }
}

async function dismissSignal(id) {
    if (!confirm('Dismiss this signal?')) return;
    await fetch(`${API_BASE}/api/signals/${id}/dismiss`, { method: 'POST' });
    const activeTab = document.querySelector('.nav-item.active')?.id?.replace('tab-', '');
    if (activeTab === 'swing') loadSignals('swing');
    if (activeTab === 'fno') loadFnoSignals();
}

async function reactivateSignal(id) {
    await fetch(`${API_BASE}/api/signals/${id}/reactivate`, { method: 'POST' });
    const activeTab = document.querySelector('.nav-item.active')?.id?.replace('tab-', '');
    if (activeTab === 'swing') loadSignals('swing');
    if (activeTab === 'fno') loadFnoSignals();
}

let _currentAnalysisSignalId = null;
let _advisoryAutoRefresh = null;

let _lastAnalysisExpiries = null;
let _lastAnalysisExpiry = null;

let _expiryScoresCache = {};
let _expiryScoreSignalId = null;

async function viewSignalDetail(id, expiry) {
    _currentAnalysisSignalId = id;
    _lastAnalysisExpiry = expiry || null;
    const panel = document.getElementById('option-analysis-panel');
    const loading = document.getElementById('analysis-loading');
    const error = document.getElementById('analysis-error');
    const content = document.getElementById('analysis-content');
    const title = document.getElementById('analysis-title');

    // Clear cache when opening a different stock
    if (_expiryScoreSignalId !== id) {
        _expiryScoresCache = {};
        _expiryScoreSignalId = id;
    }

    panel.classList.remove('hidden');
    loading.classList.remove('hidden');
    error.classList.add('hidden');
    content.classList.add('hidden');

    try {
        let url = `${API_BASE}/api/signals/${id}/option-analysis`;
        if (expiry) url += `?expiry=${expiry}`;
        const res = await fetch(url);
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            if (err.available_expiries) _lastAnalysisExpiries = err.available_expiries;
            throw new Error(err.detail || `Analysis failed (${res.status})`);
        }
        const data = await res.json();
        _lastAnalysisExpiries = data.available_expiries;
        _lastAnalysisExpiry = data.expiry;

        // Seed current expiry's conviction from analysis data we already have
        if (data.recommendation && data.recommendation.conviction) {
            _expiryScoresCache[data.expiry] = {
                conviction: data.recommendation.conviction.toLowerCase(),
                reason: data.recommendation.conviction_reason || '',
                best_score: data.recommendation.score || 0,
                best_spread: data.recommendation.spread_pct || 0,
            };
        }

        loading.classList.add('hidden');
        content.classList.remove('hidden');
        title.textContent = `${data.signal.symbol} — ${data.side} Option Analysis`;
        renderAnalysisPanel(data);

        // Fetch remaining expiry scores in background (only on first load)
        if (!expiry) {
            _fetchExpiryScores(id);
        }
    } catch (e) {
        loading.classList.add('hidden');
        error.classList.remove('hidden');
        let errorHtml = `<p class="text-xs" style="color:#f87171;margin-bottom:12px">${e.message}</p>`;
        if (_lastAnalysisExpiries && _lastAnalysisExpiries.length) {
            const pills = _lastAnalysisExpiries.map(ex => {
                const cached = _expiryScoresCache[ex.date];
                let dotHtml = '';
                if (cached && cached.conviction !== 'none') {
                    const c = cached.conviction === 'high' ? '#22c55e' : cached.conviction === 'medium' ? '#eab308' : '#ef4444';
                    dotHtml = `<span class="expiry-dot" style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${c};margin-left:4px;vertical-align:middle" title="${cached.reason || ''}"></span>`;
                }
                return `<button class="filter-pill" data-expiry-pill="${ex.date}" onclick="switchExpiry('${ex.date}')"
                    style="font-size:0.55rem;padding:3px 8px">${ex.date.slice(5)} (${ex.dte}d)${dotHtml}</button>`;
            }).join('');
            errorHtml += `<div style="margin-top:10px">
                <p style="font-size:0.6rem;color:var(--text-secondary);margin-bottom:6px">Try a different expiry:</p>
                <div class="filter-pills" style="flex-wrap:wrap;gap:4px;justify-content:center">${pills}</div>
            </div>`;
        }
        document.getElementById('analysis-error').innerHTML = errorHtml;
    }
}

async function _fetchExpiryScores(signalId) {
    // Show loading spinners on pills that don't have dots yet
    document.querySelectorAll('[data-expiry-pill]').forEach(btn => {
        if (!btn.querySelector('.expiry-dot')) {
            const spinner = document.createElement('span');
            spinner.className = 'expiry-dot-loading';
            spinner.style.cssText = 'display:inline-block;width:6px;height:6px;border-radius:50%;border:1px solid rgba(255,255,255,0.3);border-top-color:rgba(255,255,255,0.8);margin-left:4px;vertical-align:middle;animation:expiry-spin 0.8s linear infinite';
            btn.appendChild(spinner);
        }
    });

    try {
        const resp = await fetch(`${API_BASE}/api/signals/${signalId}/expiry-scores`);
        if (!resp.ok) return;
        const data = await resp.json();
        const scores = data.scores || {};
        // Merge with existing cache (don't overwrite current expiry seeded from analysis)
        for (const [k, v] of Object.entries(scores)) {
            _expiryScoresCache[k] = v;
        }
        _applyExpiryDots();
    } catch (e) {
        // Remove spinners on failure
        document.querySelectorAll('.expiry-dot-loading').forEach(s => s.remove());
    }
}

function _applyExpiryDots() {
    document.querySelectorAll('[data-expiry-pill]').forEach(btn => {
        const expDate = btn.getAttribute('data-expiry-pill');
        const scoreData = _expiryScoresCache[expDate];

        // Remove any existing dot or spinner
        btn.querySelectorAll('.expiry-dot, .expiry-dot-loading').forEach(el => el.remove());

        if (!scoreData || scoreData.conviction === 'none') return;

        const dotColor = scoreData.conviction === 'high' ? '#22c55e' : scoreData.conviction === 'medium' ? '#eab308' : '#ef4444';
        const dot = document.createElement('span');
        dot.className = 'expiry-dot';
        dot.style.cssText = `display:inline-block;width:6px;height:6px;border-radius:50%;background:${dotColor};margin-left:4px;vertical-align:middle;`;
        dot.title = scoreData.reason || '';
        btn.appendChild(dot);
    });
}

function switchExpiry(expiry) {
    if (_currentAnalysisSignalId) {
        viewSignalDetail(_currentAnalysisSignalId, expiry);
    }
}

function closeAnalysisPanel() {
    if (_advisoryAutoRefresh) {
        clearInterval(_advisoryAutoRefresh);
        _advisoryAutoRefresh = null;
    }
    document.getElementById('option-analysis-panel').classList.add('hidden');
    _currentAnalysisSignalId = null;
}

function renderAnalysisPanel(data) {
    _tradeAnalysisData = data;
    const r = data.recommendation;
    const s = data.signal;
    const fmt = n => new Intl.NumberFormat('en-IN').format(n);
    const fmtR = n => new Intl.NumberFormat('en-IN').format(Math.round(n));
    const pct = n => n != null ? n.toFixed(2) : '-';
    const dirLabel = data.side === 'CE' ? 'Bullish' : 'Bearish';
    const dirColor = data.side === 'CE' ? '#4ade80' : '#f87171';

    const greekCards = [
        { label: 'Delta', value: pct(r.delta), desc: `₹${fmtR(Math.abs(r.delta || 0) * data.lot_size)} per ₹1 move`, color: '#a5b4fc' },
        { label: 'Gamma', value: r.gamma ? r.gamma.toFixed(4) : '-', desc: 'Rate of delta change', color: '#c4b5fd' },
        { label: 'Theta', value: pct(r.theta), desc: `₹${fmtR(r.theta_daily_cost)}/day decay`, color: '#fdba74' },
        { label: 'Vega', value: pct(r.vega), desc: `₹${fmtR(Math.abs(r.vega || 0) * data.lot_size)} per 1% IV`, color: '#67e8f9' },
    ];

    const strikeRows = data.strikes.map(st => {
        const isRec = st.strike === r.strike;
        const isAtm = st.strike === data.atm_strike;
        const rowBg = isRec ? 'background:rgba(99,102,241,0.15);' : isAtm ? 'background:rgba(234,179,8,0.08);' : '';
        const badge = isRec ? '<span style="color:#a5b4fc;font-size:0.6rem;margin-left:4px;font-weight:700">★ REC</span>' :
                      isAtm ? '<span style="color:#fbbf24;font-size:0.6rem;margin-left:4px">ATM</span>' : '';
        const deltaColor = Math.abs(st.delta || 0) >= 0.4 && Math.abs(st.delta || 0) <= 0.6 ? '#4ade80' : 'var(--text-secondary)';
        return `<tr style="${rowBg}cursor:default">
            <td style="font-weight:${isRec ? '700' : '500'};color:var(--text-primary)">${st.strike}${badge}</td>
            <td style="color:var(--text-primary)">${st.premium}</td>
            <td style="color:var(--text-primary)">${fmtR(st.lot_cost)}</td>
            <td style="color:${deltaColor};font-weight:600">${st.delta != null ? st.delta.toFixed(3) : '-'}</td>
            <td style="color:var(--text-secondary)">${st.theta != null ? st.theta.toFixed(2) : '-'}</td>
            <td style="color:var(--text-secondary)">${st.iv != null ? st.iv.toFixed(1) + '%' : '-'}</td>
            <td style="color:var(--text-secondary)">${fmt(st.oi)}</td>
            <td style="color:var(--text-secondary)">${fmt(st.volume)}</td>
            <td style="color:var(--text-secondary)">${st.spread_pct.toFixed(1)}%</td>
        </tr>`;
    }).join('');

    const expiryPills = (data.available_expiries || []).map(e => {
        const isActive = e.date === data.expiry;
        const label = `${e.date.slice(5)} (${e.dte}d)`;
        const cached = _expiryScoresCache[e.date];
        let dotHtml = '';
        if (cached && cached.conviction !== 'none') {
            const c = cached.conviction === 'high' ? '#22c55e' : cached.conviction === 'medium' ? '#eab308' : '#ef4444';
            dotHtml = `<span class="expiry-dot" style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${c};margin-left:4px;vertical-align:middle" title="${cached.reason || ''}"></span>`;
        }
        return `<button class="filter-pill${isActive ? ' active' : ''}" data-expiry-pill="${e.date}" onclick="switchExpiry('${e.date}')"
            style="font-size:0.55rem;padding:3px 8px">${label}${dotHtml}</button>`;
    }).join('');

    const warnings = (data.liquidity_warnings || []).map(w => {
        const icon = w.level === 'high' ? '🔴' : w.level === 'medium' ? '🟡' : '💡';
        const bg = w.level === 'high' ? 'rgba(239,68,68,0.1)' : w.level === 'medium' ? 'rgba(234,179,8,0.1)' : 'rgba(99,102,241,0.08)';
        const color = w.level === 'high' ? '#fca5a5' : w.level === 'medium' ? '#fde68a' : '#c7d2fe';
        return `<div style="padding:6px 10px;border-radius:6px;background:${bg};font-size:0.6rem;color:${color};display:flex;align-items:center;gap:6px">
            <span>${icon}</span>${w.message}
        </div>`;
    }).join('');

    document.getElementById('analysis-content').innerHTML = `
        <!-- Expiry Selector -->
        <div style="margin-bottom:12px">
            <div style="font-size:0.6rem;color:var(--text-secondary);margin-bottom:5px">Expiry</div>
            <div class="filter-pills" style="flex-wrap:wrap;gap:4px">${expiryPills}</div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
            <div class="glass-card-sm" style="padding:14px">
                <div style="font-size:0.65rem;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px">Spot Price</div>
                <div style="font-size:1.3rem;font-weight:700;color:var(--text-heading)">${data.spot_price.toFixed(2)}</div>
                <div style="font-size:0.6rem;color:var(--text-secondary);margin-top:2px">Detected at: ${s.detected_at || '-'}</div>
            </div>
            <div class="glass-card-sm" style="padding:14px">
                <div style="font-size:0.65rem;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px">Direction</div>
                <div style="font-size:1.3rem;font-weight:700;color:${dirColor}">${dirLabel} ${data.side}</div>
                <div style="font-size:0.6rem;color:var(--text-secondary);margin-top:2px">Signal score: ${Math.round(s.score * 100)}%</div>
            </div>
        </div>

        ${warnings ? `<div style="display:flex;flex-direction:column;gap:6px;margin-bottom:14px">${warnings}</div>` : ''}

        <!-- Conviction Banner -->
        <div style="padding:10px 14px;border-radius:8px;margin-bottom:14px;display:flex;align-items:center;gap:10px;background:${r.conviction === 'High' ? 'rgba(34,197,94,0.1)' : r.conviction === 'Medium' ? 'rgba(234,179,8,0.1)' : 'rgba(239,68,68,0.1)'}">
            <span style="font-size:1rem">${r.conviction === 'High' ? '🟢' : r.conviction === 'Medium' ? '🟡' : '🔴'}</span>
            <div>
                <div style="font-size:0.7rem;font-weight:700;color:${r.conviction === 'High' ? '#4ade80' : r.conviction === 'Medium' ? '#fbbf24' : '#f87171'}">${r.conviction} Conviction</div>
                <div style="font-size:0.55rem;color:var(--text-secondary)">${r.conviction_reason}</div>
            </div>
        </div>

        <!-- Recommended Strike -->
        <div class="glass-card-sm" style="padding:16px;margin-bottom:16px;border-left:3px solid #818cf8">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                <div>
                    <div style="font-size:0.8rem;font-weight:700;color:var(--text-heading)">★ Recommended: ${r.strike} ${data.side}</div>
                    <div style="font-size:0.6rem;color:var(--text-secondary);margin-top:3px">
                        ${r.moneyness > 0 ? r.moneyness.toFixed(1) + '% OTM' : r.moneyness < -1 ? Math.abs(r.moneyness).toFixed(1) + '% ITM' : 'ATM'}
                        &nbsp;·&nbsp; Expiry: ${data.expiry} (${data.dte}d) &nbsp;·&nbsp; IV: ${r.iv ? r.iv.toFixed(1) + '%' : '-'}
                    </div>
                </div>
                <div style="text-align:right">
                    <div style="font-size:1.1rem;font-weight:700;color:var(--text-heading)">₹${fmtR(r.lot_cost)}</div>
                    <div style="font-size:0.6rem;color:var(--text-secondary)">${data.lot_size} × ₹${r.premium}</div>
                </div>
            </div>

            <!-- Buy Range -->
            <div style="padding:8px 12px;border-radius:8px;background:rgba(99,102,241,0.08);margin-bottom:12px;display:flex;align-items:center;justify-content:space-between">
                <div>
                    <div style="font-size:0.55rem;color:#c7d2fe;text-transform:uppercase;letter-spacing:0.05em">Buy Range</div>
                    <div style="font-size:0.85rem;font-weight:700;color:#a5b4fc">${r.buy_range}</div>
                </div>
                <div style="font-size:0.55rem;color:var(--text-secondary);text-align:right;max-width:55%">
                    Place limit order in this range. Don't chase if premium runs above ₹${r.buy_range_high}
                </div>
            </div>

            <!-- Target / SL / Hold -->
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:10px">
                <div style="text-align:center;padding:10px 8px;border-radius:8px;background:rgba(34,197,94,0.1)">
                    <div style="font-size:0.6rem;color:#86efac;margin-bottom:3px">Target (Stock)</div>
                    <div style="font-size:0.85rem;font-weight:700;color:#4ade80">${r.target_stock_price}</div>
                    <div style="font-size:0.55rem;color:#86efac;margin-top:3px">Premium → ₹${r.target_premium}</div>
                </div>
                <div style="text-align:center;padding:10px 8px;border-radius:8px;background:rgba(239,68,68,0.1)">
                    <div style="font-size:0.6rem;color:#fca5a5;margin-bottom:3px">Stop Loss (Stock)</div>
                    <div style="font-size:0.85rem;font-weight:700;color:#f87171">${r.sl_stock_price}</div>
                    <div style="font-size:0.55rem;color:#fca5a5;margin-top:3px">Premium → ₹${r.sl_premium}</div>
                </div>
                <div style="text-align:center;padding:10px 8px;border-radius:8px;background:rgba(99,102,241,0.1)">
                    <div style="font-size:0.6rem;color:#c7d2fe;margin-bottom:3px">Hold Duration</div>
                    <div style="font-size:0.85rem;font-weight:700;color:#a5b4fc">${r.hold_days} days</div>
                    <div style="font-size:0.55rem;color:#c7d2fe;margin-top:3px">DTE: ${data.dte}d</div>
                </div>
            </div>

            <!-- SL Reason -->
            <div style="padding:6px 10px;border-radius:6px;background:rgba(239,68,68,0.06);font-size:0.55rem;color:#fca5a5;margin-bottom:10px">
                <span style="font-weight:600">SL basis:</span> ${r.sl_reason || 'Technical support level'}
            </div>

            <!-- Partial Booking -->
            ${r.partial_book_at ? `
            <div style="padding:6px 10px;border-radius:6px;background:rgba(234,179,8,0.08);font-size:0.55rem;color:#fde68a;margin-bottom:10px">
                <span style="font-weight:600">📊 Partial profit:</span> Book 50% at stock ₹${r.partial_book_at} (premium ~₹${r.partial_book_premium}), trail SL to cost for rest
            </div>` : ''}

            <!-- P&L Summary -->
            ${r.lot_size_warning ? `
            <div style="padding:8px 10px;border-radius:6px;background:rgba(234,179,8,0.12);font-size:0.55rem;color:#fde68a;margin-bottom:10px;border:1px solid rgba(234,179,8,0.25)">
                <strong>Lot size not loaded</strong> (F&O lot in DB is 1). Run <strong>Refresh Instruments</strong> so Max P&L/Loss use the correct NSE lot. Below: per-lot using qty × ${data.lot_size}; per-unit premium is ₹${r.max_profit_per_unit != null ? r.max_profit_per_unit.toFixed(2) : '-'} / ₹${r.max_loss_per_unit != null ? r.max_loss_per_unit.toFixed(2) : '-'}.
            </div>` : ''}
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;border-top:1px solid var(--glass-border);padding-top:10px">
                <div style="text-align:center">
                    <div style="font-size:0.6rem;color:var(--text-secondary);margin-bottom:2px">Max Profit</div>
                    <div style="font-size:0.8rem;font-weight:700;color:#4ade80">₹${fmtR(r.max_profit)}</div>
                    <div style="font-size:0.45rem;color:var(--text-secondary);margin-top:2px">1 lot (${data.lot_size} qty)</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:0.6rem;color:var(--text-secondary);margin-bottom:2px">Max Loss</div>
                    <div style="font-size:0.8rem;font-weight:700;color:#f87171">₹${fmtR(r.max_loss)}</div>
                    <div style="font-size:0.45rem;color:var(--text-secondary);margin-top:2px">1 lot (${data.lot_size} qty)</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:0.6rem;color:var(--text-secondary);margin-bottom:2px">Risk:Reward</div>
                    <div style="font-size:0.8rem;font-weight:700;color:${r.risk_reward >= 2 ? '#4ade80' : r.risk_reward >= 1.5 ? '#fbbf24' : '#f87171'}">1:${r.risk_reward}</div>
                </div>
            </div>
        </div>

        <!-- Place Trade / Active Position -->
        ${data.active_trade ? `
        <div class="glass-card-sm" style="padding:16px;margin-bottom:16px;border-left:3px solid #22c55e" id="trade-section">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                <div style="font-size:0.8rem;font-weight:700;color:#4ade80">Position Open</div>
                <div style="display:flex;align-items:center;gap:8px">
                    <div style="font-size:0.5rem;color:var(--text-secondary)">Since ${data.active_trade.entry_date}</div>
                    <button onclick="_cancelTrade(${data.active_trade.id}, ${s.id})"
                        style="padding:3px 8px;border-radius:4px;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);color:#f87171;font-size:0.5rem;cursor:pointer">
                        Cancel Trade
                    </button>
                </div>
            </div>
            <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px">
                <div style="text-align:center;padding:8px;border-radius:6px;background:var(--glass-bg)">
                    <div style="font-size:0.45rem;color:var(--text-secondary)">Strike</div>
                    <div style="font-size:0.75rem;font-weight:700;color:var(--text-heading)">${data.active_trade.strike} ${data.active_trade.side}</div>
                </div>
                <div style="text-align:center;padding:8px;border-radius:6px;background:var(--glass-bg)">
                    <div style="font-size:0.45rem;color:var(--text-secondary)">Entry</div>
                    <div style="font-size:0.75rem;font-weight:700;color:var(--text-heading)">₹${data.active_trade.entry_premium}</div>
                </div>
                <div style="text-align:center;padding:8px;border-radius:6px;background:rgba(239,68,68,0.06)">
                    <div style="font-size:0.45rem;color:#fca5a5">SL</div>
                    <div style="font-size:0.75rem;font-weight:700;color:#f87171">₹${data.active_trade.sl_price}</div>
                </div>
                <div style="text-align:center;padding:8px;border-radius:6px;background:rgba(34,197,94,0.06)">
                    <div style="font-size:0.45rem;color:#86efac">Target</div>
                    <div style="font-size:0.75rem;font-weight:700;color:#4ade80">₹${data.active_trade.target_price}</div>
                </div>
            </div>
        </div>
        ` : `
        <div class="glass-card-sm" style="padding:16px;margin-bottom:16px;border-left:3px solid #22c55e" id="trade-section">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                <div style="font-size:0.8rem;font-weight:700;color:var(--text-heading)">Execute Trade</div>
                <div style="font-size:0.5rem;color:var(--text-secondary)">LIMIT Buy + SL + Target</div>
            </div>

            <div id="trade-form-area">
                <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:10px">
                    <div>
                        <label style="font-size:0.5rem;color:var(--text-secondary);display:block;margin-bottom:3px">Lots</label>
                        <div style="display:flex;align-items:center;gap:4px">
                            <button onclick="_adjustLots(-1)" style="width:24px;height:24px;border-radius:4px;background:var(--glass-bg);border:1px solid var(--glass-border);color:var(--text-primary);font-size:0.7rem;cursor:pointer;display:flex;align-items:center;justify-content:center">−</button>
                            <input type="number" id="trade-lots" value="1" min="1" max="50" oninput="_updateTotalCost()"
                                style="width:40px;text-align:center;padding:4px;border-radius:4px;border:1px solid var(--glass-border);background:var(--glass-bg);color:var(--text-primary);font-size:0.75rem;font-weight:700" />
                            <button onclick="_adjustLots(1)" style="width:24px;height:24px;border-radius:4px;background:var(--glass-bg);border:1px solid var(--glass-border);color:var(--text-primary);font-size:0.7rem;cursor:pointer;display:flex;align-items:center;justify-content:center">+</button>
                        </div>
                    </div>
                    <div>
                        <label style="font-size:0.5rem;color:var(--text-secondary);display:block;margin-bottom:3px">Limit Price</label>
                        <input type="number" step="0.05" id="trade-limit-price" value="${r.premium}" oninput="_updateTotalCost()"
                            style="width:100%;padding:4px 6px;border-radius:4px;border:1px solid var(--glass-border);background:var(--glass-bg);color:var(--text-primary);font-size:0.75rem" />
                    </div>
                    <div>
                        <label style="font-size:0.5rem;color:var(--text-secondary);display:block;margin-bottom:3px">Total Cost</label>
                        <div id="trade-total-cost" style="font-size:0.85rem;font-weight:700;color:var(--text-heading);padding-top:4px">₹${fmtR(r.lot_cost)}</div>
                    </div>
                </div>

                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">
                    <div style="padding:6px 8px;border-radius:6px;background:rgba(239,68,68,0.06)">
                        <div style="font-size:0.5rem;color:#fca5a5">SL Premium (GTT)</div>
                        <div style="font-size:0.7rem;font-weight:600;color:#f87171">₹${r.sl_premium}</div>
                        <div style="font-size:0.45rem;color:var(--text-secondary)">Triggers auto SELL</div>
                    </div>
                    <div style="padding:6px 8px;border-radius:6px;background:rgba(34,197,94,0.06)">
                        <div style="font-size:0.5rem;color:#86efac">Target Premium (GTT)</div>
                        <div style="font-size:0.7rem;font-weight:600;color:#4ade80">₹${r.target_premium}</div>
                        <div style="font-size:0.45rem;color:var(--text-secondary)">Triggers auto SELL</div>
                    </div>
                </div>

                <button id="trade-buy-btn" onclick="_confirmTrade(${s.id}, '${r.option_security_id || ''}', ${r.strike}, '${data.side}', '${data.expiry}', ${data.lot_size}, ${r.sl_premium}, ${r.target_premium})"
                    ${isAdminUser() ? '' : 'disabled'}
                    style="width:100%;padding:10px;border-radius:8px;background:linear-gradient(135deg,#22c55e,#16a34a);color:#fff;font-weight:800;font-size:0.75rem;border:none;cursor:${isAdminUser() ? 'pointer' : 'not-allowed'};opacity:${isAdminUser() ? '1' : '0.55'};letter-spacing:0.03em;transition:all 0.15s"
                    onmouseover="this.style.transform='scale(1.01)'" onmouseout="this.style.transform='scale(1)'">
                    BUY ${r.strike} ${data.side} — ₹${fmtR(r.lot_cost)}
                </button>
                <div style="font-size:0.45rem;color:var(--text-secondary);text-align:center;margin-top:4px">
                    ${isAdminUser() ? 'Limit order • SL & Target auto-placed as GTT • AMO if market closed' : 'Readonly mode: order placement disabled'}
                </div>
            </div>
            <div id="trade-result-area" style="display:none"></div>
        </div>
        `}

        <!-- Technicals -->
        ${data.technicals ? `
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px">
            <div class="glass-card-sm" style="padding:8px;text-align:center">
                <div style="font-size:0.5rem;color:var(--text-secondary)">ATR (14)</div>
                <div style="font-size:0.75rem;font-weight:700;color:var(--text-heading)">${data.technicals.atr}</div>
            </div>
            <div class="glass-card-sm" style="padding:8px;text-align:center">
                <div style="font-size:0.5rem;color:var(--text-secondary)">Support</div>
                <div style="font-size:0.75rem;font-weight:700;color:#4ade80">${data.technicals.support}</div>
            </div>
            <div class="glass-card-sm" style="padding:8px;text-align:center">
                <div style="font-size:0.5rem;color:var(--text-secondary)">Resistance</div>
                <div style="font-size:0.75rem;font-weight:700;color:#f87171">${data.technicals.resistance}</div>
            </div>
            <div class="glass-card-sm" style="padding:8px;text-align:center">
                <div style="font-size:0.5rem;color:var(--text-secondary)">20d Range</div>
                <div style="font-size:0.65rem;font-weight:600;color:var(--text-heading)">${data.technicals.swing_low} – ${data.technicals.swing_high}</div>
            </div>
        </div>` : ''}

        <!-- Greeks -->
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px">
            ${greekCards.map(g => `
                <div class="glass-card-sm" style="padding:12px;text-align:center">
                    <div style="font-size:0.6rem;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:4px">${g.label}</div>
                    <div style="font-size:1rem;font-weight:700;color:${g.color}">${g.value}</div>
                    <div style="font-size:0.55rem;color:var(--text-secondary);margin-top:4px">${g.desc}</div>
                </div>
            `).join('')}
        </div>

        <!-- Live Advisory -->
        <div class="glass-card-sm" style="padding:16px;margin-bottom:16px;border-left:3px solid #fbbf24">
            <div style="font-size:0.8rem;font-weight:700;color:var(--text-heading);margin-bottom:10px">⚡ Live Advisory</div>
            ${data.active_trade ? `
                <div style="padding:8px 10px;border-radius:6px;background:rgba(34,197,94,0.08);margin-bottom:10px;display:flex;align-items:center;justify-content:space-between">
                    <div>
                        <div style="font-size:0.55rem;color:#86efac">Active Position</div>
                        <div style="font-size:0.7rem;font-weight:700;color:#4ade80">${data.active_trade.strike} ${data.active_trade.side} × ${data.active_trade.lots} lot(s) @ ₹${data.active_trade.entry_premium}</div>
                        <div style="font-size:0.45rem;color:var(--text-secondary)">Entered ${data.active_trade.entry_date} · SL: ₹${data.active_trade.sl_price} · Target: ₹${data.active_trade.target_price}</div>
                    </div>
                </div>
            ` : `
                <div style="font-size:0.55rem;color:var(--text-secondary);margin-bottom:10px">Already entered this trade? Get real-time HOLD / EXIT / BOOK PROFIT advice</div>
            `}
            <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap" id="live-advisory-inputs">
                <div style="flex:1;min-width:80px">
                    <label style="font-size:0.55rem;color:var(--text-secondary);display:block;margin-bottom:3px">Your Entry Premium</label>
                    <input type="number" step="0.05" id="entry-premium-input"
                        value="${data.active_trade ? data.active_trade.entry_premium : ''}"
                        placeholder="e.g. ${r.premium}"
                        style="width:100%;padding:6px 8px;border-radius:6px;border:1px solid var(--glass-border);background:var(--glass-bg);color:var(--text-primary);font-size:0.75rem;outline:none" />
                </div>
                <button onclick="fetchLiveAdvisory(${s.id}, ${data.active_trade ? data.active_trade.strike : r.strike}, '${data.active_trade ? data.active_trade.side : data.side}', '${data.active_trade ? data.active_trade.expiry : data.expiry}')"
                    style="padding:6px 14px;border-radius:6px;background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;font-weight:700;font-size:0.65rem;border:none;cursor:pointer;white-space:nowrap">
                    Check Now
                </button>
            </div>
            <div id="live-advisory-result" style="margin-top:12px"></div>
        </div>

        <!-- Strike Chain Table -->
        <div style="margin-bottom:12px">
            <div style="font-size:0.75rem;font-weight:600;color:var(--text-heading);margin-bottom:8px">
                Strike Chain <span style="font-size:0.6rem;color:var(--text-secondary);font-weight:400">(±12% from spot)</span>
            </div>
            <div style="overflow-x:auto">
                <table class="data-table" style="font-size:0.6rem">
                    <thead><tr>
                        <th>Strike</th><th>Premium</th><th>Lot Cost</th>
                        <th>Delta</th><th>Theta</th><th>IV</th>
                        <th>OI</th><th>Volume</th><th>Spread</th>
                    </tr></thead>
                    <tbody>${strikeRows}</tbody>
                </table>
            </div>
        </div>
    `;

    // Auto-trigger live advisory if there's an active trade
    if (data.active_trade) {
        setTimeout(() => {
            fetchLiveAdvisory(
                data.signal.id,
                data.active_trade.strike,
                data.active_trade.side,
                data.active_trade.expiry
            );
        }, 300);
    }
}

// --- Live Advisory ---

async function fetchLiveAdvisory(signalId, strike, side, expiry) {
    const input = document.getElementById('entry-premium-input');
    const resultDiv = document.getElementById('live-advisory-result');
    const entryPremium = parseFloat(input?.value);

    if (!entryPremium || entryPremium <= 0) {
        resultDiv.innerHTML = '<div style="color:#fca5a5;font-size:0.6rem">Enter your entry premium above</div>';
        return;
    }

    resultDiv.innerHTML = '<div style="color:var(--text-secondary);font-size:0.6rem">Fetching live data...</div>';

    try {
        const params = new URLSearchParams({ entry_premium: entryPremium, strike, side, expiry });
        const resp = await fetch(`${API_BASE}/api/signals/${signalId}/live-advisory?${params}`);
        if (!resp.ok) throw new Error(await resp.text());
        const d = await resp.json();

        if (d.market_closed) {
            resultDiv.innerHTML = `
                <div style="padding:10px;border-radius:8px;background:rgba(99,102,241,0.08);text-align:center">
                    <div style="font-size:0.7rem;font-weight:600;color:#a5b4fc;margin-bottom:4px">Market Closed</div>
                    <div style="font-size:0.55rem;color:var(--text-secondary)">${d.message}</div>
                    <div style="font-size:0.5rem;color:var(--text-secondary);margin-top:6px">Position: ${d.strike} ${d.side} · Entry: ₹${d.entry_premium} · DTE: ${d.dte}d</div>
                    <div style="font-size:0.5rem;color:var(--text-secondary);margin-top:6px">Use <strong>Exit Now</strong> during market hours to close position + cancel GTTs in one click</div>
                </div>`;
            return;
        }

        const pnlColor = d.pnl_pct >= 0 ? '#4ade80' : '#f87171';
        const pnlSign = d.pnl_pct >= 0 ? '+' : '';
        const urgencyBg = d.urgency === 'high' ? 'rgba(239,68,68,0.15)' : d.urgency === 'medium' ? 'rgba(234,179,8,0.12)' : 'rgba(34,197,94,0.08)';
        const urgencyBorder = d.urgency === 'high' ? '#f87171' : d.urgency === 'medium' ? '#fbbf24' : '#4ade80';
        const actionIcon = d.action.includes('EXIT') ? '🔴' : d.action.includes('BOOK') ? '🟡' : '🟢';
        const fmt = n => new Intl.NumberFormat('en-IN').format(Math.round(n));

        resultDiv.innerHTML = `
            <!-- Action Banner -->
            <div style="padding:12px;border-radius:8px;background:${urgencyBg};border-left:3px solid ${urgencyBorder};margin-bottom:10px">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                    <span style="font-size:1.1rem">${actionIcon}</span>
                    <span style="font-size:0.85rem;font-weight:800;color:${urgencyBorder}">${d.action}</span>
                </div>
                ${d.reasons.map(r => `<div style="font-size:0.6rem;color:var(--text-primary);padding:2px 0;padding-left:28px">• ${r}</div>`).join('')}
            </div>

            <!-- Live Numbers -->
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px">
                <div style="text-align:center;padding:8px;border-radius:8px;background:var(--glass-bg)">
                    <div style="font-size:0.5rem;color:var(--text-secondary)">Current Premium</div>
                    <div style="font-size:0.85rem;font-weight:700;color:var(--text-heading)">₹${d.current_premium}</div>
                </div>
                <div style="text-align:center;padding:8px;border-radius:8px;background:var(--glass-bg)">
                    <div style="font-size:0.5rem;color:var(--text-secondary)">P&L / Lot</div>
                    <div style="font-size:0.85rem;font-weight:700;color:${pnlColor}">${pnlSign}₹${fmt(d.pnl_per_lot)}</div>
                </div>
                <div style="text-align:center;padding:8px;border-radius:8px;background:var(--glass-bg)">
                    <div style="font-size:0.5rem;color:var(--text-secondary)">Return</div>
                    <div style="font-size:0.85rem;font-weight:700;color:${pnlColor}">${pnlSign}${d.pnl_pct}%</div>
                </div>
            </div>

            <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:10px">
                <div style="text-align:center;padding:6px;border-radius:6px;background:var(--glass-bg)">
                    <div style="font-size:0.45rem;color:var(--text-secondary)">Spot</div>
                    <div style="font-size:0.7rem;font-weight:600;color:var(--text-heading)">${d.spot_price}</div>
                </div>
                <div style="text-align:center;padding:6px;border-radius:6px;background:var(--glass-bg)">
                    <div style="font-size:0.45rem;color:var(--text-secondary)">Bid / Ask</div>
                    <div style="font-size:0.65rem;font-weight:600;color:var(--text-heading)">${d.greeks.delta ? d.market.bid + ' / ' + d.market.ask : '-'}</div>
                </div>
                <div style="text-align:center;padding:6px;border-radius:6px;background:var(--glass-bg)">
                    <div style="font-size:0.45rem;color:var(--text-secondary)">Trail SL</div>
                    <div style="font-size:0.7rem;font-weight:600;color:#f87171">₹${d.trail_sl_premium}</div>
                </div>
                <div style="text-align:center;padding:6px;border-radius:6px;background:var(--glass-bg)">
                    <div style="font-size:0.45rem;color:var(--text-secondary)">DTE</div>
                    <div style="font-size:0.7rem;font-weight:600;color:${d.dte <= 3 ? '#f87171' : d.dte <= 7 ? '#fbbf24' : 'var(--text-heading)'}">${d.dte}d</div>
                </div>
            </div>

            <!-- Theta Warning -->
            ${d.theta_daily_pct > 3 ? `
            <div style="padding:5px 10px;border-radius:6px;background:rgba(251,191,36,0.08);font-size:0.55rem;color:#fde68a;margin-bottom:8px">
                ⏳ Theta burning <strong>${d.theta_daily_pct}%</strong> of your entry per day (₹${Math.abs(d.greeks.theta).toFixed(2)}/lot/day)
            </div>` : ''}

            <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:6px;flex-wrap:wrap">
                ${_tradeAnalysisData?.active_trade ? `
                <button onclick="_exitTradeNow(${_tradeAnalysisData.active_trade.id}, ${signalId})" id="exit-now-btn"
                    style="padding:5px 12px;border-radius:6px;background:rgba(239,68,68,0.15);border:1px solid #f87171;color:#f87171;font-size:0.55rem;cursor:pointer;font-weight:700">
                    Exit Now (Market Sell)
                </button>` : ''}
                <button onclick="fetchLiveAdvisory(${signalId}, ${strike}, '${side}', '${expiry}')"
                    style="padding:5px 12px;border-radius:6px;background:var(--glass-bg);border:1px solid var(--glass-border);color:var(--text-secondary);font-size:0.55rem;cursor:pointer">
                    ↻ Refresh
                </button>
                <button onclick="toggleAutoRefresh(${signalId}, ${strike}, '${side}', '${expiry}')" id="auto-refresh-btn"
                    style="padding:5px 12px;border-radius:6px;background:${_advisoryAutoRefresh ? 'rgba(239,68,68,0.2)' : 'rgba(34,197,94,0.15)'};border:1px solid ${_advisoryAutoRefresh ? '#f87171' : '#4ade80'};color:${_advisoryAutoRefresh ? '#f87171' : '#4ade80'};font-size:0.55rem;cursor:pointer">
                    ${_advisoryAutoRefresh ? '■ Stop Auto' : '▶ Auto Refresh 30s'}
                </button>
            </div>
        `;
    } catch (err) {
        resultDiv.innerHTML = `<div style="color:#fca5a5;font-size:0.6rem">Error: ${err.message}</div>`;
    }
}

function toggleAutoRefresh(signalId, strike, side, expiry) {
    if (_advisoryAutoRefresh) {
        clearInterval(_advisoryAutoRefresh);
        _advisoryAutoRefresh = null;
        const btn = document.getElementById('auto-refresh-btn');
        if (btn) {
            btn.style.background = 'rgba(34,197,94,0.15)';
            btn.style.borderColor = '#4ade80';
            btn.style.color = '#4ade80';
            btn.textContent = '▶ Auto Refresh 30s';
        }
    } else {
        fetchLiveAdvisory(signalId, strike, side, expiry);
        _advisoryAutoRefresh = setInterval(() => fetchLiveAdvisory(signalId, strike, side, expiry), 30000);
        const btn = document.getElementById('auto-refresh-btn');
        if (btn) {
            btn.style.background = 'rgba(239,68,68,0.2)';
            btn.style.borderColor = '#f87171';
            btn.style.color = '#f87171';
            btn.textContent = '■ Stop Auto';
        }
    }
}

// --- Trade Execution ---

let _tradeAnalysisData = null;

function _adjustLots(delta) {
    const input = document.getElementById('trade-lots');
    if (!input) return;
    let val = parseInt(input.value) + delta;
    val = Math.max(1, Math.min(50, val));
    input.value = val;
    _updateTotalCost();
}

function _updateTotalCost() {
    const lotsInput = document.getElementById('trade-lots');
    const priceInput = document.getElementById('trade-limit-price');
    const costDiv = document.getElementById('trade-total-cost');
    const btn = document.getElementById('trade-buy-btn');
    if (!lotsInput || !priceInput || !costDiv) return;

    const lots = parseInt(lotsInput.value) || 1;
    const price = parseFloat(priceInput.value) || 0;
    const lotSize = _tradeAnalysisData?.lot_size || 1;
    const total = Math.round(price * lotSize * lots);
    const fmt = n => new Intl.NumberFormat('en-IN').format(n);
    costDiv.textContent = `₹${fmt(total)}`;

    if (btn && _tradeAnalysisData) {
        const r = _tradeAnalysisData.recommendation;
        btn.textContent = `BUY ${lots} lot${lots > 1 ? 's' : ''} × ${r.strike} ${_tradeAnalysisData.side} — ₹${fmt(total)}`;
    }
}

function _confirmTrade(signalId, optionSecId, strike, side, expiry, lotSize, slPremium, targetPremium) {
    if (!isAdminUser()) {
        alert('Readonly user cannot place orders.');
        return;
    }
    if (!optionSecId) {
        alert('Option security ID not available. Try refreshing the analysis.');
        return;
    }

    const lots = parseInt(document.getElementById('trade-lots')?.value) || 1;
    const limitPrice = parseFloat(document.getElementById('trade-limit-price')?.value) || 0;
    if (limitPrice <= 0) {
        alert('Enter a valid limit price');
        return;
    }

    const quantity = lots * lotSize;
    const totalCost = Math.round(limitPrice * quantity);
    const fmt = n => new Intl.NumberFormat('en-IN').format(n);

    const confirmed = confirm(
        `CONFIRM TRADE\n\n` +
        `Buy ${lots} lot(s) of ${strike} ${side}\n` +
        `Quantity: ${quantity}\n` +
        `Limit Price: ₹${limitPrice}\n` +
        `Total Cost: ₹${fmt(totalCost)}\n\n` +
        `SL GTT: ₹${slPremium} (auto exit)\n` +
        `Target GTT: ₹${targetPremium} (auto exit)\n\n` +
        `Proceed?`
    );

    if (!confirmed) return;
    _executeTrade(signalId, optionSecId, strike, side, expiry, lots, lotSize, limitPrice, slPremium, targetPremium);
}

async function _executeTrade(signalId, optionSecId, strike, side, expiry, lots, lotSize, limitPrice, slPremium, targetPremium) {
    if (!isAdminUser()) return;
    const btn = document.getElementById('trade-buy-btn');
    const resultArea = document.getElementById('trade-result-area');
    const formArea = document.getElementById('trade-form-area');
    if (btn) {
        btn.disabled = true;
        btn.style.opacity = '0.6';
        btn.textContent = 'Placing orders...';
    }

    try {
        const resp = await fetch(`${API_BASE}/api/signals/place-trade`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                signal_id: signalId,
                option_security_id: optionSecId,
                strike: strike,
                side: side,
                expiry: expiry,
                lots: lots,
                lot_size: lotSize,
                limit_price: limitPrice,
                sl_trigger_price: slPremium,
                target_price: targetPremium,
            }),
        });

        const data = await resp.json();

        if (!resp.ok) {
            const errMsg = data.errors?.join(', ') || data.detail || 'Order failed';
            if (btn) {
                btn.disabled = false;
                btn.style.opacity = '1';
                btn.textContent = 'Retry';
            }
            if (resultArea) {
                resultArea.style.display = 'block';
                resultArea.innerHTML = `<div style="padding:10px;border-radius:8px;background:rgba(239,68,68,0.1);margin-top:8px">
                    <div style="font-size:0.7rem;font-weight:700;color:#f87171;margin-bottom:4px">Order Failed</div>
                    <div style="font-size:0.55rem;color:#fca5a5">${errMsg}</div>
                </div>`;
            }
            return;
        }

        // Success
        if (formArea) formArea.style.display = 'none';
        if (resultArea) {
            resultArea.style.display = 'block';
            const warnings = (data.errors || []).map(e =>
                `<div style="font-size:0.5rem;color:#fde68a;padding:2px 0">⚠ ${e}</div>`
            ).join('');

            resultArea.innerHTML = `
                <div style="padding:12px;border-radius:8px;background:rgba(34,197,94,0.1);text-align:center">
                    <div style="font-size:1.2rem;margin-bottom:6px">✅</div>
                    <div style="font-size:0.75rem;font-weight:700;color:#4ade80;margin-bottom:4px">${data.message || 'Trade Placed!'}</div>
                    ${data.is_amo ? '<div style="font-size:0.55rem;color:#fbbf24;margin-bottom:6px">📌 After Market Order — will execute at market open</div>' : ''}
                    <div style="font-size:0.55rem;color:var(--text-secondary);margin-bottom:8px">
                        Buy Order: ${data.buy?.orderId || '?'} (${data.buy?.orderStatus || '?'})
                    </div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;text-align:center;margin-bottom:6px">
                        <div style="padding:6px;border-radius:6px;background:rgba(239,68,68,0.06)">
                            <div style="font-size:0.45rem;color:#fca5a5">SL GTT</div>
                            <div style="font-size:0.6rem;font-weight:600;color:#f87171">${data.sl ? data.sl.orderId : 'Manual needed'}</div>
                        </div>
                        <div style="padding:6px;border-radius:6px;background:rgba(34,197,94,0.06)">
                            <div style="font-size:0.45rem;color:#86efac">Target GTT</div>
                            <div style="font-size:0.6rem;font-weight:600;color:#4ade80">${data.target ? data.target.orderId : 'Manual needed'}</div>
                        </div>
                    </div>
                    ${warnings}
                </div>
            `;
        }
    } catch (err) {
        if (btn) {
            btn.disabled = false;
            btn.style.opacity = '1';
            btn.textContent = 'Retry';
        }
        if (resultArea) {
            resultArea.style.display = 'block';
            resultArea.innerHTML = `<div style="padding:8px;border-radius:6px;background:rgba(239,68,68,0.1);font-size:0.6rem;color:#f87171">Network error: ${err.message}</div>`;
        }
    }
}

async function _exitTradeNow(tradeId, signalId) {
    if (!isAdminUser()) {
        alert('Readonly user cannot exit orders.');
        return;
    }
    const trade = _tradeAnalysisData?.active_trade;
    if (!trade) return;

    const confirmed = confirm(
        `EXIT NOW\n\n` +
        `Market SELL ${trade.strike} ${trade.side} × ${trade.lots} lot(s)\n` +
        `Entry: ₹${trade.entry_premium}\n\n` +
        `This will:\n` +
        `• Place MARKET SELL order\n` +
        `• Cancel SL GTT order\n` +
        `• Cancel Target GTT order\n\n` +
        `Proceed?`
    );
    if (!confirmed) return;

    const btn = document.getElementById('exit-now-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Exiting...'; btn.style.opacity = '0.6'; }

    try {
        const resp = await fetch(`${API_BASE}/api/signals/trades/${tradeId}/exit-now`, { method: 'POST' });
        const data = await resp.json();

        if (!resp.ok) {
            const errMsg = data.errors?.join(', ') || data.detail || 'Exit failed';
            alert('Exit failed: ' + errMsg);
            if (btn) { btn.disabled = false; btn.textContent = 'Exit Now (Market Sell)'; btn.style.opacity = '1'; }
            return;
        }

        const resultDiv = document.getElementById('live-advisory-result');
        if (resultDiv) {
            resultDiv.innerHTML = `
                <div style="padding:12px;border-radius:8px;background:rgba(34,197,94,0.1);text-align:center;margin-top:8px">
                    <div style="font-size:1rem;margin-bottom:4px">✅</div>
                    <div style="font-size:0.7rem;font-weight:700;color:#4ade80">${data.message}</div>
                    <div style="font-size:0.55rem;color:var(--text-secondary);margin-top:4px">Sell Order: ${data.sell?.orderId || '?'}</div>
                    ${data.errors?.length ? data.errors.map(e => `<div style="font-size:0.5rem;color:#fde68a;margin-top:2px">⚠ ${e}</div>`).join('') : ''}
                </div>`;
        }

        // Refresh the panel after a moment
        setTimeout(() => viewSignalDetail(signalId), 2000);
    } catch (err) {
        alert('Network error: ' + err.message);
        if (btn) { btn.disabled = false; btn.textContent = 'Exit Now (Market Sell)'; btn.style.opacity = '1'; }
    }
}

async function _cancelTrade(tradeId, signalId) {
    if (!isAdminUser()) {
        alert('Readonly user cannot cancel/update trade state.');
        return;
    }
    if (!confirm('Cancel this trade? This only updates the dashboard — make sure you already cancelled orders on Dhan.')) return;

    try {
        const resp = await fetch(`${API_BASE}/api/signals/trades/${tradeId}/close?reason=cancelled`, { method: 'POST' });
        if (!resp.ok) throw new Error('Failed to cancel');
        viewSignalDetail(signalId);
    } catch (err) {
        alert('Error: ' + err.message);
    }
}

// --- F&O Directional Scanner ---

let fnoScanMode = 'auto'; // 'auto' or 'custom'
let fnoSubTab = 'core';

function toggleFnoScanMode() {
    const btn = document.getElementById('btn-fno-mode');
    const panel = document.getElementById('fno-custom-panel');
    
    if (fnoScanMode === 'auto') {
        fnoScanMode = 'custom';
        btn.textContent = 'Custom Mode';
        btn.classList.remove('btn-secondary');
        btn.classList.add('btn-primary');
        panel.classList.remove('hidden');
        loadSectorOptions();
    } else {
        fnoScanMode = 'auto';
        btn.textContent = 'Auto Mode';
        btn.classList.remove('btn-primary');
        btn.classList.add('btn-secondary');
        panel.classList.add('hidden');
    }
}

function switchFnoSubTab(mode, btn) {
    fnoSubTab = mode;
    const corePanel = document.getElementById('fno-core-panel');
    const adPanel = document.getElementById('fno-advdecl-panel');
    if (corePanel) corePanel.classList.toggle('hidden', mode !== 'core');
    if (adPanel) adPanel.classList.toggle('hidden', mode !== 'advdecl');

    const pills = document.querySelectorAll('#fno-subtab .filter-pill');
    pills.forEach(p => p.classList.toggle('active', p.dataset.value === mode));
}

// --- Index Trading ---

let _indexMode = 'intraday';
let _indexSubTab = 'nifty';
let _trackingTimer = null;
let _indexManualSetup = null;
let _indexManualMonitorTimer = null;

function switchIndexSubTab(mode, btn) {
    _indexSubTab = mode === 'sensex' ? 'sensex' : 'nifty';
    const niftyPanel = document.getElementById('index-panel-nifty');
    const sensexPanel = document.getElementById('index-panel-sensex');
    if (niftyPanel) niftyPanel.classList.toggle('hidden', _indexSubTab !== 'nifty');
    if (sensexPanel) sensexPanel.classList.toggle('hidden', _indexSubTab !== 'sensex');

    const pills = document.querySelectorAll('#index-subtab .filter-pill');
    pills.forEach(p => p.classList.toggle('active', p.dataset.value === _indexSubTab));
    refreshIndexManualDesk();
}

function _setIndexManualStatus(msg, isError = false) {
    const el = document.getElementById('index-manual-status');
    if (!el) return;
    el.textContent = msg || '';
    el.style.color = isError ? '#ef4444' : 'var(--text-muted)';
}

function _activeIndexKey() {
    return _indexSubTab === 'sensex' ? 'sensex' : 'nifty';
}

function scrollToIndexOpenPositions() {
    const el = document.getElementById('index-manual-open');
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function _indexDeskPlanKey(side) {
    const s = side === 'pe' ? 'pe' : 'ce';
    return `index-desk-plan:${_activeIndexKey()}:${s}`;
}

function saveIndexDeskPlan(side) {
    const s = side === 'pe' ? 'pe' : 'ce';
    const fields = {
        strike: document.getElementById(`index-${s}-strike`)?.value || '',
        lots: document.getElementById(`index-${s}-lots`)?.value || '',
        sl: document.getElementById(`index-${s}-sl`)?.value || '',
        t1: document.getElementById(`index-${s}-t1`)?.value || '',
        t1Lots: document.getElementById(`index-${s}-t1-lots`)?.value || '',
        t2: document.getElementById(`index-${s}-t2`)?.value || '',
        t2Lots: document.getElementById(`index-${s}-t2-lots`)?.value || '',
    };
    localStorage.setItem(_indexDeskPlanKey(s), JSON.stringify(fields));
    _setIndexManualStatus(`${s.toUpperCase()} plan saved`);
}

function updateIndexManualAmount(sideKey) {
    const side = sideKey === 'pe' ? 'pe' : 'ce';
    const amountEl = document.getElementById(`index-${side}-amount`);
    const lotsEl = document.getElementById(`index-${side}-lots`);
    const slEl = document.getElementById(`index-${side}-sl`);
    const t1El = document.getElementById(`index-${side}-t1`);
    const t2El = document.getElementById(`index-${side}-t2`);
    const t1LotsEl = document.getElementById(`index-${side}-t1-lots`);
    const t2LotsEl = document.getElementById(`index-${side}-t2-lots`);
    if (!amountEl || !lotsEl || !slEl || !t1El || !t2El || !t1LotsEl || !t2LotsEl) return;

    const setup = _indexManualSetup || {};
    const sideData = setup[side] || {};
    const cmpRaw = Number(sideData.ltp || 0);
    const cmp = cmpRaw > 0 ? Number(cmpRaw.toFixed(2)) : 0;
    const sl = Number(slEl.value || 0);
    const t1 = Number(t1El.value || 0);
    const t2 = Number(t2El.value || 0);
    const t1Lots = Math.max(0, Number(t1LotsEl.value || 0));
    const t2Lots = Math.max(0, Number(t2LotsEl.value || 0));
    const fallbackLot = _activeIndexKey() === 'sensex' ? 20 : 65;
    const lotSize = Number(setup.lot_size || fallbackLot);
    const lots = Math.max(1, Number(lotsEl.value || 1));
    const qty = lotSize > 0 ? lotSize * lots : 0;
    const amount = cmp > 0 && qty > 0 ? cmp * qty : null;
    const slGap = cmp > 0 && sl > 0 ? (cmp - sl) : null;
    const riskAmount = slGap != null && qty > 0 ? (slGap * qty) : null;
    const t1Qty = lotSize > 0 ? t1Lots * lotSize : 0;
    const t2Qty = lotSize > 0 ? t2Lots * lotSize : 0;
    const t1Profit = (cmp > 0 && t1 > 0 && t1Qty > 0) ? (t1 - cmp) * t1Qty : null;
    const t2Profit = (cmp > 0 && t2 > 0 && t2Qty > 0) ? (t2 - cmp) * t2Qty : null;
    const probableProfit = ((t1Profit || 0) + (t2Profit || 0));

    const fmt = n => new Intl.NumberFormat('en-IN').format(Math.round(Number(n || 0)));
    const fmt2 = n => Number(n || 0).toFixed(2);
    const profitLabel = v => v == null ? '-' : `₹${fmt(v)}`;
    amountEl.textContent = amount != null
        ? `CMP: ₹${fmt2(cmp)} · Lot Size: ${lotSize} · Qty: ${qty} · T1 Qty: ${t1Qty} · T2 Qty: ${t2Qty} · SL Gap: ₹${slGap != null ? fmt2(slGap) : '-'} · Amount Required: ₹${fmt(amount)}${riskAmount != null ? ` · Risk @ SL: ₹${fmt(riskAmount)}` : ''} · T1 Profit: ${profitLabel(t1Profit)} · T2 Profit: ${profitLabel(t2Profit)} · Total Probable Profit: ${profitLabel((t1Profit != null || t2Profit != null) ? probableProfit : null)}`
        : 'CMP: - · Qty: - · Amount Required: -';
}

async function refreshIndexManualDesk() {
    await Promise.all([loadIndexManualSetup(), loadIndexManualOpenTrades()]);
    startIndexManualMonitor();
}

async function loadIndexManualSetup() {
    const indexKey = _activeIndexKey();
    try {
        const resp = await fetch(`${API_BASE}/api/index-trading/manual/setup?index=${indexKey}`);
        const data = await resp.json();
        if (!resp.ok) throw new Error(data?.detail || 'Failed to load execution desk');
        _indexManualSetup = data;

        const ceStrike = document.getElementById('index-ce-strike');
        const peStrike = document.getElementById('index-pe-strike');
        const ceSl = document.getElementById('index-ce-sl');
        const peSl = document.getElementById('index-pe-sl');
        const ceT1 = document.getElementById('index-ce-t1');
        const ceT2 = document.getElementById('index-ce-t2');
        const ceT1Lots = document.getElementById('index-ce-t1-lots');
        const ceT2Lots = document.getElementById('index-ce-t2-lots');
        const peT1 = document.getElementById('index-pe-t1');
        const peT2 = document.getElementById('index-pe-t2');
        const peT1Lots = document.getElementById('index-pe-t1-lots');
        const peT2Lots = document.getElementById('index-pe-t2-lots');
        const ceLots = document.getElementById('index-ce-lots');
        const peLots = document.getElementById('index-pe-lots');
        if (ceStrike && data?.ce?.strike) ceStrike.value = String(Math.round(Number(data.ce.strike)));
        if (peStrike && data?.pe?.strike) peStrike.value = String(Math.round(Number(data.pe.strike)));
        // Auto-fill SL as premium value (not percentage): default 20% below CMP.
        if (ceSl && data?.ce?.ltp != null) ceSl.value = String(Math.max(0.05, Number((Number(data.ce.ltp) * 0.8).toFixed(2))));
        if (peSl && data?.pe?.ltp != null) peSl.value = String(Math.max(0.05, Number((Number(data.pe.ltp) * 0.8).toFixed(2))));
        if (ceT1 && data?.ce?.ltp != null) ceT1.value = Number((Number(data.ce.ltp) * 1.05).toFixed(2));
        if (ceT2 && data?.ce?.ltp != null) ceT2.value = Number((Number(data.ce.ltp) * 1.10).toFixed(2));
        if (peT1 && data?.pe?.ltp != null) peT1.value = Number((Number(data.pe.ltp) * 1.05).toFixed(2));
        if (peT2 && data?.pe?.ltp != null) peT2.value = Number((Number(data.pe.ltp) * 1.10).toFixed(2));
        const ceLotsVal = Math.max(1, Number(ceLots?.value || 1));
        const peLotsVal = Math.max(1, Number(peLots?.value || 1));
        const ceHalf = Math.floor(ceLotsVal / 2);
        const peHalf = Math.floor(peLotsVal / 2);
        if (ceT1Lots) ceT1Lots.value = String(ceHalf);
        if (ceT2Lots) ceT2Lots.value = String(Math.max(0, ceLotsVal - ceHalf));
        if (peT1Lots) peT1Lots.value = String(peHalf);
        if (peT2Lots) peT2Lots.value = String(Math.max(0, peLotsVal - peHalf));

        // Load saved manual plan overrides if present.
        try {
            const ceSaved = JSON.parse(localStorage.getItem(_indexDeskPlanKey('ce')) || 'null');
            const peSaved = JSON.parse(localStorage.getItem(_indexDeskPlanKey('pe')) || 'null');
            if (ceSaved) {
                if (ceStrike && ceSaved.strike) ceStrike.value = ceSaved.strike;
                if (ceLots && ceSaved.lots) ceLots.value = ceSaved.lots;
                if (ceSl && ceSaved.sl) ceSl.value = ceSaved.sl;
                if (ceT1 && ceSaved.t1) ceT1.value = ceSaved.t1;
                if (ceT2 && ceSaved.t2) ceT2.value = ceSaved.t2;
                if (ceT1Lots && ceSaved.t1Lots) ceT1Lots.value = ceSaved.t1Lots;
                if (ceT2Lots && ceSaved.t2Lots) ceT2Lots.value = ceSaved.t2Lots;
            }
            if (peSaved) {
                if (peStrike && peSaved.strike) peStrike.value = peSaved.strike;
                if (peLots && peSaved.lots) peLots.value = peSaved.lots;
                if (peSl && peSaved.sl) peSl.value = peSaved.sl;
                if (peT1 && peSaved.t1) peT1.value = peSaved.t1;
                if (peT2 && peSaved.t2) peT2.value = peSaved.t2;
                if (peT1Lots && peSaved.t1Lots) peT1Lots.value = peSaved.t1Lots;
                if (peT2Lots && peSaved.t2Lots) peT2Lots.value = peSaved.t2Lots;
            }
        } catch (_) {
            // ignore localStorage parse errors
        }
        updateIndexManualAmount('ce');
        updateIndexManualAmount('pe');

        const mktTag = data.market_open ? 'Live' : 'Closed';
        _setIndexManualStatus(`${data.index} Spot ₹${Number(data.spot || 0).toFixed(1)} · Exp ${data.expiry} · ${mktTag}`);
    } catch (e) {
        _setIndexManualStatus(`Desk load failed: ${e.message}`, true);
    }
}

async function loadIndexManualOpenTrades() {
    const indexKey = _activeIndexKey();
    const holder = document.getElementById('index-manual-open');
    if (!holder) return;
    try {
        const resp = await fetch(`${API_BASE}/api/index-trading/manual/open?index=${indexKey}`);
        const data = await resp.json();
        if (!resp.ok) throw new Error(data?.detail || 'Failed to load open positions');
        const rows = data.rows || [];
        if (!rows.length) {
            holder.innerHTML = `<div class="text-2xs text-center p-2" style="color:var(--text-muted)">No open manual positions</div>`;
            return;
        }
        const fmt = n => new Intl.NumberFormat('en-IN').format(Math.round(Number(n || 0)));
        holder.innerHTML = `
            <table class="data-table" style="font-size:0.58rem">
                <thead>
                    <tr>
                        <th>Leg</th>
                        <th>Entry</th>
                        <th>Live</th>
                        <th>MTM</th>
                        <th>SL</th>
                        <th>T1</th>
                        <th>T2</th>
                        <th>Update Plan</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows.map(r => {
                        const live = r.live_ltp != null ? Number(r.live_ltp) : null;
                        const entry = r.entry_price != null ? Number(r.entry_price) : null;
                        const qty = r.quantity != null ? Number(r.quantity) : 0;
                        const mtm = (live != null && entry != null && qty) ? (live - entry) * qty : null;
                        const mtmColor = mtm == null ? 'var(--text-muted)' : (mtm >= 0 ? '#22c55e' : '#ef4444');
                        return `
                        <tr>
                            <td>${r.strike || '-'} ${r.side || ''} · ${(r.remaining_lots ?? r.lots ?? 1)}L left</td>
                            <td>₹${fmt(r.entry_price)}</td>
                            <td>${r.live_ltp != null ? `₹${fmt(r.live_ltp)}` : '-'}</td>
                            <td style="color:${mtmColor};font-weight:600">${mtm == null ? '-' : `₹${fmt(mtm)}`}</td>
                            <td><input id="manual-sl-${r.trade_id}" class="themed-input rounded px-1 py-0.5 text-2xs" type="number" min="1" value="${r.sl_price || ''}" style="width:72px"></td>
                            <td style="display:flex;gap:4px;align-items:center">
                                <input id="manual-t1-${r.trade_id}" class="themed-input rounded px-1 py-0.5 text-2xs" type="number" min="0.05" value="${r.t1_price ?? ''}" style="width:68px">
                                <input id="manual-t1l-${r.trade_id}" class="themed-input rounded px-1 py-0.5 text-2xs" type="number" min="0" value="${r.t1_lots ?? 0}" style="width:42px" title="T1 lots">
                            </td>
                            <td style="display:flex;gap:4px;align-items:center">
                                <input id="manual-t2-${r.trade_id}" class="themed-input rounded px-1 py-0.5 text-2xs" type="number" min="0.05" value="${r.t2_price ?? ''}" style="width:68px">
                                <input id="manual-t2l-${r.trade_id}" class="themed-input rounded px-1 py-0.5 text-2xs" type="number" min="0" value="${r.t2_lots ?? 0}" style="width:42px" title="T2 lots">
                            </td>
                            <td style="display:flex;gap:4px;align-items:center">
                                ${isAdminUser()
                                    ? `<button onclick="modifyIndexManualPlan(${r.trade_id})" class="btn-secondary px-2 py-0.5 rounded text-2xs">Save</button>`
                                    : `<span class="text-2xs" style="color:var(--text-muted)">Readonly</span>`}
                            </td>
                        </tr>
                    `;
                    }).join('')}
                </tbody>
            </table>
        `;
    } catch (e) {
        holder.innerHTML = `<div class="text-2xs text-center p-2" style="color:#ef4444">Open positions load failed: ${e.message}</div>`;
    }
}

async function placeIndexManualTrade(side) {
    if (!isAdminUser()) {
        _setIndexManualStatus('Readonly user cannot place orders', true);
        return;
    }
    const sideKey = side === 'PE' ? 'pe' : 'ce';
    const strikeEl = document.getElementById(`index-${sideKey}-strike`);
    const lotsEl = document.getElementById(`index-${sideKey}-lots`);
    const slEl = document.getElementById(`index-${sideKey}-sl`);
    const t1El = document.getElementById(`index-${sideKey}-t1`);
    const t2El = document.getElementById(`index-${sideKey}-t2`);
    const t1LotsEl = document.getElementById(`index-${sideKey}-t1-lots`);
    const t2LotsEl = document.getElementById(`index-${sideKey}-t2-lots`);
    const btn = document.getElementById(`btn-index-${sideKey}-buy`);
    if (!strikeEl || !lotsEl || !slEl || !t1El || !t2El || !t1LotsEl || !t2LotsEl) return;

    const strike = Number(strikeEl.value || 0);
    const lots = Number(lotsEl.value || 1);
    const slValue = Number(slEl.value || 0);
    const t1Price = Number(t1El.value || 0);
    const t2Price = Number(t2El.value || 0);
    const t1Lots = Number(t1LotsEl.value || 0);
    const t2Lots = Number(t2LotsEl.value || 0);
    if (!strike || strike <= 0) {
        _setIndexManualStatus('Enter a valid strike', true);
        return;
    }
    if (!lots || lots < 1) {
        _setIndexManualStatus('Lots must be at least 1', true);
        return;
    }
    if (!slValue || slValue <= 0) {
        _setIndexManualStatus('Enter a valid SL premium value', true);
        return;
    }
    if (t1Price > 0 && t2Price > 0 && t2Price <= t1Price) {
        _setIndexManualStatus('T2 should be greater than T1', true);
        return;
    }
    if ((t1Lots + t2Lots) > lots) {
        _setIndexManualStatus('T1 + T2 lots cannot exceed total lots', true);
        return;
    }

    if (btn) { btn.disabled = true; btn.textContent = 'Placing...'; btn.style.opacity = '0.7'; }
    _setIndexManualStatus(`Placing ${side} order...`);
    try {
        const payload = {
            index: _activeIndexKey(),
            side,
            strike,
            lots,
            sl_price: slValue,
            t1_price: t1Price || null,
            t1_lots: t1Lots || 0,
            t2_price: t2Price || null,
            t2_lots: t2Lots || 0,
            expiry: _indexManualSetup?.expiry || null,
        };
        const resp = await fetch(`${API_BASE}/api/index-trading/manual/place`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data?.detail || 'Order placement failed');
        _setIndexManualStatus(`Executed ${side} ${data.strike} @ ₹${data.entry_price} · SL ₹${data.sl_price}`);
        await loadIndexManualOpenTrades();
        await loadIndexSignals(_indexMode); // Moves into upper tracking section
        startTradeTracking();
    } catch (e) {
        _setIndexManualStatus(`Order failed: ${e.message}`, true);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = side === 'PE' ? 'Buy PE @ CMP + Auto SL' : 'Buy CE @ CMP + Auto SL';
            btn.style.opacity = '1';
        }
    }
}

async function modifyIndexManualPlan(tradeId) {
    if (!isAdminUser()) {
        _setIndexManualStatus('Readonly user cannot modify live plans', true);
        return;
    }
    const slInput = document.getElementById(`manual-sl-${tradeId}`);
    const t1Input = document.getElementById(`manual-t1-${tradeId}`);
    const t2Input = document.getElementById(`manual-t2-${tradeId}`);
    const t1LotsInput = document.getElementById(`manual-t1l-${tradeId}`);
    const t2LotsInput = document.getElementById(`manual-t2l-${tradeId}`);
    if (!slInput || !t1Input || !t2Input || !t1LotsInput || !t2LotsInput) return;

    const sl = Number(slInput.value || 0);
    const t1 = Number(t1Input.value || 0);
    const t2 = Number(t2Input.value || 0);
    const t1Lots = Number(t1LotsInput.value || 0);
    const t2Lots = Number(t2LotsInput.value || 0);
    if (!sl || sl <= 0) {
        _setIndexManualStatus('Enter valid SL premium value', true);
        return;
    }
    if (t1 > 0 && t2 > 0 && t2 <= t1) {
        _setIndexManualStatus('T2 should be greater than T1', true);
        return;
    }

    _setIndexManualStatus(`Updating SL/T1/T2 for trade ${tradeId}...`);
    try {
        const resp = await fetch(`${API_BASE}/api/index-trading/manual/trades/${tradeId}/targets`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                sl_price: sl,
                t1_price: t1 || null,
                t2_price: t2 || null,
                t1_lots: t1Lots || 0,
                t2_lots: t2Lots || 0
            }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data?.detail || 'Plan update failed');
        _setIndexManualStatus(`Updated: SL ₹${data.sl_price} · T1 ${data.t1_price || '-'} · T2 ${data.t2_price || '-'}`);
        await loadIndexManualOpenTrades();
        await loadIndexSignals(_indexMode);
    } catch (e) {
        _setIndexManualStatus(`Plan update failed: ${e.message}`, true);
    }
}

function startIndexManualMonitor() {
    if (_indexManualMonitorTimer) return;
    _indexManualMonitorTimer = setInterval(async () => {
        try {
            await fetch(`${API_BASE}/api/index-trading/manual/monitor?index=${_activeIndexKey()}`, { method: 'POST' });
            await loadIndexManualOpenTrades();
            await loadIndexSignals(_indexMode);
        } catch (_) {
            // ignore transient monitor errors
        }
    }, 15000);
}

async function loadIndexSignals(mode = _indexMode) {
    _indexMode = mode;
    const status = document.getElementById('index-scan-status');
    if (status) status.textContent = 'Loading index signals...';

    try {
        const resp = await fetch(`${API_BASE}/api/index-trading/signals?mode=${mode}`);
        const data = await resp.json();
        window._lastIndexSignalsData = data;
        renderIndexSignals(data, mode);
        refreshIndexManualDesk();

        if (status) {
            const ts = data.as_of ? new Date(data.as_of).toLocaleTimeString() : '';
            const count = (data.signals || []).length;
            const skipText = data.summary?.skip_reason_counts && Object.keys(data.summary.skip_reason_counts).length
                ? ` · Skips: ${JSON.stringify(data.summary.skip_reason_counts)}`
                : '';
            status.textContent = count
                ? `Last scan: ${ts || 'n/a'} · ${mode === 'positional' ? 'Positional' : 'Intraday'} signals: ${count}${skipText}`
                : (data.summary?.error ? `Index scan error: ${data.summary.error}` : `No ${mode} index signals yet${skipText}`);
        }
    } catch (e) {
        if (status) status.textContent = `Index load failed: ${e.message}`;
    }
}

async function runIndexScan(mode = _indexMode) {
    if (!isAdminUser()) {
        const status = document.getElementById('index-scan-status');
        if (status) status.textContent = 'Readonly user cannot run scans';
        return;
    }
    _indexMode = mode;
    const btn = document.getElementById('btn-index-scan');
    const btnPos = document.getElementById('btn-index-scan-positional');
    const status = document.getElementById('index-scan-status');
    if (btn) { btn.disabled = true; btn.textContent = 'Scanning...'; btn.style.opacity = '0.7'; }
    if (btnPos) { btnPos.disabled = true; btnPos.textContent = 'Scanning...'; btnPos.style.opacity = '0.7'; }
    if (status) status.textContent = `Running ${mode} index scan...`;

    try {
        const resp = await fetch(`${API_BASE}/api/index-trading/scan/run?mode=${mode}`, { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data?.detail || 'Scan failed');
        window._lastIndexSignalsData = data;
        renderIndexSignals(data, mode);
        if (status) {
            const skips = data.summary?.skip_reason_counts;
            const skipText = skips && Object.keys(skips).length
                ? ` · Skips: ${JSON.stringify(skips)}`
                : '';
            status.textContent = `Scan complete · ${mode} signals: ${(data.signals || []).length}${skipText}`;
        }
    } catch (e) {
        if (status) status.textContent = `Index scan failed: ${e.message}`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Scan Intraday'; btn.style.opacity = '1'; }
        if (btnPos) { btnPos.disabled = false; btnPos.textContent = 'Scan Positional'; btnPos.style.opacity = '1'; }
    }
}

function renderIndexSignals(data, mode = _indexMode) {
    const niftyTable = document.getElementById('index-nifty-table');
    const sensexTable = document.getElementById('index-sensex-table');
    if (!niftyTable || !sensexTable) return;

    const signals = data.signals || [];
    const fmt = n => new Intl.NumberFormat('en-IN').format(n);
    const baseBuyRangePct = 0.03;
    const liveAnchorRangePct = 0.025;
    const maxEntryDriftPct = 0.18;
    const exitRangePct = 0.1;
    const hasNumber = value => value !== null && value !== undefined && !Number.isNaN(Number(value));

    const formatPrice = value => {
        if (!hasNumber(value)) return '-';
        return `₹${fmt(Math.round(Number(value)))}`;
    };

    const formatRange = (center, pct) => {
        if (!hasNumber(center)) return '-';
        const base = Number(center);
        const low = Math.max(0.05, base * (1 - pct));
        const high = base * (1 + pct);
        return `₹${fmt(Math.round(low))}-₹${fmt(Math.round(high))}`;
    };

    const formatExplicitRange = (low, high) => {
        if (!hasNumber(low) || !hasNumber(high)) return '-';
        const minVal = Math.min(Number(low), Number(high));
        const maxVal = Math.max(Number(low), Number(high));
        return `₹${fmt(Math.round(minVal))}-₹${fmt(Math.round(maxVal))}`;
    };

    const formatLotValue = value => {
        if (!hasNumber(value)) return '-';
        return `₹${fmt(Math.round(Number(value)))}`;
    };

    const formatExpiry = exp => {
        if (!exp) return '-';
        const today = new Date();
        const d = new Date(exp);
        const diff = Math.ceil((d - today) / (1000 * 60 * 60 * 24));
        return `${exp.slice(5)} (${diff >= 0 ? diff : 0}d)`;
    };

    const renderTable = (rows, label) => {
        if (!rows.length) {
            return `<div class="p-6 text-center text-2xs" style="color:var(--text-muted)">No ${label} setups right now</div>`;
        }
        const body = rows.map(r => {
            const click = r.signal_id ? `onclick="viewSignalDetail(${r.signal_id})"` : '';
            const strikeLabel = `${r.strike || '-'} ${r.direction || ''}`.trim();
            const liveLtp = hasNumber(r.live_option_ltp) ? Number(r.live_option_ltp) : null;
            const basePremium = hasNumber(r.premium) ? Number(r.premium) : liveLtp;
            let entryLow = hasNumber(r.entry_premium_low) ? Number(r.entry_premium_low) : (hasNumber(basePremium) ? Number(basePremium) * (1 - baseBuyRangePct) : null);
            let entryHigh = hasNumber(r.entry_premium_high) ? Number(r.entry_premium_high) : (hasNumber(basePremium) ? Number(basePremium) * (1 + baseBuyRangePct) : null);
            let entryWasRecentered = false;

            // Keep scanner's planned entry zone when still relevant.
            // If market has drifted too far away, recenter around live premium.
            if (hasNumber(liveLtp) && hasNumber(entryLow) && hasNumber(entryHigh)) {
                const entryMid = (entryLow + entryHigh) / 2;
                const driftPct = entryMid > 0 ? Math.abs(liveLtp - entryMid) / entryMid : 0;
                if (driftPct >= maxEntryDriftPct) {
                    entryLow = Number(liveLtp) * (1 - liveAnchorRangePct);
                    entryHigh = Number(liveLtp) * (1 + liveAnchorRangePct);
                    entryWasRecentered = true;
                }
            }
            const exitLow = hasNumber(r.exit_premium_low) ? Number(r.exit_premium_low) : (hasNumber(r.premium_target) ? Number(r.premium_target) * (1 - exitRangePct) : null);
            const exitHigh = hasNumber(r.exit_premium_high) ? Number(r.exit_premium_high) : (hasNumber(r.premium_target) ? Number(r.premium_target) * (1 + exitRangePct) : null);
            const buyRange = hasNumber(entryLow) && hasNumber(entryHigh)
                ? formatExplicitRange(entryLow, entryHigh)
                : formatRange(r.premium, baseBuyRangePct);
            const exitRange = hasNumber(exitLow) && hasNumber(exitHigh)
                ? formatExplicitRange(exitLow, exitHigh)
                : formatRange(r.premium_target, exitRangePct);
            const lotSize = hasNumber(r.lot_size) ? Number(r.lot_size) : null;
            const totalAmount = hasNumber(entryHigh) && hasNumber(lotSize) ? entryHigh * lotSize : null;
            const minProfit = hasNumber(exitLow) && hasNumber(entryHigh) && hasNumber(lotSize)
                ? Math.max(0, (exitLow - entryHigh) * lotSize)
                : null;
            const maxLoss = hasNumber(r.premium_sl) && hasNumber(entryHigh) && hasNumber(lotSize)
                ? Math.max(0, (entryHigh - Number(r.premium_sl)) * lotSize)
                : null;
            const isTracked = r.is_tracked || false;
            const trackStatus = r.track_status || 'NONE';
            const trackReason = r.track_status_reason || '';
            let entryStateText = '-';
            let entryStateColor = '#6b7280';
            let entryStateBg = 'rgba(107,114,128,0.1)';
            if (hasNumber(liveLtp) && hasNumber(entryLow) && hasNumber(entryHigh)) {
                if (liveLtp >= entryLow && liveLtp <= entryHigh) {
                    entryStateText = 'IN RANGE';
                    entryStateColor = '#22c55e';
                    entryStateBg = 'rgba(34,197,94,0.12)';
                } else if (liveLtp > entryHigh) {
                    entryStateText = 'WAIT FOR RANGE';
                    entryStateColor = '#f59e0b';
                    entryStateBg = 'rgba(245,158,11,0.12)';
                } else if (liveLtp < entryLow) {
                    entryStateText = 'BELOW RANGE';
                    entryStateColor = '#60a5fa';
                    entryStateBg = 'rgba(96,165,250,0.12)';
                }
            }
            if (entryWasRecentered && hasNumber(liveLtp)) {
                entryStateText = 'LIVE-ALIGNED';
                entryStateColor = '#a855f7';
                entryStateBg = 'rgba(168,85,247,0.12)';
            }
            const statusColor = trackStatus === 'HOLD' ? '#3b82f6' : trackStatus === 'EARLY-EXIT' ? '#f97316' : trackStatus === 'SL-HIT' ? '#ef4444' : trackStatus === 'TARGET-HIT' ? '#22c55e' : '#6b7280';
            const statusBg = trackStatus === 'HOLD' ? 'rgba(59,130,246,0.1)' : trackStatus === 'EARLY-EXIT' ? 'rgba(249,115,22,0.1)' : trackStatus === 'SL-HIT' ? 'rgba(239,68,68,0.1)' : trackStatus === 'TARGET-HIT' ? 'rgba(34,197,94,0.1)' : 'rgba(107,114,128,0.1)';
            return `
                <tr class="cursor-pointer" ${click}>
                    <td>${r.index || '-'}</td>
                    <td>${strikeLabel}</td>
                    <td>${formatExpiry(r.expiry)}</td>
                    <td>${hasNumber(liveLtp) ? formatPrice(liveLtp) : '-'}</td>
                    <td>${buyRange}</td>
                    <td>
                        <span class="text-2xs px-2 py-0.5 rounded font-medium" style="background:${entryStateBg};color:${entryStateColor}">${entryStateText}</span>
                    </td>
                    <td>${exitRange}</td>
                    <td>${formatPrice(r.premium_sl)}</td>
                    <td>${formatLotValue(totalAmount)}</td>
                    <td>${formatLotValue(minProfit)}</td>
                    <td>${formatLotValue(maxLoss)}</td>
                    <td>
                        ${isAdminUser()
                            ? `<button onclick="toggleTradeTracking(${r.signal_id || 0}, ${!isTracked}, ${JSON.stringify(r).replace(/"/g, '&quot;')})" 
                                class="text-2xs px-2 py-1 rounded" 
                                style="background:${isTracked ? 'rgba(34,197,94,0.1)' : 'rgba(107,114,128,0.1)'};color:${isTracked ? '#22c55e' : '#6b7280'};border:1px solid ${isTracked ? '#22c55e' : '#6b7280'}">
                            ${isTracked ? 'Tracking ✕' : 'Track'}
                        </button>`
                            : `<span class="text-2xs" style="color:var(--text-muted)">Readonly</span>`}
                    </td>
                    <td title="${trackReason}">
                        <div style="display:flex;flex-direction:column;gap:2px">
                            <span class="text-2xs px-2 py-0.5 rounded font-medium" 
                                  style="background:${statusBg};color:${statusColor}">
                                ${trackStatus === 'NONE' ? '-' : trackStatus}
                            </span>
                            ${trackReason && trackStatus !== 'NONE' ? `<span style="font-size:0.5rem;color:var(--text-muted);max-width:240px;white-space:normal;word-break:break-word;line-height:1.25">${trackReason}</span>` : ''}
                        </div>
                    </td>
                </tr>
            `;
        }).join('');

        return `
            <table class="data-table" style="font-size:0.6rem">
                <thead>
                    <tr>
                        <th>Script</th>
                        <th>Strike</th>
                        <th>Expiry</th>
                        <th>Live LTP</th>
                        <th>Premium Buy Range</th>
                        <th>Entry Zone</th>
                        <th>Premium Exit Range</th>
                        <th>Premium SL</th>
                        <th>Total Amount / Lot</th>
                        <th>Min Profit / Lot</th>
                        <th>Max Loss / Lot</th>
                        <th>Action</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>${body}</tbody>
            </table>
        `;
    };

    const isSensex = name => /sensex/i.test(name || '');
    const isNifty = name => /nifty/i.test(name || '');
    const niftyRows = signals.filter(s => isNifty(s.index));
    const sensexRows = signals.filter(s => isSensex(s.index));
    const otherRows = signals.filter(s => !isNifty(s.index) && !isSensex(s.index));

    niftyTable.innerHTML = renderTable(niftyRows.concat(otherRows), 'Nifty');
    sensexTable.innerHTML = renderTable(sensexRows, 'Sensex');
    applySortable('index-nifty-table');
    applySortable('index-sensex-table');
    switchIndexSubTab(_indexSubTab);
    
    // Start tracking if any trades are being tracked
    const hasTracked = signals.some(s => s.is_tracked);
    if (hasTracked && !_trackingTimer) {
        startTradeTracking();
    }
}

async function toggleTradeTracking(signalId, enable, signalData) {
    if (!isAdminUser()) return;
    if (!signalId) return;
    
    let payload = { enable };
    if (enable && signalData) {
        // Pass trade parameters so backend can track properly
        const r = typeof signalData === 'string' ? JSON.parse(signalData) : signalData;
        payload = {
            enable,
            entry_price: r.entry_premium_high || r.premium || null,
            target_price: r.exit_premium_high || r.premium_target || null,
            sl_price: r.premium_sl || null,
            option_security_id: r.option_security_id || null,
            expiry: r.expiry || null,
            direction: r.direction || null,
            spot: r.spot || null,
            support: r.sl_price || null,       // sl_price here is spot support level
            resistance: r.target_price || null, // target_price here is spot resistance level
            oi: r.oi || null,
            volume: r.volume || null,
        };
    }
    
    try {
        const resp = await fetch(`${API_BASE}/api/index-trading/track/${signalId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!resp.ok) throw new Error('Failed to update tracking');
        
        // Reload signals to show updated status
        loadIndexSignals(_indexMode);
        
        if (enable) {
            startTradeTracking();
        }
    } catch (e) {
        console.error('Error toggling tracking:', e);
    }
}

function startTradeTracking() {
    if (_trackingTimer) return; // Already running
    
    _trackingTimer = setInterval(async () => {
        try {
            const resp = await fetch(`${API_BASE}/api/index-trading/update-tracking`, { method: 'POST' });
            if (resp.ok) {
                loadIndexSignals(_indexMode); // Refresh to show status updates
                checkStopTracking(); // Stop if no more active trades
            }
        } catch (e) {
            console.error('Error updating tracking:', e);
        }
    }, 2 * 60 * 1000); // 2 minutes
}

function checkStopTracking() {
    // This will be called after loadIndexSignals, so we can check the current data
    // For now, we'll let the timer run - it will be lightweight if no trades are tracked
}

function stopTradeTracking() {
    if (_trackingTimer) {
        clearInterval(_trackingTimer);
        _trackingTimer = null;
    }
}

// --- Value Investing ---

async function refreshValueInvesting() {
    const btn = document.getElementById('btn-value-refresh');
    const status = document.getElementById('value-scan-status');
    if (btn) { btn.disabled = true; btn.textContent = 'Refreshing...'; btn.style.opacity = '0.7'; }
    if (status) { status.textContent = 'Fetching Tapetide fundamentals...'; status.style.color = '#6366f1'; }

    try {
        const resp = await fetch(`${API_BASE}/api/value-investing/refresh`, { method: 'POST' });
        const text = await resp.text();
        let data = {};
        try { data = JSON.parse(text); } catch (_) { }
        if (!resp.ok || data?.ok === false) throw new Error(data?.message || text || 'Refresh failed');
        if (status) {
            status.textContent = `Updated ${data.stored || 0} candidates (scanned ${data.scanned || 0})`;
            status.style.color = '#22c55e';
        }
        loadValueInvesting();
    } catch (e) {
        if (status) {
            status.textContent = `Refresh failed: ${e.message}`;
            status.style.color = '#ef4444';
        }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Refresh Fundamentals'; btn.style.opacity = '1'; }
    }
}

let _valueRows = [];
let _valueSortKey = 'score';
let _valueSortDir = 'desc';

async function loadValueInvesting() {
    const table = document.getElementById('value-table');
    const status = document.getElementById('value-scan-status');
    if (status && !status.textContent) status.textContent = 'Loading value candidates...';

    try {
        const resp = await fetch(`${API_BASE}/api/value-investing/stocks`);
        const rows = await resp.json();
        _valueRows = Array.isArray(rows) ? rows : [];
        renderValueTable(_valueRows, table);
        if (status && rows.length) {
            status.textContent = `Loaded ${rows.length} candidates`;
            status.style.color = '#22c55e';
        }
    } catch (e) {
        if (table) table.innerHTML = `<div class="p-6 text-center text-2xs" style="color:var(--text-muted)">Failed to load value candidates</div>`;
        if (status) {
            status.textContent = `Load failed: ${e.message}`;
            status.style.color = '#ef4444';
        }
    }
}

function renderValueTable(rows, table) {
    if (!table) return;
    if (!rows || !rows.length) {
        table.innerHTML = `<div class="p-6 text-center text-2xs" style="color:var(--text-muted)">No value candidates yet</div>`;
        return;
    }

    const fmt = n => n == null ? '-' : new Intl.NumberFormat('en-IN').format(Math.round(n));
    const pct = n => n != null && n !== 0 ? `${n.toFixed(1)}%` : '-';
    const num = n => n != null && n !== 0 ? n.toFixed(1) : '-';
    const sortIcon = key => _valueSortKey === key ? (_valueSortDir === 'asc' ? ' ↑' : ' ↓') : '';

    const sortedRows = [...rows].sort((a, b) => compareValueRows(a, b, _valueSortKey, _valueSortDir));

    const body = sortedRows.map(r => {
        const fv = r.fair_value || {};
        const range = fv.fair_value_low && fv.fair_value_high
            ? `₹${fv.fair_value_low} - ₹${fv.fair_value_high}`
            : '-';
        const upside = fv.upside_pct != null ? `${fv.upside_pct.toFixed(1)}%` : '-';
        const badge = fv.undervalued ? '<span class="text-2xs px-1 py-0.5 rounded" style="background:rgba(34,197,94,0.12);color:#22c55e">Undervalued</span>' : '';
        const dcf = r.mini_dcf || {};
        const dcfRange = dcf.dcf_low && dcf.dcf_high ? `₹${dcf.dcf_low} - ₹${dcf.dcf_high}` : '-';
        const dcfUpside = dcf.upside_pct != null ? `${dcf.upside_pct.toFixed(1)}%` : '-';
        const dcfBadge = dcf.undervalued ? '<span class="text-2xs px-1 py-0.5 rounded" style="background:rgba(59,130,246,0.12);color:#60a5fa">Undervalued</span>' : '';
        const ltp = r.ltp != null ? `₹${Number(r.ltp).toFixed(2)}` : '-';
        const ltpSrc = r.ltp_source ? ` <span class="text-2xs" style="color:var(--text-muted)">(${r.ltp_source})</span>` : '';
        return `
        <tr class="cursor-pointer" onclick="openValueDetail(${r.id})">
            <td class="font-medium">${r.symbol || '-'}</td>
            <td>${r.display_name || '-'}</td>
            <td>${r.sector || '-'}</td>
            <td>${r.score != null ? Math.round(r.score * 100) + '%' : '-'}</td>
            <td>${fmt(r.market_cap)}</td>
            <td>${ltp}${ltpSrc}</td>
            <td>${num(r.pe)}</td>
            <td>${num(r.roce)}</td>
            <td>${num(r.roe)}</td>
            <td>${num(r.debt_to_eq)}</td>
            <td>${pct(r.promoter_holding)}</td>
            <td>${range} ${badge}</td>
            <td>${upside}</td>
            <td>${dcfRange} ${dcfBadge}</td>
            <td>${dcfUpside}</td>
        </tr>
    `}).join('');

    table.innerHTML = `
        <table class="data-table" style="font-size:0.6rem">
            <thead>
                <tr>
                    <th onclick="sortValueTable('symbol')">Symbol${sortIcon('symbol')}</th>
                    <th onclick="sortValueTable('name')">Name${sortIcon('name')}</th>
                    <th onclick="sortValueTable('sector')">Sector${sortIcon('sector')}</th>
                    <th onclick="sortValueTable('score')">Score${sortIcon('score')}</th>
                    <th onclick="sortValueTable('market_cap')">Mcap (Cr)${sortIcon('market_cap')}</th>
                    <th onclick="sortValueTable('ltp')">LTP${sortIcon('ltp')}</th>
                    <th onclick="sortValueTable('pe')">PE${sortIcon('pe')}</th>
                    <th onclick="sortValueTable('roce')">ROCE${sortIcon('roce')}</th>
                    <th onclick="sortValueTable('roe')">ROE${sortIcon('roe')}</th>
                    <th onclick="sortValueTable('debt_to_eq')">Debt/Eq${sortIcon('debt_to_eq')}</th>
                    <th onclick="sortValueTable('promoter_holding')">Promoter${sortIcon('promoter_holding')}</th>
                    <th onclick="sortValueTable('fair_value')">Fair Value${sortIcon('fair_value')}</th>
                    <th onclick="sortValueTable('upside')">Upside${sortIcon('upside')}</th>
                    <th onclick="sortValueTable('dcf')">Mini DCF${sortIcon('dcf')}</th>
                    <th onclick="sortValueTable('dcf_upside')">DCF Upside${sortIcon('dcf_upside')}</th>
                </tr>
            </thead>
            <tbody>${body}</tbody>
        </table>
    `;
}

function sortValueTable(key) {
    if (_valueSortKey === key) {
        _valueSortDir = _valueSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        _valueSortKey = key;
        _valueSortDir = ['symbol', 'name', 'sector'].includes(key) ? 'asc' : 'desc';
    }
    const table = document.getElementById('value-table');
    renderValueTable(_valueRows, table);
}

function compareValueRows(a, b, key, dir) {
    const numVal = v => {
        if (v == null || Number.isNaN(v)) {
            return dir === 'asc' ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY;
        }
        return Number(v);
    };
    const strVal = v => (v || '').toString().toLowerCase();

    let va;
    let vb;

    switch (key) {
        case 'symbol':
            va = strVal(a.symbol);
            vb = strVal(b.symbol);
            return dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        case 'name':
            va = strVal(a.display_name);
            vb = strVal(b.display_name);
            return dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        case 'sector':
            va = strVal(a.sector);
            vb = strVal(b.sector);
            return dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        case 'fair_value': {
            const ua = a.fair_value?.undervalued ? 1 : 0;
            const ub = b.fair_value?.undervalued ? 1 : 0;
            if (ua !== ub) {
                return dir === 'asc' ? ua - ub : ub - ua;
            }
            va = numVal(a.fair_value?.fair_value_low);
            vb = numVal(b.fair_value?.fair_value_low);
            break;
        }
        case 'upside':
            va = numVal(a.fair_value?.upside_pct);
            vb = numVal(b.fair_value?.upside_pct);
            break;
        case 'dcf':
            va = numVal(a.mini_dcf?.dcf_low);
            vb = numVal(b.mini_dcf?.dcf_low);
            break;
        case 'dcf_upside':
            va = numVal(a.mini_dcf?.upside_pct);
            vb = numVal(b.mini_dcf?.upside_pct);
            break;
        default:
            va = numVal(a[key]);
            vb = numVal(b[key]);
    }

    return dir === 'asc' ? va - vb : vb - va;
}

async function openValueDetail(candidateId) {
    const panel = document.getElementById('value-detail-panel');
    const content = document.getElementById('value-detail-content');
    const loading = document.getElementById('value-detail-loading');
    const errorBox = document.getElementById('value-detail-error');
    const errorMsg = document.getElementById('value-detail-error-msg');

    if (panel) panel.classList.remove('hidden');
    if (content) content.classList.add('hidden');
    if (errorBox) errorBox.classList.add('hidden');
    if (loading) loading.classList.remove('hidden');

    try {
        const resp = await fetch(`${API_BASE}/api/value-investing/stocks/${candidateId}`);
        const text = await resp.text();
        let data = {};
        try { data = JSON.parse(text); } catch (_) { }
        if (!resp.ok) throw new Error(data?.detail || data?.message || text || 'Failed to load details');
        renderValueDetail(data);
    } catch (e) {
        if (errorMsg) errorMsg.textContent = `Failed to load: ${e.message}`;
        if (errorBox) errorBox.classList.remove('hidden');
    } finally {
        if (loading) loading.classList.add('hidden');
    }
}

function closeValueDetail() {
    const panel = document.getElementById('value-detail-panel');
    if (panel) panel.classList.add('hidden');
}

function renderValueDetail(data) {
    const content = document.getElementById('value-detail-content');
    const title = document.getElementById('value-detail-title');
    if (!content) return;

    const c = data.candidate || {};
    const flags = data.moat_flags || [];
    const reasons = data.undervalued_reasons || [];
    const tap = data.sources?.tapetide || {};
    const profile = tap.profile || {};
    const fundamentals = profile.fundamentals || {};
    const growth = profile.growth_metrics || {};
    const ratios = tap.ratios || {};
    const pl = tap.profit_loss || {};
    const fair = data.fair_value || {};
    const dcf = data.mini_dcf || {};
    const timeFair = data.time_to_fair || {};
    const ltpLabel = c.ltp != null ? `₹${Number(c.ltp).toFixed(2)}` : '-';
    const ltpSource = c.ltp_source ? ` (${c.ltp_source})` : '';

    if (title) title.textContent = `${c.symbol || ''} • Value Insight`;

    const metric = (label, value, suffix = '') => `
        <div class="flex items-center justify-between text-2xs py-1" style="border-bottom:1px solid var(--glass-border)">
            <span style="color:var(--text-muted)">${label}</span>
            <span style="color:var(--text-heading)">${value != null && value !== '' ? value : '-'}${suffix}</span>
        </div>`;

    const fmt = n => n != null ? new Intl.NumberFormat('en-IN').format(Math.round(n)) : '-';
    const pct = n => n != null ? `${Number(n).toFixed(1)}%` : '-';
    const num = n => n != null ? Number(n).toFixed(2) : '-';

    const moatHtml = flags.length
        ? flags.map(f => `<span class="text-2xs px-1.5 py-0.5 rounded-full" style="background:rgba(34,197,94,0.12);color:#22c55e">${f}</span>`).join(' ')
        : `<span class="text-2xs" style="color:var(--text-muted)">No moat flags yet</span>`;

    const ratioMetrics = ratios.metrics || {};
    const plMetrics = pl.metrics || {};

    content.innerHTML = `
        <div class="mb-3">
            <div class="flex flex-wrap gap-2 mb-2">${moatHtml}</div>
            <div class="grid grid-cols-2 gap-2 text-2xs">
                <div class="glass-card-sm p-2">
                    <div class="text-2xs font-semibold mb-1" style="color:var(--text-heading)">Score Breakdown</div>
                    ${metric('Overall', c.score != null ? Math.round(c.score * 100) + '%' : '-') }
                    ${metric('Valuation', c.valuation_score != null ? Math.round(c.valuation_score * 100) + '%' : '-') }
                    ${metric('Quality', c.quality_score != null ? Math.round(c.quality_score * 100) + '%' : '-') }
                    ${metric('Growth', c.growth_score != null ? Math.round(c.growth_score * 100) + '%' : '-') }
                    ${metric('Ownership', c.ownership_score != null ? Math.round(c.ownership_score * 100) + '%' : '-') }
                </div>
                <div class="glass-card-sm p-2">
                    <div class="text-2xs font-semibold mb-1" style="color:var(--text-heading)">Key Metrics</div>
                    ${metric('Market Cap (Cr)', fmt(c.market_cap))}
                    ${metric('LTP', `${ltpLabel}${ltpSource}`)}
                    ${metric('PE / PB', `${num(c.pe)} / ${num(c.pb)}`)}
                    ${metric('ROCE / ROE', `${num(c.roce)}% / ${num(c.roe)}%`)}
                    ${metric('Debt/Equity', num(c.debt_to_eq))}
                    ${metric('Operating Margin', num(c.operating_margin) + '%')}
                    ${metric('Revenue Growth', num(c.revenue_growth) + '%')}
                    ${metric('EPS Growth', num(c.eps_growth) + '%')}
                    ${metric('Promoter Holding', pct(c.promoter_holding))}
                    ${metric('Fair Value Range', fair.fair_value_low && fair.fair_value_high ? `₹${fair.fair_value_low} - ₹${fair.fair_value_high}` : '-')}
                    ${metric('Upside', fair.upside_pct != null ? fair.upside_pct.toFixed(1) + '%' : '-')}
                    ${metric('Mini DCF Range', dcf.dcf_low && dcf.dcf_high ? `₹${dcf.dcf_low} - ₹${dcf.dcf_high}` : '-')}
                    ${metric('DCF Upside', dcf.upside_pct != null ? dcf.upside_pct.toFixed(1) + '%' : '-')}
                </div>
            </div>
        </div>

        <div class="glass-card-sm p-2 mb-3">
            <div class="text-2xs font-semibold mb-1" style="color:var(--text-heading)">Valuation Formula</div>
            <div class="text-2xs" style="color:var(--text-muted)">
                <div style="margin-bottom:6px">
                    <strong>Fair Value (PE Range)</strong><br/>
                    EPS = LTP ÷ PE (when EPS not available)<br/>
                    Fair Value Low = EPS × PE Low<br/>
                    Fair Value High = EPS × PE High<br/>
                    ${fair.pe_low && fair.pe_high ? `PE Band = ${fair.pe_low} – ${fair.pe_high}` : ''}<br/>
                    ${c.ltp != null && c.pe != null ? `LTP ₹${Number(c.ltp).toFixed(2)} / PE ${Number(c.pe).toFixed(2)} → EPS ${c.ltp && c.pe ? (Number(c.ltp) / Number(c.pe)).toFixed(2) : '-'}` : ''}
                </div>
                <div>
                    <strong>Mini DCF</strong><br/>
                    EPS grows for 5 years (growth band ${dcf.growth_low || '-'}%–${dcf.growth_high || '-'}%)<br/>
                    Discount rate = 12%<br/>
                    Terminal value = Year‑5 EPS × 12 (terminal multiple)<br/>
                    ${dcf.dcf_low && dcf.dcf_high ? `DCF Range = ₹${dcf.dcf_low} – ₹${dcf.dcf_high}` : ''}
                </div>
            </div>
        </div>

        <div class="glass-card-sm p-2 mb-3">
            <div class="text-2xs font-semibold mb-1" style="color:var(--text-heading)">Estimated Time to Fair Value</div>
            <div class="text-2xs" style="color:var(--text-muted)">
                Best probable: <strong>${timeFair.best_years != null ? `${timeFair.best_years.toFixed(1)} yrs` : '-'}</strong><br/>
                ${timeFair.estimates ? `EPS growth: ${timeFair.estimates.eps_growth ?? '-'} yrs · Fixed: ${timeFair.estimates.fixed ?? '-'} yrs · DCF mid: ${timeFair.estimates.dcf_mid ?? '-'} yrs` : ''}
            </div>
        </div>

        <div class="glass-card-sm p-2 mb-3">
            <div class="text-2xs font-semibold mb-1" style="color:var(--text-heading)">Tapetide Fundamentals</div>
            <div class="text-2xs mb-2" style="color:var(--text-muted)">${profile.company?.about || 'No company summary available.'}</div>
            <div class="grid grid-cols-2 gap-2 text-2xs">
                <div>
                    ${metric('ROCE', num(fundamentals.roce) + '%')}
                    ${metric('ROE', num(fundamentals.roe) + '%')}
                    ${metric('Debt/Equity', num(fundamentals.debt_to_equity))}
                    ${metric('Dividend Yield', num(fundamentals.dividend_yield) + '%')}
                </div>
                <div>
                    ${metric('Revenue (Yr)', fmt(fundamentals.yearly_revenue))}
                    ${metric('Net Income', fmt(fundamentals.net_income))}
                    ${metric('Revenue Growth 1Y', num(fundamentals.revenue_growth_1y) + '%')}
                    ${metric('YoY Profit Growth', num(fundamentals.yoy_quarterly_profit_growth) + '%')}
                </div>
            </div>
            <div class="text-2xs mt-2" style="color:var(--text-muted)">Latest ratios (${ratios.latest_period || 'n/a'})</div>
            <div class="grid grid-cols-2 gap-2 text-2xs">
                <div>${metric('ROCE %', num(ratioMetrics['ROCE %']))}</div>
                <div>${metric('ROE %', num(ratioMetrics['ROE %']))}</div>
                <div>${metric('OPM %', num(ratioMetrics['OPM %']))}</div>
                <div>${metric('Net Profit %', num(ratioMetrics['Net Profit %']))}</div>
            </div>
        </div>

        <div class="glass-card-sm p-2 mb-3">
            <div class="text-2xs font-semibold mb-1" style="color:var(--text-heading)">Why It Looks Undervalued</div>
            <ul class="text-2xs" style="color:var(--text-muted);padding-left:14px;list-style:disc">
                ${(reasons.length ? reasons : ['No specific undervaluation cues available']).map(r => `<li>${r}</li>`).join('')}
            </ul>
        </div>

        <div class="glass-card-sm p-2 mb-3">
            <div class="text-2xs font-semibold mb-1" style="color:var(--text-heading)">Multi-source Signals</div>
            <div class="text-2xs" style="color:var(--text-muted)">Tickertape scorecard + Moneycontrol mini statements</div>
            <div class="grid grid-cols-2 gap-2 text-2xs mt-2">
                <div>
                    <div class="text-2xs font-semibold mb-1" style="color:var(--text-heading)">Tickertape</div>
                    <div style="color:var(--text-muted)">${tapetideSectionSummary(data.sources?.tickertape)}</div>
                </div>
                <div>
                    <div class="text-2xs font-semibold mb-1" style="color:var(--text-heading)">Moneycontrol</div>
                    <div style="color:var(--text-muted)">${tapetideSectionSummary(data.sources?.moneycontrol)}</div>
                </div>
            </div>
        </div>
    `;

    content.classList.remove('hidden');
}

function tapetideSectionSummary(section) {
    if (!section || section.error) return 'Not available';
    if (section.scorecard) return `${section.scorecard.length} scorecard rows`;
    if (section.overview || section.ratios) return 'Overview + ratios loaded';
    return 'Available';
}

async function loadSectorOptions() {
    try {
        const response = await fetch(`${API_BASE}/api/settings/sectors/all`);
        const sectors = await response.json();
        
        const ceSelect = document.getElementById('fno-ce-sectors');
        const peSelect = document.getElementById('fno-pe-sectors');
        
        // Clear existing options
        ceSelect.innerHTML = '';
        peSelect.innerHTML = '';
        
        // Add sector options sorted by score
        sectors.forEach(sector => {
            const option = document.createElement('option');
            option.value = sector.sector_name;
            option.textContent = `${sector.sector_name} (${sector.combined_score}%)`;
            
            // Add to both selects
            ceSelect.appendChild(option.cloneNode(true));
            peSelect.appendChild(option);
        });
        
    } catch (err) {
        console.error('Error loading sector options:', err);
    }
}

function getSelectedCustomSectors() {
    const ceSelect = document.getElementById('fno-ce-sectors');
    const peSelect = document.getElementById('fno-pe-sectors');
    
    const ceSectors = Array.from(ceSelect.selectedOptions).map(opt => opt.value);
    const peSectors = Array.from(peSelect.selectedOptions).map(opt => opt.value);
    
    return { ceSectors, peSectors };
}

function groupSignals(signals) {
    const bySecId = {};
    for (const s of signals) {
        if (!bySecId[s.security_id]) bySecId[s.security_id] = [];
        bySecId[s.security_id].push(s);
    }
    const groups = [];
    for (const secId of Object.keys(bySecId)) {
        const all = bySecId[secId].sort((a, b) => b.id - a.id);
        const latest = all[0];
        groups.push({ ...latest, _history: all.length > 1 ? all.slice(1) : null, _totalOccurrences: all.length });
    }
    groups.sort((a, b) => b.current_score - a.current_score);
    return groups;
}

async function loadFnoSignals() {
    const dirFilters = getSelectedPills('fno-dir-filter');
    const statusFilters = getSelectedPills('fno-status-filter');
    const scoreFilters = getSelectedPills('fno-score-filter');
    const minScore = scoreFilters.length ? Math.max(...scoreFilters.map(Number)) / 100 : 0;

    try {
        const response = await fetch(`${API_BASE}/api/signals/?section=fno`);
        let signals = await response.json();

        if (statusFilters.length) {
            signals = signals.filter(s => statusFilters.includes(s.status));
        }
        if (minScore > 0) {
            signals = signals.filter(s => s.current_score >= minScore);
        }

        const showCE = !dirFilters.length || dirFilters.includes('CE');
        const showPE = !dirFilters.length || dirFilters.includes('PE');

        const advSignals = signals.filter(s => isAdvDeclSignal(s.signal_type));
        const coreSignals = signals.filter(s => !isAdvDeclSignal(s.signal_type));

        const ceSignals = showCE ? coreSignals.filter(s => getSignalDirection(s.signal_type) === 'CE') : [];
        const peSignals = showPE ? coreSignals.filter(s => getSignalDirection(s.signal_type) === 'PE') : [];
        const manualCE = showCE ? coreSignals.filter(s => s.signal_type === 'manual') : [];
        const otherSignals = !dirFilters.length
            ? coreSignals.filter(s => !['CE', 'PE', 'manual'].includes(getSignalDirection(s.signal_type)))
            : [];

        const advCe = showCE ? advSignals.filter(s => getSignalDirection(s.signal_type) === 'CE') : [];
        const advPe = showPE ? advSignals.filter(s => getSignalDirection(s.signal_type) === 'PE') : [];

        const allCe = groupSignals([...ceSignals, ...manualCE, ...otherSignals]);
        const allPe = groupSignals([...peSignals]);
        const allAdvCe = groupSignals([...advCe]);
        const allAdvPe = groupSignals([...advPe]);

        const secIds = [...new Set(signals.map(s => s.security_id))];
        let cmpMap = {};
        if (secIds.length) {
            try {
                const cmpRes = await fetch(`${API_BASE}/api/signals/cmp`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({security_ids: secIds}),
                });
                cmpMap = await cmpRes.json();
            } catch (e) { }
        }

        renderFnoTable('fno-ce-table', allCe, cmpMap, 'CE');
        renderFnoTable('fno-pe-table', allPe, cmpMap, 'PE');
        renderFnoTable('fno-ad-ce-table', allAdvCe, cmpMap, 'CE', 'No CE Adv/Decl setups found');
        renderFnoTable('fno-ad-pe-table', allAdvPe, cmpMap, 'PE', 'No PE Adv/Decl setups found');
    } catch (err) {
        console.error('Error loading F&O signals:', err);
    }
}

function _fnoStatusBadge(s) {
    if (s.status === 'invalidated')
        return `<span class="text-2xs px-1 py-0.5 rounded" style="background:rgba(239,68,68,0.12);color:#ef4444" title="${s.invalidation_reason || ''}">✖</span>`;
    if (s.status === 'dismissed')
        return `<span class="text-2xs px-1 py-0.5 rounded" style="background:rgba(148,163,184,0.1);color:var(--text-muted)">dismissed</span>`;
    return '';
}

function _fnoActionBtns(s) {
    const isTracking = s.status === 'tracking';
    let btns = '';
    if (s.status === 'active' || s.status === 'tracking') {
        btns += `<button onclick="event.stopPropagation(); toggleTrack(${s.id})" class="text-2xs ${isTracking ? 'text-brand-500 font-bold' : 'hover:text-brand-500'}" style="${isTracking ? '' : 'color:var(--text-muted)'}" title="${isTracking ? 'Untrack' : 'Track'}">${isTracking ? '★' : '☆'}</button>`;
        btns += `<button onclick="event.stopPropagation(); dismissSignal(${s.id})" class="text-2xs hover:text-red-400 ml-1" style="color:var(--text-muted)" title="Dismiss">✕</button>`;
    }
    if (s.status === 'dismissed' || s.status === 'invalidated')
        btns += `<button onclick="event.stopPropagation(); reactivateSignal(${s.id})" class="text-2xs text-brand-500 hover:text-brand-400 font-medium" title="Reactivate">↩</button>`;
    return btns;
}

function toggleHistoryRows(secId) {
    const rows = document.querySelectorAll(`.history-row[data-parent="${secId}"]`);
    const chevron = document.querySelector(`.expand-chevron[data-sec="${secId}"]`);
    const visible = rows[0] && !rows[0].classList.contains('hidden');
    rows.forEach(r => r.classList.toggle('hidden', visible));
    if (chevron) chevron.classList.toggle('expanded', !visible);
}

function renderFnoTable(containerId, groups, cmpMap, direction, emptyMessage) {
    const emptyMsg = emptyMessage || (direction === 'CE' ? 'No CE setups found' : 'No PE setups found');

    if (!groups.length) {
        document.getElementById(containerId).innerHTML = `<div class="p-4 text-center text-2xs" style="color:var(--text-muted)">${emptyMsg}</div>`;
        return;
    }

    const rows = groups.map(s => {
        const scoreClass = s.current_score >= 0.7 ? 'score-high' :
                           s.current_score >= 0.5 ? 'score-medium' : 'score-low';
        const cmp = cmpMap[s.security_id];
        const cmpDisplay = cmp ? cmp.toFixed(2) : '-';
        const pctChange = (cmp && s.close_price_at_detection)
            ? ((cmp - s.close_price_at_detection) / s.close_price_at_detection * 100).toFixed(1)
            : null;
        const pctClass = pctChange > 0 ? 'text-profit' : pctChange < 0 ? 'text-loss' : '';
        const pctDisplay = pctChange !== null ? `<span class="${pctClass}" style="font-size:0.45rem">${pctChange > 0 ? '+' : ''}${pctChange}%</span>` : '';
        const isTracking = s.status === 'tracking';

        const typeBadge = s.signal_type === 'manual'
            ? `<span class="text-2xs px-1 py-0.5 rounded" style="background:var(--badge-manual-bg);color:var(--text-muted)">manual</span>`
            : isAdvDeclSignal(s.signal_type)
                ? `<span class="text-2xs px-1 py-0.5 rounded" style="background:rgba(14,116,144,0.15);color:#67e8f9">ADV/DECL</span>`
                : '';

        const hasHistory = s._history && s._history.length > 0;
        const occBadge = s._totalOccurrences > 1
            ? `<span class="occurrence-${Math.min(s._totalOccurrences, 3)} text-2xs px-1 py-0.5 rounded-full ml-1">${ordinal(s._totalOccurrences)}</span>`
            : '';
        const expandBtn = hasHistory
            ? `<span class="expand-chevron" data-sec="${s.security_id}" onclick="event.stopPropagation(); toggleHistoryRows('${s.security_id}')" title="${s._history.length} previous occurrence(s)">▶</span>`
            : '';

        const tipData = JSON.stringify({
            symbol: s.symbol, signal_type: s.signal_type, current_score: s.current_score,
            peak_score: s.peak_score, trend_score: s.trend_score, consol_score: s.consol_score,
            breakout_score: s.breakout_score, first_detected_date: s.first_detected_date,
        }).replace(/"/g, '&quot;');

        let html = `<tr class="cursor-pointer fno-primary-row" style="${isTracking ? 'background:var(--tracking-bg)' : ''}" onclick="viewSignalDetail(${s.id})">
            <td class="font-medium" style="color:var(--text-primary)">${expandBtn}${s.symbol}${occBadge} ${typeBadge} ${_fnoStatusBadge(s)}</td>
            <td style="color:var(--text-secondary)">${s.sector || '-'}</td>
            <td><span class="${scoreClass} text-2xs px-1 py-0.5 rounded-full score-tooltip-trigger"
                onmouseenter="showFnoScoreTooltip(event, ${tipData})"
                onmouseleave="hideScoreTooltip()"
                >${(s.current_score * 100).toFixed(0)}%</span></td>
            <td>${s.close_price_at_detection ? s.close_price_at_detection.toFixed(2) : '-'}</td>
            <td>${cmpDisplay} ${pctDisplay}</td>
            <td>${daysSince(s.first_detected_date)}d</td>
            <td>${_fnoActionBtns(s)}</td>
        </tr>`;

        if (hasHistory) {
            for (const h of s._history) {
                const hScore = h.current_score >= 0.7 ? 'score-high' : h.current_score >= 0.5 ? 'score-medium' : 'score-low';
                const hPct = (cmp && h.close_price_at_detection)
                    ? ((cmp - h.close_price_at_detection) / h.close_price_at_detection * 100).toFixed(1)
                    : null;
                const hPctClass = hPct > 0 ? 'text-profit' : hPct < 0 ? 'text-loss' : '';
                const hPctDisplay = hPct !== null ? `<span class="${hPctClass}" style="font-size:0.45rem">${hPct > 0 ? '+' : ''}${hPct}%</span>` : '';
                const hTipData = JSON.stringify({
                    symbol: h.symbol, signal_type: h.signal_type, current_score: h.current_score,
                    peak_score: h.peak_score, trend_score: h.trend_score, consol_score: h.consol_score,
                    breakout_score: h.breakout_score, first_detected_date: h.first_detected_date,
                }).replace(/"/g, '&quot;');

                html += `<tr class="history-row hidden" data-parent="${s.security_id}" onclick="viewSignalDetail(${h.id})">
                    <td style="padding-left:24px;color:var(--text-muted);font-size:0.5rem">
                        <span style="color:var(--text-muted);margin-right:3px">↳</span>${ordinal(h.occurrence_number)} ${_fnoStatusBadge(h)}
                    </td>
                    <td></td>
                    <td><span class="${hScore} text-2xs px-1 py-0.5 rounded-full score-tooltip-trigger"
                        onmouseenter="showFnoScoreTooltip(event, ${hTipData})"
                        onmouseleave="hideScoreTooltip()"
                        >${(h.current_score * 100).toFixed(0)}%</span></td>
                    <td style="font-size:0.5rem">${h.close_price_at_detection ? h.close_price_at_detection.toFixed(2) : '-'}</td>
                    <td style="font-size:0.5rem">${cmpDisplay} ${hPctDisplay}</td>
                    <td style="font-size:0.5rem">${daysSince(h.first_detected_date)}d</td>
                    <td>${_fnoActionBtns(h)}</td>
                </tr>`;
            }
        }
        return html;
    }).join('');

    document.getElementById(containerId).innerHTML = `
        <table class="data-table">
            <thead><tr>
                <th>Symbol</th><th>Sector</th><th>Score</th>
                <th>Entry Price</th><th>CMP</th><th>Days</th><th></th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
    applySortable(containerId);
}

async function toggleTrack(signalId) {
    try {
        await fetch(`${API_BASE}/api/signals/${signalId}/track`, { method: 'POST' });
        loadFnoSignals();
    } catch (e) {
        console.error('Track toggle failed:', e);
    }
}

async function runFnoScan() {
    return runFnoScanCore();
}

async function runFnoScanCore() {
    const btn = document.getElementById('btn-fno-scan-core');
    const statusEl = document.getElementById('fno-scan-status');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Scanning...';
    statusEl.textContent = 'Revalidating existing signals, then discovering new setups... This may take a few minutes.';
    statusEl.style.color = '#6366f1';

    try {
        let url = `${API_BASE}/api/settings/scan/run?section=fno&setup=core`;
        let requestOptions = { method: 'POST' };
        
        // If custom mode, send custom sectors
        if (fnoScanMode === 'custom') {
            const { ceSectors, peSectors } = getSelectedCustomSectors();
            
            if (ceSectors.length === 0 && peSectors.length === 0) {
                statusEl.textContent = 'Please select at least one sector for CE or PE';
                statusEl.style.color = '#ef4444';
                btn.disabled = false;
                btn.innerHTML = 'Scan F&O';
                return;
            }
            
            url = `${API_BASE}/api/settings/scan/run/custom`;
            requestOptions = {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    section: 'fno',
                    ce_sectors: ceSectors,
                    pe_sectors: peSectors
                })
            };
            
            statusEl.textContent = `Custom scan: CE sectors: ${ceSectors.join(', ')} | PE sectors: ${peSectors.join(', ')}`;
        }
        
        const response = await fetch(url, requestOptions);
        
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP ${response.status}: ${errorText}`);
        }
        
        const responseText = await response.text();
        let data;
        try {
            data = JSON.parse(responseText);
        } catch (jsonError) {
            console.error('Failed to parse JSON response:', responseText);
            throw new Error(`Invalid JSON response: ${responseText.substring(0, 100)}...`);
        }
        const s = data.summary || {};
        const rv = s.revalidation || {};
        let parts = [];
        if (rv.revalidated) {
            parts.push(`Revalidated: ${rv.revalidated}`);
            if (rv.invalidated) parts.push(`Broken: ${rv.invalidated}`);
            if (rv.updated) parts.push(`Updated: ${rv.updated}`);
        }
        if (s.scanned) {
            parts.push(`New scan: ${s.scanned} stocks`);
            parts.push(`CE: ${s.ce_signals || 0}`);
            parts.push(`PE: ${s.pe_signals || 0}`);
        }
        const msg = parts.length ? parts.join(' | ') : (data.message || 'Scan complete');
        statusEl.textContent = msg;
        statusEl.style.color = '#22c55e';
        loadFnoSignals();
    } catch (err) {
        statusEl.textContent = `Scan failed: ${err.message}`;
        statusEl.style.color = '#ef4444';
    } finally {
        btn.disabled = false;
        btn.innerHTML = 'Scan Core';
    }
}

async function runFnoScanAdvDecl() {
    const btn = document.getElementById('btn-fno-scan-adv');
    const statusEl = document.getElementById('fno-advdecl-status');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Scanning...';
    if (statusEl) {
        statusEl.textContent = 'Computing breadth + momentum filters...';
        statusEl.style.color = '#6366f1';
    }

    try {
        const url = `${API_BASE}/api/settings/scan/run?section=fno&setup=advdecl`;
        const response = await fetch(url, { method: 'POST' });
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP ${response.status}: ${errorText}`);
        }
        const responseText = await response.text();
        let data;
        try {
            data = JSON.parse(responseText);
        } catch (jsonError) {
            console.error('Failed to parse JSON response:', responseText);
            throw new Error(`Invalid JSON response: ${responseText.substring(0, 100)}...`);
        }

        const adv = data.summary?.advdecl_intraday || {};
        const ratio = adv.ad_ratio != null ? adv.ad_ratio : '-';
        const bias = adv.bias || 'neutral';
        const msg = `A/D ${ratio} (${adv.advancers || 0} / ${adv.decliners || 0}) • Bias ${bias.toUpperCase()} • Signals ${adv.signals || 0} • Scanned ${adv.scanned || 0}`;
        if (statusEl) {
            statusEl.textContent = msg;
            statusEl.style.color = adv.signals ? '#22c55e' : '#eab308';
        }
        loadFnoSignals();
    } catch (err) {
        if (statusEl) {
            statusEl.textContent = `Scan failed: ${err.message}`;
            statusEl.style.color = '#ef4444';
        }
    } finally {
        btn.disabled = false;
        btn.innerHTML = 'Scan Adv/Decl';
    }
}

let _schedulerPollTimer = null;
let _lastKnownScanTime = null;

async function loadSchedulerStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/settings/scheduler/status`);
        const data = await res.json();
        const el = document.getElementById('fno-scheduler-status');
        if (!el) return;

        if (!data.running) {
            el.innerHTML = `<span style="width:6px;height:6px;border-radius:50%;background:#6b7280;display:inline-block"></span>
                <span style="color:var(--text-muted)">Auto-scan paused</span>
                <button onclick="toggleScheduler(true)" class="text-brand-500 hover:underline ml-1">Resume</button>`;
            if (_schedulerPollTimer) clearTimeout(_schedulerPollTimer);
            _schedulerPollTimer = setTimeout(loadSchedulerStatus, 15000);
            return;
        }

        const jobs = data.jobs || [];
        let nextRun = null;
        for (const j of jobs) {
            if (j.next_run) {
                const t = new Date(j.next_run);
                if (!nextRun || t < nextRun) nextRun = t;
            }
        }

        const nextStr = nextRun ? nextRun.toLocaleTimeString('en-IN', {hour: '2-digit', minute: '2-digit', hour12: true}) : '-';
        const lastStr = data.last_scan_time
            ? new Date(data.last_scan_time).toLocaleTimeString('en-IN', {hour: '2-digit', minute: '2-digit', hour12: true})
            : '-';

        let lastResult = '';
        if (data.last_scan_result && !data.last_scan_result.error) {
            const rv = data.last_scan_result.revalidation;
            if (rv && rv.invalidated) lastResult = ` (${rv.invalidated} invalidated)`;
        }

        el.innerHTML = `<span style="width:6px;height:6px;border-radius:50%;background:#22c55e;display:inline-block;animation:pulse 2s infinite"></span>
            <span>Auto-scan on &middot; Next: ${nextStr} &middot; Last: ${lastStr}${lastResult}</span>
            <button onclick="toggleScheduler(false)" style="color:var(--text-muted)" class="hover:text-red-400 ml-1" title="Pause auto-scan">\u23f8</button>`;

        if (data.last_scan_time && data.last_scan_time !== _lastKnownScanTime) {
            if (_lastKnownScanTime !== null) {
                const activeTab = document.querySelector('.nav-item.active')?.id?.replace('tab-', '');
                if (activeTab === 'fno') loadFnoSignals();
            }
            _lastKnownScanTime = data.last_scan_time;
        }

    } catch (e) {
        console.error('Scheduler status error:', e);
    }

    if (_schedulerPollTimer) clearTimeout(_schedulerPollTimer);
    _schedulerPollTimer = setTimeout(loadSchedulerStatus, 60000);
}

async function toggleScheduler(resume) {
    try {
        const endpoint = resume ? 'resume' : 'pause';
        await fetch(`${API_BASE}/api/settings/scheduler/${endpoint}`, { method: 'POST' });
        loadSchedulerStatus();
    } catch (e) {
        console.error('Scheduler toggle error:', e);
    }
}

function openFnoAddModal() {
    document.getElementById('fno-add-modal').classList.remove('hidden');
    document.getElementById('fno-add-search').value = '';
    document.getElementById('fno-add-results').innerHTML = '';
    document.getElementById('fno-add-search').focus();
}

function closeFnoAddModal() {
    document.getElementById('fno-add-modal').classList.add('hidden');
}

let fnoSearchTimeout;
function debounceSearchFno(query) {
    clearTimeout(fnoSearchTimeout);
    fnoSearchTimeout = setTimeout(() => searchFnoInstruments(query), 300);
}

async function searchFnoInstruments(query) {
    if (!query || query.length < 2) {
        document.getElementById('fno-add-results').innerHTML = '';
        return;
    }
    try {
        const response = await fetch(`${API_BASE}/api/settings/instruments/search?q=${encodeURIComponent(query)}`);
        const results = await response.json();
        const fnoResults = results.filter(r => r.is_fno);

        if (!fnoResults.length) {
            document.getElementById('fno-add-results').innerHTML = `<p class="text-2xs py-2" style="color:var(--text-muted)">No F&O stocks found.</p>`;
            return;
        }

        const rows = fnoResults.map(r => `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 4px;border-bottom:1px solid var(--border-light);cursor:pointer;border-radius:4px"
                 onmouseover="this.style.background='var(--bg-surface-hover)'" onmouseout="this.style.background=''"
                 onclick="addFnoStock('${r.security_id}', '${r.symbol}')">
                <div>
                    <span class="text-xs font-medium" style="color:var(--text-primary)">${r.symbol}</span>
                    <span class="text-2xs ml-1" style="color:var(--text-muted)">${r.display_name || ''}</span>
                </div>
                <span class="text-2xs text-brand-500 font-medium">+ Add</span>
            </div>`).join('');

        document.getElementById('fno-add-results').innerHTML = rows;
    } catch (err) {
        console.error('F&O search error:', err);
    }
}

async function addFnoStock(securityId, symbol) {
    const resultsEl = document.getElementById('fno-add-results');
    resultsEl.innerHTML = `<div class="py-2 text-center"><span class="spinner"></span> <span class="text-2xs" style="color:var(--text-muted)">Analyzing...</span></div>`;

    try {
        const response = await fetch(`${API_BASE}/api/signals/manual-add`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({security_id: securityId, symbol: symbol}),
        });
        const data = await response.json();

        closeFnoAddModal();
        loadFnoSignals();

        const statusEl = document.getElementById('fno-scan-status');
        const analysis = data.analysis;
        if (analysis) {
            const dir = analysis.direction || '?';
            const score = (analysis.score * 100).toFixed(0);
            statusEl.textContent = `Added ${symbol}: ${dir} setup, Score ${score}%`;
            statusEl.style.color = '#22c55e';
        } else {
            statusEl.textContent = `Added ${symbol} (manual, no clear directional setup detected)`;
            statusEl.style.color = '#eab308';
        }
    } catch (err) {
        resultsEl.innerHTML = `<p class="text-2xs text-red-400 py-2">Failed to add: ${err.message}</p>`;
    }
}

// --- Trades ---

async function loadTrades() {
    try {
        const [tradesRes, statsRes] = await Promise.all([
            fetch(`${API_BASE}/api/trades/`),
            fetch(`${API_BASE}/api/trades/stats`),
        ]);
        const trades = await tradesRes.json();
        const stats = await statsRes.json();

        document.getElementById('stat-total').textContent = stats.total_trades;
        document.getElementById('stat-winrate').textContent = `${stats.win_rate}%`;

        const pnlEl = document.getElementById('stat-pnl');
        pnlEl.textContent = formatCurrency(stats.total_pnl);
        pnlEl.className = `text-xl font-bold mt-0.5 ${stats.total_pnl >= 0 ? 'text-profit' : 'text-loss'}`;

        document.getElementById('stat-best').textContent = stats.best_trade != null ? formatCurrency(stats.best_trade) : '--';
        document.getElementById('stat-worst').textContent = stats.worst_trade != null ? formatCurrency(stats.worst_trade) : '--';

        if (!trades.length) {
            document.getElementById('trades-table').innerHTML = `
                <div class="p-10 text-center" style="color:var(--text-muted)">
                    <p class="text-sm mb-1">No trades logged yet</p>
                    <p class="text-xs">Click "+ New Trade" to log your first trade.</p>
                </div>`;
            return;
        }

        const statusColor = s => s === 'open' ? '#4ade80' : s === 'cancelled' ? '#a78bfa' : s === 'sl_hit' ? '#f87171' : s === 'target_hit' ? '#22c55e' : '#94a3b8';
        const statusBg = s => s === 'open' ? 'rgba(34,197,94,0.1)' : s === 'cancelled' ? 'rgba(168,85,247,0.1)' : s === 'sl_hit' ? 'rgba(239,68,68,0.1)' : s === 'target_hit' ? 'rgba(34,197,94,0.1)' : 'rgba(148,163,184,0.1)';

        const rows = trades.map(t => {
            const pnlClass = t.pnl > 0 ? 'text-profit' : t.pnl < 0 ? 'text-loss' : '';
            const strikeInfo = t.trade_type === 'option' ? `${t.strike_price || ''} ${t.option_type || ''}` : '';
            const isOpen = t.status === 'open';
            return `<tr id="trade-row-${t.id}">
                <td class="font-medium" style="color:var(--text-primary)">
                    ${t.symbol}
                    ${strikeInfo ? `<div style="font-size:0.55rem;color:var(--text-secondary)">${strikeInfo} · ${t.expiry_date || ''}</div>` : ''}
                </td>
                <td><span class="text-2xs px-1.5 py-0.5 rounded-full" style="background:${t.trade_type === 'option' ? 'rgba(168,85,247,0.12)' : 'rgba(59,130,246,0.12)'};color:${t.trade_type === 'option' ? '#a78bfa' : '#60a5fa'}">${t.trade_type}</span></td>
                <td class="trade-editable" data-trade-id="${t.id}" data-field="entry_price" data-type="number">${t.entry_price || '-'}</td>
                <td class="trade-editable" data-trade-id="${t.id}" data-field="exit_price" data-type="number">${t.exit_price || '-'}</td>
                <td class="trade-editable" data-trade-id="${t.id}" data-field="sl_price" data-type="number">${t.sl_price || '-'}</td>
                <td class="trade-editable" data-trade-id="${t.id}" data-field="quantity" data-type="number">${t.quantity || '-'}</td>
                <td class="${pnlClass} font-medium">${t.pnl != null ? formatCurrency(t.pnl) : '-'}</td>
                <td>${t.entry_date || '-'}</td>
                <td>
                    <span style="background:${statusBg(t.status)};color:${statusColor(t.status)}" class="text-2xs px-1.5 py-0.5 rounded-full">${t.status}</span>
                </td>
                <td style="white-space:nowrap">
                    ${isOpen ? `<button onclick="_journalCancel(${t.id})" title="Cancel" style="background:none;border:none;color:#fbbf24;cursor:pointer;font-size:0.6rem;padding:2px 4px">Cancel</button>` : ''}
                    <button onclick="_journalEdit(${t.id})" title="Edit" style="background:none;border:none;color:#a5b4fc;cursor:pointer;font-size:0.6rem;padding:2px 4px">Edit</button>
                    <button onclick="_journalDelete(${t.id})" title="Delete" style="background:none;border:none;color:#f87171;cursor:pointer;font-size:0.6rem;padding:2px 4px">Delete</button>
                </td>
            </tr>`;
        }).join('');

        document.getElementById('trades-table').innerHTML = `
            <table class="data-table">
                <thead><tr>
                    <th>Symbol</th><th>Type</th><th>Entry</th><th>Exit</th>
                    <th>SL</th><th>Qty</th><th>P&L</th><th>Date</th><th>Status</th><th>Actions</th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
        applySortable('trades-table');
    } catch (err) {
        console.error('Error loading trades:', err);
    }
}

function openTradeModal() {
    alert('Use the Stock Options tab to place trades via the analysis panel.');
}

async function _journalCancel(tradeId) {
    if (!confirm('Mark this trade as cancelled?')) return;
    try {
        await fetch(`${API_BASE}/api/trades/${tradeId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: 'cancelled' }),
        });
        loadTrades();
    } catch (e) { alert('Error: ' + e.message); }
}

async function _journalDelete(tradeId) {
    if (!confirm('Permanently delete this trade record?')) return;
    try {
        await fetch(`${API_BASE}/api/trades/${tradeId}`, { method: 'DELETE' });
        loadTrades();
    } catch (e) { alert('Error: ' + e.message); }
}

function _journalEdit(tradeId) {
    const row = document.getElementById(`trade-row-${tradeId}`);
    if (!row) return;

    const editables = row.querySelectorAll('.trade-editable');
    const alreadyEditing = row.querySelector('.trade-edit-input');
    if (alreadyEditing) return;

    editables.forEach(td => {
        const field = td.dataset.field;
        const currentVal = td.textContent.trim();
        const val = currentVal === '-' ? '' : currentVal;
        td.dataset.originalValue = currentVal;
        td.innerHTML = `<input type="number" step="0.01" class="trade-edit-input" value="${val}"
            style="width:60px;padding:2px 4px;border-radius:4px;border:1px solid var(--glass-border);background:var(--glass-bg);color:var(--text-primary);font-size:0.65rem" />`;
    });

    // Change Edit button to Save
    const editBtn = row.querySelector('[title="Edit"]');
    if (editBtn) {
        editBtn.textContent = 'Save';
        editBtn.title = 'Save';
        editBtn.style.color = '#4ade80';
        editBtn.style.fontWeight = '700';
        editBtn.onclick = () => _journalSave(tradeId);
    }
}

async function _journalSave(tradeId) {
    const row = document.getElementById(`trade-row-${tradeId}`);
    if (!row) return;

    const updates = {};
    row.querySelectorAll('.trade-editable').forEach(td => {
        const input = td.querySelector('.trade-edit-input');
        if (input) {
            const val = input.value.trim();
            const field = td.dataset.field;
            if (val !== '' && val !== td.dataset.originalValue) {
                updates[field] = parseFloat(val);
            }
        }
    });

    if (Object.keys(updates).length === 0) {
        loadTrades();
        return;
    }

    try {
        await fetch(`${API_BASE}/api/trades/${tradeId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updates),
        });
        loadTrades();
    } catch (e) { alert('Error: ' + e.message); }
}

// --- Scanner ---

async function runScan(section) {
    const btn = document.getElementById(`btn-scan-${section}`);
    const statusEl = document.getElementById('scan-status');
    btn.disabled = true;
    const label = section === 'fno' ? 'F&O' : 'Swing';
    btn.innerHTML = `<span class="spinner"></span> Scanning ${label}...`;
    statusEl.textContent = `Running ${label} scan... This may take a few minutes.`;
    statusEl.style.color = '#6366f1';

    try {
        const response = await fetch(`${API_BASE}/api/settings/scan/run?section=${section}`, { method: 'POST' });
        const data = await response.json();
        
        // Check if response indicates an error
        if (data.error) {
            throw new Error(data.message || 'Scan failed');
        }
        
        const s = data.summary || {};
        let msg = data.message || 'Scan completed';
        
        // Add summary details if available
        if (s.scanned) {
            msg += ` | Scanned: ${s.scanned}`;
            if (s.new_signals || s.vcp_signals) msg += `, New signals: ${s.new_signals || s.vcp_signals || 0}`;
            if (s.updated) msg += `, Updated: ${s.updated}`;
            if (s.exploded) msg += `, Exploded: ${s.exploded}`;
            if (s.invalidated) msg += `, Invalidated: ${s.invalidated}`;
        }
        if (s.errors) msg += ` | Errors: ${s.errors}`;
        if (s.rate_limited) msg += ` | Rate limited: ${s.rate_limited}`;
        
        statusEl.textContent = msg;
        statusEl.style.color = '#22c55e';

        if (s.top_sectors) loadSectorDisplay();

        if (section === 'fno') loadFnoSignals();
        else loadSignals('swing');
    } catch (err) {
        statusEl.textContent = `Scan failed: ${err.message}`;
        statusEl.style.color = '#ef4444';
    } finally {
        btn.disabled = false;
        btn.innerHTML = section === 'fno' ? 'Scan F&O Stocks' : 'Scan Swing Trades';
    }
}

async function runSectorAnalysis() {
    const btn = document.getElementById('btn-sector-analysis');
    const statusEl = document.getElementById('scan-status');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Analyzing...';
    statusEl.textContent = 'Running sector momentum analysis...';
    statusEl.style.color = '#6366f1';

    try {
        const response = await fetch(`${API_BASE}/api/settings/scan/sectors`, { method: 'POST' });
        const data = await response.json();
        statusEl.textContent = `Sector analysis complete. ${data.sectors.length} sectors analyzed.`;
        statusEl.style.color = '#22c55e';
        displaySectorResults(data.sectors);
    } catch (err) {
        statusEl.textContent = `Sector analysis failed: ${err.message}`;
        statusEl.style.color = '#ef4444';
    } finally {
        btn.disabled = false;
        btn.innerHTML = 'Sector Analysis Only';
    }
}

async function loadSectorDisplay() {
    try {
        const response = await fetch(`${API_BASE}/api/settings/sectors/top`);
        const sectors = await response.json();
        if (sectors.length) displaySectorResults(sectors);
    } catch (err) {
        console.error('Error loading sectors:', err);
    }
}

function displaySectorResults(sectors) {
    const container = document.getElementById('sector-momentum');
    const listEl = document.getElementById('sector-list');
    container.classList.remove('hidden');

    const rows = sectors.map((s, i) => {
        const scoreColor = s.combined_score >= 0.7 ? '#22c55e' :
                           s.combined_score >= 0.4 ? '#eab308' : 'var(--text-muted)';
        const rank = i + 1;
        const badges = [
            s.is_above_20ema ? '<span class="text-2xs px-1 py-0.5 rounded" style="background:rgba(34,197,94,0.12);color:#22c55e">20EMA</span>' : '',
            s.is_above_50ema ? '<span class="text-2xs px-1 py-0.5 rounded" style="background:rgba(34,197,94,0.12);color:#22c55e">50EMA</span>' : '',
            s.is_making_higher_highs ? '<span class="text-2xs px-1 py-0.5 rounded" style="background:rgba(59,130,246,0.12);color:#3b82f6">HH</span>' : '',
        ].filter(Boolean).join(' ');

        return `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border-light)">
                <div style="display:flex;align-items:center;gap:8px">
                    <span class="text-2xs" style="color:var(--text-muted);width:16px">#${rank}</span>
                    <span class="text-xs font-medium" style="color:var(--text-primary)">${s.sector_name}</span>
                    <span style="display:flex;gap:4px">${badges}</span>
                </div>
                <div style="display:flex;align-items:center;gap:12px">
                    <span class="text-2xs" style="color:var(--text-muted)">RS: ${(s.relative_strength * 100).toFixed(0)}%</span>
                    <span class="text-2xs" style="color:var(--text-muted)">PA: ${(s.price_action_score * 100).toFixed(0)}%</span>
                    <span class="text-xs font-semibold" style="color:${scoreColor}">${(s.combined_score * 100).toFixed(0)}%</span>
                </div>
            </div>`;
    }).join('');

    listEl.innerHTML = rows;
}

async function resetSignals() {
    if (!confirm('This will delete ALL signals and their history. Are you sure?')) return;
    try {
        const response = await fetch(`${API_BASE}/api/settings/reset/signals`, { method: 'POST' });
        const data = await response.json();
        alert(`Reset complete. Deleted ${data.deleted_signals} signals and ${data.deleted_history} history records.`);
    } catch (err) {
        alert(`Reset failed: ${err.message}`);
    }
}

// --- Settings ---

async function refreshInstruments() {
    const btn = document.getElementById('btn-refresh-instruments');
    const statusEl = document.getElementById('instruments-status');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Refreshing...';
    statusEl.textContent = '';

    try {
        const response = await fetch(`${API_BASE}/api/settings/instruments/refresh`, { method: 'POST' });
        const data = await response.json();
        statusEl.textContent = `Done! ${data.total_equity} stocks loaded, ${data.fno_symbols} F&O symbols found. (${data.added} new, ${data.updated} updated)`;
        statusEl.style.color = '#22c55e';
    } catch (err) {
        statusEl.textContent = `Error: ${err.message}`;
        statusEl.style.color = '#ef4444';
    } finally {
        btn.disabled = false;
        btn.innerHTML = 'Refresh Instruments';
    }
}

async function refreshDhanToken() {
    const btn = document.getElementById('btn-refresh-token');
    const statusEl = document.getElementById('token-status');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Refreshing...';
    statusEl.textContent = 'Contacting Dhan API...';
    statusEl.style.color = '#6366f1';

    try {
        const response = await fetch(`${API_BASE}/api/settings/token/refresh`, { method: 'POST' });
        const data = await response.json();
        
        if (data.success) {
            statusEl.textContent = `✅ ${data.message} | Old: ${data.old_token} → New: ${data.new_token}`;
            statusEl.style.color = '#22c55e';
        } else {
            statusEl.textContent = `❌ ${data.message}`;
            statusEl.style.color = '#ef4444';
        }
    } catch (err) {
        statusEl.textContent = `Error: ${err.message}`;
        statusEl.style.color = '#ef4444';
    } finally {
        btn.disabled = false;
        btn.innerHTML = 'Refresh Token';
    }
}

async function createBackup() {
    try {
        const response = await fetch(`${API_BASE}/api/settings/backup/now`, { method: 'POST' });
        const data = await response.json();
        alert(`Backup created: ${data.filename} (${data.size_mb} MB)`);
        loadBackups();
    } catch (err) {
        alert(`Backup failed: ${err.message}`);
    }
}

async function exportCSV() {
    try {
        const response = await fetch(`${API_BASE}/api/settings/backup/export-csv`, { method: 'POST' });
        const data = await response.json();
        alert(`Trades exported to: ${data.path}`);
    } catch (err) {
        alert(`Export failed: ${err.message}`);
    }
}

async function loadBackups() {
    try {
        const response = await fetch(`${API_BASE}/api/settings/backup/list`);
        const backups = await response.json();

        if (!backups.length) {
            document.getElementById('backup-list').innerHTML = `<p class="text-xs" style="color:var(--text-muted)">No backups found.</p>`;
            return;
        }

        const rows = backups.slice(0, 10).map(b => `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border-light)">
                <div>
                    <p class="text-xs font-medium" style="color:var(--text-primary)">${b.filename}</p>
                    <p class="text-2xs" style="color:var(--text-muted)">${b.created_at} &middot; ${b.size_mb} MB</p>
                </div>
                <button onclick="restoreBackup('${b.filename}')"
                    class="text-2xs text-brand-500 hover:text-brand-400 font-medium">Restore</button>
            </div>`).join('');

        document.getElementById('backup-list').innerHTML = `
            <h4 class="text-xs font-medium mb-1.5" style="color:var(--text-muted)">Recent Backups</h4>
            ${rows}`;
    } catch (err) {
        console.error('Error loading backups:', err);
    }
}

async function restoreBackup(filename) {
    if (!confirm(`Restore database from ${filename}? A pre-restore backup will be created first.`)) return;
    try {
        const response = await fetch(`${API_BASE}/api/settings/backup/restore?filename=${encodeURIComponent(filename)}`, { method: 'POST' });
        const data = await response.json();
        alert(`Database restored from ${data.restored_from}. Please refresh the page.`);
    } catch (err) {
        alert(`Restore failed: ${err.message}`);
    }
}

let searchTimeout;
function debounceSearch(query) {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => searchInstruments(query), 300);
}

async function searchInstruments(query) {
    if (!query || query.length < 2) {
        document.getElementById('search-results').innerHTML = '';
        return;
    }
    try {
        const response = await fetch(`${API_BASE}/api/settings/instruments/search?q=${encodeURIComponent(query)}`);
        const results = await response.json();

        if (!results.length) {
            document.getElementById('search-results').innerHTML = `<p class="text-xs" style="color:var(--text-muted)">No instruments found.</p>`;
            return;
        }

        const rows = results.map(r => `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border-light)">
                <div>
                    <span class="text-xs font-medium" style="color:var(--text-primary)">${r.symbol}</span>
                    <span class="text-2xs" style="color:var(--text-muted);margin-left:6px">${r.display_name || ''}</span>
                    ${r.is_fno ? '<span class="text-2xs px-1.5 py-0.5 rounded-full" style="background:rgba(168,85,247,0.12);color:#a78bfa;margin-left:6px">F&O</span>' : ''}
                </div>
                <span class="text-2xs" style="color:var(--text-muted)">${r.security_id}${r.is_fno ? ` \u00b7 Lot: ${r.lot_size}` : ''}</span>
            </div>`).join('');

        document.getElementById('search-results').innerHTML = rows;
    } catch (err) {
        console.error('Error searching instruments:', err);
    }
}

// --- Utilities ---

function ordinal(n) {
    const s = ['th', 'st', 'nd', 'rd'];
    const v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function daysSince(dateStr) {
    if (!dateStr) return 0;
    const now = new Date();
    let d = null;

    if (typeof dateStr === 'string') {
        const dateOnly = dateStr.split('T')[0];
        const parts = dateOnly.split('-');
        if (parts.length === 3) {
            const year = Number(parts[0]);
            const month = Number(parts[1]);
            const day = Number(parts[2]);
            if (!Number.isNaN(year) && !Number.isNaN(month) && !Number.isNaN(day)) {
                d = new Date(year, month - 1, day);
            }
        }
    }

    if (!d) {
        d = new Date(dateStr);
    }

    if (Number.isNaN(d.getTime())) return 0;

    const diffDays = Math.floor((now - d) / (1000 * 60 * 60 * 24));
    return Math.max(0, diffDays);
}

function formatCurrency(amount) {
    if (amount == null) return '--';
    const prefix = amount >= 0 ? '+' : '';
    return prefix + new Intl.NumberFormat('en-IN', {
        style: 'currency', currency: 'INR', maximumFractionDigits: 0
    }).format(amount);
}

// --- VCP Scanner Functions ---

function showVcpInfo() {
    const panel = document.getElementById('vcp-info-panel');
    if (panel) {
        panel.classList.remove('hidden');
    }
}

function toggleVcpInfo() {
    const panel = document.getElementById('vcp-info-panel');
    if (panel) {
        panel.classList.toggle('hidden');
    }
}

function loadSwingSignalsWithVcpFilters() {
    const statusFilters = getSelectedPills('swing-status-filter');
    const scoreFilters = getSelectedPills('swing-score-filter');
    
    // Apply VCP-specific filtering
    loadSignals('swing', { statusFilters, scoreFilters });
}

async function runSwingScan() {
    const btn = document.getElementById('btn-swing-scan');
    const statusEl = document.getElementById('swing-scan-status');
    const originalText = btn.innerHTML;
    
    // Show loading state
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Scanning...';
    
    if (statusEl) {
        statusEl.textContent = 'Running VCP scan... This may take a few minutes.';
        statusEl.style.color = '#6366f1';
    }
    
    try {
        // Run the swing scan
        const response = await fetch(`${API_BASE}/api/settings/scan/run?section=swing`, { method: 'POST' });
        const data = await response.json();
        
        // Check if response indicates an error
        if (data.error) {
            throw new Error(data.message || 'Scan failed');
        }
        
        const s = data.summary || {};
        
        // Show success feedback with scan results
        let resultMsg = '✓ Scan Complete';
        let statusMsg = data.message || 'Scan completed successfully';
        
        if (s.scanned) {
            resultMsg = `✓ Found ${s.vcp_signals || 0} VCP`;
            statusMsg = `Scanned ${s.scanned} stocks, found ${s.vcp_signals || 0} VCP patterns`;
            if (s.chartink_matches) statusMsg += ` (${s.chartink_matches} passed Chartink filters)`;
        }
        
        btn.innerHTML = resultMsg;
        
        if (statusEl) {
            statusEl.textContent = statusMsg;
            statusEl.style.color = '#22c55e';
        }
        
        // Reload the signals to show new results
        await loadSignals('swing');
        
        // Reset button after delay
        setTimeout(() => {
            btn.innerHTML = originalText;
            btn.disabled = false;
            if (statusEl) {
                statusEl.textContent = '';
            }
        }, 5000);
        
    } catch (error) {
        console.error('Error running swing scan:', error);
        btn.innerHTML = '✗ Scan Failed';
        
        if (statusEl) {
            statusEl.textContent = `Scan failed: ${error.message}`;
            statusEl.style.color = '#ef4444';
        }
        
        setTimeout(() => {
            btn.innerHTML = originalText;
            btn.disabled = false;
            if (statusEl) {
                statusEl.textContent = '';
            }
        }, 5000);
    }
}

async function refreshSwingSignals() {
    const btn = document.getElementById('btn-refresh-swing');
    const originalText = btn.innerHTML;
    
    // Show loading state
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Refreshing...';
    
    try {
        // Reload signals with current filters
        await loadSignals('swing');
        
        // Show success feedback briefly
        btn.innerHTML = '✓ Refreshed';
        setTimeout(() => {
            btn.innerHTML = originalText;
            btn.disabled = false;
        }, 1000);
        
    } catch (error) {
        console.error('Error refreshing signals:', error);
        btn.innerHTML = '✗ Error';
        setTimeout(() => {
            btn.innerHTML = originalText;
            btn.disabled = false;
        }, 2000);
    }
}

// Enhanced signal rendering for VCP patterns
function renderVcpSignal(signal) {
    const vcpBadge = signal.signal_type === 'VCP' 
        ? `<span class="text-2xs px-1.5 py-0.5 rounded-full" style="background:rgba(34,197,94,0.12);color:#22c55e;margin-left:4px">VCP</span>`
        : '';
    
    const contractionInfo = signal.contraction_score 
        ? `<div class="text-2xs mt-1" style="color:var(--text-muted)">Contraction: ${(signal.contraction_score * 100).toFixed(0)}% • Volume: ${(signal.breakout_score * 100).toFixed(0)}%</div>`
        : '';
    
    return { vcpBadge, contractionInfo };
}

// --- Initialize ---
function startApp() {
    initTheme();
    initSidebar();
    loadPnlGuardStatus();
    switchTab('sectors');
    loadCredentialsStatus();
    applyRoleAccess();
}

document.addEventListener('DOMContentLoaded', async () => {
    const ok = await initAuth();
    if (ok) startApp();
    
    // Make functions globally available for testing
    window.testCredentials = testCredentials;
    window.togglePassword = togglePassword;
    window.saveCredentials = saveCredentials;
    window.clearCredentials = clearCredentials;
    window.getCredentialsFromForm = getCredentialsFromForm;
    window.loadCredentialsStatus = loadCredentialsStatus;
    window.generateTokenNow = generateTokenNow;
    window.loginAppUser = loginAppUser;
    window.logoutAppUser = logoutAppUser;
    
    console.log('🚀 App initialized. Functions available globally.');
    console.log('🔧 Debug commands:');
    console.log('  - testCredentials() - Test the credentials');
    console.log('  - togglePassword("inputId") - Toggle password visibility');
    console.log('  - getCredentialsFromForm() - Get form data');
    console.log('  - loadCredentialsStatus() - Reload status');
});

// ── Credentials Management Functions ──

function togglePassword(inputId) {
    console.log('Toggle password called for:', inputId);
    
    const input = document.getElementById(inputId);
    if (!input) {
        console.error('Input not found:', inputId);
        return;
    }
    
    const container = input.parentElement;
    const button = container.querySelector('.toggle-password');
    
    if (!button) {
        console.error('Toggle button not found for:', inputId);
        return;
    }
    
    console.log('Current input type:', input.type);
    
    if (input.type === 'password') {
        input.type = 'text';
        button.textContent = '🙈';
        console.log('Changed to text type');
    } else {
        input.type = 'password';
        button.textContent = '👁️';
        console.log('Changed to password type');
    }
}

async function loadCredentialsStatus() {
    try {
        const statusDiv = document.getElementById('credentialsStatus');
        const listDiv = document.getElementById('credentialsList');
        
        if (!statusDiv || !listDiv) {
            console.log('Credentials status elements not found, skipping load');
            return;
        }
        
        const response = await fetch('/api/settings/credentials/status');
        const data = await response.json();
        
        if (data.configured) {
            statusDiv.innerHTML = `<div class="success-message">✅ Credentials configured and ready</div>`;
            
            const creds = data.credentials;
            listDiv.innerHTML = `
                <div class="credentials-list">
                    <div><strong>Client ID:</strong> ${creds.client_id}</div>
                    <div><strong>API Key:</strong> ${creds.api_key}</div>
                    <div><strong>API Secret:</strong> ${creds.api_secret}</div>
                    <div><strong>PIN:</strong> ${creds.pin}</div>
                    <div><strong>TOTP Secret:</strong> ${creds.totp_secret}</div>
                </div>
            `;
            
            // Load current values into form (only non-sensitive fields)
            const clientIdInput = document.getElementById('clientId');
            const apiKeyInput = document.getElementById('apiKey');
            
            if (clientIdInput) clientIdInput.value = creds.client_id;
            if (apiKeyInput) apiKeyInput.value = creds.api_key;
            
            // Update placeholders for sensitive fields
            const apiSecretInput = document.getElementById('apiSecret');
            const pinInput = document.getElementById('dhanPin');
            const totpInput = document.getElementById('totpSecret');
            
            if (apiSecretInput) apiSecretInput.placeholder = `Current: ${creds.api_secret}`;
            if (pinInput) pinInput.placeholder = `Current: ${creds.pin}`;
            if (totpInput) totpInput.placeholder = `Current: ${creds.totp_secret}`;
        } else {
            statusDiv.innerHTML = `<div class="warning-message">⚠️ No credentials configured</div>`;
            listDiv.innerHTML = '<div style="color: var(--text-muted);">Please configure your Dhan API credentials</div>';
        }
    } catch (error) {
        console.error('Error loading credentials status:', error);
        const statusDiv = document.getElementById('credentialsStatus');
        if (statusDiv) {
            statusDiv.innerHTML = `<div class="error-message">❌ Error loading credentials status</div>`;
        }
    }
}

async function testCredentials() {
    console.log('🔧 Test credentials function called');
    
    // First, test if elements exist
    const button = document.getElementById('btn-test-credentials');
    const statusDiv = document.getElementById('credentialsStatus');
    
    console.log('Button found:', !!button);
    console.log('Status div found:', !!statusDiv);
    
    if (!button || !statusDiv) {
        console.error('❌ Required elements not found');
        alert('UI elements not found. Check console for details.');
        return;
    }
    
    // Test form data
    const credentials = getCredentialsFromForm();
    if (!credentials) {
        console.log('❌ No credentials provided');
        return;
    }
    
    console.log('✅ Credentials obtained from form');
    
    // Update UI
    button.disabled = true;
    button.textContent = 'Testing...';
    statusDiv.innerHTML = '<div class="warning-message">🔄 Testing credentials...</div>';
    
    try {
        console.log('📡 Sending test request...');
        const response = await fetch('/api/settings/credentials/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(credentials)
        });
        
        console.log('📡 Response status:', response.status);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        console.log('📡 Test response:', data);
        
        if (data.success) {
            statusDiv.innerHTML = `
                <div class="success-message">
                    ✅ <strong>Credentials Valid!</strong><br>
                    🔐 <strong>TOTP Code:</strong> ${data.totp_code || 'Generated successfully'}<br>
                    <small>Generated at ${new Date().toLocaleTimeString()}</small>
                </div>
            `;
        } else {
            statusDiv.innerHTML = `
                <div class="error-message">
                    ❌ <strong>Validation Failed</strong><br>
                    ${data.message || 'Unknown error occurred'}
                </div>
            `;
        }
    } catch (error) {
        console.error('❌ Test credentials error:', error);
        statusDiv.innerHTML = `
            <div class="error-message">
                ❌ <strong>Connection Error</strong><br>
                ${error.message}<br>
                <small>Check console for details</small>
            </div>
        `;
    } finally {
        button.disabled = false;
        button.textContent = 'TEST';
        console.log('🔧 Test function completed');
    }
}

async function saveCredentials() {
    const credentials = getCredentialsFromForm();
    if (!credentials) return;
    
    const button = document.getElementById('btn-save-credentials');
    const statusDiv = document.getElementById('credentialsStatus');
    
    button.disabled = true;
    button.textContent = 'Saving...';
    statusDiv.innerHTML = '<div class="warning-message">🔄 Saving credentials...</div>';
    
    try {
        const response = await fetch('/api/settings/credentials/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(credentials)
        });
        
        const data = await response.json();
        
        if (data.success) {
            statusDiv.innerHTML = `
                <div class="success-message">
                    ✅ ${data.message}<br>
                    TOTP Test: ${data.totp_test}
                </div>
            `;
            // Reload status to show updated info
            setTimeout(() => loadCredentialsStatus(), 1000);
        } else {
            statusDiv.innerHTML = `
                <div class="error-message">
                    ❌ ${data.message}
                </div>
            `;
        }
    } catch (error) {
        statusDiv.innerHTML = `
            <div class="error-message">
                ❌ Error saving credentials: ${error.message}
            </div>
        `;
    } finally {
        button.disabled = false;
        button.textContent = 'Save';
    }
}

async function clearCredentials() {
    if (!confirm('Are you sure you want to clear all credentials? This cannot be undone.')) {
        return;
    }
    
    const button = document.getElementById('btn-clear-credentials');
    const statusDiv = document.getElementById('credentialsStatus');
    
    button.disabled = true;
    button.textContent = 'Clearing...';
    
    try {
        const response = await fetch('/api/settings/credentials/clear', {
            method: 'POST'
        });
        
        const data = await response.json();
        
        if (data.success) {
            statusDiv.innerHTML = `<div class="success-message">✅ ${data.message}</div>`;
            
            // Clear form
            document.getElementById('clientId').value = '';
            document.getElementById('apiKey').value = '';
            document.getElementById('apiSecret').value = '';
            document.getElementById('dhanPin').value = '';
            document.getElementById('totpSecret').value = '';
            
            // Reload status
            setTimeout(() => loadCredentialsStatus(), 1000);
        } else {
            statusDiv.innerHTML = `<div class="error-message">❌ ${data.message}</div>`;
        }
    } catch (error) {
        statusDiv.innerHTML = `<div class="error-message">❌ Error: ${error.message}</div>`;
    } finally {
        button.disabled = false;
        button.textContent = 'Clear';
    }
}

function getCredentialsFromForm() {
    console.log('🔍 Getting credentials from form...');
    
    const clientIdEl = document.getElementById('clientId');
    const apiKeyEl = document.getElementById('apiKey');
    const apiSecretEl = document.getElementById('apiSecret');
    const pinEl = document.getElementById('dhanPin');
    const totpSecretEl = document.getElementById('totpSecret');
    const statusDiv = document.getElementById('credentialsStatus');
    
    // Check if elements exist
    const elements = { clientIdEl, apiKeyEl, apiSecretEl, pinEl, totpSecretEl };
    const missingElements = Object.entries(elements).filter(([key, el]) => !el).map(([key]) => key);
    
    if (missingElements.length > 0) {
        console.error('❌ Form elements not found:', missingElements);
        if (statusDiv) {
            statusDiv.innerHTML = `<div class="error-message">❌ Form elements not found: ${missingElements.join(', ')}</div>`;
        }
        return null;
    }
    
    // Get values
    const clientId = clientIdEl.value.trim();
    const apiKey = apiKeyEl.value.trim();
    const apiSecret = apiSecretEl.value.trim();
    const pin = pinEl.value.trim();
    const totpSecret = totpSecretEl.value.trim();
    
    console.log('📋 Form values:', { 
        clientId: clientId || '(empty)', 
        apiKey: apiKey || '(empty)', 
        apiSecret: apiSecret ? '***' : '(empty)', 
        pin: pin ? '***' : '(empty)', 
        totpSecret: totpSecret ? '***' : '(empty)' 
    });
    
    // Validate required fields
    const emptyFields = [];
    if (!clientId) emptyFields.push('Client ID');
    if (!apiKey) emptyFields.push('API Key');
    if (!apiSecret) emptyFields.push('API Secret');
    if (!pin) emptyFields.push('PIN');
    if (!totpSecret) emptyFields.push('TOTP Secret');
    
    if (emptyFields.length > 0) {
        if (statusDiv) {
            statusDiv.innerHTML = `<div class="error-message">❌ Please fill in: <strong>${emptyFields.join(', ')}</strong></div>`;
        }
        return null;
    }
    
    // Validate PIN format
    if (pin.length !== 6 || !/^\d+$/.test(pin)) {
        if (statusDiv) {
            statusDiv.innerHTML = '<div class="error-message">❌ <strong>PIN must be exactly 6 digits</strong></div>';
        }
        return null;
    }
    
    console.log('✅ Form validation passed');
    
    return {
        client_id: clientId,
        api_key: apiKey,
        api_secret: apiSecret,
        pin: pin,
        totp_secret: totpSecret
    };
}

async function generateTokenNow() {
    console.log('🔧 Generate token now function called');
    
    const button = document.getElementById('btn-generate-token');
    const statusDiv = document.getElementById('token-status');
    
    if (!button || !statusDiv) {
        console.error('❌ Required elements not found');
        return;
    }
    
    button.disabled = true;
    button.textContent = 'Generating...';
    statusDiv.innerHTML = '<div style="color: #f59e0b;">🔄 Generating token using UI credentials...</div>';
    
    try {
        console.log('📡 Sending generate token request...');
        const response = await fetch('/api/settings/credentials/generate-token', {
            method: 'POST'
        });
        
        console.log('📡 Response status:', response.status);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        console.log('📡 Generate token response:', data);
        
        if (data.success) {
            statusDiv.innerHTML = `
                <div style="color: #10b981; font-weight: 600;">
                    ✅ <strong>Token Generated!</strong><br>
                    🔐 Preview: ${data.token_preview}<br>
                    <small>Generated at ${new Date().toLocaleTimeString()}</small>
                </div>
            `;
        } else {
            statusDiv.innerHTML = `
                <div style="color: #ef4444; font-weight: 600;">
                    ❌ <strong>Generation Failed</strong><br>
                    ${data.message}
                </div>
            `;
        }
    } catch (error) {
        console.error('❌ Generate token error:', error);
        statusDiv.innerHTML = `
            <div style="color: #ef4444; font-weight: 600;">
                ❌ <strong>Connection Error</strong><br>
                ${error.message}
            </div>
        `;
    } finally {
        button.disabled = false;
        button.textContent = 'Generate Token';
        console.log('🔧 Generate token function completed');
    }
}
