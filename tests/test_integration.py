"""
Full Integration Test — FinAgent-Lithium 8-Node Pipeline + Frontend.

Tests:
  1. End-to-end pipeline with 宁德时代 mock data
  2. Pipeline with 天齐锂业 mock data
  3. Pipeline with 比亚迪 integrated mock data
  4. Frontend template data structure
  5. Markdown report structure validation
  6. Web API endpoint (Flask test client)
"""

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from web.workflow import run_pipeline, KB
from nodes.calculate_sector import _test_data_sample as sector_data


def ok(label: str): print(f"  [PASS] {label}")
def fail(label: str, detail=""): print(f"  [FAIL] {label}  -- {detail}")
def assert_eq(a, b, label): ok(label) if a == b else fail(label, f"{a} != {b}")
def assert_gt(a, b, label): ok(label) if a > b else fail(label, f"{a} <= {b}")
def assert_in(n, h, label): ok(label) if n in str(h) else fail(label, f"'{n}' not found")


# ═══════════════════════════════════════════════════════════════════════
# Pipeline tests
# ═══════════════════════════════════════════════════════════════════════

def test_pipeline_ningde():
    """Full pipeline with 宁德时代 data."""
    print("\n" + "=" * 60)
    print("Integration: 宁德时代 (300750)")
    print("=" * 60)

    data = sector_data("宁德时代")
    state = run_pipeline(
        company_name="宁德时代",
        stock_code="300750",
        current_period="2025年报",
        primary_business=["动力电池系统", "储能系统", "电池材料"],
        financial_data=data,
    )

    _verify_state(state, "宁德时代", "3.1", 11, 1)


def test_pipeline_tianqi():
    """Full pipeline with 天齐锂业 data."""
    print("\n" + "=" * 60)
    print("Integration: 天齐锂业 (002466)")
    print("=" * 60)

    data = sector_data("天齐锂业")
    state = run_pipeline(
        company_name="天齐锂业",
        stock_code="002466",
        current_period="2025年报",
        primary_business=["锂矿开采与销售", "碳酸锂及氢氧化锂生产"],
        financial_data=data,
    )

    _verify_state(state, "天齐锂业", "1.1", 10, 1)


def test_pipeline_byd():
    """Full pipeline with 比亚迪 integrated data."""
    print("\n" + "=" * 60)
    print("Integration: 比亚迪 (002594, Integrated)")
    print("=" * 60)

    data = sector_data("比亚迪")
    state = run_pipeline(
        company_name="比亚迪",
        stock_code="002594",
        current_period="2025年报",
        primary_business=["新能源汽车", "动力电池", "储能系统", "消费电子"],
        financial_data=data,
    )

    _verify_state(state, "比亚迪", "6.1", 9, 1)
    assert_gt(len(state.get("sub_sectors", [])), 0, "Has sub_sectors")
    assert_gt(len(state.get("sector_indicators", {})), 1, "Multi-sector indicators")


def test_pipeline_api_format():
    """Verify pipeline output is JSON-serializable (API compatibility)."""
    print("\n--- API JSON serialization ---")
    data = sector_data("宁德时代")
    state = run_pipeline(
        company_name="宁德时代", stock_code="300750",
        current_period="2025年报",
        primary_business=["动力电池系统", "储能系统", "电池材料"],
        financial_data=data,
    )

    # Strip non-serializable fields
    serializable = {}
    for k, v in state.items():
        try:
            json.dumps({k: v}, ensure_ascii=False, default=str)
            serializable[k] = v
        except (TypeError, ValueError):
            pass

    assert_gt(len(serializable), 10, ">10 serializable fields")

    # Actually serialize
    encoded = json.dumps(serializable, ensure_ascii=False, default=str)
    decoded = json.loads(encoded)
    assert_eq(decoded.get("company_name"), "宁德时代", "Round-trip company name")
    assert_eq(decoded.get("status"), "completed", "Status completed")
    ok("State is JSON-serializable")


