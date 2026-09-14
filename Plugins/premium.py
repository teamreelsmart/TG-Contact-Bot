import asyncio
import io
import uuid
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


def collection_button(name, callback_data):
    """Collection names must stay in the normal font, not the UI small-caps font."""
    return InlineKeyboardButton(f"🖼️ {name}", callback_data=callback_data)

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
               [("Premium channels", "admin:channels", "📣")],
               [("Add collection", "admin:addcollection", "🖼️"), ("Update collection link", "admin:updatecollection", "🔗")],
               [("Delete collection", "admin:deletecollection", "🗑️")]]
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
    db.initialise()
    db.register_user(message.from_user)
    if db.is_banned(message.from_user.id):
        return await message.reply_text("You are banned from using this bot.")
    if len(message.command) > 1 and message.command[1].startswith("collection_"):
        return await show_special_collection(client, message.chat.id, message.command[1].removeprefix("collection_"))
    text = (f"<blockquote><b>👋 Welcome, {message.from_user.first_name}!</b></blockquote>\n\n"
            "<i>Get help instantly, explore premium access, or browse special collections.</i>\n\n"
            "<b>✨ Choose an option below to continue.</b>")
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup([
        [button("Help", "home:help", "🆘"), button("Premium", "home:premium", "💎")],
        [button("Special collection", "home:collections", "🖼️")],
    ]))

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
    if data == "home:collections":
        collections = db.special_collections_list()
        if not collections:
            return await query.message.reply_text("<blockquote><b>🖼️ Special collection</b></blockquote>\n<i>No collections are available right now. Please check again later.</i>")
        return await show_special_collection(client, query.message.chat.id, collections[0]["slug"])
    if data.startswith("special:update:") and user.id == ADMIN:
        states[user.id] = {"action": "collection_link_update", "slug": data.split(":", 2)[2]}
        return await query.message.reply_text("<b>🔗 Send the new collection access link.</b>")
    if data.startswith("special:delete:") and user.id == ADMIN:
        slug = data.split(":", 2)[2]
        collection = db.get_special_collection(slug)
        if not collection:
            return await query.message.reply_text("❌ This special collection no longer exists.")
        db.delete_special_collection(slug)
        return await query.message.reply_text(f"✅ Deleted special collection: <b>{collection['name']}</b>.")
    if data.startswith("special:view:"):
        return await show_special_collection(client, query.message.chat.id, data.split(":", 2)[2])
    if data.startswith("special:buy:"):
        return await show_special_checkout(client, query.message.chat.id, data.split(":", 2)[2])
    if data.startswith("special:pay:"):
        return await send_special_payment_qr(client, query.message, user, data.split(":", 2)[2])
    if data.startswith("special:approve:") and user.id == ADMIN:
        return await approve_special_purchase(client, query.message, data.split(":", 2)[2])
    if data.startswith("special:reject:") and user.id == ADMIN:
        return await reject_special_purchase(client, query.message, data.split(":", 2)[2])
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
        slug = data.split(":", 1)[1]
        if not db.get_plan(slug):
            return await query.message.reply_text("❌ This plan is no longer available.")
        return await query.message.reply_text(
            "Send what to edit:",
            reply_markup=InlineKeyboardMarkup([
                [button("Plan name", f"planfield:{slug}:name", "✏️"), button("Original price", f"planfield:{slug}:original_price", "💰")],
                [button("Discounted price", f"planfield:{slug}:discounted_price", "🏷️")],
            ]),
        )
    if data.startswith("planfield:") and user.id == ADMIN:
        _, slug, field = data.split(":", 2)
        if field not in {"name", "original_price", "discounted_price"} or not db.get_plan(slug):
            return await query.message.reply_text("❌ This plan option is no longer available.")
        # Include the plan in the button callback itself. This means the admin
        # can always edit the discounted amount, even if an earlier state was
        # cleared while they were looking at the plan options.
        states[user.id] = {"action": "plan_value", "plan": slug, "field": field}
        return await query.message.reply_text("Send the new value.")
    if data.startswith("field:") and user.id == ADMIN:
        if states.get(user.id,{}).get("action") != "plan_field":
            return await query.message.reply_text("❌ Please select the plan again, then choose the value to edit.")
        states[user.id]["field"] = data.split(":",1)[1]; states[user.id]["action"] = "plan_value"
        return await query.message.reply_text("Send the new value.")
    if not data.startswith("admin:") or user.id != ADMIN:
        return
    action = data.split(":", 1)[1]
    if action == "coupon":
        states[user.id] = {"action": "coupon_code"}; return await query.message.reply_text("Send coupon code.")
    if action == "addcollection":
        states[user.id] = {"action": "collection_name"}
        return await query.message.reply_text("<b>🖼️ Send the collection name.</b>")
    if action == "updatecollection":
        collections = db.special_collections_list()
        if not collections:
            return await query.message.reply_text("❌ No special collections have been added yet.")
        return await query.message.reply_text(
            "<b>🔗 Select a collection to update its access link.</b>",
            reply_markup=InlineKeyboardMarkup([[collection_button(item["name"], f"special:update:{item['slug']}")] for item in collections]),
        )
    if action == "deletecollection":
        collections = db.special_collections_list()
        if not collections:
            return await query.message.reply_text("❌ No special collections have been added yet.")
        return await query.message.reply_text(
            "<b>🗑️ Select a special collection to permanently delete.</b>",
            reply_markup=InlineKeyboardMarkup([[collection_button(item["name"], f"special:delete:{item['slug']}")] for item in collections]),
        )
    if action == "channels":
        configured_channels = db.channels()
        listing = "\n".join(f"• <code>{channel['channel_id']}</code>" for channel in configured_channels)
        text = ("<blockquote><b>📣 Premium channels</b></blockquote>\n\n"
                f"<b>Configured channels:</b>\n{listing or '<i>No premium channels added yet.</i>'}\n\n"
                "<i>Send a channel ID to add it, or send an existing channel ID to remove it.</i>")
        return await query.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup([[button("Add or remove channel", "admin:channel", "🔄")]]),
        )
    if action in {"ban", "unban", "upi", "channel", "broadcast"}:
        prompts = {"ban":"Send the user ID to ban.", "unban":"Send the user ID to unban.", "upi":"Send the new UPI ID.", "channel":"Send the premium channel ID (for example <code>-1001234567890</code>). <b>🔄 An existing ID will be removed; a new ID will be verified and added.</b>", "broadcast":"Send or forward any text, photo, video, document, or other message to broadcast."}
        states[user.id] = {"action": action}; return await query.message.reply_text(prompts[action])
    if action == "plans":
        return await query.message.reply_text("Select a plan to edit:", reply_markup=InlineKeyboardMarkup([[button(p['name'], f"adminplan:{p['slug']}", "💎")] for p in db.get_plans()]))
    if action == "addpremium":
        states[user.id] = {"action":"premium_user"}; return await query.message.reply_text("Send the user ID to add as premium.")
    if action == "users":
        users = db.premium_users()
        text = "<b>Premium users</b>\n\n" + ("\n".join(f"• <code>{x['user_id']}</code> — {x['name'] or x['first_name'] or 'User'} — {'Lifetime' if not x['ends_at'] else x['ends_at'][:10]}" for x in users) or "No premium users yet.")
        return await query.message.reply_text(text)


