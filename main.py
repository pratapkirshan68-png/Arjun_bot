import os
import re
import asyncio
import aiohttp
import logging
import base64
from datetime import datetime
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import UserNotParticipant, FloodWait, UserIsBlocked, InputUserDeactivated
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from aiohttp import web
from urllib.parse import quote, unquote

# ================= CONFIGURATION =================
def get_clean_var(key, default=""):
    val = os.environ.get(key, default)
    if not val:
        return default
    return str(val).strip()

API_ID = int(get_clean_var("API_ID", "0"))
API_HASH = get_clean_var("API_HASH", "")
BOT_TOKEN = get_clean_var("BOT_TOKEN", "")
MONGO_URL = get_clean_var("MONGO_URL", "")
TMDB_API_KEY = get_clean_var("TMDB_API_KEY", "")

ADMIN_IDS = [int(x) for x in get_clean_var("ADMIN_IDS", "0").split() if x.strip().isdigit()]
STORAGE_CHANNEL = int(get_clean_var("STORAGE_CHANNEL", "0")) 
SEARCH_CHAT = int(get_clean_var("SEARCH_CHAT", "0")) 
FSUB_CHANNEL = int(get_clean_var("FSUB_CHANNEL", "0")) 
MAIN_CHANNEL_LINK = get_clean_var("MAIN_CHANNEL_LINK", "https://t.me/Movies2026Cinema")

SHORT_DOMAIN = get_clean_var("SHORT_DOMAIN", "arolinks.com")
SHORT_API_KEY = get_clean_var("SHORT_API_KEY", "")

