"""Протокол облака приложения Huawei Health (com.huawei.health): константы и чистые функции.

Перенесено из and7ey/huawei_health (MIT, см. NOTICE): протокол восстановлен из APK
Huawei Health 16.1.6.320 и проверен живыми запросами. Здесь нет сети — только данные
и разбор ответов, чтобы всё покрывалось тестами без облака.
"""

from __future__ import annotations

import json
import urllib.parse
import uuid
from datetime import date, datetime, timedelta, tzinfo
from typing import Any, Iterable, Iterator

# ── регионы ──────────────────────────────────────────────────────────────
# Хосты берутся приложением из GRS по стране аккаунта: токены живут на «session»
# (domainHealthCloudCommon), данные — на «data» (healthCloudUrl).
REGIONS: dict[str, dict[str, str]] = {
    "ru": {"session": "https://healthcommon-drru.things.dbankcloud.ru",
           "data": "https://sportdata-drru.things.dbankcloud.ru"},
    "eu": {"session": "https://healthcommon-dre.things.dbankcloud.com",
           "data": "https://sportdata-dre.things.dbankcloud.com"},
    # проверено в astrbot_plugin_huawei_health на аккаунте КНР (2026-10-07)
    "cn": {"session": "https://healthcommon-drcn.things.dbankcloud.com",
           "data": "https://healthdata.dbankcloud.cn"},
}

OAUTH_AUTHORIZE = "https://oauth-login.cloud.huawei.com/oauth2/v3/authorize"
OBTAIN_PATH = "/commonAbility/userAccessToken/obtain"
REFRESH_PATH = "/commonAbility/userAccessToken/refresh"

# числовой HMS_APPLICATION_ID приложения Health (не имя пакета)
HMS_APP_ID = "10414141"
APP_ID = "com.huawei.health"
TOKEN_TYPE = 2
APP_VERSION = "and_health_16.1.6.320"
REDIRECT_URI = "hms://redirect_url"
SCOPES = (
    "https://www.huawei.com/auth/account/base.profile",
    "https://www.huawei.com/healthkit/step.both",
    "https://www.huawei.com/healthkit/activityrecord.both",
)
REFRESH_TOKEN_TTL = 180 * 24 * 3600   # ThirdLoginDataStorageUtil.REFRESH_TOKEN_INTERVAL
TOKEN_LIVE_SLACK = 120                # ротируем access-токен за 2 минуты до истечения

READ_PATHS = {
    "sports_stat": "/dataQuery/sport/v2/getSportsStat",
    "sports_detail": "/dataQuery/sport/getSportsDataByTime",
    "health_stat": "/dataQuery/health/getHealthStat",
    "bind_devices": "/profile/device/getBindDevice",
}

HEALTH_TYPES = {"heart_rate": 7, "sleep": 9, "stress": 11, "intensity": 12}
# getHealthData type 8 — измерения весов (Huawei Scale), точка WEIGHT_BODYFAT_BROAD с JSON-значением.
# Найдено пробой на живом аккаунте со Scale 3 Pro (2026-10-10); 10006 — синоним типа 8.
WEIGHT_TYPE = 8
HEALTH_DATA_PATH = "/dataQuery/health/getHealthData"
SPORT_TYPES = {1: "stairs", 2: "hill", 3: "cycling", 4: "running", 5: "walking",
               6: "deep_sleep", 7: "light_sleep", 8: "awake", 9: "swimming",
               10: "other", 14: "unknown"}
# 5 — поминутный трек ходьбы за весь день, 6/7/8 — сон: это не тренировки
SESSION_SPORT_TYPES = tuple(t for t in SPORT_TYPES if t not in (5, 6, 7, 8))
BY_TIME_WINDOW_DAYS = 10              # *ByTime отвечает 1001 на окно шире 10 дней

AUTH_FAILED_CODES = (1002, 1004)      # пару ротировало приложение — ротируем и повторяем
BAD_HUID_CODE = 20010004
FATAL_CODES = (20020003, 20020001, BAD_HUID_CODE, 1002, 1004, 1005)  # нужен вход в браузере