async def show_special_collection(client, chat_id, slug):
    collection = db.get_special_collection(slug)
    if not collection:
        return await client.send_message(chat_id, "❌ This special collection is no longer available.")
    collections = db.special_collections_list()
    position = next((index for index, item in enumerate(collections) if item["slug"] == slug), 0)
    navigation = []
    if position > 0:
        navigation.append(button("Previous", f"special:view:{collections[position - 1]['slug']}", "⬅️"))
    if position < len(collections) - 1:
        navigation.append(button("Next", f"special:view:{collections[position + 1]['slug']}", "➡️"))
    keyboard = []
    if navigation:
        keyboard.append(navigation)
    keyboard.append([button("Buy now", f"special:buy:{slug}", "🛒")])
    caption = (f"<blockquote><b>🖼️ {collection['name']}</b></blockquote>\n\n"
               f"{collection['caption']}\n\n<b>💰 Price: ₹{collection['amount']}</b>")
    return await client.send_photo(chat_id, collection["photo_file_id"], caption=caption, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_special_checkout(client, chat_id, slug):
    collection = db.get_special_collection(slug)
    if not collection:
        return await client.send_message(chat_id, "❌ This special collection is no longer available.")
    caption = (f"<blockquote><b>🛒 Buy {collection['name']}</b></blockquote>\n\n"
               f"{collection['caption']}\n\n<b>💰 Price: ₹{collection['amount']}</b>\n"
               "<i>Tap the button below to continue to secure payment.</i>")
    return await client.send_photo(chat_id, collection["photo_file_id"], caption=caption,
                                   reply_markup=InlineKeyboardMarkup([[button("Buy now", f"special:pay:{slug}", "💳")]]))


async def send_special_payment_qr(client, message, user, slug):
    collection = db.get_special_collection(slug)
    if not collection:
        return await message.reply_text("❌ This special collection is no longer available.")
    upi = db.get_setting("upi_id")
    if not upi:
        return await message.reply_text("❌ Payments are not configured yet. Please contact the admin.")
    selections[user.id] = {"special_collection": slug}
    uri = f"upi://pay?pa={upi}&pn=Premium%20Bot&am={collection['amount']}&cu=INR&tn={user.id}"
    image = qrcode.make(uri)
    output = io.BytesIO()
    image.save(output, "PNG")
    output.name = "special-collection-payment-qr.png"
    output.seek(0)
    caption = (f"<blockquote><b>💳 Pay ₹{collection['amount']} for {collection['name']}</b></blockquote>\n"
               f"<b>UPI ID:</b> <code>{upi}</code>\n<b>Note:</b> <code>{user.id}</code>\n\n"
               "<i>📱 Scan the QR and use your User ID as the payment note.</i>\n\n"
               "<b>📸 After payment, tap below to send the screenshot.</b>")
    await client.send_photo(message.chat.id, output, caption=caption,
                            reply_markup=InlineKeyboardMarkup([[button("Send payment screenshot", "pay:screenshot", "📸")]]))


async def approve_special_purchase(client, message, order_id):
    purchase = db.get_special_purchase(order_id)
    if not purchase or purchase["status"] != "pending":
        return await message.reply_text("❌ This payment request is no longer pending.")
    collection = db.get_special_collection(purchase["collection_slug"])
    if not collection:
        return await message.reply_text("❌ The linked special collection was not found.")
    db.set_special_purchase_status(order_id, "approved")
    await client.send_message(purchase["user_id"],
                              f"<blockquote><b>🎉 Congratulations! Your payment is approved.</b></blockquote>\n\n"
                              f"<b>🖼️ {collection['name']}</b> access link:\n{collection['access_link']}")
    return await message.reply_text(f"✅ Approved <code>{order_id}</code> and sent collection access.")


async def reject_special_purchase(client, message, order_id):
    purchase = db.get_special_purchase(order_id)
    if not purchase or purchase["status"] != "pending":
        return await message.reply_text("❌ This payment request is no longer pending.")
    db.set_special_purchase_status(order_id, "rejected")
    selections[purchase["user_id"]] = {"special_collection": purchase["collection_slug"]}
    states[purchase["user_id"]] = {"action": "screenshot", "selection": selections[purchase["user_id"]]}
    await client.send_message(purchase["user_id"],
                              "<blockquote><b>❌ Payment rejected</b></blockquote>\n\n"
                              "<i>The payment may be fake or incomplete. Please check again and send a new payment screenshot.</i>")
    return await message.reply_text(f"✅ Rejected <code>{order_id}</code>; the user can send another screenshot.")


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
        if not (message.photo or message.document):
            return await message.reply_text("Please send the payment screenshot as a photo or document.")
        selection = state.get("selection") or {}
        if selection.get("special_collection"):
            collection = db.get_special_collection(selection["special_collection"])
            if not collection:
                states.pop(message.from_user.id, None)
                return await message.reply_text("❌ This special collection is no longer available.")
            order_id = uuid.uuid4().hex
            db.create_special_purchase(order_id, message.from_user.id, collection["slug"])
            username = f"@{message.from_user.username}" if message.from_user.username else "Not set"
            caption = ("<blockquote><b>🖼️ Special collection payment verification</b></blockquote>\n"
                       f"<b>Collection:</b> {collection['name']}\n<b>Amount:</b> ₹{collection['amount']}\n"
                       f"<b>User:</b> {message.from_user.first_name}\n<b>Username:</b> {username}\n"
                       f"<b>User ID:</b> <code>{message.from_user.id}</code>\n<b>Order:</b> <code>{order_id}</code>")
            await message.copy(ADMIN, caption=caption, reply_markup=InlineKeyboardMarkup([[
                button("Approve", f"special:approve:{order_id}", "✅"),
                button("Reject", f"special:reject:{order_id}", "❌"),
            ]]))
            states.pop(message.from_user.id, None)
            return await message.reply_text("<blockquote><b>✅ Payment screenshot sent to admin</b></blockquote>\n\n<i>Please wait while we verify your special collection payment.</i>")
        plan = db.get_plan(selection.get("plan"))
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
        if action == "collection_name":
            name = db.normalise_collection_name(text)
            if not name:
                return await message.reply_text("❌ Please send a valid collection name.")
            states[ADMIN] = {"action": "collection_photo", "name": name}
            return await message.reply_text("<b>🖼️ Send the collection photo.</b>")
        if action == "collection_photo":
            if not message.photo:
                return await message.reply_text("❌ Please send the collection as a photo.")
            state["photo_file_id"] = message.photo.file_id
            state["action"] = "collection_caption"
            return await message.reply_text("<b>📝 Send the collection caption.</b>")
        if action == "collection_caption":
            if not text:
                return await message.reply_text("❌ Please send a caption as text.")
            state["caption"] = text
            state["action"] = "collection_amount"
            return await message.reply_text("<b>💰 Send the collection amount in INR.</b>")
        if action == "collection_amount":
            amount = int(text)
            if amount <= 0:
                raise ValueError
            state["amount"] = amount
            state["action"] = "collection_link"
            return await message.reply_text("<b>🔗 Send the collection access link.</b>")
        if action == "collection_link":
            if not text:
                return await message.reply_text("❌ Please send a valid access link.")
            slug = db.add_special_collection(state["name"], state["photo_file_id"], state["caption"], state["amount"], text)
            states.pop(ADMIN, None)
            me = await client.get_me()
            purchase_link = f"https://t.me/{me.username}?start=collection_{slug}"
            return await message.reply_text("<blockquote><b>✅ Special collection saved</b></blockquote>\n\n"
                                            f"<b>🔗 Share this purchase link:</b>\n{purchase_link}")
        if action == "collection_link_update":
            if not text:
                return await message.reply_text("❌ Please send a valid access link.")
            collection = db.get_special_collection(state["slug"])
            if not collection:
                states.pop(ADMIN, None)
                return await message.reply_text("❌ This special collection no longer exists.")
            db.update_special_collection_link(state["slug"], text)
            notified = 0
            for user_id in db.approved_collection_user_ids(state["slug"]):
                try:
                    await client.send_message(user_id, f"<blockquote><b>🔗 Collection access link updated</b></blockquote>\n\n<b>{collection['name']}</b> new link:\n{text}")
                    notified += 1
                except RPCError:
                    pass
            states.pop(ADMIN, None)
            return await message.reply_text(f"✅ Collection link updated and sent to {notified} previous buyer(s).")
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
            cid = int(text)
            if any(channel["channel_id"] == cid for channel in db.channels()):
                db.remove_channel(cid)
                states.pop(ADMIN, None)
                return await message.reply_text(f"✅ Premium channel <code>{cid}</code> removed.")
            member = await client.get_chat_member(cid, "me")
            if not member.privileges or not member.privileges.can_invite_users:
                return await message.reply_text("❌ Bot needs admin invite-user permission in that channel.")
            db.add_channel(cid)
            states.pop(ADMIN, None)
            return await message.reply_text(f"✅ Premium channel <code>{cid}</code> verified and added.")
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
