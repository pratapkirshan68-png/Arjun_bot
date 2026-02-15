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
from urllib.parse import quote

# ================= CONFIGURATION =================
def get_clean_var(key, default=""):
    val = os.environ.get(key, default)
    return str(val).strip()

# Check values correctly
API_ID_STR = get_clean_var("API_ID", "0")
API_ID = int(API_ID_STR) if API_ID_STR.isdigit() else 0
API_HASH = get_clean_var("API_HASH", "")
BOT_TOKEN = get_clean_var("BOT_TOKEN", "")
MONGO_URL = get_clean_var("MONGO_URL", "")

ADMIN_IDS = [int(x) for x in get_clean_var("ADMIN_IDS", "0").split() if x.isdigit()]
STORAGE_CHANNEL = int(get_clean_var("STORAGE_CHANNEL", "0")) 
LOG_CHANNEL = int(get_clean_var("LOG_CHANNEL", "0")) 
SEARCH_CHAT = int(get_clean_var("SEARCH_CHAT", "0")) 
FSUB_CHANNEL = int(get_clean_var("FSUB_CHANNEL", "0")) 
MAIN_CHANNEL_LINK = get_clean_var("MAIN_CHANNEL_LINK", "https://t.me/Movies2026Cinema")

TMDB_API_KEY = get_clean_var("TMDB_API_KEY", "")
SHORT_DOMAIN = get_clean_var("SHORT_DOMAIN", "arolinks.com")
SHORT_API_KEY = get_clean_var("SHORT_API_KEY", "")

SHORTLINK_ENABLED = True 
PAGE_SIZE = 6 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MovieBot(Client):
    def __init__(self):
        super().__init__("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
        self.mongo_client = None
        self.db = None
        self.movies = None

    async def start(self):
        if not API_ID or not API_HASH or not BOT_TOKEN:
            logger.critical("❌ API_ID, API_HASH, or BOT_TOKEN is missing!")
            return
            
        await super().start()
        try:
            self.mongo_client = AsyncIOMotorClient(MONGO_URL)
            self.db = self.mongo_client["PratapCinemaBot"]
            self.movies = self.db["movies"]
            print("✅ MongoDB Connected!")
        except Exception as e:
            print(f"❌ MongoDB Error: {e}")
            
        self.bot_info = await self.get_me()
        print(f"🚀 BOT @{self.bot_info.username} STARTED")

    async def stop(self, *args):
        await super().stop()
        if self.mongo_client:
            self.mongo_client.close()

app = MovieBot()

# ================= HELPERS (Same as your code) =================
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
    junk = [r'\(.*?\)', r'\[.*?\]', '1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english', 'dual audio', 'web-dl', 'bluray']
    for word in junk: text = re.sub(word, '', text)
    return " ".join(text.replace(".", " ").replace("_", " ").split()).strip()

async def get_search_buttons(query, results, offset=0):
    btn_list = []
    for res in results[offset : offset + PAGE_SIZE]:
        db_id = str(res["_id"])
        db_title = res["title"]
        display_name = db_title[:35] + "..." if len(db_title) > 35 else db_title
        bot_url = f"https://t.me/{app.bot_info.username}?start=file_{db_id}"
        final_link = await get_shortlink(bot_url)
        btn_list.append([InlineKeyboardButton(f"🎬 {display_name}", url=final_link)])

    nav_btns = []
    if offset > 0:
        nav_btns.append(InlineKeyboardButton("⬅️ Back", callback_data=f"page_{offset - PAGE_SIZE}_{quote(query)}"))
    if offset + PAGE_SIZE < len(results):
        nav_btns.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{offset + PAGE_SIZE}_{quote(query)}"))
    
    if nav_btns: btn_list.append(nav_btns)
    btn_list.append([InlineKeyboardButton("📂 GET ALL FILES (IN PM) 📂", url=f"https://t.me/{app.bot_info.username}?start=all_{quote(query)}")])
    return InlineKeyboardMarkup(btn_list)

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except: pass

# ================= HANDLERS (Same as your code) =================
@app.on_message(filters.command("delall") & filters.user(ADMIN_IDS))
async def delete_all_movies(client, msg):
    try:
        await client.movies.delete_many({})
        await msg.reply("🗑️ **Database Cleared!**")
    except Exception as e:
        await msg.reply(f"❌ Error: {e}")

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    count = await client.movies.count_documents({})
    await msg.reply(f"📊 **Total Movies:** `{count}`")

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "delall", "shortlink", "del"]))
async def search_movie(client, msg):
    original_query = msg.text
    query = clean_name(original_query)
    if len(query) < 2: return
    sm = await client.send_message(msg.chat.id, "🔍 **Searching...**")
    cursor = client.movies.find({"title": {"$regex": query, "$options": "i"}})
    results = await cursor.to_list(length=100)
    if not results:
        words = query.split()
        if len(words) > 1:
            keyword = max(words, key=len)
            cursor = client.movies.find({"title": {"$regex": keyword, "$options": "i"}})
            results = await cursor.to_list(length=100)
    if not results:
        await sm.edit(f"❌ `{original_query}` nahi mili!")
        return 
    markup = await get_search_buttons(query, results, offset=0)
    cap = f"🎬 **Results for:** `{original_query}`"
    await client.send_message(msg.chat.id, cap, reply_markup=markup)
    await sm.delete()

@app.on_callback_query(filters.regex(r"^page_"))
async def handle_pagination(client, query: CallbackQuery):
    _, offset, search_q = query.data.split("_", 2)
    offset = int(offset)
    cursor = client.movies.find({"title": {"$regex": search_q, "$options": "i"}})
    results = await cursor.to_list(length=100)
    markup = await get_search_buttons(search_q, results, offset=offset)
    try: await query.message.edit_reply_markup(reply_markup=markup)
    except: pass
    await query.answer()

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    user_id = msg.from_user.id
    try:
        await client.get_chat_member(FSUB_CHANNEL, user_id)
    except UserNotParticipant:
        invite = (await client.get_chat(FSUB_CHANNEL)).invite_link or MAIN_CHANNEL_LINK 
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL 📢", url=invite)]])
        return await msg.reply("❌ Join channel first.", reply_markup=btn)
    except: pass

    if len(msg.command) < 2: return await msg.reply("👋 Group me movie search karein.")
    data = msg.command[1]
    if data.startswith("file_"):
        m_id = data.split("_")[1]
        res = await client.movies.find_one({"_id": ObjectId(m_id)})
        if res:
            sf = await client.send_cached_media(msg.chat.id, res["file_id"], caption=f"📂 `{res['title']}`")
    elif data.startswith("all_"):
        search_q = data.split("_", 1)[1]
        cursor = client.movies.find({"title": {"$regex": search_q, "$options": "i"}})
        results = await cursor.to_list(length=50)
        for res in results:
            await client.send_cached_media(msg.chat.id, res["file_id"])
            await asyncio.sleep(1)

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    title = clean_name(msg.caption or file.file_name or "Unknown")
    await client.movies.insert_one({"title": title, "file_id": file.file_id, "caption": msg.caption or title})
    await msg.reply_text(f"✅ Added: `{title}`")

# ================= WEB SERVER =================
async def health_check(request): 
    return web.Response(text="Bot is running!")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Web Server started on port {port}")

# ================= MAIN RUNNER =================
async def main():
    await start_web_server()
    await app.start()
    await idle()
    await app.stop()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
