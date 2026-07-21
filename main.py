import os
import re
import asyncio
import aiohttp
import logging
import base64
from datetime import datetime
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import UserNotParticipant, PeerIdInvalid, UserIsBlocked, InputUserDeactivated
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from aiohttp import web
from urllib.parse import quote, unquote
from fuzzywuzzy import fuzz
import google.generativeai as genai

# ================= CONFIGURATION =================
def get_clean_var(key, default=""):
    val = os.environ.get(key, default)
    return str(val).strip()

API_ID = int(get_clean_var("API_ID", "0"))
API_HASH = get_clean_var("API_HASH", "")
BOT_TOKEN = get_clean_var("BOT_TOKEN", "")
MONGO_URL = get_clean_var("MONGO_URL", "")
TMDB_API_KEY = get_clean_var("TMDB_API_KEY", "")
GEMINI_API_KEY = get_clean_var("GEMINI_API_KEY", "")  # For Gemini AI Chat
ADMIN_IDS = [int(x) for x in get_clean_var("ADMIN_IDS", "0").split() if x.isdigit()]
STORAGE_CHANNEL = int(get_clean_var("STORAGE_CHANNEL", "0")) 
SEARCH_CHAT = int(get_clean_var("SEARCH_CHAT", "0")) 
FSUB_CHANNEL = int(get_clean_var("FSUB_CHANNEL", "0")) 
MAIN_CHANNEL_LINK = get_clean_var("MAIN_CHANNEL_LINK", "https://t.me/Movies2026Cinema")
SHORT_DOMAIN = get_clean_var("SHORT_DOMAIN", "arolinks.com")
SHORT_API_KEY = get_clean_var("SHORT_API_KEY", "")