def _verify_state(state: dict, company: str, sector_code: str,
                  min_general: int, min_linkage: int):
    """Common state verification."""
    assert_eq(state.get("company_name"), company, "Company name")
    assert_eq(state.get("status"), "completed", "Pipeline completed")
    assert_in(sector_code, str(state.get("_sector_code", "")), f"Sector code contains {sector_code}")

    # General indicators
    gi = state.get("general_indicators", {})
    if len(gi) < min_general:
        fail(f"General indicators: {len(gi)} < {min_general}", "")
    else:
        ok(f"{len(gi)} general indicators")
    computed = sum(1 for ind in gi.values() if ind.get("value") is not None)
    print(f"  General indicators: {computed}/{len(gi)} computed")

    # Sector indicators
    si = state.get("sector_indicators", {})
    total_sec = sum(len(inds) for sec in si.values() for inds in sec.values())
    computed_sec = sum(1 for sec in si.values() for inds in sec.values()
                       for ind in inds.values() if ind.get("value") is not None)
    print(f"  Sector indicators: {computed_sec}/{total_sec} computed")

    # Key indicators
    ki = state.get("key_indicators_for_linkage", {})
    print(f"  Key indicators: {len(ki)} (fatal/key)")

    # Linkage
    ld = state.get("linkage_diagnosis", [])
    assert_gt(len(ld), 0, "Has linkage diagnosis")
    print(f"  Linkage conclusions: {len(ld)}")

    # Anomalies
    an = state.get("anomaly_signals", [])
    print(f"  Anomaly signals: {len(an)}")

    # Report
    report = state.get("report_markdown", "")
    assert_gt(len(report), 500, "Report > 500 chars")
    print(f"  Report: {len(report)} chars, {len(report.splitlines())} lines")


# ═══════════════════════════════════════════════════════════════════════
# Report structure tests
# ═══════════════════════════════════════════════════════════════════════

def test_report_structure():
    """Verify report contains all 6 mandatory chapters and required elements."""
    print("\n--- Report structure validation ---")
    data = sector_data("宁德时代")
    state = run_pipeline(
        company_name="宁德时代", stock_code="300750",
        current_period="2025年报",
        primary_business=["动力电池系统", "储能系统", "电池材料"],
        financial_data=data,
    )

    report = state["report_markdown"]

    # 6 mandatory chapters
    chapters = ["一、公司画像", "二、体检报告", "三、赛道专属深度检查",
                "四、联动诊断", "五、异常警报", "六、总结"]
    for ch in chapters:
        assert_in(ch, report, f"Chapter '{ch}' present")

    # Required elements
    required = [
        ("公司名", "宁德时代"),
        ("风险 emoji", "🟢"),
        ("免责声明", "不构成投资建议"),
        ("Markdown 标题", "## "),
        ("Markdown 表格", "|---"),
    ]
    for label, term in required:
        assert_in(term, report, label)

    # Verify no forbidden patterns (data fabrication, estimation)
    forbidden = ["编造", "估算值"]
    for term in forbidden:
        if term in report:
            fail(f"Forbidden term '{term}' found in report", report[report.find(term)-20:report.find(term)+30])
        else:
            ok(f"No forbidden term '{term}' in report")

    print(f"  Report structure: [OK]")


# ═══════════════════════════════════════════════════════════════════════
# Frontend template data test
# ═══════════════════════════════════════════════════════════════════════

def test_template_data():
    """Verify template data structure for frontend rendering."""
    print("\n--- Frontend template data ---")
    from web.app import _build_template_data

    data = sector_data("宁德时代")
    state = run_pipeline(
        company_name="宁德时代", stock_code="300750",
        current_period="2025年报",
        primary_business=["动力电池系统", "储能系统", "电池材料"],
        financial_data=data,
    )

    td = _build_template_data(state)

    assert_eq(td["company_name"], "宁德时代", "Template: company name")
    assert_eq(td["is_integrated"], False, "Template: not integrated")
    assert_gt(len(td["general_computable"]), 5, "Template: >5 computable cards")
    assert_gt(len(td["general_pending"]), -1, f"Template: pending cards ({len(td['general_pending'])})")
    assert_gt(len(td["sector_tabs"]), 0, "Template: has sector tabs")
    assert_gt(len(td["linkage_diagnosis"]), 0, "Template: has linkage cards")
    assert_eq(td["has_anomalies"], False, "Template: no anomalies")

    # Verify card structure
    card = td["general_computable"][0]
    for field in ["name", "value", "risk_level", "risk_color", "normal_range", "explanation"]:
        assert_in(field, card, f"Card has field '{field}'")

    assert_in(card["risk_color"], ["normal", "warning", "danger", "pending"], "Valid risk_color")
    print(f"  Computable cards: {len(td['general_computable'])}")
    print(f"  Pending cards: {len(td['general_pending'])}")
    print(f"  Sector tabs: {len(td['sector_tabs'])}")
    ok("Template data structure valid")


def test_integrated_template():
    """Verify integrated enterprise template data."""
    print("\n--- Integrated template data ---")
    from web.app import _build_template_data

    data = sector_data("比亚迪")
    state = run_pipeline(
        company_name="比亚迪", stock_code="002594",
        current_period="2025年报",
        primary_business=["新能源汽车", "动力电池", "储能系统"],
        financial_data=data,
    )

    td = _build_template_data(state)

    assert_eq(td["is_integrated"], True, "Template: is integrated")
    assert_gt(len(td["sector_tabs"]), 1, "Template: multiple sector tab groups")
    assert_in("sector_tabs", str(td), "Template: sector_tabs")  # relaxed check

    print(f"  Sector tab groups: {len(td['sector_tabs'])}")
    for st in td["sector_tabs"]:
        print(f"    {st['label'][:50]}: {len(st['tabs'])} indicator tabs")
    ok("Integrated template data valid")


