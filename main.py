import os, re, asyncio, aiohttp
from aiohttp import web
from pyrogram import Client, filters
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from bson.objectid import ObjectId

# ================= ENV CONFIG =================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URL = os.environ.get("MONGO_URL", "")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")

STORAGE_CHANNEL = int(os.environ.get("STORAGE_CHANNEL", 0))
SEARCH_CHAT = int(os.environ.get("SEARCH_CHAT", 0))
FSUB_CHANNEL = int(os.environ.get("FSUB_CHANNEL", 0))
MAIN_CHANNEL_LINK = os.environ.get("MAIN_CHANNEL_LINK", "")

ADMIN_IDS = [int(x) for x in re.findall(r"\d+", os.environ.get("ADMIN_IDS", ""))]

SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN", "")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY", "")
SHORTLINK_ENABLED = True

# ================= DATABASE =================
db = AsyncIOMotorClient(MONGO_URL)["PratapCinemaBot"]["movies"]

# ================= BOT =================
app = Client("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ================= WEB SERVER (RENDER) =================
async def health_check(request):
    return web.Response(text="Bot is alive")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

# ================= HELPERS =================
def clean_name(text):
    if not text:
        return ""
    text = text.lower()
    junk = ["1080p","720p","480p","x264","x265","hevc","hindi","english"]
    for j in junk:
        text = text.replace(j, "")
    return " ".join(text.replace(".", " ").replace("_", " ").split())

async def get_short(url):
    if not SHORTLINK_ENABLED or not SHORT_DOMAIN:
        return url
    try:
        api = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as s:
            async with s.get(api, timeout=10) as r:
                j = await r.json()
                return j.get("shortenedUrl", url)
    except:
        return url

async def auto_delete(msg, sec=120):
    await asyncio.sleep(sec)
    try:
        await msg.delete()
    except:
        pass

# ================= ADMIN COMMANDS =================
@app.on_message(filters.command("pratap.movie") & filters.user(ADMIN_IDS))
async def stats(_, m):
    c = await db.count_documents({})
    await m.reply(f"📊 Total Movies: `{c}`")

@app.on_message(filters.command("shortnr") & filters.user(ADMIN_IDS))
async def shortnr(_, m):
    global SHORTLINK_ENABLED
    if len(m.command) < 2:
        return
    SHORTLINK_ENABLED = m.command[1].lower() == "on"
    await m.reply(f"🔗 Shortnr {'ON' if SHORTLINK_ENABLED else 'OFF'}")

@app.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie(_, m):
    if len(m.command) < 2:
        return
    q = " ".join(m.command[1:])
    r = await db.delete_many({"title": {"$regex": q, "$options": "i"}})
    await m.reply(f"🗑️ Deleted `{r.deleted_count}` movies")

# ================= STORAGE =================
@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_movie(_, m):
    f = m.video or m.document
    title = clean_name(m.caption or f.file_name or "unknown")
    await db.update_one(
        {"title": title},
        {"$set": {"file_id": f.file_id}},
        upsert=True
    )
    await m.reply_text(f"✅ Movie Added: `{title}`")

# ================= SEARCH =================
@app.on_message(filters.chat(SEARCH_CHAT) & filters.text)
async def search(_, m):
    if "http://" in m.text or "https://" in m.text:
        return await m.reply("❌ Link allowed nahi. Sirf movie name search karein.")

    q = clean_name(m.text)
    if len(q) < 3:
        return

    wait = await m.reply("😔 Searching...")
    res = await db.find_one({"title": {"$regex": q, "$options": "i"}})

    if not res:
        await wait.edit("😢 Movie nahi mili")
        await auto_delete(wait, 15)
        return

    me = await app.get_me()
    start_link = f"https://t.me/{me.username}?start=file_{res['_id']}"
    final_link = await get_short(start_link)

    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 GET LINK", url=final_link)]])

    cap = (
        f"🎥 **Movie:** `{res['title']}`\n"
        f"👤 **User:** {m.from_user.first_name}\n"
        f"🆔 `{m.from_user.id}`"
    )

    msg = await m.reply(cap, reply_markup=btn)
    await wait.delete()
    await auto_delete(msg)

# ================= START / FSUB =================
@app.on_message(filters.command("start") & filters.private)
async def start(_, m):
    try:
        await app.get_chat_member(FSUB_CHANNEL, m.from_user.id)
    except UserNotParticipant:
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 JOIN CHANNEL", url=MAIN_CHANNEL_LINK)],
            [InlineKeyboardButton(
                "✅ TRY AGAIN",
                url=f"https://t.me/{(await app.get_me()).username}?start={m.command[1] if len(m.command)>1 else ''}"
            )]
        ])
        return await m.reply("❌ Pehle channel join karo", reply_markup=btn)

    if len(m.command) > 1 and m.command[1].startswith("file_"):
        mid = m.command[1].split("_")[1]
        r = await db.find_one({"_id": ObjectId(mid)})
        if r:
            msg = await m.reply_cached_media(r["file_id"])
            await auto_delete(msg)

# ================= MAIN =================
async def main():
    await start_web_server()
    await app.start()
    await asyncio.Event().wait()

asyncio.run(main())
