"""Диагностика Tgrass: что реально возвращает API для пользователя."""
import asyncio
import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("TGRASS_API_KEY", "")
API_URL = os.getenv("TGRASS_API_URL", "https://tgrass.space").rstrip("/")


async def main() -> None:
    user_id = int(sys.argv[1]) if len(sys.argv) > 1 else int(input("user_id: "))
    if not API_KEY:
        print("TGRASS_API_KEY пуст в .env")
        return

    payload = {
        "tg_user_id": user_id,
        "lang": "ru",
        "is_premium": False,
    }
    headers = {"Auth": API_KEY, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(f"{API_URL}/offers", json=payload, headers=headers)
        print("status:", r.status_code)
        try:
            data = r.json()
        except Exception:
            print("raw:", r.text)
            return

    print(json.dumps(data, ensure_ascii=False, indent=2))

    offers = data.get("offers", [])
    print(f"\nВсего офферов: {len(offers)}")
    for o in offers:
        print(
            f"  offer_id={o.get('offer_id')} "
            f"type={o.get('type')} "
            f"subscribed={o.get('subscribed')} "
            f"name={o.get('name')!r}"
        )


if __name__ == "__main__":
    asyncio.run(main())