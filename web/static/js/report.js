/**
 * report.js — FinAgent-Lithium report page interactions.
 * Handles ask/follow-up chat, markdown download, and
 * integrated-enterprise sub-sector tab switching.
 */

// ── Ask / follow-up chat ──
var askConversation = [];

function fillAsk(text) {
  var box = document.getElementById('askQuestion');
  if (box) { box.value = text; box.focus(); }
}

function appendChatBubble(role, text) {
  var history = document.getElementById('askHistory');
  var bubble = document.createElement('div');
  bubble.className = 'chat-bubble chat-' + role;
  bubble.textContent = text;
  history.appendChild(bubble);
  history.scrollTop = history.scrollHeight;
}

function askReport() {
  var box = document.getElementById('askQuestion');
  var btn = document.getElementById('askSubmitBtn');
  var ridNode = document.getElementById('report-id-data');
  var question = box ? box.value.trim() : '';
  var reportId = ridNode ? ridNode.textContent.trim() : '';
  if (!question) return;

  appendChatBubble('user', question);
  askConversation.push({role: 'user', content: question});
  box.value = '';
  btn.disabled = true;
  btn.textContent = '思考中...';

  fetch('/api/ask', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      report_id: reportId,
      question: question,
      conversation: askConversation
    })
  }).then(function(resp) {
    return resp.json();
  }).then(function(data) {
    var answer = data.answer || data.message || '未生成回答。';
    appendChatBubble('assistant', answer);
    askConversation.push({role: 'assistant', content: answer});
    btn.disabled = false;
    btn.textContent = '发送';
  }).catch(function(err) {
    appendChatBubble('assistant', '追问失败：' + err);
    btn.disabled = false;
    btn.textContent = '发送';
  });
}

// ── Markdown download ──
function downloadMarkdown() {
  var md = document.getElementById('report-markdown-data').textContent;
  // Decode HTML entities
  var txt = document.createElement('textarea');
  txt.innerHTML = md;
  md = txt.value;

  // Derive company name from the report header instead of a Jinja variable
  var headerEl = document.querySelector('.report-header h1');
  var companyName = headerEl ? headerEl.textContent.split(/\s+/)[0] : 'report';

  var blob = new Blob([md], {type: 'text/markdown;charset=utf-8'});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = companyName + '_analysis.md';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── Integrated-enterprise sub-sector tab switching ──
// Included unconditionally; harmless if the tab elements don't exist.
function switchSubSector(idx) {
  document.querySelectorAll('.sector-panel').forEach(function(el, i) {
    el.style.display = (i === idx) ? '' : 'none';
  });
  document.querySelectorAll('.sub-tab').forEach(function(el, i) {
    el.classList.toggle('active', i === idx);
  });
  history.replaceState(null, '', '#sector-' + idx);
}
(function() {
  var m = location.hash.match(/^#sector-(\d+)$/);
  if (m) switchSubSector(parseInt(m[1], 10));
})();
