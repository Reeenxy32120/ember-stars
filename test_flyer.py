import asyncio
from flyerapi import Flyer

async def main():
    flyer = Flyer("FL-XLLHjU-HSelIT-ZeswJb-iFAkMZ")  # временно
    result = await flyer.check(user_id=8728680606, language_code="ru")
    print("check:", result)

asyncio.run(main())