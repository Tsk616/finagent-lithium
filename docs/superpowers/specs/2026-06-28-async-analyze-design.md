# 异步分析改造设计 (Async /analyze)

**日期**: 2026-06-28
**状态**: 设计已批准，待实施
**目标**: 消除 `/analyze` 同步长请求导致的 gunicorn 超时 502，并提供真实分阶段进度反馈。

## 背景与问题

当前 `/analyze` 是整页表单提交：浏览器 POST 后阻塞等待，服务器在单个请求内**同步串行**执行整条 8 节点流水线，期间发起约 7 个 DeepSeek LLM 调用 + 约 8 个 Wind 调用。最坏情况总耗时突破 gunicorn `--timeout`，sync worker 被 SIGKILL，Render 代理返回 **502**。

已先行通过配置缓解（`--workers 1 --threads 4 --worker-class gthread --timeout 300`），但架构上"一个请求同步跑几分钟"未变。本设计做异步化，从根上解决。

`_loading.html` 现有 5 个步骤是**假进度**（`workbench.js` 用 `setInterval` 机械点亮），本次接到真实进度。

## 方案选型

采用**方案 A：进程内后台线程 + 内存任务表 + 前端轮询**。

- 否决方案 B（Celery/RQ + Redis）：免费套餐只能跑单个服务进程、Redis 需付费，过度设计。
- 否决方案 C（SSE 流式）：Render 代理可能切断长连接、gthread 整段占线程、断线重连复杂，比轮询脆弱。

## 范围

**纳入**：用户经表单触发的 `/analyze` 主分析路径异步化。
**不纳入**：`/demo`（GET，同样有 502 风险，但本次不改）；任务持久化/重启恢复（采用尽力而为，丢失则用户重试）。

## 鲁棒性约定

- 任务状态**只存内存**。实例重启（部署/休眠/OOM）会丢任务，前端轮询到 404 时提示"任务丢失，请重试"。
- 免费实例磁盘也是临时的，持久化收益不大，故 YAGNI。

## 架构与组件

### 1. `web/shared_state.py` — 新增任务表

```python
import threading

JOBS: dict = {}          # job_id -> JobRecord
JOBS_LOCK = threading.Lock()
```

JobRecord 字段：
- `status`: `"running" | "done" | "error"`
- `step`: int 0–5（当前阶段索引）
- `label`: str（当前阶段中文标签）
- `report_id`: str | None（完成后写入）
- `error`: str | None（出错时写入）
- `created_at`: float（time.time()，用于清理）

提供辅助函数：
- `create_job() -> job_id`（生成 12 位 id，写入 status=running, step=0）
- `update_job(job_id, **fields)`（加锁更新）
- `get_job(job_id) -> dict | None`
- `prune_jobs(max_age_seconds=3600)`（清理过期任务，每次 create 时调用）

并发计数：`update_job`/`create_job` 通过 `JOBS_LOCK` 保护。当前 `status=="running"` 的任务数 ≥ `MAX_CONCURRENT`(=2) 时，`create_job` 返回 None，路由层据此回 429。

### 2. `web/workflow.py` — `run_pipeline` 加进度回调

签名改为 `run_pipeline(..., progress_cb=None)`，向后兼容（默认 None = no-op）。

内部定义 `_emit(step, label)`，仅当 `progress_cb` 非空时调用。在 5 个节点边界插入：

| step | label | 插入位置 |
|------|-------|---------|
| 1 | 识别公司与行业赛道 | classify_sector 前 |
| 2 | 校验财务数据 | validate_data 前 |
| 3 | 计算指标与评分 | calculate_general 前 |
| 4 | 异常扫描与行业对标 | linkage_analysis 前 |
| 5 | 生成评分与报告 | generate_report 前 |

仅新增约 5 行回调，不改动节点逻辑。

### 3. `web/routes/analysis.py` — 新增端点 + 抽取解析

抽取 helper `_parse_analyze_input(request) -> dict`：把现有 `/analyze` 里文件上传/表单/JSON 解析逻辑提取出来，新旧端点共用，避免重复。返回的 dict 键与 `run_pipeline` 参数对齐（company_name/stock_code/current_period/primary_business/manual_sector/financial_data/notes_data）。

**关键约束**：`_parse_analyze_input` 必须在 `/start` 的请求线程内完整执行（含文件上传读取、pandas 解析、临时文件清理），产出纯数据 dict。后台线程**只接收 dict，不再访问 `request`**——Flask 的 `request` 是线程局部对象，在后台线程不可用。

