import os
import sys
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from config import ADMIN

@Client.on_message(filters.command("restart") & filters.private & filters.user(ADMIN))
async def restart_bot(client: Client, message: Message):
    steve = await message.reply_text("**🔄 Restarting bot...**")
    await asyncio.sleep(3)
    await steve.edit("**✅ Bot restarted successfully**")
    os.execl(sys.executable, sys.executable, *sys.argv)
