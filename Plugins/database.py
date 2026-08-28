"""MongoDB persistence layer for premium purchases and bot administration."""
from datetime import datetime

from pymongo import ASCENDING, MongoClient

from config import DATABASE_NAME, MONGO_URI

DEFAULT_PLANS = (
    ("1month", "1 month", 150, 100, 30),
    ("2months", "2 months", 300, 180, 60),
    ("3months", "3 months", 450, 250, 90),
    ("lifetime", "Lifetime", 999, 499, 0),
)

_client = MongoClient(MONGO_URI, tz_aware=False)
database = _client[DATABASE_NAME]
users = database.users
settings = database.settings
plans = database.plans
coupons = database.coupons
premium_memberships = database.premium_users
premium_channels = database.premium_channels
banned_users = database.banned_users


def initialise():
    """Create MongoDB indexes and seed plans without overwriting admin changes."""
    users.create_index("user_id", unique=True)
    settings.create_index("key", unique=True)
    plans.create_index("slug", unique=True)
    coupons.create_index("code", unique=True)
    premium_memberships.create_index("user_id", unique=True)
    premium_channels.create_index("channel_id", unique=True)
    banned_users.create_index("user_id", unique=True)
    for slug, name, original_price, discounted_price, duration_days in DEFAULT_PLANS:
        plans.update_one(
            {"slug": slug},
            {"$setOnInsert": {
                "slug": slug,
                "name": name,
                "original_price": original_price,
                "discounted_price": discounted_price,
                "duration_days": duration_days,
            }},
            upsert=True,
        )


def _strip_id(document):
    if document:
        document.pop("_id", None)
    return document


def register_user(user):
    if not user:
        return
    users.update_one(
        {"user_id": user.id},
        {"$set": {
            "first_name": user.first_name or "User",
            "username": user.username or "",
            "joined_at": datetime.utcnow().isoformat(),
        }, "$setOnInsert": {"user_id": user.id}},
        upsert=True,
    )


def get_setting(key, default=""):
    setting = settings.find_one({"key": key})
    return setting["value"] if setting else default


def set_setting(key, value):
    settings.update_one({"key": key}, {"$set": {"value": str(value)}}, upsert=True)


def get_plans():
    return [_strip_id(plan) for plan in plans.find().sort("slug", ASCENDING)]


def get_plan(slug):
    return _strip_id(plans.find_one({"slug": slug}))


def update_plan(slug, field, value):
    if field not in {"name", "original_price", "discounted_price"}:
        raise ValueError("Invalid plan field")
    plans.update_one({"slug": slug}, {"$set": {field: value}})


def add_coupon(code, percent, valid_until):
    coupons.update_one(
        {"code": code.upper()},
        {"$set": {"percent": percent, "valid_until": valid_until.isoformat(), "active": True}, "$setOnInsert": {"code": code.upper()}},
        upsert=True,
    )


def get_coupon(code):
    coupon = _strip_id(coupons.find_one({"code": code.upper(), "active": True}))
    if not coupon or datetime.fromisoformat(coupon["valid_until"]) < datetime.utcnow():
        return None
    return coupon


def add_channel(channel_id):
    premium_channels.update_one(
        {"channel_id": channel_id},
        {"$setOnInsert": {"channel_id": channel_id, "added_at": datetime.utcnow().isoformat()}},
        upsert=True,
    )


def channels():
    return [_strip_id(channel) for channel in premium_channels.find().sort("added_at", ASCENDING)]


def add_premium(user_id, plan_slug, ends_at, channel_id):
    premium_memberships.update_one(
        {"user_id": user_id},
        {"$set": {
            "plan_slug": plan_slug,
            "ends_at": ends_at.isoformat() if ends_at else None,
            "channel_id": channel_id,
            "added_at": datetime.utcnow().isoformat(),
        }, "$setOnInsert": {"user_id": user_id}},
        upsert=True,
    )


def premium_users():
    records = []
    for membership in premium_memberships.find():
        user = users.find_one({"user_id": membership["user_id"]}) or {}
        plan = plans.find_one({"slug": membership["plan_slug"]}) or {}
        membership.update({"first_name": user.get("first_name"), "username": user.get("username"), "name": plan.get("name")})
        records.append(_strip_id(membership))
    return sorted(records, key=lambda record: (record["ends_at"] is None, record["ends_at"] or ""))


def all_user_ids():
    return [user["user_id"] for user in users.find({}, {"user_id": 1, "_id": 0})]


def ban_user(user_id):
    banned_users.update_one({"user_id": user_id}, {"$setOnInsert": {"user_id": user_id}}, upsert=True)


def unban_user(user_id):
    banned_users.delete_one({"user_id": user_id})


def is_banned(user_id):
    return banned_users.find_one({"user_id": user_id}, {"_id": 1}) is not None


def expired_premium_users():
    now = datetime.utcnow().isoformat()
    return [_strip_id(record) for record in premium_memberships.find({"ends_at": {"$ne": None, "$lte": now}})]


def remove_premium(user_id):
    premium_memberships.delete_one({"user_id": user_id})
