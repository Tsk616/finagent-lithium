# FinAgent-Lithium API 文档

## 端点总览

| Method | Path | Blueprint | 响应格式 | 说明 |
|--------|------|-----------|----------|------|
| GET | `/` | analysis | HTML | 首页（数据输入表单） |
| GET | `/health` | app | JSON | 健康检查 |
| GET | `/history` | analysis | HTML | 首页（历史面板激活） |
| GET | `/ask` | analysis | HTML | 首页（问答面板激活） |
| POST | `/analyze` | analysis | HTML | 运行完整分析流水线 |
| POST | `/api/analyze` | analysis | JSON | API：原始 AnalysisState |
| POST | `/api/report.md` | analysis | Markdown 文件 | 下载 Markdown 报告 |
| GET | `/demo` | analysis | HTML | 宁德时代 Demo |
| GET | `/report/<id>` | history | HTML | 查看历史报告 |
| GET | `/api/history` | history | JSON | 最近报告列表 |
| POST | `/api/ask` | followup | JSON | 追问问答 |
| POST | `/compare` | peers | HTML | 文件同业对比 |
| POST | `/api/peer-compare` | peers | JSON | Wind 同业对比 |

---

## 详细说明

### GET `/`

首页，显示数据输入表单。

**响应**：HTML（`index.html`，`initial_panel="upload"`）

```bash
curl http://localhost:5002/
```

---

### GET `/health`

健康检查端点，用于云平台监控。

**响应**：JSON

```json
{
  "status": "ok",
  "kb_loaded": true
}
```

`status` 为 `"ok"`（知识库已加载）或 `"degraded"`（知识库缺失）。

```bash
curl http://localhost:5002/health
```

---

### GET `/history`

与 `/` 相同的页面，但自动展开历史报告面板。

**响应**：HTML（`index.html`，`initial_panel="history"`）

---

### GET `/ask`

与 `/` 相同的页面，但自动展开追问问答面板。

**响应**：HTML（`index.html`，`initial_panel="ask"`）

---

### POST `/analyze`

运行完整 8 节点分析流水线，返回渲染后的报告页面。

**请求格式**：`multipart/form-data` 或 `application/json`

#### Form Data 模式

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `company_name` | string | 否 | 公司名称 |
| `stock_code` | string | 否 | 股票代码（如 `300750`） |
| `current_period` | string | 否 | 报告期（如 `2025年报`） |
| `primary_business` | string | 否 | 主营业务描述，换行分隔 |
| `manual_sector` | string | 否 | 手动指定赛道代码 |
| `financial_data_json` | string | 否 | 财务数据 JSON 字符串 |
| `file` | file | 否 | 上传的财报文件（.xlsx / .pdf） |

#### JSON 模式

```json
{
  "company_name": "宁德时代",
  "stock_code": "300750",
  "current_period": "2025年报",
  "primary_business": ["动力电池系统", "储能系统"],
  "financial_data": {
    "营业收入": 360000000000,
    "营业成本": 280000000000,
    "净利润": 48000000000
  }
}
```

**响应**：HTML（`report.html`），分析失败时返回带错误信息的 `index.html`。

```bash
# 文件上传
curl -X POST http://localhost:5002/analyze \
  -F "company_name=宁德时代" \
  -F "stock_code=300750" \
  -F "current_period=2025年报" \
  -F "file=@宁德时代2025年报.xlsx"

# JSON 模式
curl -X POST http://localhost:5002/analyze \
  -H "Content-Type: application/json" \
  -d '{"company_name":"宁德时代","stock_code":"300750","financial_data":{"营业收入":360000000000}}'
```

---

### POST `/api/analyze`

API 端点，返回原始 AnalysisState JSON（不渲染 HTML）。

**请求格式**：`application/json`

```json
{
  "company_name": "宁德时代",
  "stock_code": "300750",
  "current_period": "2025年报",
  "primary_business": ["动力电池系统"],
  "manual_sector": null,
  "financial_data": {"营业收入": 360000000000},
  "notes_data": {}
}
```

**响应**：JSON（完整 AnalysisState 字典，含 `report_id`）

```bash
curl -X POST http://localhost:5002/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"company_name":"宁德时代","stock_code":"300750","financial_data":{"营业收入":360000000000}}'
```

**错误响应**（500）：

```json
{
  "status": "error",
  "message": "error description"
}
```

