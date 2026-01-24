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

# ================= FIXED CONFIG (No Render Variables Needed) =================
API_ID = int(os.environ.get("API_ID", "24119315")) # Aapka API ID
API_HASH = os.environ.get("API_HASH", "899e69c1737f59f635f994df5684617c") # Aapka API HASH
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7718044733:AAFlz-6_7hY39_KjBfS3A86_N7pX82_v_Yc") # Aapka TOKEN

# Aapki IDs jo aapne di hain
STORAGE_CHANNEL = -1003536285620
SEARCH_CHAT = -1003556253573
FSUB_CHANNEL = -1003652459294
MAIN_CHANNEL_LINK = "https://t.me/Movies2026Cinema"
ADMIN_IDS = [6429831771]

MONGO_URL = os.environ.get("MONGO_URL") # Isko Render se hi rehne dena safety ke liye
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")

WATERMARK = "👁️ 2  [Movies 2026 - Cinema Pratap ❤️🌹]\n\n⚠️ Ye file 2 minute me auto delete ho jayegi!"
SHORTLINK_ENABLED = True

# ================= DB & BOT =================
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["PratapCinemaBot"]
movies = db["movies"]
bot = Client("Arjun_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ================= HELPERS =================
async def auto_delete(msg, delay=120):
    await asyncio.sleep(delay)
    try: await msg.delete()
    except: pass

async def get_poster(query):
    try:
        if TMDB_API_KEY:
            url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"
            async with aiohttp.ClientSession() as s:
                async with s.get(url) as r:
                    res = await r.json()
                    if res.get('results'):
                        path = res['results'][0].get('poster_path')
                        return f"https://image.tmdb.org/t/p/w342{path}"
    except: pass
    return "https://telegra.ph/file/0f0f3a47990177708573a.jpg"

# ================= ADMIN COMMANDS =================
@bot.on_message(filters.command(["pratap", "del", "shortnr"]) & filters.user(ADMIN_IDS))
async def admin_handler(client, msg):
    cmd = msg.command[0]
    if cmd == "pratap":
        count = await movies.count_documents({})
        await msg.reply(f"📊 **Total Movies:** `{count}`\n✅ **DB Connected**")
    elif cmd == "del":
        if len(msg.command) < 2: return await msg.reply("❌ Naam likho delete ke liye.")
        query = " ".join(msg.command[1:])
        res = await movies.delete_many({"title": {"$regex": query, "$options": "i"}})
        await msg.reply(f"🗑️ `{res.deleted_count}` movies delete kar di gayi.")
    elif cmd == "shortnr":
        global SHORTLINK_ENABLED
        SHORTLINK_ENABLED = (msg.command[1].lower() == "on") if len(msg.command) > 1 else SHORTLINK_ENABLED
        await msg.reply(f"✅ **Shortlink Enabled:** {SHORTLINK_ENABLED}")

# ================= SEARCH & FORCE JOIN =================
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["pratap", "del", "shortnr"]))
async def search_handler(client, msg):
    query = msg.text.strip().lower()
    if len(query) < 3: return
    asyncio.create_task(auto_delete(msg, 60))
    res = await movies.find_one({"title": {"$regex": query, "$options": "i"}})
    if res:
        poster = await get_poster(res['title'])
        link = f"https://t.me/{client.me.username}?start=file_{res['_id']}"
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 GET MOVIE", url=link)]])
        sent = await client.send_photo(msg.chat.id, photo=poster, caption=f"🎥 **Movie Mil Gayi!**\n\n👤 **User:** {msg.from_user.mention}\n🎬 **Name:** `{res['title'].upper()}`", reply_markup=btn)
        asyncio.create_task(auto_delete(sent, 60))

@bot.on_message(filters.private & filters.command("start"))
async def start_handler(client, msg):
    try:
        await client.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
    except UserNotParticipant:
        arg = msg.command[1] if len(msg.command) > 1 else ""
        return await msg.reply("❌ **Pehle Join Channel!**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL", url=MAIN_CHANNEL_LINK)], [InlineKeyboardButton("🔄 TRY AGAIN", url=f"https://t.me/{client.me.username}?start={arg}")]]))

    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        f_id = msg.command[1].replace("file_", "")
        data = await movies.find_one({"_id": ObjectId(f_id)})
        if data:
            f_sent = await client.send_cached_media(msg.chat.id, data["file_id"], caption=WATERMARK)
            asyncio.create_task(auto_delete(f_sent, 120))
    else:
        await msg.reply("👋 Bot active hai!")

# ================= STORAGE =================
@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def storage_handler(_, msg):
    file = msg.video or msg.document
    title = (msg.caption or file.file_name or "unknown").lower()
    await movies.insert_one({"title": title.strip(), "file_id": file.file_id})
    await msg.reply(f"✅ Saved: `{title.strip()}`")

# ================= RUN =================
async def main_run():
    app = web.Application()
    app.router.add_get("/", lambda _: web.Response(text="Running"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()
    await bot.start()
    await idle()

if __name__ == "__main__":
    asyncio.run(main_run())