SHORTLINK_ENABLED = True 
PAGE_SIZE = 6 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ================= BOT CLIENT =================
class MovieBot(Client):
    def __init__(self):
        super().__init__("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
        self.movies = None
        self.requests = None
        self.users = None

    async def start(self):
        await super().start()
        try:
            mongo_client = AsyncIOMotorClient(MONGO_URL)
            db = mongo_client["PratapCinemaBot"]
            self.movies = db["movies"]
            self.requests = db["movie_requests"]
            self.users = db["users"]
            print("✅ MongoDB Connected Successfully!")
        except Exception as e:
            print(f"❌ MongoDB Connection Error: {e}")
        print(f"🚀 BOT STARTED as @{self.me.username}")

    async def stop(self, *args):
        await super().stop()
        print("Bot Stopped.")

app = MovieBot()

# ================= HELPERS & UTILS =================
def clean_name(text):
    if not text: return ""
    text = text.lower()
    junk = [r'\(.*?\)', r'\[.*?\]', '1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english', 'dual audio', 'web-dl', 'bluray', 'camrip', 'pre-dvd']
    for word in junk: text = re.sub(word, '', text)
    # Remove special characters for smart clean search
    text = re.sub(r'[^a-zA-Z0-9\s\u0900-\u097F]', ' ', text)
    return " ".join(text.split()).strip()

async def get_poster(query):
    if not TMDB_API_KEY: return None
    try:
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(query)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if data.get("results"):
                    poster_path = data["results"][0].get("poster_path")
                    if poster_path:
                        return f"https://image.tmdb.org/t/p/w342{poster_path}"
    except Exception as e:
        logger.error(f"TMDB Poster Error: {e}")
    return None

async def check_upcoming_movie(query):
    """ Feature 1: Check TMDB for upcoming unreleased movies """
    if not TMDB_API_KEY: return None
    try:
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(query)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                results = data.get("results", [])
                today = datetime.now().date()
                
                for item in results:
                    release_date_str = item.get("release_date") or item.get("first_air_date")
                    if release_date_str:
                        rel_date = datetime.strptime(release_date_str, "%Y-%m-%d").date()
                        if rel_date > today:
                            days_left = (rel_date - today).days
                            poster = f"https://image.tmdb.org/t/p/w342{item.get('poster_path')}" if item.get('poster_path') else None
                            title = item.get("title") or item.get("name")
                            return {
                                "title": title,
                                "release_date": release_date_str,
                                "days_left": days_left,
                                "poster": poster
                            }
    except Exception as e:
        logger.error(f"Upcoming Check Error: {e}")
    return None

async def smart_db_search(client, query):
    """ Feature 5: Smart & Fuzzy Search Implementation """
    all_docs = await client.movies.find({}).to_list(length=2000)
    matched = []
    
    clean_q = clean_name(query)
    
    for doc in all_docs:
        doc_title = clean_name(doc.get("title", ""))
        
        # Regex or Partial check
        if clean_q in doc_title or doc_title in clean_q:
            matched.append(doc)
            continue
            
        # Fuzzy Match Check
        ratio = fuzz.partial_ratio(clean_q, doc_title)
        if ratio > 75:  # High confidence match threshold
            matched.append(doc)

    return matched

async def get_shortlink(url):
    if not SHORTLINK_ENABLED: return url
    try:
        api_url = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as resp:
                res = await resp.json()
                if res.get("status") == "success": return res["shortenedUrl"]
    except: pass
    return url

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
    
    if nav_btns: btn_list.append(nav_btns)
    
    query_b64 = base64.urlsafe_b64encode(query.encode()).decode().rstrip("=")
    btn_list.append([InlineKeyboardButton("📂 GET ALL FILES (IN PM) 📂", url=f"https://t.me/{me.username}?start=all_{query_b64}")])
    return InlineKeyboardMarkup(btn_list)

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except: pass

# ================= AUTO-FILTER SEARCH (GROUP) =================
@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "delall", "del", "shortlink", "broadcast", "sms", "ai"]))
async def search_movie(client, msg):
    is_admin = msg.from_user and msg.from_user.id in ADMIN_IDS
    query = clean_name(msg.text)
    if len(query) < 2: return
    
    sm = await client.send_message(msg.chat.id, "🔍 Searching...")
    
    # Smart Fuzzy DB Search
    results = await smart_db_search(client, msg.text)

    # Feature 1: Check Upcoming Movie if not found in DB
    if not results:
        upcoming_info = await check_upcoming_movie(msg.text)
        if upcoming_info:
            text = (
                f"🎬 **Movie:** `{upcoming_info['title']}`\n"
                f"📅 **Release Date:** `{upcoming_info['release_date']}`\n"
                f"📌 **Status:** Upcoming\n"
                f"⏳ **Days Remaining:** `{upcoming_info['days_left']} Days`\n\n"
                f"ℹ️ _Ye movie release hote hi humare database me add kar di jayegi!_"
            )
            await sm.delete()
            if upcoming_info['poster']:
                res_msg = await client.send_photo(msg.chat.id, photo=upcoming_info['poster'], caption=text)
            else:
                res_msg = await client.send_message(msg.chat.id, text)
            
            # Feature 6: Non-admin messages deleted after delay
            if not is_admin:
                asyncio.create_task(delete_after_delay([res_msg, msg], 300))
            return

    # Feature 2: Movie Not Found & Request System
    if not results:
        await sm.delete()
        req_msg = await client.send_message(
            msg.chat.id, 
            "Maaf kijiye, ye movie abhi hamare database me available nahi hai.\n\n"
            "Hamne aapki request admin ko bhej di hai.\n\n"
            "Jaise hi movie database me add hogi, aapko automatically private message (SMS) mil jayega."
        )
        
        # Save Request to Mongo
        await client.requests.update_one(
            {"user_id": msg.from_user.id, "query": query},
            {"$set": {"user_id": msg.from_user.id, "query": query, "time": datetime.now()}},
            upsert=True
        )
        
        # Feature 6: Exempt admins from auto-delete
        if not is_admin:
            asyncio.create_task(delete_after_delay([req_msg, msg], 60))
        return 

    # If results exist
    poster = await get_poster(query)
    markup = await get_search_buttons(query, results, offset=0)
    text = f"🎬 **Results for:** `{msg.text}`\n\n⏳ _Ye result 5 minute mein delete ho jayega._"
    
    try:
        if poster:
            res_msg = await client.send_photo(msg.chat.id, photo=poster, caption=text, reply_markup=markup)
        else:
            res_msg = await client.send_message(msg.chat.id, text=text, reply_markup=markup)
    except:
        res_msg = await client.send_message(msg.chat.id, text=text, reply_markup=markup)
        
    await sm.delete()
    
    # Feature 6: Admin Messages Never Auto-Delete
    if not is_admin:
        try: await msg.delete()
        except: pass
        asyncio.create_task(delete_after_delay([res_msg], 300))

# ================= START HANDLER (PM) =================
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    # Save User ID for Broadcast System
    await client.users.update_one({"user_id": msg.from_user.id}, {"$set": {"user_id": msg.from_user.id}}, upsert=True)
    
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
    except: pass
    
    if not data: return await msg.reply("👋 Namaste! Group me search karein.")
    
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
            search_q = base64.urlsafe_b64decode(b64_str).decode()
        except: search_q = unquote(data.split("_", 1)[1])
        
        results = await smart_db_search(client, search_q)
        if not results: return await msg.reply("❌ Files nahi mili!")
        
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
            except: pass
        await sts.edit("✅ **Batch Complete!**")
        asyncio.create_task(delete_after_delay(sent_messages + [sts], 300))

