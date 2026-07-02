# CLAUDE.md — FinAgent-Lithium 项目说明

> 仅本项目生效。锂电行业财报**横向截面分析**系统：上传/抓取财报 → 识别赛道 → 计算通用+赛道专属指标 → 联动诊断/异常扫描 → 估值与六维趋势预测 → 生成可视化报告。

## 技术栈

- **工作流**：纯 Python 串行流水线（8 节点 + 额外增强步骤），不依赖 LangGraph
- **LLM**：DeepSeek（`api.deepseek.com`，OpenAI 兼容 `/chat/completions`；含 `/anthropic` 时走 Messages API）。仅用于判断/起草/叙述（联动诊断、报告生成、追问、宏观兜底、哈佛框架、预测情景解读）
- **数据源（优先级）**：Wind MCP → MX Data（东方财富妙想） → DeepSeek 补充
- **前端**：Flask + Jinja2 + Blueprint 模块化路由，暗色金融仪表盘主题，**完整版 Plotly**（非 basic，因需 gauge/waterfall/radar/scatterpolar）
- **数值计算**：纯 Python + numpy（确定性）。知识库 `lithium_knowledge_base.json` + 赋权 `metric_weight_config.json`
- **依赖**：`flask gunicorn requests numpy pandas openpyxl python-docx`
- **Python**：3.12

## 启动 / 构建 / 测试命令

```bash
# 本地开发（FLASK_DEBUG 默认 1）
python web/app.py                      # → http://localhost:5002

# 测试（部分 integration/historical/mx 测试联网会卡，离线只跑相关子集）
python3 -m pytest -q
python3 -m pytest tests/test_routes_async.py tests/test_prediction_model.py -q

# 生产（Render 自动用 Procfile / render.yaml 启动）
gunicorn web.app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --worker-class gthread --timeout 300 --graceful-timeout 30
```

- **部署**：GitHub `Tsk616/finagent-lithium`（remote origin，分支 `main`）→ push 自动触发 Render 部署
- **线上**：`https://finagent-lithium-jcl6.onrender.com`（免费实例，闲置 15min 休眠，512MB 内存）
- 关键环境变量：`DEEPSEEK_API_KEY`、`WIND_API_KEY`、`MX_APIKEY`（Render 上 sync:false 手填）；`WIND_ENABLE_LIVE`、`WIND_*_TIMEOUT_SECONDS`、`HISTORICAL_YEARS` 等见 `render.yaml`

## 目录结构

```
nodes/                 # 8 节点 + 数据/适配/计算/预测模块
  classify_sector.py   # 节点1 三级赛道识别（查表→关键词→Wind→手动）
  validate_data.py     # 节点2 AccountResolver 别名映射 + 完整度校验
  calculate_general.py # 节点3 通用指标 + 阈值方向推断
  calculate_sector.py  # 节点4 赛道公式求值器；_test_data_sample 提供宁德时代样例
  filter_key_indicators.py  # 节点5
  linkage_analysis.py  # 节点6 LLM 联动诊断
  anomaly_scan.py      # 节点7 规则引擎异常扫描
  generate_report.py   # 节点8 LLM + Python fallback 双路径
  llm_client.py        # DeepSeek 客户端（无 key 时返回 mock）
  data_extractor.py    # Excel/PDF 解析 + 单位检测
  wind_adapter.py      # Wind + MX Data 三级数据源；fetch_financials/market_data/peers
  historical_data.py   # 多年序列抓取 + 派生比率 + CAGR + 拐点
  metric_config.py     # load_metric_config 赛道权重（单一数据源）+ 赋权评分 + 行业对比
  interpretation.py    # 结构化解读（指标/宏观/行业基准/风险）
  advanced_models.py   # 四大板块 8 模型（杜邦/CVP/现金流/Z-score/DCF/相对估值/哈佛/EVA）
  prediction_model.py  # 六维趋势预测引擎（趋势外推+周期修正+蒙特卡洛）
web/
  app.py               # Flask 入口 + /health
  workflow.py          # run_pipeline() 编排全部节点；KB 在此加载
  template_data.py     # build_template_data 构建模板数据
  shared_state.py      # 内存报告历史 REPORT_STATES + 异步任务表 JOBS
  routes/              # analysis(主+异步start/status) / followup / peers / history
  templates/           # index/report/compare + partials/_*.html
  static/              # style.css, js/workbench.js, js/report.js
tests/                 # ~14 测试文件
lithium_kb.py, excel_to_json.py, lithium_knowledge_base.json, metric_weight_config.json
Procfile, render.yaml, requirements.txt, docs/
```

