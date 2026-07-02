/**
 * local-history.js — browser-side report history (localStorage).
 *
 * The server keeps history in memory only and loses it whenever the free
 * instance restarts. This module mirrors each generated report into
 * localStorage so the user can still reopen it (read-only markdown view)
 * after the server has forgotten it.
 */

var LOCAL_HISTORY_KEY = 'finagent_history_v1';
var LOCAL_HISTORY_MAX = 20;

function getLocalReports() {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_HISTORY_KEY)) || [];
  } catch (e) {
    return [];
  }
}

function saveLocalReport(meta, markdown) {
  if (!meta || !meta.report_id) return;
  try {
    var items = getLocalReports().filter(function(it) { return it.report_id !== meta.report_id; });
    items.unshift({
      report_id: meta.report_id,
      company_name: meta.company_name || '未命名公司',
      current_period: meta.current_period || '',
      weighted_score: (meta.weighted_score === undefined) ? null : meta.weighted_score,
      sector: meta.sector || '',
      generated_at: meta.generated_at || new Date().toLocaleString('zh-CN'),
      markdown: markdown || ''
    });
    while (items.length > LOCAL_HISTORY_MAX) items.pop();
    localStorage.setItem(LOCAL_HISTORY_KEY, JSON.stringify(items));
  } catch (e) {
    // Quota exceeded: drop the oldest half and retry once
    try {
      var half = getLocalReports().slice(0, Math.floor(LOCAL_HISTORY_MAX / 2));
      localStorage.setItem(LOCAL_HISTORY_KEY, JSON.stringify(half));
    } catch (e2) { /* localStorage unavailable -- give up quietly */ }
  }
}

// ── Auto-save when on a report page (meta tag present) ──
(function() {
  var metaEl = document.getElementById('report-meta-json');
  var mdEl = document.getElementById('report-markdown-data');
  if (!metaEl) return;
  try {
    var meta = JSON.parse(metaEl.textContent);
    var md = '';
    if (mdEl) {
      var txt = document.createElement('textarea');
      txt.innerHTML = mdEl.textContent;
      md = txt.value;
    }
    saveLocalReport(meta, md);
  } catch (e) { /* non-fatal */ }
})();

// ── Minimal safe markdown renderer for the local viewer ──
function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderMarkdownSafe(md) {
  var lines = (md || '').split('\n');
  var html = [];
  var inList = false, inTable = false;

  function closeBlocks() {
    if (inList) { html.push('</ul>'); inList = false; }
    if (inTable) { html.push('</pre>'); inTable = false; }
  }

  for (var i = 0; i < lines.length; i++) {
    var raw = lines[i];
    var line = escapeHtml(raw.trim());
    var isTableRow = raw.trim().indexOf('|') === 0;

    if (isTableRow) {
      if (!inTable) { closeBlocks(); html.push('<pre class="md-table">'); inTable = true; }
      html.push(line);
      continue;
    }
    if (inTable) { html.push('</pre>'); inTable = false; }

    line = line.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    if (line.indexOf('### ') === 0) { closeBlocks(); html.push('<h4>' + line.slice(4) + '</h4>'); }
    else if (line.indexOf('## ') === 0) { closeBlocks(); html.push('<h3>' + line.slice(3) + '</h3>'); }
    else if (line.indexOf('# ') === 0) { closeBlocks(); html.push('<h2>' + line.slice(2) + '</h2>'); }
    else if (line === '---') { closeBlocks(); html.push('<hr>'); }
    else if (line.indexOf('- ') === 0 || line.indexOf('* ') === 0) {
      if (!inList) { html.push('<ul>'); inList = true; }
      html.push('<li>' + line.slice(2) + '</li>');
    }
    else if (line) { closeBlocks(); html.push('<p>' + line + '</p>'); }
    else { closeBlocks(); }
  }
  closeBlocks();
  return html.join('\n');
}

// ── Render merged local history into the workbench panel ──
function renderLocalHistory(containerId, serverIds) {
  var box = document.getElementById(containerId);
  if (!box) return;
  var known = {};
  (serverIds || []).forEach(function(id) { known[id] = true; });
  var localOnly = getLocalReports().filter(function(it) { return !known[it.report_id]; });
  if (!localOnly.length) return;

  var frag = document.createDocumentFragment();
  var head = document.createElement('div');
  head.className = 'local-history-head';
  head.textContent = '💾 本浏览器保存的历史（服务器重启后仍可查看，只读）';
  frag.appendChild(head);

  localOnly.forEach(function(it) {
    var a = document.createElement('a');
    a.className = 'history-item local-history-item';
    a.href = '/local-report#' + encodeURIComponent(it.report_id);
    var strong = document.createElement('strong');
    strong.textContent = it.company_name;
    var span = document.createElement('span');
    span.textContent = (it.current_period || '报告期未注明') +
      (it.weighted_score != null ? ' · 评分 ' + it.weighted_score : '') + ' · 本地存档';
    var small = document.createElement('small');
    small.textContent = it.generated_at + (it.sector ? ' · ' + it.sector : '');
    a.appendChild(strong); a.appendChild(span); a.appendChild(small);
    frag.appendChild(a);
  });
  box.appendChild(frag);
}
