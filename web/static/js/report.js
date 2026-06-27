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

  var controller = new AbortController();
  var timeoutId = setTimeout(function() { controller.abort(); }, 30000);

  fetch('/api/ask', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      report_id: reportId,
      question: question,
      conversation: askConversation
    }),
    signal: controller.signal
  }).then(function(resp) {
    clearTimeout(timeoutId);
    if (!resp.ok) throw new Error('服务器错误 (' + resp.status + ')');
    return resp.json();
  }).then(function(data) {
    var answer = data.answer || data.message || '未生成回答。';
    appendChatBubble('assistant', answer);
    askConversation.push({role: 'assistant', content: answer});
    btn.disabled = false;
    btn.textContent = '发送';
  }).catch(function(err) {
    var msg;
    if (err.name === 'AbortError') {
      msg = '请求超时，请稍后重试。';
    } else {
      msg = '追问失败：' + err.message;
    }
    appendChatBubble('assistant', msg);
    // Remove the unanswered user message from conversation
    askConversation.pop();
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

// ── Plotly trend charts initialization ──
(function() {
  var el = document.getElementById('chart-data-json');
  if (!el) return;
  var data;
  try { data = JSON.parse(el.textContent); } catch(e) { return; }

  var darkLayout = {
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    font: { color: '#c0c4cc', family: 'system-ui, sans-serif', size: 12 },
    margin: { t: 20, r: 20, b: 40, l: 60 },
    legend: { orientation: 'h', y: -0.15, x: 0.5, xanchor: 'center' },
    xaxis: { gridcolor: 'rgba(255,255,255,0.06)' },
    yaxis: { gridcolor: 'rgba(255,255,255,0.06)' },
  };
  var plotConfig = { responsive: true, displayModeBar: true, modeBarButtonsToRemove: ['lasso2d', 'select2d'] };

  function renderWhenReady() {
    if (typeof Plotly === 'undefined') {
      setTimeout(renderWhenReady, 200);
      return;
    }

    if (data.revenue_profit && document.getElementById('chart-revenue-profit')) {
      var rp = data.revenue_profit;
      var traces = [{
        x: rp.labels, y: rp.revenue, type: 'bar', name: '营业收入(亿)',
        marker: { color: 'rgba(64,158,255,0.7)' }
      }];
      if (rp.profit && rp.profit.length) {
        traces.push({
          x: rp.labels, y: rp.profit, type: 'scatter', mode: 'lines+markers',
          name: '净利润(亿)', yaxis: 'y2',
          line: { color: '#f5a623', width: 2 },
          marker: { size: 6 }
        });
      }
      var layout = Object.assign({}, darkLayout, {
        yaxis: Object.assign({}, darkLayout.yaxis, { title: '营业收入(亿元)' }),
        yaxis2: { title: '净利润(亿元)', overlaying: 'y', side: 'right', gridcolor: 'rgba(255,255,255,0.03)', font: { color: '#f5a623' } },
      });
      Plotly.newPlot('chart-revenue-profit', traces, layout, plotConfig);
    }

    if (data.ratios && data.ratios.series && document.getElementById('chart-ratios')) {
      var rt = data.ratios;
      var colors = ['#409eff', '#f5a623', '#67c23a', '#e6a23c'];
      var i = 0;
      var ratioTraces = [];
      for (var name in rt.series) {
        ratioTraces.push({
          x: rt.labels, y: rt.series[name], type: 'scatter', mode: 'lines+markers',
          name: name, line: { color: colors[i % colors.length], width: 2 },
          marker: { size: 5 }
        });
        i++;
      }
      var ratioLayout = Object.assign({}, darkLayout, {
        yaxis: Object.assign({}, darkLayout.yaxis, { title: '百分比(%)' }),
      });
      Plotly.newPlot('chart-ratios', ratioTraces, ratioLayout, plotConfig);
    }
  }

    // Sparklines in ratio cards
    document.querySelectorAll('.ratio-sparkline').forEach(function(el) {
      var labels, values;
      try {
        labels = JSON.parse(el.getAttribute('data-labels'));
        values = JSON.parse(el.getAttribute('data-values'));
      } catch(e) { return; }
      if (!values || !values.length) return;

      Plotly.newPlot(el, [{
        x: labels, y: values, type: 'scatter', mode: 'lines',
        line: { color: '#409eff', width: 1.5 },
        fill: 'tozeroy', fillcolor: 'rgba(64,158,255,0.1)',
      }], {
        paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
        margin: { t: 2, r: 4, b: 14, l: 4 },
        xaxis: { showgrid: false, tickfont: { size: 9, color: '#666' } },
        yaxis: { showgrid: false, showticklabels: false },
        showlegend: false,
      }, { responsive: true, displayModeBar: false, staticPlot: true });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', renderWhenReady);
  } else {
    renderWhenReady();
  }
})();
