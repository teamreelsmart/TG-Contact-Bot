import time
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from config import ADMIN
from Plugins.premium import consume_state_input, is_pending_user_input
from Plugins import database as db

# RAM memory dictionaries
user_cooldowns = {}
message_memory = {}

# Constants
COOLDOWN_TIME = 300  # 5 minutes in seconds
MAX_MEMORY_LIMIT = 100  # Max records before auto-cleaning

@Client.on_message(filters.private & ~filters.user(ADMIN) & ~filters.command(["start", "admin"]))
async def forward_to_admin(client: Client, message: Message):
    user_id = message.from_user.id
    if consume_state_input(user_id) or is_pending_user_input(user_id):
        return
    if db.is_banned(user_id):
        return await message.reply("You are banned from using this bot.")
    try:
        fwd = await message.forward(ADMIN)
        message_memory[fwd.id] = user_id
        current_time = time.time()
        if current_time - user_cooldowns.get(user_id, 0) >= COOLDOWN_TIME:
            await message.reply(
                "<blockquote><b><i>Owner Will Be Reply Soon</i>..</b></blockquote>", 
                parse_mode=enums.ParseMode.HTML,
                quote=True
            )
            user_cooldowns[user_id] = current_time
            
        if len(message_memory) > MAX_MEMORY_LIMIT:
            oldest_msg_keys = list(message_memory.keys())[:50]
            for key in oldest_msg_keys:
                del message_memory[key]
        if len(user_cooldowns) > MAX_MEMORY_LIMIT:
            oldest_user_keys = list(user_cooldowns.keys())[:50]
            for key in oldest_user_keys:
                del user_cooldowns[key]

    except Exception as e:
        await message.reply(f"❌ **Error:** Message could not be sent.\n`{e}`")

@Client.on_message(filters.private & filters.user(ADMIN) & filters.reply)
async def reply_to_user(client: Client, message: Message):
    replied_msg = message.reply_to_message
    user_id = message_memory.get(replied_msg.id)
    if not user_id and replied_msg.forward_from:
        user_id = replied_msg.forward_from.id
    if not user_id:
        return await message.reply("❌ **User ID not detected.**")
    try:
        await message.copy(user_id)
    except Exception as e:
        await message.reply(f"⚠️ **Error sending to user:**\n`{e}`")
