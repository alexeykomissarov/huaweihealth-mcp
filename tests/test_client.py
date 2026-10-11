import json
import os
import time

import httpx
import pytest

from huawei_mcp import protocol as p
from huawei_mcp.client import AuthRequired, HuaweiClient, HuaweiError, Tokens


def fresh(tmp_path, **kw):
    t = Tokens(access_token="A1", refresh_token="R", uid="U", expires_at=time.time() + 3600,
               refresh_expires_at=time.time() + 1e6, region="ru", **kw)
    t.save(tmp_path / "tokens.json")
    return tmp_path / "tokens.json"


async def test_exchange_code_saves_private_file(tmp_path):
    def h(req):
        assert str(req.url) == p.REGIONS["ru"]["session"] + p.OBTAIN_PATH
        assert json.loads(req.content) == {"authorizationCode": "C", "appId": "10414141"}
        return httpx.Response(200, json={"resultCode": 0, "accessToken": "A", "refreshToken": "R", "uid": "U",
                                         "accessTokenExpireTime": str(int((time.time() + 1800) * 1000))})

    c = HuaweiClient(tmp_path / "t.json", transport=httpx.MockTransport(h))
    t = await c.exchange_code("C", "ru")
    assert (t.access_token, t.refresh_token, t.uid) == ("A", "R", "U")
    assert oct(os.stat(tmp_path / "t.json").st_mode & 0o777) == "0o600"
    assert Tokens.load(tmp_path / "t.json").refresh_token == "R"


async def test_exchange_rejected_code(tmp_path):
    h = lambda req: httpx.Response(200, json={"resultCode": 20020001, "resultDesc": "invalid code"})
    c = HuaweiClient(tmp_path / "t.json", transport=httpx.MockTransport(h))
    with pytest.raises(AuthRequired):
        await c.exchange_code("C", "ru")


async def test_token_sent_three_ways(tmp_path):
    def h(req):
        body = json.loads(req.content)
        assert body["token"] == "A1" and body["tokenType"] == 2 and body["appId"] == "com.huawei.health"
        assert req.headers["Authorization"] == "Bearer A1" and req.headers["x-token"] == "A1"
        assert req.headers["x-huid"] == "U" and body["startTime"] == 20261005
        return httpx.Response(200, json={"resultCode": 0, "sportStat": []})

    c = HuaweiClient(fresh(tmp_path), transport=httpx.MockTransport(h))
    assert (await c.sports_stat(20261005, 20261005))["sportStat"] == []


async def test_retry_after_app_rotated_the_pair(tmp_path):
    calls = []

    def h(req):
        calls.append(req.url.path)
        if req.url.path == p.REFRESH_PATH:
            return httpx.Response(200, json={"resultCode": 0, "accessToken": "A2", "refreshToken": "R"})
        token = json.loads(req.content)["token"]
        return httpx.Response(200, json={"resultCode": 1004} if token == "A1" else {"resultCode": 0, "ok": 1})

    path = fresh(tmp_path)
    c = HuaweiClient(path, transport=httpx.MockTransport(h))
    assert (await c.sports_stat(1, 1))["ok"] == 1
    assert calls == [p.READ_PATHS["sports_stat"], p.REFRESH_PATH, p.READ_PATHS["sports_stat"]]
    assert Tokens.load(path).access_token == "A2"      # новая пара сразу на диске


async def test_rotates_before_expiry(tmp_path):
    calls = []

    def h(req):
        calls.append(req.url.path)
        if req.url.path == p.REFRESH_PATH:
            return httpx.Response(200, json={"resultCode": 0, "accessToken": "A2"})
        return httpx.Response(200, json={"resultCode": 0})

    t = Tokens(access_token="A1", refresh_token="R", expires_at=time.time() + 30,
               refresh_expires_at=time.time() + 1e6)
    t.save(tmp_path / "t.json")
    c = HuaweiClient(tmp_path / "t.json", transport=httpx.MockTransport(h))
    await c.health_stat(1, 1, [9])
    assert calls[0] == p.REFRESH_PATH


async def test_fatal_and_api_errors(tmp_path):
    c = HuaweiClient(tmp_path / "missing.json", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(AuthRequired):
        await c.sports_stat(1, 1)
    c = HuaweiClient(fresh(tmp_path), transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"resultCode": 20020003, "resultDesc": "rt invalid"})))
    with pytest.raises(AuthRequired):
        await c.sports_stat(1, 1)
    c = HuaweiClient(fresh(tmp_path), transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"resultCode": 1001, "resultDesc": "range too wide"})))
    with pytest.raises(HuaweiError, match="1001"):
        await c.sports_stat(1, 1)


async def test_by_time_dedups_across_windows(tmp_path):
    def h(req):
        return httpx.Response(200, json={"resultCode": 0, "data": {"x": [{"dataId": 1}, {"dataId": 2}]}})

    c = HuaweiClient(fresh(tmp_path), transport=httpx.MockTransport(h))
    recs = await c.by_time(lambda a, b: c.sports_detail(a, b, (4,)), [(1, 1), (2, 2)])
    assert [r["dataId"] for r in recs] == [1, 2]
