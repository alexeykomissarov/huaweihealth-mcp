"""Формат записей type 8 (WEIGHT_BODYFAT_BROAD) — как в ответе облака для Scale 3 Pro; все значения синтетические."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from huawei_mcp import protocol as p
from huawei_mcp.client import HuaweiClient
from tests.test_client import fresh

MSK = ZoneInfo("Europe/Moscow")


def ms(*a):
    return int(datetime(*a, tzinfo=MSK).timestamp() * 1000)


FULL = {"age": 34, "basalMetabolism": 1700.0, "bmi": 23.5, "bodyAge": 37, "bodyFat": 16.0, "bodyFatRate": 20.0,
        "bodyScore": 83.0, "bodyWeight": 80.0, "boneSalt": 3.3, "conflictFlag": 1, "extendAttribute": "0", "gender": 1,
        "heartRate": 70, "height": 178, "leftArmFatMass": 1.1, "leftArmMuscleMass": 3.2, "leftLegFatMass": 2.6,
        "leftLegMuscleMass": 9.8, "rightArmFatMass": 1.0, "rightArmMuscleMass": 3.4, "rightLegFatMass": 2.7,
        "rightLegMuscleMass": 10.2, "trunkFatMass": 9.0, "trunkMuscleMass": 8.0, "moisture": 46.4, "moistureRate": 58.0,
        "proteinRate": 14.0, "rasm": 7.7, "skeletalMusclelMass": 34.0, "visceralFatLevel": 9.0, "waistHipRatio": 0.88,
        "lhrhImpedance": 640.0, "pole": 2, "weightDivision": 2}


def rec(ts, value, data_id=0):
    return {"dataId": data_id, "startTime": ts, "endTime": ts, "recordId": f"r{ts}", "type": 8,
            "samplePoints": [{"startTime": ts, "endTime": ts, "key": "WEIGHT_BODYFAT_BROAD", "value": json.dumps(value)}]}


def test_full_measurement_fields():
    m = p.body_measurements([rec(ms(2026, 10, 10, 8, 5), FULL)], MSK, detailed=True)[0]
    assert m["time"] == "2026-10-10 08:05" and m["full_analysis"] is True
    assert (m["weight_kg"], m["bmi"], m["body_fat_pct"], m["fat_mass_kg"]) == (80, 23.5, 20, 16)
    assert m["fat_free_mass_kg"] == 64.0                     # считается: вес − жир
    assert m["protein_kg"] == 14 and m["protein_pct"] == 17.5  # proteinRate — это кг
    assert m["skeletal_muscle_kg"] == 34 and m["water_pct"] == 58 and m["bmr_kcal"] == 1700
    assert m["segments"]["trunk"] == {"fat_kg": 9, "muscle_kg": 8}
    assert m["segments"]["left_leg"] == {"fat_kg": 2.6, "muscle_kg": 9.8}
    assert m["impedance_ohm"] == {"lhrhImpedance": 640.0}
    assert m["profile"] == {"height_cm": 178, "age": 34, "gender": "male"}
    assert "segments" not in p.body_measurements([rec(ms(2026, 10, 10, 8, 5), FULL)], MSK)[0]


def test_owner_filter_unassigned_and_duplicates():
    other = {**FULL, "height": 164, "age": 31, "bodyWeight": 82.0}
    weight_only = {"age": 34, "bodyWeight": 81.0, "conflictFlag": 1, "gender": 1, "height": 178, "pole": 1}
    unknown = {"bodyWeight": 21.5, "conflictFlag": 3, "extendAttribute": "-1", "pole": 1}
    t = ms(2026, 10, 10, 8, 5)
    records = [rec(ms(2026, 10, 10, 7, 12), weight_only), rec(t, FULL),
               rec(t + 45_000, {k: v for k, v in FULL.items() if k != "heartRate"}),   # повтор того же взвешивания
               rec(ms(2026, 10, 10, 20, 31), other), rec(ms(2026, 10, 10, 20, 33), unknown)]
    mine = p.body_measurements(records, MSK, owner_height=178)
    assert [(m["time"], m["weight_kg"], m["full_analysis"]) for m in mine] == [
        ("2026-10-10 07:12", 81, False), ("2026-10-10 08:05", 80, True)]
    assert mine[1]["heart_rate"] == 70                       # из двух копий осталась полная
    everyone = p.body_measurements(records, MSK, include_unassigned=True)
    assert [m["weight_kg"] for m in everyone] == [81, 80, 82, 21.5]
    assert everyone[-1]["unassigned"] is True


def test_trend_uses_full_analyses():
    later = {**FULL, "bodyWeight": 79.0, "bodyFat": 15.0, "bodyFatRate": 19.0, "skeletalMusclelMass": 34.5}
    ms_ = p.body_measurements([rec(ms(2026, 9, 1, 8, 0), FULL), rec(ms(2026, 10, 1, 8, 0), later)], MSK)
    tr = p.body_trend(ms_)
    assert tr["measurements"] == 2 and tr["from"] == "2026-09-01 08:00"
    assert tr["change"] == {"weight_kg": -1, "body_fat_pct": -1, "fat_mass_kg": -1, "skeletal_muscle_kg": 0.5,
                            "fat_free_mass_kg": 0, "water_pct": 0, "visceral_fat_level": 0}
    assert p.body_trend(ms_[:1]) is None


async def test_by_time_keeps_health_records_with_zero_data_id(tmp_path):
    """Регрессия: у записей здоровья dataId=0, дедупликация по dataId оставляла одно измерение из многих."""
    a, b = ms(2026, 10, 10, 8, 5), ms(2026, 10, 10, 20, 31)

    def h(req):
        return httpx.Response(200, json={"resultCode": 0, "data": {"20261010": [rec(a, FULL), rec(b, FULL)]}})

    c = HuaweiClient(fresh(tmp_path), transport=httpx.MockTransport(h))
    recs = await c.by_time(lambda x, y: c.health_data(x, y, 8), [(1, 1), (2, 2)])   # два окна, один и тот же ответ
    assert len(recs) == 2
