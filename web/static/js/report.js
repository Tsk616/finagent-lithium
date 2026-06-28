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

// ── Advanced analysis charts (杜邦/CVP/现金流/Z-score/DCF/相对估值/EVA) ──
(function() {
  var el = document.getElementById('advanced-data-json');
  if (!el) return;
  var adv;
  try { adv = JSON.parse(el.textContent); } catch(e) { return; }

  var C = { blue: '#1d6fd6', orange: '#f5a623', green: '#67c23a', red: '#f56c6c', yellow: '#e6a23c',
            violet: '#6f4bd8', gold: '#b8842a' };
  var base = {
    paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
    font: { color: '#8a93a5', family: 'system-ui, sans-serif', size: 12 },
    margin: { t: 24, r: 20, b: 40, l: 50 },
    xaxis: { gridcolor: 'rgba(125,140,170,0.12)' },
    yaxis: { gridcolor: 'rgba(125,140,170,0.12)' },
  };
  var cfg = { responsive: true, displayModeBar: false };
  function num(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }
  function has(id) { return document.getElementById(id); }

  function draw() {
    if (typeof Plotly === 'undefined') { setTimeout(draw, 200); return; }

    // CVP break-even line chart
    try {
      var cvp = adv.business && adv.business.cvp && adv.business.cvp.chart;
      if (cvp && has('chart-cvp') && num(cvp.sales) != null) {
        var s = cvp.sales, fc = num(cvp.fixed_cost) || 0, vr = num(cvp.vc_ratio) || 0;
        var xmax = Math.max(s * 1.4, (num(cvp.bep) || 0) * 1.2, 0.1);
        var xs = [0, xmax];
        var traces = [
          { x: xs, y: xs, type: 'scatter', mode: 'lines', name: '营业收入', line: { color: C.blue, width: 2 } },
          { x: xs, y: [fc, fc + vr * xmax], type: 'scatter', mode: 'lines', name: '总成本', line: { color: C.orange, width: 2 } },
        ];
        if (num(cvp.bep) != null) {
          traces.push({ x: [cvp.bep], y: [cvp.bep], type: 'scatter', mode: 'markers+text', name: '盈亏平衡点',
            marker: { color: C.red, size: 10 }, text: ['盈亏平衡'], textposition: 'top center', textfont: { color: C.red } });
        }
        Plotly.newPlot('chart-cvp', traces, Object.assign({}, base, {
          xaxis: Object.assign({}, base.xaxis, { title: '营业收入(亿元)' }),
          yaxis: Object.assign({}, base.yaxis, { title: '金额(亿元)' }),
          legend: { orientation: 'h', y: -0.2, x: 0.5, xanchor: 'center' },
        }), cfg);
      }
    } catch (e) {}

    // Cash-flow waterfall
    try {
      var cf = adv.risk && adv.risk.cashflow && adv.risk.cashflow.chart;
      if (cf && has('chart-cashflow')) {
        Plotly.newPlot('chart-cashflow', [{
          type: 'waterfall', orientation: 'v',
          x: ['经营活动', '投资活动', '筹资活动', '现金净增加'],
          measure: ['relative', 'relative', 'relative', 'total'],
          y: [num(cf.operating) || 0, num(cf.investing) || 0, num(cf.financing) || 0, num(cf.net) || 0],
          connector: { line: { color: 'rgba(125,140,170,0.3)' } },
          increasing: { marker: { color: C.green } },
          decreasing: { marker: { color: C.red } },
          totals: { marker: { color: C.blue } },
        }], Object.assign({}, base, { yaxis: Object.assign({}, base.yaxis, { title: '亿元' }) }), cfg);
      }
    } catch (e) {}

    // Z-score gauge
    try {
      var zs = adv.risk && adv.risk.zscore && adv.risk.zscore.chart;
      if (zs && has('chart-zscore') && num(zs.value) != null) {
        var cmap = { normal: C.green, warning: C.yellow, danger: C.red };
        var zones = zs.zones || [];
        var axmax = zones.length ? zones[zones.length - 1].max : zs.safe * 1.7;
        var fillMap = { danger: 'rgba(245,108,108,0.25)', warning: 'rgba(230,162,60,0.25)', normal: 'rgba(103,194,58,0.25)' };
        var steps = zones.map(function (z) { return { range: [z.min, z.max], color: fillMap[z.color] || 'rgba(125,140,170,0.1)' }; });
        Plotly.newPlot('chart-zscore', [{
          type: 'indicator', mode: 'gauge+number', value: zs.value,
          number: { font: { size: 30, color: cmap[zs.color] || C.blue } },
          gauge: {
            axis: { range: [0, axmax], tickcolor: '#8a93a5' },
            bar: { color: cmap[zs.color] || C.blue, thickness: 0.25 },
            steps: steps,
            threshold: { line: { color: '#fff', width: 3 }, thickness: 0.8, value: zs.value },
          },
        }], Object.assign({}, base, { margin: { t: 20, r: 24, b: 10, l: 24 } }), cfg);
      }
    } catch (e) {}

    // DCF intrinsic vs price
    try {
      var dcf = adv.valuation && adv.valuation.dcf && adv.valuation.dcf.chart;
      if (dcf && has('chart-dcf') && (num(dcf.intrinsic) != null || num(dcf.price) != null)) {
        Plotly.newPlot('chart-dcf', [{
          type: 'bar', orientation: 'h',
          y: ['当前股价', '每股内在价值'], x: [num(dcf.price) || 0, num(dcf.intrinsic) || 0],
          marker: { color: [C.gold, C.violet] },
          text: [num(dcf.price) != null ? dcf.price.toFixed(2) + '元' : '—',
                 num(dcf.intrinsic) != null ? dcf.intrinsic.toFixed(2) + '元' : '—'],
          textposition: 'auto',
        }], Object.assign({}, base, {
          xaxis: Object.assign({}, base.xaxis, { title: '元/股' }), margin: { t: 20, r: 20, b: 40, l: 90 },
        }), cfg);
      }
    } catch (e) {}

    // Relative valuation radar (normalized to comparable scale)
    try {
      var rel = adv.valuation && adv.valuation.relative && adv.valuation.relative.chart;
      if (rel && has('chart-relative') && rel.company) {
        var caps = { PE: 50, PB: 8, PS: 12, PEG: 3 };
        var labels = rel.labels || [];
        var norm = rel.company.map(function (v, i) {
          var cap = caps[labels[i]] || 1;
          if (typeof v !== 'number' || !isFinite(v) || v < 0) return 0;
          return Math.min(v / cap * 100, 100);
        });
        var raw = rel.company.map(function (v) { return (typeof v === 'number' && isFinite(v)) ? v.toFixed(2) : '—'; });
        Plotly.newPlot('chart-relative', [{
          type: 'scatterpolar', r: norm.concat([norm[0]]), theta: labels.concat([labels[0]]),
          fill: 'toself', name: '估值水平', fillcolor: 'rgba(111,75,216,0.18)', line: { color: C.violet },
          text: raw.concat([raw[0]]), hovertemplate: '%{theta}: %{text}<extra></extra>',
        }], Object.assign({}, base, {
          polar: {
            radialaxis: { visible: true, range: [0, 100], showticklabels: false, gridcolor: 'rgba(125,140,170,0.2)' },
            angularaxis: { gridcolor: 'rgba(125,140,170,0.2)' }, bgcolor: 'transparent',
          },
          margin: { t: 30, r: 40, b: 30, l: 40 },
        }), cfg);
      }
    } catch (e) {}

    // EVA waterfall
    try {
      var eva = adv.strategy && adv.strategy.eva && adv.strategy.eva.chart;
      if (eva && has('chart-eva')) {
        Plotly.newPlot('chart-eva', [{
          type: 'waterfall', orientation: 'v',
          x: ['NOPAT', '资本成本', 'EVA'],
          measure: ['relative', 'relative', 'total'],
          y: [num(eva.nopat) || 0, -(num(eva.capital_charge) || 0), num(eva.eva) || 0],
          connector: { line: { color: 'rgba(125,140,170,0.3)' } },
          increasing: { marker: { color: C.green } },
          decreasing: { marker: { color: C.orange } },
          totals: { marker: { color: (num(eva.eva) || 0) >= 0 ? C.green : C.red } },
        }], Object.assign({}, base, { yaxis: Object.assign({}, base.yaxis, { title: '亿元' }) }), cfg);
      }
    } catch (e) {}
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', draw);
  } else {
    draw();
  }
})();
