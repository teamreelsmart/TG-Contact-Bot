import asyncio
import io
from datetime import datetime, timedelta

import qrcode
from pyrogram import Client, filters, enums
from pyrogram.errors import RPCError
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import ADMIN
from Plugins import database as db

# Ephemeral conversation state; business data is persisted in MongoDB.
states = {}
selections = {}
consumed_inputs = set()

SMALL_CAPS = str.maketrans("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ", "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ")


def button(label, callback_data, emoji=""):
    """Create consistently styled inline buttons without changing callback data."""
    prefix = f"{emoji} " if emoji else ""
    return InlineKeyboardButton(f"{prefix}{label.translate(SMALL_CAPS)}", callback_data=callback_data)

def is_pending_user_input(user_id):
    return user_id in states

def consume_state_input(user_id):
    """Return whether this update was handled by the premium conversation."""
    if user_id in consumed_inputs:
        consumed_inputs.discard(user_id)
        return True
    return False

def admin_only(message):
    return bool(message.from_user and message.from_user.id == ADMIN)

def plans_keyboard():
    return InlineKeyboardMarkup([[button(row["name"], f"buy:{row['slug']}", "💎")] for row in db.get_plans()])

def admin_keyboard():
    buttons = [[("Ban user", "admin:ban", "🚫"), ("Unban user", "admin:unban", "✅")],
               [("Generate coupon", "admin:coupon", "🎟️"), ("Plans", "admin:plans", "💎")],
               [("Add premium user", "admin:addpremium", "➕"), ("Premium users", "admin:users", "👥")],
               [("Broadcast", "admin:broadcast", "📢"), ("UPI ID", "admin:upi", "💳")],
               [("Premium channels", "admin:channel", "📣")]]
    return InlineKeyboardMarkup([[button(text, data, emoji) for text, data, emoji in row] for row in buttons])

def plan_text(plan, user, coupon=None):
    price = plan["discounted_price"]
    if coupon:
        price = max(0, round(price * (100 - coupon["percent"]) / 100))
    username = f"@{user.username}" if user.username else "Not set"
    return (f"<b>{plan['name']} plan</b>\n\nOriginal price - ₹{plan['original_price']}\n"
            f"Discounted - ₹{plan['discounted_price']}\nFinal price - ₹{price}\n\n<b>Your details</b>\n"
            f"Name - {user.first_name}\nUsername - {username}\nUser ID - <code>{user.id}</code>\n\n"
            "<i>No refund in any circumstances.</i>"), price

@Client.on_message(filters.command("start") & filters.private, group=-2)
async def start_cmd(client, message):
    db.initialise(); db.register_user(message.from_user)
    if db.is_banned(message.from_user.id):
        return await message.reply_text("You are banned from using this bot.")
    text = (f"<blockquote><b>👋 Welcome, {message.from_user.first_name}!</b></blockquote>\n\n"
            "<i>Get help instantly or explore exclusive premium access.</i>\n\n"
            "<b>✨ Choose an option below to continue.</b>")
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup([[button("Help", "home:help", "🆘"), button("Premium", "home:premium", "💎")]]))

@Client.on_message(filters.command("admin") & filters.private, group=-2)
async def admin_command(client, message):
    db.initialise()
    if not admin_only(message):
        return await message.reply_text("❌ You are not authorised to use the admin panel.")
    await message.reply_text("<blockquote><b>🛠️ Admin controls</b></blockquote>\n<i>Choose an action below.</i>", reply_markup=admin_keyboard())

