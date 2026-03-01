
import asyncio
from src.scripts.simple.v2_top_speed import TopSpeedPlot_Telemetry

async def main():
    from src.data_loader.f1_static_client import F1StaticClient
    client = F1StaticClient()
    index = client.fetch_season_index(2023)
    meetings = index.get('Meetings', [])
    first_key = meetings[0].get('Key')
    print('Testing with Year=2023, Key=', first_key)
    path = await asyncio.to_thread(TopSpeedPlot_Telemetry, 2023, first_key, 'Race')
    print('Output path:', path)

asyncio.run(main())
