import os
import re
import asyncio
import aiohttp
import logging
from pyrogram import Client, filters
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

API_ID = int(get_clean_var("API_ID", "0"))
API_HASH = get_clean_var("API_HASH", "")
BOT_TOKEN = get_clean_var("BOT_TOKEN", "")
MONGO_URL = get_clean_var("MONGO_URL", "")

ADMIN_IDS = [int(x) for x in get_clean_var("ADMIN_IDS", "0").split()]
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

# ================= ADMIN COMMANDS =================

@app.on_message(filters.command("delall") & filters.user(ADMIN_IDS))
async def delete_all_movies(client, msg):
    try:
        await client.movies.delete_many({})
        await msg.reply("🗑️ **Database Cleared!** Saari files delete ho gayi hain.")
    except Exception as e:
        await msg.reply(f"❌ Error: {e}")

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    count = await client.movies.count_documents({})
    await msg.reply(f"📊 **Total Movies:** `{count}`")

# ================= SEARCH LOGIC (AUTO-FILTER + SPELLING) =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "delall", "shortlink", "del"]))
async def search_movie(client, msg):
    original_query = msg.text
    query = clean_name(original_query)
    if len(query) < 2: return

    sm = await client.send_message(msg.chat.id, "🔍 **Searching...**")

    # 1. PEHLE EXACT SEARCH (Best Results)
    cursor = client.movies.find({"title": {"$regex": query, "$options": "i"}})
    results = await cursor.to_list(length=100)

    # 2. AGAR NA MILE TO SPELLING CORRECTION (Word-by-word Search)
    if not results:
        words = query.split()
        if len(words) > 1:
            # Sabse bade word se search karega (Jisme galti kam ho sakti hai)
            keyword = max(words, key=len)
            cursor = client.movies.find({"title": {"$regex": keyword, "$options": "i"}})
            results = await cursor.to_list(length=100)

    if not results:
        await sm.edit(f"❌ `{original_query}` nahi mili! Spelling check karein.")
        asyncio.create_task(delete_after_delay([sm, msg], 15))
        return 

    markup = await get_search_buttons(query, results, offset=0)
    cap = f"🎬 **Results for:** `{original_query}`\n👤 **Requested by:** {msg.from_user.first_name if msg.from_user else 'User'}"
    
    await client.send_message(msg.chat.id, cap, reply_markup=markup)
    await sm.delete()
    try: await msg.delete()
    except: pass

# ================= HANDLERS =================

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
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL FIRST 📢", url=invite)],
                                    [InlineKeyboardButton("✅ TRY AGAIN", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1] if len(msg.command)>1 else ''}")]])
        return await msg.reply("❌ **Access Denied!** Join channel first.", reply_markup=btn)
    except: pass

    if len(msg.command) < 2: return await msg.reply("👋 Namaste! Group me movie search karein.")

    data = msg.command[1]
    if data.startswith("file_"):
        m_id = data.split("_")[1]
        res = await client.movies.find_one({"_id": ObjectId(m_id)})
        if res:
            sf = await client.send_cached_media(msg.chat.id, res["file_id"], caption=f"📂 `{res['title']}`\n\n⚠️ 2 min me delete ho jayegi!")
            asyncio.create_task(delete_after_delay([sf], 120))
    
    elif data.startswith("all_"):
        search_q = data.split("_", 1)[1]
        cursor = client.movies.find({"title": {"$regex": search_q, "$options": "i"}})
        results = await cursor.to_list(length=50)
        for res in results:
            try:
                await client.send_cached_media(msg.chat.id, res["file_id"], caption=f"📂 `{res['title']}`")
                await asyncio.sleep(1)
            except: pass

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    title = clean_name(msg.caption or file.file_name or "Unknown")
    await client.movies.insert_one({"title": title, "file_id": file.file_id, "caption": msg.caption or title})
    await msg.reply_text(f"✅ Added: `{title}`")

# ================= WEB SERVER =================
async def health_check(request): return web.Response(text="Alive")
async def start_web_server():
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    app.run()