# ── вход ─────────────────────────────────────────────────────────────────

def authorization_url(language: str = "en") -> str:
    """Страница входа HUAWEI ID; заканчивается переходом на hms://redirect_url?code=..."""
    return OAUTH_AUTHORIZE + "?" + urllib.parse.urlencode({
        "client_id": HMS_APP_ID,
        "response_type": "code",
        "access_type": "offline",
        "display": "touch",
        "scope": " ".join(SCOPES),
        "redirect_uri": REDIRECT_URI,
        "state": uuid.uuid4().hex[:8],
        "lang": language,
        "prompt": "consent",
    })


def authorization_code_from(answer: str) -> str | None:
    """Код из того, что вставил человек: адрес hms://..., строка консоли Chrome
    «Failed to launch 'hms://redirect_url?code=..' ...» или голый код.
    Код percent-encoded (~360 символов) — разбирать только как query-строку."""
    text = (answer or "").strip().strip('"').strip("'")
    if not text:
        return None
    if "hms://" in text and not text.startswith("hms://"):
        text = text[text.index("hms://"):].split("'")[0].split(" ")[0]
    query = urllib.parse.urlparse(text).query
    if query:
        found = urllib.parse.parse_qs(query).get("code")
        if found and found[0]:
            return found[0]
    if "code=" in text:
        tail = text.split("code=", 1)[1].split("&", 1)[0].split("'")[0]
        return urllib.parse.unquote(tail) or None
    if "://" in text or any(ch.isspace() for ch in text):
        return None
    return urllib.parse.unquote(text)


# ── разбор значений ──────────────────────────────────────────────────────

def to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    got = to_float(value)
    return None if got is None else int(got)


def day_int(value: date) -> int:
    return int(value.strftime("%Y%m%d"))


def _from_ymd(digits: str) -> date | None:
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return None


def normalize_day(raw: Any, tz: tzinfo | None = None) -> date | None:
    """Huawei смешивает YYYYMMDD, ISO-строки и epoch в мс/нс."""
    if isinstance(raw, str) and "-" in raw:
        digits = "".join(ch for ch in raw if ch.isdigit())
        return _from_ymd(digits[:8]) if len(digits) >= 8 else None
    value = to_float(raw)
    if value is None:
        return None
    text = str(int(value))
    if len(text) == 8:
        return _from_ymd(text)
    scale = {13: 1e3, 10: 1, 19: 1e9}.get(len(text))
    return datetime.fromtimestamp(value / scale, tz).date() if scale else None


def windows(start: date, end: date, days: int) -> Iterator[tuple[int, int]]:
    """Диапазон дат -> окна YYYYMMDD не длиннее days (включительно)."""
    while start <= end:
        stop = min(start + timedelta(days=days - 1), end)
        yield day_int(start), day_int(stop)
        start = stop + timedelta(days=1)


def stamp(ms: Any, tz: tzinfo | None) -> str | None:
    value = to_float(ms)
    if not value or value < 1e12:
        return None
    return datetime.fromtimestamp(value / 1000, tz).strftime("%Y-%m-%d %H:%M")


# ── ответы облака ────────────────────────────────────────────────────────

def result_code(payload: dict) -> Any:
    for key in ("resultCode", "code", "retCode"):
        if payload.get(key) is not None:
            return payload[key]
    return None


def result_desc(payload: dict) -> str:
    for key in ("resultDesc", "errorMsg", "message", "errorDesc", "retMsg"):
        if payload.get(key):
            return str(payload[key])
    return str(payload)[:200]


def is_ok(code: Any) -> bool:
    return code in (None, 0, "0")


def code_int(code: Any) -> int | None:
    return to_int(code)