# Settings
SHORTLINK_ENABLED = True 
PAGE_SIZE = 6 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= BOT CLIENT =================
class MovieBot(Client):
    def __init__(self):
        super().__init__("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
        self.movies = None
        self.users = None
        self.requests = None

    async def start(self):
        await super().start()
        try:
            mongo_client = AsyncIOMotorClient(MONGO_URL)
            db = mongo_client["PratapCinemaBot"]
            self.movies = db["movies"]
            self.users = db["users"]
            self.requests = db["movie_requests"]
            print("✅ MongoDB Connected Successfully!")
        except Exception as e:
            print(f"❌ MongoDB Error: {e}")
        print(f"🚀 BOT STARTED as @{self.me.username}")

    async def stop(self, *args):
        await super().stop()
        print("Bot Stopped.")

app = MovieBot()

# ================= HELPERS =================

async def add_user_to_db(user):
    """Save user details for broadcasting and requests"""
    if not user:
        return
    try:
        first_name = user.first_name or "User"
        username = user.username or ""
        await app.users.update_one(
            {"user_id": user.id},
            {"$set": {
                "name": first_name,
                "username": username,
                "last_active": datetime.utcnow()
            }},
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error saving user to DB: {e}")

async def get_poster(query):
    if not TMDB_API_KEY:
        return None
    try:
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(query)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
                if data.get("results"):
                    poster_path = data["results"][0].get("poster_path")
                    if poster_path:
                        return f"https://image.tmdb.org/t/p/w342{poster_path}"
    except Exception as e:
        logger.error(f"TMDB Error: {e}")
    return None

async def get_tmdb_details(query):
    """Fetch complete TMDB details including status and release date"""
    if not TMDB_API_KEY:
        return None
    try:
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(query)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    if not results:
                        return None
                    
                    item = results[0]
                    title = item.get("title") or item.get("name") or item.get("original_title") or query
                    release_date_str = item.get("release_date") or item.get("first_air_date") or ""
                    poster_path = item.get("poster_path")
                    poster = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None
                    overview = item.get("overview", "Koi synopsis uplabdh nahi hai.")
                    rating = item.get("vote_average", "N/A")

                    status = "Released"
                    days_remaining = 0

                    if release_date_str:
                        try:
                            release_dt = datetime.strptime(release_date_str, "%Y-%m-%d").date()
                            today = datetime.now().date()
                            if release_dt > today:
                                status = "Upcoming"
                                days_remaining = (release_dt - today).days
                        except Exception:
                            pass

                    return {
                        "title": title,
                        "release_date": release_date_str if release_date_str else "N/A",
                        "poster": poster,
                        "overview": overview,
                        "rating": rating,
                        "status": status,
                        "days_remaining": days_remaining
                    }
    except Exception as e:
        logger.error(f"TMDB Details Error: {e}")
    return None

async def get_shortlink(url):
    if not SHORTLINK_ENABLED:
        return url
    try:
        api_url = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as resp:
                res = await resp.json()
                if res.get("status") == "success":
                    return res["shortenedUrl"]
    except Exception:
        pass
    return url

def clean_name(text):
    if not text:
        return ""
    text = text.lower()
    junk = [r'\(.*?\)', r'\[.*?\]', '1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english', 'dual audio', 'web-dl', 'bluray', 'camrip', 'pre-dvd']
    for word in junk:
        text = re.sub(word, '', text)
    return " ".join(text.replace(".", " ").replace("_", " ").split()).strip()

async def get_search_buttons(query, results, offset=0):
    btn_list = []
    me = await app.get_me()
    for res in results[offset : offset + PAGE_SIZE]:
        db_id = str(res["_id"])
        db_title = res.get("original_title", res["title"])
        display_name = db_title[:35] + "..." if len(db_title) > 35 else db_title
        bot_url = f"https://t.me/{me.username}?start=file_{db_id}"
        final_link = await get_shortlink(bot_url)
        btn_list.append([InlineKeyboardButton(f"🎬 {display_name}", url=final_link)])

    nav_btns = []
    if offset > 0:
        nav_btns.append(InlineKeyboardButton("⬅️ Back", callback_data=f"page_{offset - PAGE_SIZE}_{quote(query)}"))
    if offset + PAGE_SIZE < len(results):
        nav_btns.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{offset + PAGE_SIZE}_{quote(query)}"))
    
    if nav_btns:
        btn_list.append(nav_btns)
    
    query_b64 = base64.urlsafe_b64encode(query.encode()).decode().rstrip("=")
    btn_list.append([InlineKeyboardButton("📂 GET ALL FILES (IN PM) 📂", url=f"https://t.me/{me.username}?start=all_{query_b64}")])
    return InlineKeyboardMarkup(btn_list)

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try:
            await m.delete()
        except Exception:
            pass

# ================= AUTO-FILTER SEARCH (GROUP) =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "delall", "del", "shortlink", "broadcast", "sms", "details", "tmdb"]))
async def search_movie(client, msg):
    user = msg.from_user

    # Safe Admin / Channel Admin Detection
    is_admin = False
    if user and user.id in ADMIN_IDS:
        is_admin = True
    elif msg.sender_chat and (msg.sender_chat.id in [SEARCH_CHAT, STORAGE_CHANNEL] or msg.sender_chat.id in ADMIN_IDS):
        is_admin = True

    user_id = user.id if user else (msg.sender_chat.id if msg.sender_chat else msg.chat.id)
    user_name = user.first_name if (user and user.first_name) else (msg.sender_chat.title if msg.sender_chat else "User")

    if user:
        await add_user_to_db(user)

    raw_text = msg.text
    if "\n" in raw_text:
        raw_text = raw_text.split("\n")[0]

    query = clean_name(raw_text)
    if len(query) < 2:
        return

    sm = await client.send_message(msg.chat.id, "🔍 Searching...")

    # Search in Mongo DB
    cursor = client.movies.find({"title": {"$regex": query, "$options": "i"}})
    results = await cursor.to_list(length=100)

    if not results:
        words = query.split()
        if words:
            keyword = max(words, key=len)
            cursor = client.movies.find({"title": {"$regex": keyword, "$options": "i"}})
            results = await cursor.to_list(length=100)

    # ================= CASE 1: MOVIE NOT IN DATABASE =================
    if not results:
        tmdb_info = await get_tmdb_details(query)
        user_mention = f"[{user_name}](tg://user?id={user_id})" if user else f"**{user_name}**"

        # Save request to MongoDB
        if user_id > 0:
            try:
                await client.requests.update_one(
                    {"clean_name": query},
                    {
                        "$set": {"movie_name": raw_text, "clean_name": query, "tmdb": tmdb_info},
                        "$addToSet": {"requested_users": user_id},
                        "$setOnInsert": {"created_at": datetime.utcnow()}
                    },
                    upsert=True
                )
            except Exception as e:
                logger.error(f"Request save error: {e}")

        # Upcoming Movie Response
        if tmdb_info and tmdb_info.get("status") == "Upcoming":
            rel_date = tmdb_info.get("release_date", "N/A")
            days_left = tmdb_info.get("days_remaining", 0)
            text = (
                f"ℹ️ **MOVIE RELEASE DETAILS**\n\n"
                f"👤 **User:** {user_mention} (`{user_id}`)\n"
                f"🎬 **Movie:** `{tmdb_info['title']}`\n"
                f"📅 **Release Date:** `{rel_date}`\n"
                f"⏳ **Status:** Abhi release nahi hui (Releasing in `{days_left}` days)\n\n"
                f"📝 Movie release hote hi hamare database me add kar di jayegi!"
            )
            poster = tmdb_info.get("poster")
            res_msg = None
            if poster:
                try:
                    res_msg = await client.send_photo(msg.chat.id, photo=poster, caption=text)
                except Exception:
                    res_msg = await client.send_message(msg.chat.id, text=text)
            else:
                res_msg = await client.send_message(msg.chat.id, text=text)

        # General Missing Movie Response
        else:
            text = (
                f"Maaf karna {user_mention}, abhi hamare database me **'{raw_text}'** movie nahi hai. 😔\n\n"
                f"✅ Humne aapki request Admin ko bhej di hai.\n"
                f"📲 Jaise hi movie add ho jayegi, aapko personal message (SMS) mil jayega!"
            )
            res_msg = await client.send_message(msg.chat.id, text=text)

        # Send Private Alert ONLY to Admins in PM
        admin_alert = (
            f"📥 **NEW MOVIE REQUEST**\n\n"
            f"🎬 **Requested Movie:** `{raw_text}`\n"
            f"👤 **User:** {user_mention}\n"
            f"🆔 **User ID:** `{user_id}`"
        )
        if tmdb_info:
            admin_alert += f"\n📌 **TMDB Title:** `{tmdb_info['title']}` | **Date:** `{tmdb_info['release_date']}`"

        for admin_id in ADMIN_IDS:
            try:
                await client.send_message(admin_id, admin_alert)
            except Exception:
                pass

        await sm.delete()

        # Normal Users: Auto-delete; Admin: Keep message
        if not is_admin:
            try:
                await msg.delete()
            except Exception:
                pass
            asyncio.create_task(delete_after_delay([res_msg], 180))
        return

    # ================= CASE 2: MOVIE FOUND IN DATABASE =================
    poster = await get_poster(query)
    markup = await get_search_buttons(query, results, offset=0)
    
    delete_note = "" if is_admin else "\n\n⏳ _Ye result 5 minute mein delete ho jayega._"
    text = f"🎬 **Results for:** `{raw_text}`{delete_note}"

    try:
        if poster:
            res_msg = await client.send_photo(msg.chat.id, photo=poster, caption=text, reply_markup=markup)
        else:
            res_msg = await client.send_message(msg.chat.id, text=text, reply_markup=markup)
    except Exception:
        res_msg = await client.send_message(msg.chat.id, text=text, reply_markup=markup)

    await sm.delete()

    if not is_admin:
        try:
            await msg.delete()
        except Exception:
            pass
        asyncio.create_task(delete_after_delay([res_msg], 300))

# ================= CALLBACK QUERY (PAGINATION) =================

@app.on_callback_query(filters.regex(r"^page_"))
async def page_callback(client, query: CallbackQuery):
    try:
        _, offset, raw_q = query.data.split("_", 2)
        offset = int(offset)
        search_q = unquote(raw_q)

        cursor = client.movies.find({"title": {"$regex": search_q, "$options": "i"}})
        results = await cursor.to_list(length=100)

        if not results:
            return await query.answer("❌ Ab koi result nahi hai!", show_alert=True)

        markup = await get_search_buttons(search_q, results, offset=offset)
        await query.message.edit_reply_markup(reply_markup=markup)
        await query.answer()
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await query.answer("Error processing request", show_alert=True)

# ================= START HANDLER (PM) =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    await add_user_to_db(msg.from_user)
    data = msg.command[1] if len(msg.command) > 1 else ""

    # FSUB Check
    try:
        await client.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
    except UserNotParticipant:
        invite = (await client.get_chat(FSUB_CHANNEL)).invite_link or MAIN_CHANNEL_LINK
        me = await client.get_me()
        buttons = [[InlineKeyboardButton("📢 JOIN CHANNEL 📢", url=invite)]]
        if data:
            try_again_link = f"https://t.me/{me.username}?start={data}"
            buttons.append([InlineKeyboardButton("🔄 TRY AGAIN / VERIFY 🔄", url=try_again_link)])
        btn = InlineKeyboardMarkup(buttons)
        return await msg.reply("❌ Pehle channel join karein!", reply_markup=btn)
    except Exception:
        pass

    if not data: 
        return await msg.reply("👋 **Namaste!** Bot active hai. Search group me jaakar movie search karein.")

    if data.startswith("file_"):
        res = await client.movies.find_one({"_id": ObjectId(data.split("_")[1])})
        if res:
            cap = f"📂 `{res.get('original_title', res['title'])}`\n\n⚠️ **5 min mein delete ho jayegi.**"
            sf = await client.send_cached_media(msg.chat.id, res["file_id"], caption=cap)
            asyncio.create_task(delete_after_delay([sf], 300))
            
    elif data.startswith("all_"):
        try:
            b64_str = data.split("_", 1)[1]
            b64_str += "=" * ((4 - len(b64_str) % 4) % 4)
            search_q = base64.urlsafe_b64encode(b64_str).decode()
        except Exception:
            search_q = unquote(data.split("_", 1)[1])

        cursor = client.movies.find({"title": {"$regex": search_q, "$options": "i"}})
        results = await cursor.to_list(length=100)

        if not results:
            words = search_q.split()
            if words:
                keyword = max(words, key=len)
                cursor = client.movies.find({"title": {"$regex": keyword, "$options": "i"}})
                results = await cursor.to_list(length=100)

        if not results:
            return await msg.reply("❌ Files nahi mili!")

        sts = await msg.reply(f"🚀 **Found {len(results)} files. Sending...**")
        sent_messages = []

        for res in results:
            try:
                m = await client.send_cached_media(
                    msg.chat.id,
                    res["file_id"],
                    caption=f"📂 `{res.get('original_title', res['title'])}`\n\n⚠️ **5 min mein delete ho jayegi.**"
                )
                sent_messages.append(m)
                await asyncio.sleep(1.2)
            except Exception:
                pass

        await sts.edit("✅ **Batch Complete!**")
        asyncio.create_task(delete_after_delay(sent_messages + [sts], 300))

# ================= ADMIN COMMANDS =================

@app.on_message(filters.command(["broadcast", "sms"]) & filters.user(ADMIN_IDS))
async def broadcast_command(client, msg):
    if not msg.reply_to_message and len(msg.command) < 2:
        return await msg.reply("Usage:\n`/broadcast <Your Message>` OR reply to a message with `/broadcast`")

    users_cursor = client.users.find({})
    users = await users_cursor.to_list(length=10000)
    
    total = len(users)
    success = 0
    blocked = 0
    failed = 0

    sts = await msg.reply(f"📢 **Broadcasting SMS to {total} users...**")

    for u in users:
        uid = u["user_id"]
        try:
            if msg.reply_to_message:
                await msg.reply_to_message.copy(uid)
            else:
                text = msg.text.split(" ", 1)[1]
                await client.send_message(uid, text)
            success += 1
            await asyncio.sleep(0.05)
        except (UserIsBlocked, InputUserDeactivated):
            blocked += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
            success += 1
        except Exception:
            failed += 1

    await sts.edit(
        f"✅ **Broadcast Completed!**\n\n"
        f"📊 **Total Users:** `{total}`\n"
        f"✅ **Successful:** `{success}`\n"
        f"🚫 **Blocked/Deleted:** `{blocked}`\n"
        f"❌ **Failed:** `{failed}`"
    )

@app.on_message(filters.command(["details", "tmdb"]) & filters.user(ADMIN_IDS))
async def tmdb_details_cmd(client, msg):
    if len(msg.command) < 2:
        return await msg.reply("Usage: `/details <movie name>` (e.g. `/details Jawan`)")

    query = " ".join(msg.command[1:])
    sts = await msg.reply("🔍 Fetching details from TMDB...")

    details = await get_tmdb_details(query)
    if not details:
        return await sts.edit(f"❌ `{query}` ki details nahi mili!")

    status_str = f"{details['status']}"
    if details['status'] == "Upcoming":
        status_str += f" (Releasing in {details['days_remaining']} days)"

    text = (
        f"🎬 **MOVIE DETAILS (AI / TMDB)**\n\n"
        f"📌 **Title:** `{details['title']}`\n"
        f"📅 **Release Date:** `{details['release_date']}`\n"
        f"⭐ **Rating:** `{details['rating']}`\n"
        f"🚀 **Status:** `{status_str}`\n\n"
        f"📖 **Overview:**\n_{details['overview']}_"
    )

    if details.get("poster"):
        await client.send_photo(msg.chat.id, photo=details["poster"], caption=text)
        await sts.delete()
    else:
        await sts.edit(text)

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    original_title = msg.caption or file.file_name or "Unknown"
    search_title = clean_name(original_title)

    # Save to Movies Collection
    await client.movies.insert_one({
        "title": search_title,
        "original_title": original_title,
        "file_id": file.file_id
    })

    await msg.reply_text(f"✅ Added to DB: {original_title}")

    # Check matching requests from MongoDB
    req_cursor = client.requests.find({
        "$or": [
            {"clean_name": {"$regex": search_title, "$options": "i"}},
            {"movie_name": {"$regex": search_title, "$options": "i"}}
        ]
    })
    requests = await req_cursor.to_list(length=100)

    for req in requests:
        for user_id in req.get("requested_users", []):
            if user_id > 0:
                try:
                    alert_msg = (
                        f"🎉 **MOVIE AVAILABLE NOW!**\n\n"
                        f"Aapne jis movie (`{original_title}`) ke liye request ki thi, wo ab hamare database me add ho chuki hai! 🎬\n\n"
                        f"👉 Search Group me jaakar dobara search karein aur file praapt karein."
                    )
                    await client.send_message(user_id, alert_msg)
                except Exception as err:
                    logger.error(f"Failed to notify user {user_id}: {err}")

        # AUTOMATICALLY DELETE REQUEST FROM MONGODB AFTER NOTIFICATION
        await client.requests.delete_one({"_id": req["_id"]})

@app.on_message(filters.command("shortlink") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    choice = msg.command[1].lower() if len(msg.command) > 1 else ""
    if choice == "on":
        SHORTLINK_ENABLED = True
        await msg.reply("✅ Shortlink ON")
    elif choice == "off":
        SHORTLINK_ENABLED = False
        await msg.reply("❌ Shortlink OFF")

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    count = await client.movies.count_documents({})
    users_cnt = await client.users.count_documents({})
    reqs_cnt = await client.requests.count_documents({})
    await msg.reply(f"📊 **BOT STATS**\n\n🎬 Total Movies: `{count}`\n👥 Total Users: `{users_cnt}`\n📥 Pending Requests: `{reqs_cnt}`")

@app.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie_cmd(client, msg):
    if len(msg.command) < 2:
        return await msg.reply("Usage:\n/del movie_name")

    query = clean_name(" ".join(msg.command[1:]))
    result = await client.movies.delete_many({"title": {"$regex": query, "$options": "i"}})
    await msg.reply(f"🗑️ Deleted: {result.deleted_count} movie(s).")

# ================= RUNNER =================

async def start_bot():
    try:
        app_web = web.Application()
        app_web.router.add_get("/", lambda r: web.Response(text="Bot Alive and Running on Render!"))
        runner = web.AppRunner(app_web)
        await runner.setup()
        
        port = int(os.environ.get("PORT", 8080))
        await web.TCPSite(runner, "0.0.0.0", port).start()
        logger.info(f"🌐 Web server started on port {port}")

        await app.start()
        logger.info("🤖 Pyrogram Bot Started Successfully!")
        await idle()
        await app.stop()
    except Exception as e:
        logger.error(f"💥 CRITICAL STARTUP ERROR: {e}", exc_info=True)
        raise e

if __name__ == "__main__":
    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        print("Bot Stopped Manual.")
