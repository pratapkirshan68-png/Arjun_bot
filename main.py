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

# ================= CONFIG =================
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URL = os.environ["MONGO_URL"]

STORAGE_CHANNEL = int(os.environ["STORAGE_CHANNEL"])
SEARCH_CHAT = int(os.environ["SEARCH_CHAT"]) # ID like -100...
FSUB_CHANNEL = int(os.environ["FSUB_CHANNEL"])

MAIN_CHANNEL_LINK = os.environ["MAIN_CHANNEL_LINK"] # Channel link for button
SEARCH_GROUP_LINK = os.environ.get("SEARCH_GROUP_LINK", "https://t.me/your_group") # Redirect link

ADMIN_IDS = [int(x) for x in re.findall(r'-?\d+', os.environ.get("ADMIN_IDS", ""))]
SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY")

SHORTLINK_ENABLED = True
WATERMARK_TEXT = "👁️ 2  [Movies 2026 - Cinema Pratap ❤️🌹]\n\n⚠️ Ye file 2 minute me auto delete ho jayegi!"

# ================= DB =================
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["PratapCinemaBot"]
movies = db["movies"]

# ================= WEB SERVER =================
async def health(_): return web.Response(text="Bot is Alive")
async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()

# ================= BOT CLIENT =================
bot = Client("pratap_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ================= HELPERS =================
async def auto_delete(msg, t):
    await asyncio.sleep(t)
    try: await msg.delete()
    except: pass

async def get_shortlink(url):
    if not SHORTLINK_ENABLED or not SHORT_DOMAIN: return url
    try:
        api = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as s:
            async with s.get(api, timeout=10) as r:
                j = await r.json()
                return j.get("shortenedUrl", url)
    except: return url

# ================= 1. PRIVATE REDIRECT (USER SEARCH) =================
# Agar user bot ko PM me text bhejta hai (commands ke ilawa)
@bot.on_message(filters.private & filters.text & ~filters.command(["start", "pratap", "shortnr", "del", "id"]))
async def private_redirect(_, msg):
    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔍 SEARCH GROUP ME JAO", url=SEARCH_GROUP_LINK)]])
    await msg.reply("Bhai, yahan search mat karo. Niche button par click karke search group me jao aur wahan movie mangon.", reply_markup=btn)

# ================= 2. GROUP SEARCH LOGIC =================
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "shortnr", "del", "id"]))
async def group_search(_, msg):
    query = msg.text.strip().lower()
    if len(query) < 3: return
    
    asyncio.create_task(auto_delete(msg, 60)) # User msg delete in 1 min
    
    res = await movies.find_one({"title": {"$regex": query, "$options": "i"}})
    if not res:
        err = await msg.reply(f"❌ Sorry {msg.from_user.mention}, ye movie nahi mili.")
        asyncio.create_task(auto_delete(err, 60))
        return

    me = await bot.get_me()
    link = await get_shortlink(f"https://t.me/{me.username}?start=file_{res['_id']}")
    
    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 GET MOVIE LINK", url=link)]])
    # Poster style message as requested
    sent = await msg.reply(
        f"🎥 **Movie Mil Gayi!**\n\n👤 **User:** {msg.from_user.mention}\n🆔 **ID:** `{msg.from_user.id}`\n\n👇 Niche link par click karke bot se file le lo.",
        reply_markup=btn
    )
    asyncio.create_task(auto_delete(sent, 60))

# ================= 3. START & FILE DELIVERY (FSUB) =================
@bot.on_message(filters.private & filters.command("start"))
async def start_handler(_, msg):
    user_id = msg.from_user.id
    
    # Force Join Check
    try:
        await bot.get_chat_member(FSUB_CHANNEL, user_id)
    except UserNotParticipant:
        arg = msg.command[1] if len(msg.command) > 1 else ""
        return await msg.reply(
            "❌ **Pehle Join Karo!**\nJoin karne ke baad wapas Try Again par click karna.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 JOIN CHANNEL", url=MAIN_CHANNEL_LINK)],
                [InlineKeyboardButton("🔄 TRY AGAIN", url=f"https://t.me/{bot.me.username}?start={arg}")]
            ])
        )

    # File Sending Logic
    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        f_id = msg.command[1].replace("file_", "")
        data = await movies.find_one({"_id": ObjectId(f_id)})
        if data:
            f_msg = await bot.send_cached_media(msg.chat.id, data["file_id"], caption=WATERMARK_TEXT)
            asyncio.create_task(auto_delete(f_msg, 120)) # Delete in 2 mins
            
            warn = await msg.reply("☝️ **Ye file 2 minute me delete ho jayegi!**")
            asyncio.create_task(auto_delete(warn, 120))
        else:
            await msg.reply("❌ File purani ho gayi hai ya delete ho chuki hai.")
    else:
        await msg.reply("✅ Bot active hai! Group me search karo.")

# ================= 4. ADMIN COMMANDS =================
@bot.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(_, msg):
    c = await movies.count_documents({})
    await msg.reply(f"📊 Total Movies: {c}")

@bot.on_message(filters.command("shortnr") & filters.user(ADMIN_IDS))
async def short_cmd(_, msg):
    global SHORTLINK_ENABLED
    if "on" in msg.text: SHORTLINK_ENABLED = True
    else: SHORTLINK_ENABLED = False
    await msg.reply(f"Shortener: {SHORTLINK_ENABLED}")

@bot.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def del_cmd(_, msg):
    q = " ".join(msg.command[1:])
    r = await movies.delete_many({"title": {"$regex": q, "$options": "i"}})
    await msg.reply(f"🗑 Deleted: {r.deleted_count}")

@bot.on_message(filters.command("id"))
async def id_cmd(_, msg):
    await msg.reply(f"Chat ID: `{msg.chat.id}`\nUser ID: `{msg.from_user.id}`")

# ================= STORAGE =================
@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def storage_logic(_, msg):
    file = msg.video or msg.document
    title = (msg.caption or file.file_name or "unknown").lower()
    await movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply(f"✅ Added: {title}")

# ================= RUN =================
async def main():
    await start_web()
    await bot.start()
    await idle()

if __name__ == "__main__":
    asyncio.run(main())
