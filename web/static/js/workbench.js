/**
 * workbench.js — FinAgent-Lithium workbench (index) page interactions.
 * Handles panel switching, file upload/drag-drop, form validation,
 * loading overlay, ask/follow-up, peer comparison, and health check.
 */

// ── Conversation history (per report_id) ──
var workbenchConversations = {};
var historyConversations = {};

// ── DOM references ──
var dropZone = document.getElementById('dropZone');
var fileInput = document.getElementById('fileInput');
var fileName = document.getElementById('fileName');

// ── Panel switching ──
function activatePanel(panel) {
  document.querySelectorAll('[data-feature-panel]').forEach(function(el) {
    el.classList.toggle('active', el.getAttribute('data-feature-panel') === panel);
  });
  document.querySelectorAll('[data-panel]').forEach(function(el) {
    el.classList.toggle('active', el.getAttribute('data-panel') === panel);
  });
}

document.querySelectorAll('[data-panel]').forEach(function(link) {
  link.addEventListener('click', function(e) {
    e.preventDefault();
    var panel = link.getAttribute('data-panel');
    activatePanel(panel);
    history.replaceState(null, '', '#panel-' + panel);
  });
});

activatePanel(document.body.getAttribute('data-initial-panel') || 'upload');

// ── File upload / drag-drop ──
if (dropZone && fileInput) {
  dropZone.addEventListener('click', function(e) {
    if (e.target !== fileInput && e.target.tagName !== 'BUTTON') fileInput.click();
  });
  dropZone.addEventListener('dragover', function(e) {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });
  dropZone.addEventListener('dragleave', function() {
    dropZone.classList.remove('dragover');
  });
  dropZone.addEventListener('drop', function(e) {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
      fileInput.files = e.dataTransfer.files;
      onFileSelected(fileInput);
    }
  });
}

function onFileSelected(input) {
  if (input.files && input.files[0]) {
    var name = input.files[0].name;
    fileName.textContent = '已选择：' + name;
    fileName.className = 'file-name active';
    dropZone.classList.add('has-file');
    var companyInput = document.querySelector('input[name="company_name"]');
    if (companyInput && !companyInput.value) {
      var guess = name.replace(/\.(xlsx|xls|pdf)$/i, '')
                      .replace(/[_\-\d]+$/g, '')
                      .replace(/财务报表|年报|审计|年度报告/g, '');
      companyInput.placeholder = guess || '宁德时代';
    }
  }
}

