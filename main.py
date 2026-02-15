import os
import re
import asyncio
import aiohttp
import logging
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import UserNotParticipant
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from aiohttp import web
from urllib.parse import quote, unquote # <-- Yahan 'unquote' add kiya gaya hai

# ================= CONFIGURATION =================
def get_clean_var(key, default=""):
    val = os.environ.get(key, default)
    return str(val).strip()

API_ID = int(get_clean_var("API_ID", "0"))
API_HASH = get_clean_var("API_HASH", "")
BOT_TOKEN = get_clean_var("BOT_TOKEN", "")
MONGO_URL = get_clean_var("MONGO_URL", "")

ADMIN_IDS = [int(x) for x in get_clean_var("ADMIN_IDS", "0").split()]
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

    async def start(self):
        await super().start()
        try:
            mongo_client = AsyncIOMotorClient(MONGO_URL)
            db = mongo_client["PratapCinemaBot"]
            self.movies = db["movies"]
            print("✅ MongoDB Connected!")
        except Exception as e:
            print(f"❌ MongoDB Error: {e}")
        print(f"🚀 BOT STARTED as @{self.me.username}")

    async def stop(self, *args):
        await super().stop()
        print("Bot Stopped.")

app = MovieBot()

# ================= HELPERS =================

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

def clean_name(text):
    if not text: return ""
    text = text.lower()
    junk = [r'\(.*?\)', r'\[.*?\]', '1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english', 'dual audio', 'web-dl', 'bluray', 'camrip', 'pre-dvd']
    for word in junk: text = re.sub(word, '', text)
    return " ".join(text.replace(".", " ").replace("_", " ").split()).strip()

async def get_search_buttons(query, results, offset=0):
    btn_list = []
    me = await app.get_me()
    for res in results[offset : offset + PAGE_SIZE]:
        db_id = str(res["_id"])
        db_title = res["title"]
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
    btn_list.append([InlineKeyboardButton("📂 GET ALL FILES (IN PM) 📂", url=f"https://t.me/{me.username}?start=all_{quote(query)}")])
    return InlineKeyboardMarkup(btn_list)

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except: pass

# ================= COMMANDS =================

@app.on_message(filters.command("shortlink") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Usage: `/shortlink on` or `/shortlink off`")
    choice = msg.command[1].lower()
    if choice == "on":
        SHORTLINK_ENABLED = True
        await msg.reply("✅ Shortlink: ON")
    elif choice == "off":
        SHORTLINK_ENABLED = False
        await msg.reply("❌ Shortlink: OFF")

@app.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie_cmd(client, msg):
    if len(msg.command) < 2:
        return await msg.reply("Usage: `/del movie_name`")
    query = " ".join(msg.command[1:])
    result = await client.movies.delete_many({"title": {"$regex": query, "$options": "i"}})
    await msg.reply(f"🗑️ Deleted {result.deleted_count} files matching `{query}`.")

@app.on_message(filters.command("delall") & filters.user(ADMIN_IDS))
async def delete_all_movies(client, msg):
    await client.movies.delete_many({})
    await msg.reply("🗑️ **FULL RESET:** Database cleared.")

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    count = await client.movies.count_documents({})
    await msg.reply(f"📊 Total Files: `{count}`")

# ================= AUTO-FILTER SEARCH =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "delall", "del", "shortlink"]))
async def search_movie(client, msg):
    query = clean_name(msg.text)
    if len(query) < 2: return
    sm = await client.send_message(msg.chat.id, "🔍 Searching...")

    # 1. Exact Search
    cursor = client.movies.find({"title": {"$regex": query, "$options": "i"}})
    results = await cursor.to_list(length=100)

    # 2. Spelling Correction (Split Words)
    if not results:
        words = query.split()
        if len(words) > 0:
            keyword = max(words, key=len)
            cursor = client.movies.find({"title": {"$regex": keyword, "$options": "i"}})
            results = await cursor.to_list(length=100)

    if not results:
        await sm.edit(f"❌ Movie `{msg.text}` nahi mili! Spelling thik karein.")
        asyncio.create_task(delete_after_delay([sm, msg], 15))
        return 

    markup = await get_search_buttons(query, results, offset=0)
    await client.send_message(msg.chat.id, f"🎬 **Results for:** `{msg.text}`", reply_markup=markup)
    await sm.delete()
    try: await msg.delete()
    except: pass

@app.on_callback_query(filters.regex(r"^page_"))
async def handle_pagination(client, query: CallbackQuery):
    _, offset, search_q = query.data.split("_", 2)
    cursor = client.movies.find({"title": {"$regex": search_q, "$options": "i"}})
    results = await cursor.to_list(length=100)
    markup = await get_search_buttons(search_q, results, offset=int(offset))
    await query.message.edit_reply_markup(reply_markup=markup)

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    # FSUB Check
    try:
        await client.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
    except UserNotParticipant:
        invite = (await client.get_chat(FSUB_CHANNEL)).invite_link or MAIN_CHANNEL_LINK
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL 📢", url=invite)]])
        return await msg.reply("❌ Pehle channel join karein!", reply_markup=btn)
    except: pass

    if len(msg.command) < 2: return await msg.reply("👋 Namaste! Group me search karein.")

    data = msg.command[1]
    if data.startswith("file_"):
        res = await client.movies.find_one({"_id": ObjectId(data.split("_")[1])})
        if res:
            sf = await client.send_cached_media(msg.chat.id, res["file_id"], caption=f"📂 `{res['title']}`")
            asyncio.create_task(delete_after_delay([sf], 120))
    elif data.startswith("all_"):
        # Yahan par 'unquote' lagaya gaya hai taaki encoded URL wapas text ban jaye
        search_q = unquote(data.split("_", 1)[1])
        cursor = client.movies.find({"title": {"$regex": search_q, "$options": "i"}})
        async for res in cursor:
            await client.send_cached_media(msg.chat.id, res["file_id"])
            await asyncio.sleep(1)

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    title = clean_name(msg.caption or file.file_name or "Unknown")
    await client.movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply_text(f"✅ Added: {title}")

# ================= RUNNER =================

async def start_bot():
    # 1. Start Web Server (For Render Ping)
    app_web = web.Application()
    app_web.router.add_get("/", lambda r: web.Response(text="Bot Alive"))
    runner = web.AppRunner(app_web)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()
    
    # 2. Start Bot
    await app.start()
    
    # 3. Keep Running
    await idle()
    await app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_bot())