def daily_activity(rows: Iterable[dict]) -> dict[str, dict]:
    """getSportsStat -> {дата: суточные итоги}. Строка sportType 0 — суточный агрегат
    (совпадает с кольцами приложения), она важнее покатегорийных строк."""
    out: dict[str, dict] = {}
    for record in rows or []:
        if not isinstance(record, dict):
            continue
        day = normalize_day(record.get("recordDay"))
        if not day:
            continue
        basic = record.get("sportBasicInfo") or {}
        row = {
            "date": day.isoformat(),
            "sport_type": record.get("sportType"),
            "steps": to_int(basic.get("steps")),
            "distance_m": to_int(basic.get("distance")),
            "kcal": _milli(basic.get("calorie")),            # в ответе 0.001 ккал
            "active_min": to_int(basic.get("duration")),
            "walk_min": to_int((record.get("dimenDailyActivity") or {}).get("walkDurations")),
            "active_hours": to_int((record.get("activeHourBasic") or {}).get("countActiveHour")),
            "intensity_min": to_int((record.get("exerciseTimeBasic") or {}).get("totalMidHighIntensity")),
            "step_goal": to_int((record.get("goalAchieveBasic") or {}).get("stepGoalValueStat")),
        }
        current = out.get(row["date"])
        if current is None or (not row["sport_type"] and current["sport_type"]):
            out[row["date"]] = row
    return out


def _milli(value: Any) -> float | None:
    got = to_float(value)
    return None if got is None else round(got / 1000, 1)


# getHealthStat: список на семейство, в каждой записи вложенный *Basic-блок
HEALTH_FAMILIES = {
    "heartRateTotal": ("heart_rate", "heartRateBasic", {
        "lastRestHeartRate": "resting",
        "avgRestingHeartRate": "resting_avg",
        "minHeartRate": "min",
        "maxHeartRate": "max",
        "lastHeartRate": "last"}),
    "professionalSleepTotal": ("sleep", "professionalSleep", {
        "allSleepTime": "total_min",
        "deepSleepTime": "deep_min",
        "lightSleepTime": "light_min",
        "dreamTime": "rem_min",
        "awakeTime": "awake_min",
        "sleepScore": "score",
        "sleepEfficiency": "efficiency_pct",
        "sleepLatency": "latency_min",
        "wakeupCnt": "wake_ups",
        "lastAvgHeartrate": "hr_avg",
        "lastAvgHrv": "hrv_ms",
        "lastAvgSpO2": "spo2_pct"}),
    "stressTotal": ("stress", "stressBasic", {
        "meanScore": "avg",
        "maxScore": "max",
        "minScore": "min",
        "lastScore": "last",
        "measureCount": "measurements"}),
    "exerciseIntensityTotal": ("intensity", "exerciseIntensityBasic", {
        "totalMidHighIntensity": "moderate_high_min"}),
}


def health_by_day(payload: dict, tz: tzinfo | None = None) -> dict[str, dict[str, dict]]:
    """getHealthStat -> {дата: {семейство: {метрика: значение}}}.
    0 и -1 в облаке значат «не измерялось», такие значения отбрасываются.
    Ночь сна относится к дню пробуждения."""
    out: dict[str, dict[str, dict]] = {}
    for family, (name, block, fields) in HEALTH_FAMILIES.items():
        for record in payload.get(family) or []:
            if not isinstance(record, dict):
                continue
            day = normalize_day(record.get("recordDay"), tz)
            values = record.get(block) or {}
            if not day or not isinstance(values, dict):
                continue
            row: dict[str, Any] = {}
            for key, metric in fields.items():
                got = to_float(values.get(key))
                if got is not None and got > 0:
                    row[metric] = int(got) if got == int(got) else round(got, 1)
            if name == "sleep":
                row["fell_asleep"] = stamp(values.get("fallAsleepTime"), tz)
                row["woke_up"] = stamp(values.get("wakeupTime"), tz)
            row = {k: v for k, v in row.items() if v is not None}
            if row:
                out.setdefault(day.isoformat(), {})[name] = row
    return out