@Client.on_callback_query(group=-2)
async def callbacks(client: Client, query: CallbackQuery):
    db.initialise(); user = query.from_user; data = query.data
    await query.answer()
    if db.is_banned(user.id):
        return
    if data == "home:help":
        return await query.message.reply_text("<blockquote><b>🆘 Help</b></blockquote>\n\n<i>Choose a premium plan, optionally apply a coupon, then pay with the generated UPI QR.</i>\n\n<b>📸 Send the payment screenshot when prompted.</b> For support, send a message here.")
    if data == "home:premium":
        return await query.message.reply_text("<blockquote><b>💎 Choose your premium plan</b></blockquote>\n<i>Select the access duration that suits you.</i>", reply_markup=plans_keyboard())
    if data.startswith("buy:"):
        plan = db.get_plan(data.split(":", 1)[1])
        if not plan: return
        selections[user.id] = {"plan": plan["slug"], "coupon": None}
        text, _ = plan_text(plan, user)
        return await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup([[button("Add coupon code", "pay:coupon", "🎟️"), button("Proceed to pay", "pay:proceed", "💳")]]))
    if data == "pay:coupon":
        if user.id not in selections: return await query.message.reply_text("Please choose a plan first.")
        states[user.id] = {"action": "coupon"}
        return await query.message.reply_text("Please send a valid coupon code.")
    if data == "pay:proceed":
        return await send_payment_qr(client, query.message, user)
    if data == "pay:screenshot":
        states[user.id] = {"action": "screenshot", "selection": selections.get(user.id)}
        return await query.message.reply_text("Please send the payment screenshot.")
    if data.startswith("adminplan:") and user.id == ADMIN:
        slug = data.split(":", 1)[1]; states[user.id] = {"action":"plan_field", "plan":slug}
        return await query.message.reply_text("Send what to edit:", reply_markup=InlineKeyboardMarkup([[button("Plan name", "field:name", "✏️"), button("Original price", "field:original_price", "💰")], [button("Discounted price", "field:discounted_price", "🏷️")]]))
    if data.startswith("field:") and user.id == ADMIN:
        if states.get(user.id,{}).get("action") != "plan_field": return
        states[user.id]["field"] = data.split(":",1)[1]; states[user.id]["action"] = "plan_value"
        return await query.message.reply_text("Send the new value.")
    if not data.startswith("admin:") or user.id != ADMIN:
        return
    action = data.split(":", 1)[1]
    if action == "coupon":
        states[user.id] = {"action": "coupon_code"}; return await query.message.reply_text("Send coupon code.")
    if action in {"ban", "unban", "upi", "channel", "broadcast"}:
        prompts = {"ban":"Send the user ID to ban.", "unban":"Send the user ID to unban.", "upi":"Send the new UPI ID.", "channel":"Send the premium channel ID (for example <code>-1001234567890</code>). The bot must be an admin with invite-user permission.", "broadcast":"Send or forward any text, photo, video, document, or other message to broadcast."}
        states[user.id] = {"action": action}; return await query.message.reply_text(prompts[action])
    if action == "plans":
        return await query.message.reply_text("Select a plan to edit:", reply_markup=InlineKeyboardMarkup([[button(p['name'], f"adminplan:{p['slug']}", "💎")] for p in db.get_plans()]))
    if action == "addpremium":
        states[user.id] = {"action":"premium_user"}; return await query.message.reply_text("Send the user ID to add as premium.")
    if action == "users":
        users = db.premium_users()
        text = "<b>Premium users</b>\n\n" + ("\n".join(f"• <code>{x['user_id']}</code> — {x['name'] or x['first_name'] or 'User'} — {'Lifetime' if not x['ends_at'] else x['ends_at'][:10]}" for x in users) or "No premium users yet.")
        return await query.message.reply_text(text)


async def send_payment_qr(client, message, user):
    selection = selections.get(user.id)
    if not selection: return await message.reply_text("Please choose a plan first.")
    plan = db.get_plan(selection["plan"]); coupon = selection.get("coupon")
    coupon = db.get_coupon(coupon) if coupon else None
    _, amount = plan_text(plan, user, coupon)
    upi = db.get_setting("upi_id")
    if not upi: return await message.reply_text("Payments are not configured yet. Please contact the admin.")
    uri = f"upi://pay?pa={upi}&pn=Premium%20Bot&am={amount}&cu=INR&tn={user.id}"
    image = qrcode.make(uri); output = io.BytesIO(); image.save(output, "PNG"); output.name = "payment-qr.png"; output.seek(0)
    code = coupon["code"] if coupon else "None"
    caption = (f"<blockquote><b>💳 Pay ₹{amount} for {plan['name']}</b></blockquote>\n"
               f"<b>UPI ID:</b> <code>{upi}</code>\n<b>Note:</b> <code>{user.id}</code>\n"
               f"<b>Coupon applied:</b> {code}\n\n<i>📱 Scan the QR to pay ₹{amount}. Use your User ID as the note.</i>\n\n"
               "<b>📸 After payment, tap below to send the screenshot.</b>")
    await client.send_photo(message.chat.id, output, caption=caption, reply_markup=InlineKeyboardMarkup([[button("Send payment screenshot", "pay:screenshot", "📸")]]))

