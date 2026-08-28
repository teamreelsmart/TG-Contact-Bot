import os
import asyncio
from pyrogram import Client
from aiohttp import web
from config import API_ID, API_HASH, BOT_TOKEN, ADMIN
from Plugins import database as premium_db

routes = web.RouteTableDef()

@routes.get("/", allow_head=True)
async def root_route(request):
    return web.Response(text="<h3 align='center'><b>Bot is Alive</b></h3>", content_type='text/html')

async def web_server():
    app = web.Application(client_max_size=30_000_000)
    app.add_routes(routes)
    return app

class Bot(Client):
    def __init__(self):
        super().__init__(
            "contact_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            plugins=dict(root="Plugins"),
            workers=200,
            sleep_threshold=15
        )

    async def start(self, *args, **kwargs):
        app = web.AppRunner(await web_server())
        await app.setup()
        try:
            await web.TCPSite(app, "0.0.0.0", int(os.getenv("PORT", 8080))).start()
            print("Web server started.")
        except Exception as e:
            print(f"Web server error: {e}")
            
        await super().start(*args, **kwargs)
        premium_db.initialise()
        self.expiry_task = asyncio.create_task(self.expire_premium_access())
        
        me = await self.get_me()
        print(f"Bot Started as {me.first_name}")
        
        if ADMIN:
            try:
                await self.send_message(ADMIN, f"**🤖 {me.first_name} Contact Bot is started...**")
            except Exception as e:
                print(f"Error sending message to admin: {e}")

    async def expire_premium_access(self):
        """Remove users whose timed premium access has expired."""
        while True:
            for record in premium_db.expired_premium_users():
                try:
                    await self.ban_chat_member(record["channel_id"], record["user_id"])
                    await self.unban_chat_member(record["channel_id"], record["user_id"])
                    await self.send_message(record["user_id"], "Your premium access has expired.")
                except Exception as exc:
                    print(f"Could not remove expired premium user {record['user_id']}: {exc}")
                    continue
                premium_db.remove_premium(record["user_id"])
            await asyncio.sleep(3600)

    async def stop(self, *args):
        if hasattr(self, "expiry_task"):
            self.expiry_task.cancel()
        await super().stop()
        print("Bot stopped.")

if __name__ == "__main__":
    Bot().run()