新增端点：

- `POST /api/analyze/start`
  1. `_parse_analyze_input(request)` 得到 company_name/stock_code/financial_data 等
  2. `prune_jobs()` → `create_job()`；若返回 None → `429 {"status":"busy","message":"服务繁忙，请稍候重试"}`
  3. 起 `threading.Thread(target=_run_job, args=(job_id, parsed))`，`daemon=True`，启动
  4. 返回 `{"job_id": job_id}`

- `GET /api/analyze/status/<job_id>`
  - `get_job(job_id)`；None → `404 {"status":"not_found"}`
  - 否则返回 `{status, step, label, report_id, error}`

后台 worker `_run_job(job_id, parsed)`：
```python
def _run_job(job_id, parsed):
    try:
        def cb(step, label):
            update_job(job_id, step=step, label=label)
        state = run_pipeline(**parsed, progress_cb=cb)
        report_id = save_report_state(state)
        update_job(job_id, status="done", step=5, report_id=report_id)
    except Exception as e:
        import traceback; traceback.print_exc()
        update_job(job_id, status="error", error=str(e))
```
**后台线程不调用 render_template**（避免 Flask 上下文/线程安全问题）；渲染交给完成后跳转的 `/report/<report_id>`。

现有 `/analyze`（整页）保留不动作为无 JS 兜底；现有 `/api/analyze` 保留不动。

## 数据流

```
浏览器提交表单 (JS 拦截 submit)
  → fetch POST /api/analyze/start  → {job_id}
  → 显示 loading 遮罩
  → 每 2s: GET /api/analyze/status/<job_id>
       running → 进度条点亮到 step N（label 写入 loadingSub）
       done    → location.href = "/report/<report_id>"
       error   → 显示 error，恢复提交按钮
       404     → "任务丢失，请重试"，恢复按钮
  → 轮询累计 > 5 分钟仍未 done → "分析超时，请重试"
```

## 前端改动（`web/static/js/workbench.js`）

- `validateAndLoad()`：校验通过后**不再让表单原生提交**，改为 `e.preventDefault()` + 调用新的 `startAsyncAnalyze()`。
- 新增 `startAsyncAnalyze()`：用 `FormData` POST 到 `/api/analyze/start`，拿 job_id 后启动 `pollStatus(job_id)`。
- 删除 `startLoading()` 里基于 `setInterval` 的假步进；改由 `pollStatus` 用真实 `step` 驱动现有 `step1..step5` 的 `active/done` class。
- `index.html` 表单：`onsubmit="return validateAndLoad()"` 保持；JS 内部改为异步提交。

`_loading.html` 与 CSS 无需改动（沿用现有 5 步结构与 dot/active/done 样式）。

## 错误处理

| 场景 | 处理 |
|------|------|
| 输入校验失败 | 前端 `showError`，不发请求 |
| 超并发 | `/start` 返回 429，前端提示"服务繁忙" |
| 流水线抛异常 | 线程写 status=error，前端显示 error 文本，恢复按钮 |
| 实例重启丢任务 | `/status` 返回 404，前端提示"任务丢失，请重试" |
| 轮询超时(>5min) | 前端停止轮询，提示重试 |

## 测试

- **单元**：`run_pipeline(progress_cb=cb)` 用空 api_key + 无 stock_code 走快路径（免网络），断言 `cb` 按 step 1→5 顺序被调用。
- **单元**：shared_state 任务表 — `create_job`/`update_job`/`get_job` 状态机；`MAX_CONCURRENT` 满时 `create_job` 返回 None；`prune_jobs` 清理过期。
- **路由**：`/api/analyze/start` 返回 job_id；并发满返回 429；`/api/analyze/status/<id>` 返回状态；未知 id 返回 404。
- **回归**：现有 `/analyze`、`/api/analyze` 测试仍通过（`progress_cb` 可选保证签名兼容）。

## 部署注意

- 无需新增依赖（`threading` 标准库；gthread 内置于 gunicorn）。
- 已应用的 gunicorn 配置（1 worker + 4 threads + timeout 300）是本方案的运行前提：单 worker 保证所有线程共享同一 `JOBS` 字典；4 线程让轮询与后台分析并发。
- `MAX_CONCURRENT=2` 配合 1 worker，避免多个 pandas 流水线同时 OOM。
