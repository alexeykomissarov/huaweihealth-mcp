"""Вход в Huawei Health: ссылка HUAWEI ID -> код из консоли браузера -> пара токенов.

    huawei-mcp-login [--region ru|eu|cn]   # интерактивный вход (раз в 180 дней)
    huawei-mcp-login --verify              # проверить токены запросом за сегодня (exit 0/1)

Пароль HUAWEI ID сюда не вводится: вход идёт в вашем браузере, сервер получает только код.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import protocol as p
from .client import AuthRequired, HuaweiClient, HuaweiError

GUIDE = """
1. Откройте ссылку в Chrome/Яндекс.Браузере на компьютере и войдите в HUAWEI ID.
2. Включите мобильный режим: F12, затем Ctrl+Shift+M (macOS: Cmd+Shift+M),
   выберите устройство (например, iPhone 12 Pro) и ОБЯЗАТЕЛЬНО перезагрузите страницу (F5).
   Кнопка «Разрешить»/«Allow» реагирует только на касание — без мобильного режима она не нажмётся.
3. Нажмите «Разрешить». В DevTools на вкладке Console появится строка
   «Failed to launch 'hms://redirect_url?code=...'» — скопируйте её целиком (ПКМ → Copy).
   Код одноразовый и живёт несколько минут.
"""


async def _check(client: HuaweiClient) -> bool:
    tz = ZoneInfo(os.getenv("HUAWEI_TZ", "Europe/Moscow"))
    today = p.day_int(datetime.now(tz).date())
    try:
        answer = await client.sports_stat(today, today)
    except AuthRequired as e:
        print(f"Вход недействителен: {e}")
        return False
    except HuaweiError as e:
        print(f"Облако ответило ошибкой: {e}")
        return False
    days = p.daily_activity(answer.get("sportStat") or [])
    steps = next(iter(days.values()), {}).get("steps")
    t = client.tokens
    left = int((t.refresh_expires_at - datetime.now().timestamp()) // 86400)
    print(f"Вход действителен (регион {t.region}, повторный вход через {left} дн.)"
          + (f"; шагов сегодня: {steps}" if steps is not None else "; за сегодня данных пока нет"))
    return True


async def _login(region: str, path: Path) -> bool:
    client = HuaweiClient(path)
    try:
        print("Ссылка входа:\n\n" + p.authorization_url() + "\n" + GUIDE)
        line = input("Вставьте строку с hms://redirect_url?code=...: ")
        code = p.authorization_code_from(line)
        if not code:
            print("В строке не найден code=… — скопируйте строку из Console целиком")
            return False
        try:
            await client.exchange_code(code, region)
        except AuthRequired as e:
            print(f"Huawei отклонил код (истёк или уже использован): {e}\nОткройте ссылку заново.")
            return False
        print(f"Токены сохранены в {path}")
        return await _check(client)
    finally:
        await client.aclose()


async def _verify(path: Path) -> bool:
    client = HuaweiClient(path)
    try:
        return await _check(client)
    finally:
        await client.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default=os.getenv("HUAWEI_REGION", "ru"), choices=sorted(p.REGIONS),
                    help="регион аккаунта HUAWEI ID (по умолчанию ru)")
    ap.add_argument("--verify", action="store_true", help="только проверить вход")
    args = ap.parse_args()
    path = Path(os.getenv("HUAWEI_TOKEN_FILE", "/data/tokens.json"))
    ok = asyncio.run(_verify(path) if args.verify else _login(args.region, path))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