## 业务逻辑（核心数据流）

```
START → classify_sector → validate_data → calculate_general → calculate_sector
      → filter_key_indicators → linkage_analysis(LLM) → anomaly_scan
      → generate_report(LLM) → END
```

流水线之外的增强步骤（均 graceful degradation）：
1. **Wind 赛道增强**：仅股票代码时用 Wind 取公司信息/关键词辅助赛道识别
2. **Wind 财务增强**：财务数据稀疏时用 Wind 补全
3. **DeepSeek 补缺**：扣非净利润/经营现金流等关键科目缺失时 LLM 补
4. **历史数据**：`fetch_historical_periods` 取 3-5 年序列（供趋势图/六维预测/杜邦DCF）
5. **市值/同行/宏观**：Wind 取股价市值、同行财务、锂价趋势；不可用时 DeepSeek 宏观兜底
6. **metric_config 赋权评分 + 行业对比**
7. **结构化解读 interpretation**
8. **advanced_models**：四大方法论 8 模型（数值纯 Python，仅哈佛用 LLM）
9. **prediction_model**：六维（盈利/运营/成长/偿债/现金/综合）未来一年预测 + 三情景 + 蒙特卡洛 + LLM 情景解读

**异步执行**：`POST /api/analyze/start` 起 daemon 线程跑 run_pipeline → 返回 job_id；前端 2s 轮询 `/api/analyze/status/<id>` → 完成跳 `/report/<id>`。`run_pipeline(progress_cb=)` 上报 5 阶段。`JOBS` 内存任务表（MAX_CONCURRENT=2，单 worker 共享）。

**报告渲染**：`report.html` include 各 `partials/_*.html`；图表数据塞进 `<script type="application/json">` 标签，`report.js` 读取后用完整版 Plotly 渲染。新增模块照搬此范式（state 产出 → template_data 透传 `x`+`has_x` → partial + report.js 图）。

## 历史 Bug（避免重犯）

1. **别名冲突**：canonical self-mapping 必须优先于 alias，否则被覆盖（lithium_kb / validate_data）
2. **阈值方向**：超区间不等于高风险，需 `_infer_higher_better` 推断方向（calculate_general）
3. **公式重复 token**：用全局 replace + 按 token 长度降序（calculate_sector）
4. **单位检测**："亿元" 含 "元"，须先匹配完整单位（data_extractor）
5. **期末科目缺失**：模糊前缀匹配推导
6. **多源 fallback 链路**：5 个链式 bug（wind_adapter）
7. **Py3.12 `RiskLevel(str,Enum)` 显示泄漏**：用 `.value`，禁 `str(member)`
8. **502 / ERR_CONNECTION_CLOSED**：同步长请求被 Render 代理掐断 → 异步化（提交 526433c）+ gunicorn 单 worker/300s（b13164a）
9. **Jinja `dict.values` 陷阱**：模板对 dict 一律用 `obj["key"]` 下标，禁 `obj.values/.items/.keys`（致含数据报告 500，edcca5b）
10. **report.js 语法错误**：多余 `}` 致整段脚本 SyntaxError、所有图表空白（线上也坏）；标题 `::before` 圆点是 flex 子项 + space-between 把标题挤右（110d34e）

## 关键开发决策

- **复用 `metric_weight_config.json` 权重**，不引入预测模型自带的重复 `TRACK_WEIGHTS_LIBRARY`（单一数据源）
- **数值用代码、模型只做判断/叙述**：六维/估值数值纯 Python+numpy（蒙特卡洛固定随机种子可复现），仅情景文字/哈佛/联动用 LLM
- **异步 + 轮询** 是免费 PaaS 上长请求的唯一正解，配置调优只是缓解
- **gunicorn 单 worker + gthread**：省内存（512MB）、线程共享内存态、I/O 密集靠线程并发
- **周期指数 `LITHIUM_CYCLE_INDEX` 目前静态**（TODO：接实时锂价趋势动态判断周期阶段）
- 调试图表/JS 全空白：先 `node --check report.js` 验 JS 合法，再用无头 Edge `--dump-dom`/`--enable-logging=stderr` 查渲染与 console 错误

## 用户编码偏好（本项目）

代码注释用中文；输出精简不解释基础语法；优先定向修复不重写；匹配既有代码风格（本项目 Python **snake_case**）。
