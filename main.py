import os
import re
import asyncio
import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from bson.objectid import ObjectId
from aiohttp import web

# ================= CONFIG (Aapki 4 Main Variables) =================
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URL = os.environ["MONGO_URL"]
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")

STORAGE_CHANNEL = int(os.environ["STORAGE_CHANNEL"])
SEARCH_CHAT = int(os.environ["SEARCH_CHAT"])
FSUB_CHANNEL = int(os.environ["FSUB_CHANNEL"])  # <--- Ye raha
MAIN_CHANNEL_LINK = os.environ["MAIN_CHANNEL_LINK"] # <--- Ye raha

ADMIN_IDS = [int(x) for x in re.findall(r'-?\d+', os.environ.get("ADMIN_IDS", ""))]
SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY")

# --- LOGIC SETTINGS ---
WATERMARK_TEXT = "👁️ 2  [Movies 2026 - Cinema Pratap ❤️🌹]\n\n⚠️ Ye file 2 minute me auto delete ho jayegi!"
AUTO_DEL_TIME = 120 
SEARCH_DEL_TIME = 60 
SHORTLINK_ENABLED = True

# ================= DB & BOT =================
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["PratapCinemaBot"]
movies = db["movies"]
bot = Client("pratap_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ================= HELPERS =================
async def auto_delete(msg, delay):
    await asyncio.sleep(delay)
    try: await msg.delete()
    except: pass

async def get_poster(query):
    if not TMDB_API_KEY: return "https://telegra.ph/file/0f0f3a47990177708573a.jpg"
    try:
        url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                res = await r.json()
                if res.get('results'):
                    path = res['results'][0].get('poster_path')
                    return f"https://image.tmdb.org/t/p/w342{path}"
    except: pass
    return "https://telegra.ph/file/0f0f3a47990177708573a.jpg"

# ================= FORCE JOIN & DELIVERY =================

@bot.on_message(filters.private & filters.command("start"))
async def delivery_handler(client, msg):
    user_id = msg.from_user.id
    
    # --- FORCE JOIN LOGIC (Ab nahi katega) ---
    try:
        await client.get_chat_member(FSUB_CHANNEL, user_id)
    except UserNotParticipant:
        arg = msg.command[1] if len(msg.command) > 1 else ""
        return await msg.reply(
            "❌ **Bhai, pehle channel join karo tabhi file milegi!**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 JOIN CHANNEL", url=MAIN_CHANNEL_LINK)],
                [InlineKeyboardButton("🔄 TRY AGAIN", url=f"https://t.me/{client.me.username}?start={arg}")]
            ])
        )

    # File Delivery
    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        f_id = msg.command[1].replace("file_", "")
        data = await movies.find_one({"_id": ObjectId(f_id)})
        if data:
            f_sent = await client.send_cached_media(msg.chat.id, data["file_id"], caption=WATERMARK_TEXT)
            asyncio.create_task(auto_delete(f_sent, AUTO_DEL_TIME))

# ================= 3 ADMIN COMMANDS =================

@bot.on_message(filters.command(["pratap", "del", "shortnr"]) & filters.user(ADMIN_IDS))
async def admin_handler(client, msg):
    if "/pratap" in msg.text:
        c = await movies.count_documents({})
        await msg.reply(f"📊 Total Movies: {c}")
    elif "/del" in msg.text:
        query = " ".join(msg.command[1:])
        res = await movies.delete_many({"title": {"$regex": query, "$options": "i"}})
        await msg.reply(f"🗑 Deleted: {res.deleted_count}")
    elif "/shortnr" in msg.text:
        global SHORTLINK_ENABLED
        SHORTLINK_ENABLED = ("on" in msg.text.lower())
        await msg.reply(f"✅ Shortlink: {SHORTLINK_ENABLED}")

# ================= SEARCH & STORAGE =================

@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["pratap", "del", "shortnr"]))
async def search_handler(client, msg):
    query = msg.text.strip().lower()
    if len(query) < 3: return
    asyncio.create_task(auto_delete(msg, SEARCH_DEL_TIME))
    
    res = await movies.find_one({"title": {"$regex": query, "$options": "i"}})
    if res:
        poster = await get_poster(res['title'])
        # Shortlink button logic
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 GET MOVIE", url=f"https://t.me/{client.me.username}?start=file_{res['_id']}") ]])
        sent = await client.send_photo(msg.chat.id, photo=poster, caption=f"🎥 **Movie Mil Gayi!**\n\n👤 **User:** {msg.from_user.mention}\n🎬 **Name:** `{res['title'].upper()}`", reply_markup=btn)
        asyncio.create_task(auto_delete(sent, SEARCH_DEL_TIME))

@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def store_handler(_, msg):
    file = msg.video or msg.document
    title = (msg.caption or file.file_name or "unknown").lower()
    await movies.insert_one({"title": title.strip(), "file_id": file.file_id})
    await msg.reply(f"✅ Saved: {title}")

# ================= RUN =================
async def runner():
    app = web.Application()
    app.router.add_get("/", lambda _: web.Response(text="Running"))
    r = web.AppRunner(app)
    await r.setup()
    await web.TCPSite(r, "0.0.0.0", 8080).start()
    await bot.start()
    await idle()

if __name__ == "__main__":
    asyncio.run(runner())