def sport_sessions(records: Iterable[dict], tz: tzinfo | None = None, gap_minutes: int = 15) -> list[dict]:
    """Тренировки из поминутных сегментов getSportsDataByTime.

    Каждое устройство, видевшее минуту, присылает свою копию (часы и телефон — два
    комплекта одной пробежки), поэтому на каждую минуту остаётся самая «полная» запись,
    а соседние минуты одного типа склеиваются, если разрыв не больше gap_minutes.
    """
    by_type: dict[Any, dict[int, dict]] = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        start, end = to_float(record.get("startTime")), to_float(record.get("endTime"))
        if not start or not end:
            continue
        basic = (record.get("sportBasicInfos") or [{}])[0] or {}
        weight = (to_float(basic.get("distance")) or 0, to_float(basic.get("steps")) or 0,
                  to_float(basic.get("calorie")) or 0)
        minutes = by_type.setdefault(to_int(record.get("sportType")), {})
        key = int(start // 60000)
        if key not in minutes or weight > minutes[key]["weight"]:
            minutes[key] = {"start": start, "end": end, "basic": basic, "weight": weight,
                            "device_code": record.get("deviceCode")}

    sessions: list[dict] = []
    for sport_type, minutes in sorted(by_type.items(), key=lambda kv: kv[0] or 0):
        for _, m in sorted(minutes.items()):
            b = m["basic"]
            last = sessions[-1] if sessions else None
            if last and last["sport_type"] == sport_type and m["start"] - last["end"] <= gap_minutes * 60000:
                last["end"] = max(last["end"], m["end"])
                last["duration_min"] += to_int(b.get("duration")) or 0
                last["steps"] += to_int(b.get("steps")) or 0
                last["distance_m"] += to_int(b.get("distance")) or 0
                last["kcal"] += (to_float(b.get("calorie")) or 0) / 1000
                last["segments"] += 1
                continue
            sessions.append({
                "sport_type": sport_type, "start": m["start"], "end": m["end"],
                "duration_min": to_int(b.get("duration")) or 0,
                "steps": to_int(b.get("steps")) or 0,
                "distance_m": to_int(b.get("distance")) or 0,
                "kcal": (to_float(b.get("calorie")) or 0) / 1000,
                "segments": 1, "device_code": m["device_code"],
            })
    out = []
    for s in sorted(sessions, key=lambda s: s["start"]):
        out.append({
            "sport": SPORT_TYPES.get(s["sport_type"], f"type_{s['sport_type']}"),
            "start": stamp(s["start"], tz),
            "end": stamp(s["end"], tz),
            "duration_min": s["duration_min"],
            "distance_km": round(s["distance_m"] / 1000, 2) if s["distance_m"] else None,
            "steps": s["steps"] or None,
            "kcal": round(s["kcal"], 1) or None,
            "segments": s["segments"],
            "device_code": s["device_code"],
        })
    return out


# ── весы ─────────────────────────────────────────────────────────────────
# поле Huawei -> (имя в ответе, округление). Проверено по экрану «Состав тела» приложения.
BODY_FIELDS = {
    "bodyWeight": ("weight_kg", 2),
    "bmi": ("bmi", 1),
    "bodyFatRate": ("body_fat_pct", 1),
    "bodyFat": ("fat_mass_kg", 2),
    "skeletalMusclelMass": ("skeletal_muscle_kg", 2),   # sic: опечатка в имени поля у Huawei
    "moistureRate": ("water_pct", 1),
    "moisture": ("water_kg", 2),
    "proteinRate": ("protein_kg", 2),                     # несмотря на имя — килограммы
    "boneSalt": ("bone_mass_kg", 2),
    "visceralFatLevel": ("visceral_fat_level", 1),
    "basalMetabolism": ("bmr_kcal", 0),
    "bodyAge": ("body_age", 0),
    "bodyScore": ("body_score", 0),
    "waistHipRatio": ("waist_hip_ratio", 2),
    "rasm": ("appendicular_muscle_index", 2),             # (руки+ноги, кг) / рост² — совпадает с расчётом
    "heartRate": ("heart_rate", 0),
}
SEGMENTS = {"right_arm": "rightArm", "left_arm": "leftArm", "trunk": "trunk",
            "right_leg": "rightLeg", "left_leg": "leftLeg"}


def _r(v: Any, nd: int) -> float | int | None:
    got = to_float(v)
    if got is None or got <= 0:
        return None
    got = round(got, nd)
    return int(got) if nd == 0 or got == int(got) else got


def body_measurement(value: dict, ts: Any, tz: tzinfo | None, *, detailed: bool = False) -> dict:
    """Одно взвешивание WEIGHT_BODYFAT_BROAD -> понятные поля."""
    m: dict[str, Any] = {"time": stamp(ts, tz)}
    for key, (name, nd) in BODY_FIELDS.items():
        m[name] = _r(value.get(key), nd)
    w, fat, protein = m.get("weight_kg"), m.get("fat_mass_kg"), m.get("protein_kg")
    if w and fat:
        m["fat_free_mass_kg"] = round(w - fat, 2)
    if w and protein:
        m["protein_pct"] = round(100 * protein / w, 1)
    # профиль, с которым весы записали измерение: по нему отличаются члены семьи
    gender = {1: "male", 2: "female"}.get(to_int(value.get("gender")))
    m["profile"] = {"height_cm": _r(value.get("height"), 0), "age": _r(value.get("age"), 0), "gender": gender}
    m["full_analysis"] = to_int(value.get("pole")) == 2 and m.get("body_fat_pct") is not None
    if detailed:
        seg = {}
        for name, prefix in SEGMENTS.items():
            row = {"fat_kg": _r(value.get(f"{prefix}FatMass"), 2), "muscle_kg": _r(value.get(f"{prefix}MuscleMass"), 2)}
            row = {k: v for k, v in row.items() if v is not None}
            if row:
                seg[name] = row
        m["segments"] = seg or None
        imp = {k: v for k, v in value.items() if k.endswith("Impedance")}
        m["impedance_ohm"] = imp or None
    return {k: v for k, v in m.items() if v not in (None, {}, [])}


def body_measurements(records: Iterable[dict], tz: tzinfo | None = None, *, detailed: bool = False,
                      owner_height: int | None = None, include_unassigned: bool = False) -> list[dict]:
    """Записи type 8 -> взвешивания по времени.

    conflictFlag 3 — весы не узнали человека (нет профиля): по умолчанию пропускаются.
    owner_height — рост владельца аккаунта в профиле весов: оставляет только его измерения.
    Одно взвешивание приходит повторно (≈через минуту, тот же вес и профиль) — склеивается,
    остаётся запись с бо́льшим числом полей.
    """
    rows = []
    for rec in records or []:
        for sp in rec.get("samplePoints") or []:
            if sp.get("key") != "WEIGHT_BODYFAT_BROAD":
                continue
            try:
                value = json.loads(sp.get("value") or "{}")
            except ValueError:
                continue
            if not isinstance(value, dict) or not to_float(value.get("bodyWeight")):
                continue
            unassigned = to_int(value.get("conflictFlag")) == 3 or not value.get("height")
            if unassigned and not include_unassigned:
                continue
            if owner_height and not unassigned and to_int(value.get("height")) != int(owner_height):
                continue
            ts = to_float(sp.get("startTime") or rec.get("startTime")) or 0
            m = body_measurement(value, ts, tz, detailed=detailed)
            if unassigned:
                m["unassigned"] = True
            rows.append((ts, m))
    rows.sort(key=lambda r: r[0])
    out: list[tuple[float, dict]] = []
    for ts, m in rows:
        prev = out[-1] if out else None
        if (prev and ts - prev[0] <= 5 * 60_000 and prev[1].get("weight_kg") == m.get("weight_kg")
                and prev[1].get("profile") == m.get("profile")):
            if len(m) > len(prev[1]):
                out[-1] = (ts, m)
            continue
        out.append((ts, m))
    return [m for _, m in out]


def body_trend(measurements: list[dict]) -> dict | None:
    """Изменения за период по полным анализам (с электродами): первое vs последнее."""
    full = [m for m in measurements if m.get("full_analysis")] or measurements
    if len(full) < 2:
        return None
    a, b = full[0], full[-1]
    keys = ("weight_kg", "body_fat_pct", "fat_mass_kg", "skeletal_muscle_kg", "fat_free_mass_kg",
            "water_pct", "visceral_fat_level")
    change = {k: round(b[k] - a[k], 2) for k in keys if k in a and k in b}
    return {"from": a["time"], "to": b["time"], "measurements": len(full), "change": change}
