from datetime import date
from zoneinfo import ZoneInfo

from huawei_mcp import protocol as p

MSK = ZoneInfo("Europe/Moscow")
CODE = "EQFEAQNaOARz0K1p+mR7sT4u/klMN=="
ENCODED = "EQFEAQNaOARz0K1p%2BmR7sT4u%2FklMN%3D%3D"


def test_authorization_url():
    url = p.authorization_url()
    assert url.startswith(p.OAUTH_AUTHORIZE + "?")
    assert "client_id=10414141" in url and "redirect_uri=hms%3A%2F%2Fredirect_url" in url
    assert "access_type=offline" in url and "display=touch" in url


def test_code_from_console_line_address_and_bare_code():
    line = f"Failed to launch 'hms://redirect_url?code={ENCODED}&state=9f2c1ab7' because the scheme does not have a registered handler."
    assert p.authorization_code_from(line) == CODE
    assert p.authorization_code_from(f"hms://redirect_url?code={ENCODED}&state=x") == CODE
    assert p.authorization_code_from(ENCODED) == CODE
    assert p.authorization_code_from("") is None
    assert p.authorization_code_from("Failed to launch 'hms://redirect_url?state=x'") is None


def test_windows():
    assert list(p.windows(date(2026, 1, 1), date(2026, 1, 25), 10)) == [
        (20260101, 20260110), (20260111, 20260120), (20260121, 20260125)]
    assert list(p.windows(date(2026, 1, 1), date(2026, 1, 1), 10)) == [(20260101, 20260101)]


def test_normalize_day():
    assert p.normalize_day(20261005) == date(2026, 10, 5)
    assert p.normalize_day("2026-10-05T00:00:00") == date(2026, 10, 5)
    assert p.normalize_day(1791234000000, MSK) == date(2026, 10, 6)   # 2026-10-06 00:00 MSK
    assert p.normalize_day("garbage") is None


def test_daily_activity_prefers_day_aggregate():
    rows = [
        {"recordDay": 20261005, "sportType": 4, "sportBasicInfo": {"steps": 9000, "distance": 7000, "calorie": 400000}},
        {"recordDay": 20261005, "sportType": 0, "sportBasicInfo": {"steps": 12345, "distance": 9100, "calorie": 512345, "duration": 75},
         "exerciseTimeBasic": {"totalMidHighIntensity": 42}, "activeHourBasic": {"countActiveHour": 9}},
    ]
    d = p.daily_activity(rows)["2026-10-05"]
    assert (d["steps"], d["distance_m"], d["kcal"], d["active_min"], d["intensity_min"], d["active_hours"]) == \
        (12345, 9100, 512.3, 75, 42, 9)


def test_health_by_day_drops_unmeasured_and_localizes_sleep():
    payload = {
        "heartRateTotal": [{"recordDay": 20261005, "heartRateBasic": {"lastRestHeartRate": 55, "maxHeartRate": 150, "minHeartRate": -1}}],
        "professionalSleepTotal": [{"recordDay": 20261005, "professionalSleep": {
            "allSleepTime": 437, "deepSleepTime": 80, "dreamTime": 0, "sleepScore": 77, "lastAvgHrv": 41.2,
            "fallAsleepTime": 1791140400000, "wakeupTime": 1791170100000}}],
        "stressTotal": [{"recordDay": 20261005, "stressBasic": {"meanScore": 0}}],
    }
    d = p.health_by_day(payload, MSK)["2026-10-05"]
    assert d["heart_rate"] == {"resting": 55, "max": 150}                 # -1 отброшен
    assert d["sleep"]["total_min"] == 437 and "rem_min" not in d["sleep"]  # 0 отброшен
    assert d["sleep"]["hrv_ms"] == 41.2
    assert d["sleep"]["fell_asleep"] == "2026-10-04 22:00" and d["sleep"]["woke_up"] == "2026-10-05 06:15"
    assert "stress" not in d


def test_sport_sessions_dedup_devices_and_stitch_minutes():
    t0 = 1791190800000  # 2026-10-05 12:00 MSK
    minute = 60000

    def seg(i, dist, steps, device, sport=4):
        return {"sportType": sport, "startTime": t0 + i * minute, "endTime": t0 + (i + 1) * minute, "deviceCode": device,
                "sportBasicInfos": [{"distance": dist, "steps": steps, "calorie": 12000, "duration": 1}]}

    records = [seg(i, 200, 170, "watch") for i in range(30)]
    records += [seg(i, 150, 160, "phone") for i in range(30)]          # копия с телефона — меньше
    records += [seg(60 + i, 200, 170, "watch") for i in range(5)]       # через 30 мин — новая сессия
    records += [seg(100, 5000, 0, "watch", sport=3)]                    # велосипед
    s = p.sport_sessions(records, MSK)
    assert [(x["sport"], x["duration_min"], x["distance_km"]) for x in s] == [
        ("running", 30, 6.0), ("running", 5, 1.0), ("cycling", 1, 5.0)]
    assert s[0]["start"] == "2026-10-05 12:00" and s[0]["end"] == "2026-10-05 12:30"
    assert s[0]["kcal"] == 360.0 and s[0]["device_code"] == "watch"
