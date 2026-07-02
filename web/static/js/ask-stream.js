/**
 * ask-stream.js — streaming follow-up Q&A client.
 *
 * POSTs to /api/ask/stream and delivers text deltas as they arrive
 * (fetch + ReadableStream; EventSource can't POST the conversation).
 * On 503/any failure it transparently falls back to the non-streaming
 * /api/ask so the answer still arrives, just without the typing effect.
 */

function streamAsk(payload, handlers) {
  var onDelta = handlers.onDelta || function() {};
  var onDone = handlers.onDone || function() {};
  var onError = handlers.onError || function() {};

  function fallbackAsk() {
    var controller = new AbortController();
    var timeoutId = setTimeout(function() { controller.abort(); }, 30000);
    fetch('/api/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
      signal: controller.signal
    }).then(function(resp) {
      clearTimeout(timeoutId);
      if (!resp.ok) throw new Error('服务器错误 (' + resp.status + ')');
      return resp.json();
    }).then(function(data) {
      var answer = data.answer || data.message || '未生成回答。';
      onDelta(answer);
      onDone(answer);
    }).catch(function(err) {
      onError(err.name === 'AbortError' ? '请求超时，请稍后重试。' : ('追问失败：' + err.message));
    });
  }

  fetch('/api/ask/stream', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  }).then(function(resp) {
    if (resp.status === 404) {
      return resp.json().then(function(data) {
        throw Object.assign(new Error(data.message || '报告已过期'), { noFallback: true });
      });
    }
    if (!resp.ok || !resp.body) { fallbackAsk(); return null; }

    var reader = resp.body.getReader();
    var decoder = new TextDecoder();
    var buf = '';
    var full = '';

    function pump() {
      return reader.read().then(function(step) {
        if (step.done) { onDone(full); return; }
        buf += decoder.decode(step.value, { stream: true });
        var frames = buf.split('\n\n');
        buf = frames.pop();  // keep incomplete tail
        for (var i = 0; i < frames.length; i++) {
          var line = frames[i].trim();
          if (line.indexOf('data:') !== 0) continue;
          try {
            var evt = JSON.parse(line.slice(5).trim());
            if (evt.delta) { full += evt.delta; onDelta(evt.delta); }
            if (evt.done) { onDone(full); return; }
          } catch (e) { /* skip malformed frame */ }
        }
        return pump();
      });
    }
    return pump();
  }).catch(function(err) {
    if (err && err.noFallback) onError(err.message);
    else fallbackAsk();  // network error on stream path -> try plain path once
  });
}