// ── Error display ──
function showError(msg) {
  var el = document.getElementById('clientError');
  document.getElementById('clientErrorMsg').textContent = msg;
  el.style.display = 'flex';
  el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
function hideError() {
  document.getElementById('clientError').style.display = 'none';
}

// ── Form validation ──
function validateAndLoad() {
  hideError();
  var hasFile = fileInput.files && fileInput.files.length > 0;
  var jsonField = document.querySelector('textarea[name="financial_data_json"]');
  var hasJson = jsonField && jsonField.value.trim().length > 2;
  var hasCompany = document.querySelector('input[name="company_name"]').value.trim();
  if (!hasFile && !hasJson && !hasCompany) {
    showError('请上传财报文件，或填写公司名称后手动录入数据，再提交分析。');
    return false;
  }
  startAsyncAnalyze();
  return false;  // never do the full-page POST; analysis runs async
}

// ── Async analysis: start job, poll status, redirect on done ──
var stepNames = ['step1', 'step2', 'step3', 'step4', 'step5'];
var POLL_INTERVAL_MS = 2000;
var POLL_TIMEOUT_MS = 300000;  // 5 minutes

function setStepProgress(step) {
  for (var i = 0; i < stepNames.length; i++) {
    var el = document.getElementById(stepNames[i]);
    if (!el) continue;
    el.classList.remove('active', 'done');
    if (i < step - 1) el.classList.add('done');
    else if (i === step - 1) el.classList.add('active');
  }
}

function startLoading() {
  var overlay = document.getElementById('loadingOverlay');
  overlay.classList.add('active');
  var btn = document.getElementById('submitBtn');
  btn.disabled = true;
  btn.textContent = '分析中';
  setStepProgress(1);
  document.getElementById('loadingSub').textContent = '正在启动分析…';
}

function finishWithError(msg) {
  var overlay = document.getElementById('loadingOverlay');
  if (overlay) overlay.classList.remove('active');
  var btn = document.getElementById('submitBtn');
  if (btn) { btn.disabled = false; btn.textContent = '开始分析'; }
  showError(msg);
}

function startAsyncAnalyze() {
  var form = document.getElementById('analysisForm');
  var formData = new FormData(form);
  startLoading();
  fetch('/api/analyze/start', { method: 'POST', body: formData })
    .then(function(resp) {
      if (resp.status === 429) throw new Error('BUSY');
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.json();
    })
    .then(function(data) {
      if (!data.job_id) throw new Error('NO_JOB');
      pollStatus(data.job_id);
    })
    .catch(function(err) {
      if (err.message === 'BUSY') {
        finishWithError('服务繁忙（已有分析在进行中），请稍候再试。');
      } else {
        finishWithError('启动分析失败，请重试。');
      }
    });
}

function pollStatus(jobId) {
  var started = Date.now();
  var timer = setInterval(function() {
    if (Date.now() - started > POLL_TIMEOUT_MS) {
      clearInterval(timer);
      finishWithError('分析超时，请重试。');
      return;
    }
    fetch('/api/analyze/status/' + jobId)
      .then(function(resp) {
        if (resp.status === 404) throw new Error('LOST');
        return resp.json();
      })
      .then(function(data) {
        if (data.status === 'running') {
          if (data.step > 0) setStepProgress(data.step);
          if (data.label) {
            var sub = document.getElementById('loadingSub');
            if (sub) sub.textContent = '正在' + data.label + '…';
          }
        } else if (data.status === 'done') {
          clearInterval(timer);
          setStepProgress(5);
          document.getElementById('loadingSub').textContent = '报告已生成，正在跳转…';
          window.location.href = '/report/' + data.report_id;
        } else if (data.status === 'error') {
          clearInterval(timer);
          finishWithError('分析失败：' + (data.error || '未知错误'));
        }
      })
      .catch(function(err) {
        clearInterval(timer);
        if (err.message === 'LOST') {
          finishWithError('任务已丢失（服务可能重启），请重新提交分析。');
        } else {
          finishWithError('网络中断，请重试。');
        }
      });
  }, POLL_INTERVAL_MS);
}

// ── Workbench ask (follow-up) ──
function fillWorkbenchAsk(text) {
  document.getElementById('workbenchAskQuestion').value = text;
}

function appendWorkbenchBubble(reportId, role, text) {
  var out = document.getElementById('workbenchAskAnswer');
  // On first message, clear the placeholder and switch to chat layout
  if (!out.querySelector('.chat-bubble')) {
    out.textContent = '';
    out.style.maxHeight = '400px';
    out.style.overflowY = 'auto';
  }
  var bubble = document.createElement('div');
  bubble.className = 'chat-bubble chat-' + role;
  bubble.textContent = text;
  out.appendChild(bubble);
  out.scrollTop = out.scrollHeight;
}

function askFromWorkbench() {
  var reportId = document.getElementById('askReportId').value;
  var input = document.getElementById('workbenchAskQuestion');
  var question = input.value.trim();
  var out = document.getElementById('workbenchAskAnswer');
  if (!reportId) {
    out.textContent = '请先在历史记录中选择一个已生成报告。';
    return;
  }
  if (!question) {
    out.textContent = '请先输入追问问题。';
    return;
  }

  // Initialize conversation for this report if needed
  if (!workbenchConversations[reportId]) {
    workbenchConversations[reportId] = [];
  }
  workbenchConversations[reportId].push({role: 'user', content: question});
  appendWorkbenchBubble(reportId, 'user', question);
  input.value = '';

  var controller = new AbortController();
  var timeoutId = setTimeout(function() { controller.abort(); }, 30000);

  fetch('/api/ask', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      report_id: reportId,
      question: question,
      conversation: workbenchConversations[reportId]
    }),
    signal: controller.signal
  }).then(function(resp) {
    clearTimeout(timeoutId);
    if (!resp.ok) throw new Error('服务器错误 (' + resp.status + ')');
    return resp.json();
  }).then(function(data) {
    var answer = data.answer || data.message || '未生成回答。';
    workbenchConversations[reportId].push({role: 'assistant', content: answer});
    appendWorkbenchBubble(reportId, 'assistant', answer);
  }).catch(function(err) {
    var msg;
    if (err.name === 'AbortError') {
      msg = '请求超时，请稍后重试。';
    } else {
      msg = '追问失败：' + err.message;
    }
    appendWorkbenchBubble(reportId, 'assistant', msg);
    // Remove the unanswered user message from conversation
    workbenchConversations[reportId].pop();
  });
}