---

### POST `/api/report.md`

运行分析流水线并返回 Markdown 格式报告文件下载。

**请求格式**：`application/json`（与 `/api/analyze` 相同）

**响应**：`text/markdown` 文件下载，文件名为 `{company_name}_analysis.md`

```bash
curl -X POST http://localhost:5002/api/report.md \
  -H "Content-Type: application/json" \
  -d '{"company_name":"宁德时代","financial_data":{"营业收入":360000000000}}' \
  -o report.md
```

---

### GET `/demo`

使用宁德时代（300750.SZ）数据运行 Demo 分析。优先从 Wind 拉取实时数据，不可用时使用内置样本数据。

**查询参数**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `period` | string | `2025年报` | 报告期 |

**响应**：HTML（`report.html`）

```bash
curl http://localhost:5002/demo
curl http://localhost:5002/demo?period=2024年报
```

---

### GET `/report/<report_id>`

查看内存中保存的历史报告。

**路径参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `report_id` | string | 12 位 UUID 短串 |

**响应**：HTML（`report.html`），报告不存在或服务已重启时返回 404 页面。

```bash
curl http://localhost:5002/report/a1b2c3d4e5f6
```

---

### GET `/api/history`

返回最近 20 条分析报告摘要。

**响应**：JSON

```json
{
  "items": [
    {
      "report_id": "a1b2c3d4e5f6",
      "company_name": "宁德时代",
      "stock_code": "300750",
      "current_period": "2025年报",
      "generated_at": "2026-06-26 14:30:00",
      "data_completeness": 85,
      "weighted_score": 72.5,
      "sector": "动力电池"
    }
  ]
}
```

```bash
curl http://localhost:5002/api/history
```

---

### POST `/api/ask`

基于已生成报告的追问问答。不重新运行分析流水线，从内存中读取报告状态。

**请求格式**：`application/json`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `report_id` | string | 是 | 目标报告 ID |
| `question` | string | 是 | 用户问题 |
| `conversation` | array | 否 | 对话历史（最多保留最近 3 轮） |

```json
{
  "report_id": "a1b2c3d4e5f6",
  "question": "这家公司的毛利率为什么下降了？",
  "conversation": [
    {"role": "user", "content": "综合评分怎么样？"},
    {"role": "assistant", "content": "综合评分为 72.5/100..."}
  ]
}
```

**响应**：JSON

```json
{
  "status": "ok",
  "answer": "根据报告数据，宁德时代销售毛利率为..."
}
```

**错误响应**：

- 400：`question` 缺失
- 404：`report_id` 不存在或已过期

```js
// 前端调用示例
const res = await fetch('/api/ask', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    report_id: currentReportId,
    question: '这家公司的现金流状况如何？'
  })
});
const data = await res.json();
console.log(data.answer);
```

---

### POST `/compare`

上传多份财报文件进行同业对比分析。

**请求格式**：`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `peer_files` | file[] | 是 | 至少 2 个财报文件，最多 8 个 |

**响应**：HTML（`compare.html`），包含指标对比表和雷达图数据。

对比指标：销售毛利率、扣非销售净利率、净利润现金含量、资产负债率、流动比率、速动比率。

```bash
curl -X POST http://localhost:5002/compare \
  -F "peer_files=@宁德时代2025.xlsx" \
  -F "peer_files=@比亚迪2025.xlsx" \
  -F "peer_files=@亿纬锂能2025.xlsx"
```

---

### POST `/api/peer-compare`

通过 Wind API 按股票代码拉取同业对比数据。

**请求格式**：`application/json`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `stock_codes` | string | 是 | 逗号分隔的股票代码（如 `300750,002594,300014`） |

**响应**：JSON

```json
{
  "status": "ok",
  "peers": [
    {
      "windcode": "300750.SZ",
      "name": "宁德时代",
      "revenue": "3600亿",
      "profit": "480亿",
      "assets": "8500亿",
      "equity": "3200亿",
      "gross_margin": "22.2%",
      "roe": "15.0%"
    }
  ]
}
```

**错误响应**：

- 400：`stock_codes` 缺失或为空
- 500：Wind API 调用失败

```js
// 前端调用示例
const res = await fetch('/api/peer-compare', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ stock_codes: '300750,002594,300014' })
});
const data = await res.json();
console.log(data.peers);
```