@Client.on_message(filters.private & ~filters.command(["start", "admin", "restart"]), group=-1)
async def state_input(client, message: Message):
    if not message.from_user or message.from_user.id not in states: return
    state = states[message.from_user.id]; action = state["action"]
    consumed_inputs.add(message.from_user.id)
    if action == "screenshot":
        if not (message.photo or message.document): return await message.reply_text("Please send the payment screenshot as a photo or document.")
        selection = state.get("selection") or {}; plan = db.get_plan(selection.get("plan"))
        await message.copy(ADMIN, caption=f"<b>Payment verification required</b>\nUser ID: <code>{message.from_user.id}</code>\nName: {message.from_user.first_name}\nPlan: {plan['name'] if plan else 'Unknown'}\nCoupon: {selection.get('coupon') or 'None'}")
        states.pop(message.from_user.id, None)
        return await message.reply_text("<blockquote><b>✅ Payment screenshot sent to admin</b></blockquote>\n\n<i>Please wait while we verify your payment.</i> You will receive access once verified.")
    text = (message.text or "").strip()
    if action == "coupon":
        coupon = db.get_coupon(text)
        if not coupon: return await message.reply_text("Send a valid coupon code.")
        selections[message.from_user.id]["coupon"] = coupon["code"]; states.pop(message.from_user.id, None)
        plan = db.get_plan(selections[message.from_user.id]["plan"]); rendered, _ = plan_text(plan, message.from_user, coupon)
        return await message.reply_text(f"Coupon <code>{coupon['code']}</code> applied ({coupon['percent']}% off).\n\n{rendered}", reply_markup=InlineKeyboardMarkup([[button("Proceed to pay", "pay:proceed", "💳")]]))
    if message.from_user.id != ADMIN: return
    try:
        if action == "coupon_code": states[ADMIN] = {"action":"coupon_percent", "code":text.upper()}; return await message.reply_text("Send discount percentage (for example, 20).")
        if action == "coupon_percent":
            percent = int(text); assert 0 < percent <= 100
            states[ADMIN] = {"action":"coupon_date", "code":state["code"], "percent":percent}; return await message.reply_text("Send valid-until date in DD-MM-YYYY format.")
        if action == "coupon_date":
            date = datetime.strptime(text, "%d-%m-%Y").replace(hour=23, minute=59, second=59); db.add_coupon(state["code"], state["percent"], date); states.pop(ADMIN, None); return await message.reply_text("✅ Coupon code generated and saved in database.")
        if action in {"ban", "unban"}:
            uid = int(text)
            (db.ban_user if action == "ban" else db.unban_user)(uid)
            states.pop(ADMIN, None)
            return await message.reply_text(f"✅ User <code>{uid}</code> {action}ned.")
        if action == "upi": db.set_setting("upi_id", text); states.pop(ADMIN,None); return await message.reply_text("✅ UPI ID saved.")
        if action == "channel":
            cid=int(text); member=await client.get_chat_member(cid, "me")
            if not member.privileges or not member.privileges.can_invite_users: return await message.reply_text("Bot needs admin invite-user permission in that channel.")
            db.add_channel(cid); states.pop(ADMIN,None); return await message.reply_text("✅ Premium channel saved.")
        if action == "broadcast":
            sent=0
            for uid in db.all_user_ids():
                try: await message.copy(uid); sent += 1
                except RPCError: pass
            states.pop(ADMIN,None); return await message.reply_text(f"✅ Broadcast sent to {sent} users.")
        if action == "plan_value":
            field=state["field"]; value=text if field == "name" else int(text); db.update_plan(state["plan"], field, value); states.pop(ADMIN,None); return await message.reply_text("✅ Plan updated.")
        if action == "premium_user": states[ADMIN] = {"action":"premium_plan", "user_id":int(text)}; return await message.reply_text("Send plan name: 1month, 2months, 3months, or lifetime.")
        if action == "premium_plan":
            plan=db.get_plan(text.lower())
            if not plan: return await message.reply_text("Invalid plan. Send: 1month, 2months, 3months, or lifetime.")
            channel_rows=db.channels()
            if not channel_rows: return await message.reply_text("Add a premium channel first.")
            uid=state["user_id"]; channel_id=channel_rows[0]["channel_id"]; ends=None if plan["duration_days"] == 0 else datetime.utcnow()+timedelta(days=plan["duration_days"])
            link=await client.create_chat_invite_link(channel_id, member_limit=1, name=f"Premium {uid}")
            db.add_premium(uid, plan["slug"], ends, channel_id); states.pop(ADMIN,None)
            await client.send_message(uid, f"<blockquote><b>🎉 Your premium access link</b></blockquote>\n\n<i>🔐 This link can be used only once. Keep it private and use it carefully.</i>\n\n{link.invite_link}")
            return await message.reply_text("✅ Premium user added and single-use access link sent.")
    except (ValueError, AssertionError):
        return await message.reply_text("Invalid value. Please try again.")
    except RPCError as exc:
        return await message.reply_text(f"Telegram error: <code>{exc}</code>")