// ── Peer auto-fetch ──
function fetchPeerComparison() {
  var input = document.getElementById('peerStockCodes');
  var resultDiv = document.getElementById('peerAutoResult');
  var codes = input.value.trim();
  if (!codes) { resultDiv.style.display = 'block'; resultDiv.innerHTML = '<span style="color:var(--red-text)">请输入股票代码</span>'; return; }
  resultDiv.style.display = 'block';
  resultDiv.innerHTML = '<span>正在从 Wind 获取数据...</span>';
  fetch('/api/peer-compare', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({stock_codes: codes})
  }).then(function(resp) { return resp.json(); })
  .then(function(data) {
    if (data.status === 'ok' && data.peers && data.peers.length > 0) {
      var html = '<table class="peer-compare-table" style="font-size:12px;"><thead><tr><th style="text-align:left;">公司</th><th>营业收入</th><th>净利润</th><th>总资产</th><th>毛利率</th><th>ROE</th></tr></thead><tbody>';
      data.peers.forEach(function(p) {
        html += '<tr><td style="text-align:left;">' + p.name + ' <small>' + p.windcode + '</small></td>';
        html += '<td>' + (p.revenue || '-') + '</td>';
        html += '<td>' + (p.profit || '-') + '</td>';
        html += '<td>' + (p.assets || '-') + '</td>';
        html += '<td>' + (p.gross_margin || '-') + '</td>';
        html += '<td>' + (p.roe || '-') + '</td></tr>';
      });
      html += '</tbody></table>';
      resultDiv.innerHTML = html;
    } else {
      resultDiv.innerHTML = '<span style="color:var(--red-text)">' + (data.message || '获取失败') + '</span>';
    }
  }).catch(function(err) {
    resultDiv.innerHTML = '<span style="color:var(--red-text)">请求失败：' + err + '</span>';
  });
}

// ── History chat panel ──
function toggleHistoryChat(reportId) {
  var panel = document.getElementById('history-chat-' + reportId);
  if (!panel) return;
  panel.classList.toggle('open');
  if (panel.classList.contains('open')) {
    var input = panel.querySelector('input');
    if (input) input.focus();
  }
}

function appendHistoryChatBubble(reportId, role, text) {
  var container = document.getElementById('history-msgs-' + reportId);
  if (!container) return;
  var bubble = document.createElement('div');
  bubble.className = 'chat-bubble ' + role;
  bubble.textContent = text;
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
}

function sendHistoryChat(reportId) {
  var panel = document.getElementById('history-chat-' + reportId);
  if (!panel) return;
  var input = panel.querySelector('input');
  var question = input.value.trim();
  if (!question) return;

  if (!historyConversations[reportId]) {
    historyConversations[reportId] = [];
  }
  historyConversations[reportId].push({role: 'user', content: question});
  appendHistoryChatBubble(reportId, 'user', question);
  input.value = '';
  input.disabled = true;

  var controller = new AbortController();
  var timeoutId = setTimeout(function() { controller.abort(); }, 30000);

  fetch('/api/ask', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      report_id: reportId,
      question: question,
      conversation: historyConversations[reportId]
    }),
    signal: controller.signal
  }).then(function(resp) {
    clearTimeout(timeoutId);
    if (!resp.ok) throw new Error('服务器错误 (' + resp.status + ')');
    return resp.json();
  }).then(function(data) {
    var answer = data.answer || data.message || '未生成回答。';
    historyConversations[reportId].push({role: 'assistant', content: answer});
    appendHistoryChatBubble(reportId, 'assistant', answer);
    input.disabled = false;
    input.focus();
  }).catch(function(err) {
    var msg;
    if (err.name === 'AbortError') {
      msg = '请求超时，请稍后重试。';
    } else {
      msg = '追问失败：' + err.message;
    }
    appendHistoryChatBubble(reportId, 'assistant', msg);
    historyConversations[reportId].pop();
    input.disabled = false;
  });
}

// ── Startup health check ──
(function() {
  var banner = document.getElementById('startupBanner');
  var dot = document.getElementById('startupDot');
  var msg = document.getElementById('startupMsg');
  var timer = setTimeout(function() { banner.classList.add('visible'); }, 700);
  fetch('/health').then(function() {
    clearTimeout(timer);
    msg.textContent = '服务就绪 ✓';
    dot.classList.add('dot-ok');
    banner.classList.add('visible');
    setTimeout(function() { banner.classList.remove('visible'); }, 2000);
  }).catch(function() {
    clearTimeout(timer);
    msg.textContent = '服务响应超时，请刷新页面重试';
    dot.classList.add('dot-err');
    banner.classList.add('visible', 'banner-err');
  });
})();