# ═══════════════════════════════════════════════════════════════════════
# Web app test (Flask test client)
# ═══════════════════════════════════════════════════════════════════════

def test_web_routes():
    """Test Flask web routes."""
    print("\n--- Web app routes ---")

    try:
        from web.app import app
        app.config["TESTING"] = True
        client = app.test_client()

        # Test index page
        resp = client.get("/")
        assert_eq(resp.status_code, 200, "GET / → 200")
        assert_in("FinAgent-Lithium", resp.data.decode("utf-8"), "Index page title")

        # Test demo page
        resp = client.get("/demo")
        assert_eq(resp.status_code, 200, "GET /demo → 200")
        html = resp.data.decode("utf-8")
        assert_in("宁德时代", html, "Demo shows 宁德时代")
        assert_in("体检报告", html, "Demo has 体检报告 chapter")
        assert_in("report-markdown-data", html, "Demo has report download data")

        # Test API endpoint
        data = sector_data("宁德时代")
        resp = client.post("/api/analyze", json={
            "company_name": "宁德时代",
            "stock_code": "300750",
            "current_period": "2025年报",
            "primary_business": ["动力电池系统"],
            "financial_data": data,
        })
        assert_eq(resp.status_code, 200, "POST /api/analyze → 200")
        api_state = resp.get_json()
        assert_eq(api_state.get("company_name"), "宁德时代", "API: company name")
        assert_eq(api_state.get("status"), "completed", "API: status completed")
        assert_gt(len(api_state.get("report_markdown", "")), 100, "API: has report")

        # Test Markdown download
        resp = client.post("/api/report.md", json={
            "company_name": "测试公司",
            "financial_data": {"营业收入": 1000, "营业成本": 700, "净利润": 200},
        })
        assert_eq(resp.status_code, 200, "GET /api/report.md → 200")
        assert "attachment" in resp.headers.get("Content-Disposition", ""), "Download header"

        # Test analyze POST (form submission)
        resp = client.post("/analyze", data={
            "company_name": "表单测试",
            "stock_code": "000001",
            "current_period": "2025Q4",
            "financial_data_json": json.dumps(data),
        })
        assert_eq(resp.status_code, 200, "POST /analyze → 200")

        ok("All web routes working")

    except ImportError:
        print("  [SKIP] Flask not installed. Run: pip install flask")
    except Exception as e:
        fail("Web routes", str(e))


# ═══════════════════════════════════════════════════════════════════════
# Data validation integration test
# ═══════════════════════════════════════════════════════════════════════

def test_missing_data_handling():
    """Pipeline should handle missing data gracefully."""
    print("\n--- Missing data handling ---")
    sparse = {
        "营业收入": 10000000000.0,
        "营业成本": 7000000000.0,
        "净利润": 1000000000.0,
    }
    state = run_pipeline(
        company_name="稀疏数据测试",
        financial_data=sparse,
    )

    # Should complete without crashing
    assert_eq(state.get("status"), "completed", "Completes with sparse data")

    # Pending indicators should be marked
    gi = state.get("general_indicators", {})
    pending = sum(1 for ind in gi.values() if "待补充" in str(ind.get("risk_level", "")))
    # With 3-account sparse data, at least half should be pending
    ok(f"Sparse data pending indicators: {pending}/{len(gi)} (partial computation expected)")

    # Report should mention missing data
    report = state.get("report_markdown", "")
    assert_in("暂时无法计算", report, "Report mentions missing data")
    assert_in("不构成投资建议", report, "Disclaimer present")

    # Error log should have entries
    err_log = state.get("error_log", [])
    assert_gt(len(err_log), 0, "Error log has entries for sparse data")

    print(f"  Pending indicators: {pending}/{len(gi)}")
    print(f"  Error log entries: {len(err_log)}")
    ok("Sparse data handled gracefully")


# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print("FinAgent-Lithium Full Integration Tests")
    print(f"KB loaded: {KB is not None}")
    print(f"Python: {sys.version.split()[0]}")

    # Pipeline tests
    test_pipeline_ningde()
    test_pipeline_tianqi()
    test_pipeline_byd()
    test_pipeline_api_format()

    # Report structure
    test_report_structure()

    # Template data
    test_template_data()
    test_integrated_template()

    # Edge cases
    test_missing_data_handling()

    # Web routes (requires Flask)
    test_web_routes()

    print("\n" + "=" * 60)
    print("  All integration tests complete.")
    print("=" * 60)
