import pytest

from huawei_mcp import server


async def test_tools_read_only():
    tools = await server.mcp.list_tools()
    assert {t.name for t in tools} == {"get_daily_activity", "get_daily_health", "get_workouts", "get_body_composition",
                                       "get_devices", "get_login_status"}
    for t in tools:
        assert t.annotations.readOnlyHint is True and t.annotations.destructiveHint is False


async def test_filter_tools():
    from mcp.server.fastmcp import FastMCP
    srv = FastMCP("t")
    for name in ("a", "b", "c"):
        srv.add_tool(lambda: "", name=name)
    server.filter_tools(srv, "")
    assert len(await srv.list_tools()) == 3
    server.filter_tools(srv, " a, c ,")
    assert {t.name for t in await srv.list_tools()} == {"a", "c"}
    with pytest.raises(SystemExit, match="typo"):
        server.filter_tools(srv, "a,typo")


def test_date_range():
    server.date_range("2026-01-01", "2026-12-31")
    for a, b, m in [("2026-02-01", "2026-01-01", 366), ("2026-01-01", "2026-04-01", 62), ("1.1.2026", "2026-01-02", 10)]:
        with pytest.raises(ValueError):
            server.date_range(a, b, m)


async def test_unknown_metric():
    with pytest.raises(ValueError, match="metrics"):
        await server.get_daily_health("2026-01-01", "2026-01-02", ["weight"])


async def test_daily_activity_drops_empty_days(monkeypatch):
    async def fake_stat(a, b):
        return {"sportStat": [
            {"recordDay": 20261007, "sportType": 0, "sportBasicInfo": {"steps": 0, "calorie": 0, "duration": 0},
             "goalAchieveBasic": {"stepGoalValueStat": 10000}},
            {"recordDay": 20261010, "sportType": 0, "sportBasicInfo": {"steps": 4321, "distance": 3000, "calorie": 150000},
             "goalAchieveBasic": {"stepGoalValueStat": 10000}}]}
    monkeypatch.setattr(server.client, "sports_stat", fake_stat)
    out = await server.get_daily_activity("2026-10-07", "2026-10-10")
    assert out == '[{"date":"2026-10-10","steps":4321,"kcal":150.0,"distance_km":3.0,"step_goal":10000}]'