# ================= FEATURE 3: STORAGE UPLOAD & AUTO NOTIFY =================
@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    original_title = msg.caption or file.file_name or "Unknown"
    search_title = clean_name(original_title)
    
    # Save to Movies Database
    await client.movies.insert_one({
        "title": search_title,
        "original_title": original_title,
        "file_id": file.file_id
    })
    await msg.reply_text(f"✅ Added: {original_title}")

    # Feature 3: Automatic Requested Users Notification
    all_requests = await client.requests.find({}).to_list(length=5000)
    for req in all_requests:
        req_q = req.get("query", "")
        if req_q and (req_q in search_title or fuzz.partial_ratio(req_q, search_title) > 80):
            user_id = req.get("user_id")
            try:
                await client.send_message(
                    user_id,
                    "🎉 Aapki request wali movie ab hamare database me add ho gayi hai.\n\n"
                    "Kripya Search Group me dobara search karein."
                )
                # Delete request after notification
                await client.requests.delete_one({"_id": req["_id"]})
            except Exception as e:
                logger.error(f"Failed notification to {user_id}: {e}")

# ================= FEATURE 4: BROADCAST SYSTEM =================
@app.on_message(filters.command(["broadcast", "sms"]) & filters.user(ADMIN_IDS))
async def broadcast_cmd(client, msg):
    if not msg.reply_to_message:
        return await msg.reply("⚠️ Broadcast bhejne ke liye kisi message ko reply karein `/broadcast` ya `/sms` se.")
        
    status = await msg.reply("📢 **Broadcast shuru ho raha hai...**")
    users = await client.users.find({}).to_list(length=100000)
    
    total = len(users)
    success = 0
    blocked = 0
    failed = 0
    
    for u in users:
        uid = u.get("user_id")
        if not uid: continue
        try:
            await msg.reply_to_message.copy(uid)
            success += 1
            await asyncio.sleep(0.05)
        except (UserIsBlocked, InputUserDeactivated):
            blocked += 1
        except Exception:
            failed += 1
            
    report = (
        f"📊 **Broadcast Finished Report**\n\n"
        f"👥 **Total Users:** `{total}`\n"
        f"✅ **Sent Successfully:** `{success}`\n"
        f"🚫 **Blocked/Deleted:** `{blocked}`\n"
        f"❌ **Failed:** `{failed}`"
    )
    await status.edit(report)

# ================= FEATURE 7: AI CHAT (ADMIN PM ONLY - GEMINI API) =================
@app.on_message(filters.private & filters.user(ADMIN_IDS) & (filters.command("ai") | ~filters.command(["start", "pratap", "del", "shortlink", "broadcast", "sms"])))
async def ai_chat_handler(client, msg):
    if not GEMINI_API_KEY:
        return await msg.reply("⚠️ GEMINI_API_KEY set nahi hai.")

    prompt = msg.text.split(" ", 1)[1] if msg.text.startswith("/ai ") else msg.text
    if not prompt or prompt.startswith("/"): return

    st = await msg.reply("🤖 **AI Processing...**")
    
    try:
        system_instruction = (
            "You are an expert Telegram Bot, Python, Pyrogram, and MongoDB developer and coding assistant. "
            "Help the user in a clear, concise, and professional manner. You can reply in Hindi, English, or Hinglish as required."
        )
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=system_instruction
        )
        
        def generate_content():
            return model.generate_content(prompt)
            
        response = await asyncio.wait_for(asyncio.to_thread(generate_content), timeout=30.0)
        ai_reply = response.text if response and hasattr(response, 'text') else "⚠️ Response nahi mil saka."
        
        if len(ai_reply) > 4000:
            for i in range(0, len(ai_reply), 4000):
                await msg.reply(ai_reply[i:i+4000])
            await st.delete()
        else:
            await st.edit(ai_reply)
            
    except asyncio.TimeoutError:
        await st.edit("⚠️ **AI Error:** Response timed out. Kripya dobara try karein.")
    except Exception as e:
        logger.error(f"Gemini AI Error: {e}")
        await st.edit(f"❌ **AI Error:** `{e}`")

# ================= EXISTING ADMIN COMMANDS =================
@app.on_message(filters.command("shortlink") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    choice = msg.command[1].lower() if len(msg.command) > 1 else ""
    if choice == "on": SHORTLINK_ENABLED = True; await msg.reply("✅ ON")
    elif choice == "off": SHORTLINK_ENABLED = False; await msg.reply("❌ OFF")

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    count = await client.movies.count_documents({})
    users_count = await client.users.count_documents({})
    req_count = await client.requests.count_documents({})
    await msg.reply(
        f"📊 **Bot Status**\n\n"
        f"🎬 Total Movies: `{count}`\n"
        f"👤 Total Users: `{users_count}`\n"
        f"📌 Pending Requests: `{req_count}`"
    )

@app.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie_cmd(client, msg):
    if len(msg.command) < 2:
        return await msg.reply("Usage:\n/del movie_name")
    query = clean_name(" ".join(msg.command[1:]))
    result = await client.movies.delete_many({
        "title": {"$regex": query, "$options": "i"}
    })
    await msg.reply(f"🗑️ Deleted: {result.deleted_count} movie(s).")

# ================= RUNNER =================
async def start_bot():
    app_web = web.Application()
    app_web.router.add_get("/", lambda r: web.Response(text="Bot Alive"))
    runner = web.AppRunner(app_web)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()
    await app.start()
    await idle()
    await app.stop()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(start_bot())


