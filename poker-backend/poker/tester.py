import asyncio
import websockets

async def foo():
    uri = 'ws://localhost:8000/ws/myconsumer'
    async with websockets.connect(uri) as websocket:
        # await websocket.send("Hello server!")
        await websocket.close()

asyncio.run(foo())
