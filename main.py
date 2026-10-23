from pyrogram import enums
import os
import io
import re
import asyncio
import aiohttp
import logging
import base64
from datetime import datetime
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from pyrogram.errors import UserNotParticipant, UserIsBlocked, InputUserDeactivated
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from aiohttp import web
from urllib.parse import quote, unquote
from fuzzywuzzy import fuzz
from pyrogram.types import InputMediaPhoto, InputMediaDocument
DEFAULT_POSTER = "https://i.postimg.cc/6q8z5GRk/IMG-20260828-193852.jpg"

# ================= CONFIGURATION =================
def get_clean_var(key, default=""):
    val = os.environ.get(key, default)
    return str(val).strip()

API_ID = int(get_clean_var("API_ID", "0"))
API_HASH = get_clean_var("API_HASH", "")
BOT_TOKEN = get_clean_var("BOT_TOKEN", "")
MONGO_URL = get_clean_var("MONGO_URL", "")
TMDB_API_KEY = get_clean_var("TMDB_API_KEY", "")

raw_admins = get_clean_var("ADMIN_IDS", "0").replace(",", " ").split()
ADMIN_IDS = [int(x) for x in raw_admins if x.strip().isdigit()]

def parse_chat_id(key):
    val = get_clean_var(key, "0")
    if val.startswith("-100") or val.lstrip("-").isdigit():
        try:
            return int(val)
        except ValueError:
            return val
    return val

STORAGE_CHANNEL = parse_chat_id("STORAGE_CHANNEL")
SEARCH_CHAT = parse_chat_id("SEARCH_CHAT")
FSUB_CHANNEL = parse_chat_id("FSUB_CHANNEL")
MAIN_CHANNEL_LINK = get_clean_var("MAIN_CHANNEL_LINK", "https://t.me/Movies2026Cinema")
SHORT_DOMAIN = get_clean_var("SHORT_DOMAIN", "arolinks.com")
SHORT_API_KEY = get_clean_var("SHORT_API_KEY", "")

SHORTLINK_ENABLED = False
PAGE_SIZE = 6 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= BOT CLIENT =================
class MovieBot(Client):
    def __init__(self):
        super().__init__("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
        self.movies = None
        self.requests = None
        self.users = None
        self.banned = None

    async def start(self):
        await super().start()
        try:
            mongo_client = AsyncIOMotorClient(MONGO_URL)
            db = mongo_client["PratapCinemaBot"]
            self.movies = db["movies"]
            self.requests = db["movie_requests"]
            self.users = db["users"]
            self.banned = db["banned_users"]
            async for d in self.banned.find({}):
                if d.get("user_id"):
                    BANNED_USERS.add(int(d["user_id"]))
            print("✅ MongoDB Connected Successfully!")
        except Exception as e:
            print(f"❌ MongoDB Connection Error: {e}")

        try:
            await self.set_bot_commands([
                BotCommand("start", "Bot start karein ya file access karein"),
                BotCommand("pratap", "Database ki movies aur bot stats dekhein (Admin)"),
                BotCommand("stats", "Bot ka full status aur count dekhein (Admin)"),
                BotCommand("requests", "Pending movie requests ki list dekhein (Admin)"),
                BotCommand("delreq", "Koi ek movie request delete karein (Admin)"),
                BotCommand("clearreq", "Saari pending requests clear karein (Admin)"),
                BotCommand("shortlink", "Shortlink enable ya disable karein (Admin)"),
                BotCommand("del", "Database se movie delete karein (Admin)"),
                BotCommand("delall", "Database se movies delete karein (Admin)"),
                BotCommand("broadcast", "Sabhi users ko message bhejein (Admin)"),
                BotCommand("ban", "User ko bot se ban karein (Admin)"),
                BotCommand("unban", "User ko unban karein (Admin)"),
                BotCommand("banlist", "Banned users ki list dekhein (Admin)")
            ])
            print("✅ Telegram Menu Commands Configured!")
        except Exception as e:
            print(f"⚠️ Could not set menu commands: {e}")

        print(f"🚀 BOT STARTED as @{self.me.username}")

    async def stop(self, *args):
        await super().stop()
        print("Bot Stopped.")

BANNED_USERS = set()
TITLE_LOCKS = {}

app = MovieBot()

# ================= HELPER & UTILS =================
def clean_name(text):
    if not text:
        return ""
    text = str(text).lower()

    text = re.sub(r'@[a-zA-Z0-9_]+', '', text)
    text = re.sub(r'https?://\S+|www\.\S+', '', text)
    text = re.sub(r'\(.*?\)|\[.*?\]', '', text)

    junk = [
        r'\bs\d{1,2}e\d{1,2}\b', r'\bs\d{1,2}\b', r'\be\d{1,2}\b', r'season\s*\d+', r'episodes?\s*\d+',
        r'\bcombined\b', r'\bcomplete\b', r'\bpart\s*\d+', r'\bvol\s*\d+',
        r'\b1080p\b', r'\b720p\b', r'\b480p\b', r'\b2160p\b', r'\b4k\b', r'\bhevc\b', r'\bx264\b', r'\bx265\b',
        r'\bweb-?dl\b', r'\bweb-?rip\b', r'\bbluray\b', r'\bcamrip\b', r'\bpre-?dvd\b', r'\bhdtv\b', r'\bhdrip\b', r'\bhsrip\b',
        r'\bweb\b', r'\bdl\b', r'\bhs\b', r'\bhd\b', r'\bmkv\b', r'\bmp4\b', r'\bavi\b',
        r'\bhindi\b', r'\benglish\b', r'\bitalian\b', r'\bdual audio\b', r'\besubs?\b', r'\bsub\b',
        r'\b10-?bit\b', r'\baac\b', r'\bdd5\b', r'\blol\b', r'\bms\b', r'\bjoin\b'
    ]

    for word in junk:
        text = re.sub(word, ' ', text, flags=re.IGNORECASE)

    text = re.sub(r'[^a-zA-Z0-9\s]', ' ', text)
    return " ".join(text.split()).strip()

async def notify_admins_about_request(client, user_name, user_id, user_mention, raw_query):
    alert_text = (
        f"📥 **NEW MOVIE REQUEST RECEIVED!**\n\n"
        f"🎬 **Movie Name:** `{raw_query}`\n"
        f"👤 **Requested By:** {user_mention}\n"
        f"🆔 **User ID:** `{user_id}`\n"
        f"🕒 **Time:** `{datetime.now().strftime('%d-%m-%Y %I:%M %p')}`\n\n"
        f"💡 _Upload this movie in Storage Channel to auto-notify the user!_"
    )
    for admin_id in ADMIN_IDS:
        try:
            await client.send_message(chat_id=admin_id, text=alert_text)
        except Exception as e:
            logger.error(f"Could not send request alert to admin {admin_id}: {e}")

async def get_tmdb_corrected_title(query):
    if not TMDB_API_KEY:
        return query
    try:
        clean_q = clean_name(query)
        if not clean_q:
            return query
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(clean_q)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=3.0) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    if results:
                        top = results[0]
                        corrected = top.get("title") or top.get("name") or top.get("original_title")
                        if corrected:
                            return corrected
    except Exception as e:
        logger.error(f"TMDB Spell Check Error: {e}")
    return query

async def tmdb_best_match(title, year=None):
    """TMDB me sabse sahi movie/series dhoondhta hai (naam similarity + year + popularity se)."""
    if not TMDB_API_KEY:
        return None
    clean_t = clean_name(title) or str(title).strip().lower()
    if not clean_t:
        return None

    urls = []
    if year:
        urls.append((f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={quote(clean_t)}&primary_release_year={year}", "movie"))
    urls.append((f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={quote(clean_t)}", "movie"))
    urls.append((f"https://api.themoviedb.org/3/search/tv?api_key={TMDB_API_KEY}&query={quote(clean_t)}", "tv"))

    async def fetch(session, url, mt):
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                items = data.get("results", [])[:10]
                for it in items:
                    it["media_type"] = mt
                return items
        except Exception:
            return []

    candidates = []
    try:
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            lists = await asyncio.gather(*[fetch(session, u, m) for u, m in urls])
            for l in lists:
                candidates.extend(l)
    except Exception as e:
        logger.error(f"TMDB Match Error: {e}")
        return None

    best, best_score = None, 0
    for it in candidates:
        names = [clean_name(it.get("title") or it.get("name") or ""),
                 clean_name(it.get("original_title") or it.get("original_name") or "")]
        names = [n for n in names if n]
        if not names:
            continue
        score = max(fuzz.ratio(clean_t, n) for n in names)
        date = it.get("release_date") or it.get("first_air_date") or ""
        if year and date[:4] == str(year):
            score += 15
        if not it.get("poster_path") and not it.get("backdrop_path"):
            score -= 40
        score += min(it.get("popularity", 0) or 0, 100) / 20
        if it.get("media_type") == "tv":
            score -= 3
        if score > best_score:
            best, best_score = it, score

    return best if best and best_score >= 60 else None

async def get_poster(query, year=None):
    item = await tmdb_best_match(query, year)
    if item:
        p = item.get("poster_path") or item.get("backdrop_path")
        if p:
            return f"https://image.tmdb.org/t/p/w500{p}"
    return None

async def check_upcoming_movie(query):
    if not TMDB_API_KEY:
        return None

    clean_q = re.sub(r'(?i)\b(hindi|dubbed|english|tamil|telugu|full|movie|720p|1080p|480p|web-dl|hdrip|bluray)\b', '', query).strip()
    if not clean_q:
        clean_q = query

    try:
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(clean_q)}"
        timeout = aiohttp.ClientTimeout(total=4.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    if not results:
                        return None

                    today = datetime.now().date()

                    for item in results:
                        rel_date_str = item.get("release_date") or item.get("first_air_date")
                        if rel_date_str:
                            try:
                                rel_date = datetime.strptime(rel_date_str, "%Y-%m-%d").date()
                                days_left = (rel_date - today).days
                                title = item.get("title") or item.get("name") or item.get("original_title") or query
                                poster_path = item.get("poster_path")
                                poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

                                if days_left > 0:
                                    return {
                                        "title": title,
                                        "release_date": rel_date_str,
                                        "days_remaining": f"{days_left} Days",
                                        "status": "Upcoming",
                                        "poster": poster_url
                                    }
                            except Exception:
                                continue

                    top_item = results[0]
                    rel_date_str = top_item.get("release_date") or top_item.get("first_air_date") or "N/A"
                    title = top_item.get("title") or top_item.get("name") or top_item.get("original_title") or query
                    poster_path = top_item.get("poster_path")
                    poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

                    days_status = "N/A"
                    if rel_date_str != "N/A":
                        try:
                            rel_date = datetime.strptime(rel_date_str, "%Y-%m-%d").date()
                            if rel_date > today:
                                days_status = f"{(rel_date - today).days} Days"
                            else:
                                days_status = "Already Released"
                        except Exception:
                            days_status = "N/A"

                    return {
                        "title": title,
                        "release_date": rel_date_str,
                        "days_remaining": days_status,
                        "status": "TMDB Found",
                        "poster": poster_url
                    }
    except Exception as e:
        logger.error(f"TMDB Upcoming Error: {e}")
        return None
    return None

# ================= SPELLING-TOLERANT SEARCH LOGIC =================
def _skeleton(word):
    """Sound-alike key: jailer / jelar / jeelar -> 'jlr'"""
    w = word.lower()
    for a_, b_ in (("ph", "f"), ("kh", "k"), ("gh", "g"), ("w", "v"), ("z", "j"), ("q", "k"), ("c", "k"), ("y", "i")):
        w = w.replace(a_, b_)
    w = re.sub(r'[aeiou]', '', w)
    return re.sub(r'(.)\1+', r'\1', w)

def _word_matches(qw, doc_tokens):
    """Query ka ek word doc ke kisi token se (galat spelling ke saath bhi) match kare."""
    for dt in doc_tokens:
        if qw == dt:
            return True
        short, long_ = min(len(qw), len(dt)), max(len(qw), len(dt))
        if short < 4:
            continue  # chhote words (hop, the, war) sirf exact match
        if (qw in dt or dt in qw) and long_ - short <= 2:
            return True
        ratio = fuzz.ratio(qw, dt)
        thr = 88 if long_ <= 4 else (80 if long_ <= 6 else 75)
        if ratio >= thr:
            return True
        sk_q, sk_d = _skeleton(qw), _skeleton(dt)
        if len(sk_q) >= 3 and sk_q == sk_d and qw[0] == dt[0] and ratio >= 55:
            return True
        if len(sk_q) >= 4 and qw[0] == dt[0] and fuzz.ratio(sk_q, sk_d) >= 80 and ratio >= 55:
            return True
    return False

def _doc_text(doc):
    return (clean_name(doc.get("title", "")) + " " + clean_name(doc.get("original_title", ""))).strip()

def _exact_match(clean_q, doc):
    txt = _doc_text(doc)
    return bool(txt) and re.search(r'(?<![a-z0-9])' + re.escape(clean_q) + r'(?![a-z0-9])', txt) is not None

def _doc_matches(clean_q, doc):
    q_tokens = clean_q.split()
    q_numbers = [t for t in q_tokens if t.isdigit()]
    q_words = [t for t in q_tokens if not t.isdigit()]

    # title + original_title dono se match (title me sequel number ya year kat sakta hai)
    doc_text = clean_name(doc.get("title", "")) + " " + clean_name(doc.get("original_title", ""))
    doc_text = doc_text.strip()
    if not doc_text:
        return False
    doc_tokens = doc_text.split()

    # Number (jaise Jailer 2) exact token hona chahiye, warna Jailer 2023 bhi aa jayega
    if q_numbers and not all(n in doc_tokens for n in q_numbers):
        return False

    if not q_words:
        return bool(q_numbers)

    if clean_q in doc_text:
        return True

    # Har query word (galat spelling ke saath bhi) match hona chahiye
    if all(_word_matches(w, doc_tokens) for w in q_words):
        return True

    # Space galat ho (jaise "avengersendgame") to joined compare
    joined_q = "".join(q_words)
    if len(joined_q) > 5 and fuzz.partial_ratio(joined_q, doc_text.replace(" ", "")) >= 88:
        return True
    return False

async def smart_db_search(client, query):
    if not query or not query.strip():
        return []

    clean_q = clean_name(query)
    if not clean_q:
        clean_q = query.strip().lower()

    all_docs = await client.movies.find({}).to_list(length=20000)

    # 1) Pehle bilkul sahi naam wale results; mil gaye to sirf wahi dikhao
    matched = [d for d in all_docs if _exact_match(clean_q, d)]
    if matched:
        return matched

    # 2) Galat spelling wala fuzzy match
    matched = [d for d in all_docs if _doc_matches(clean_q, d)]

    # Kuch nahi mila to TMDB se sahi spelling lekar dobara try
    if not matched:
        try:
            corrected_title = await get_tmdb_corrected_title(query)
            clean_corrected = clean_name(corrected_title)
        except Exception:
            clean_corrected = ""
        if clean_corrected and clean_corrected != clean_q:
            # original query ka number (jaise "2") TMDB title me na ho to jod do
            q_nums = re.findall(r'\b\d+\b', clean_q)
            c_nums = re.findall(r'\b\d+\b', clean_corrected)
            if q_nums and not c_nums:
                clean_corrected = f"{clean_corrected} {' '.join(q_nums)}"
            matched = [d for d in all_docs if _exact_match(clean_corrected, d)]
            if not matched:
                matched = [d for d in all_docs if _doc_matches(clean_corrected, d)]

    return matched

async def get_shortlink(url):
    if not SHORTLINK_ENABLED: return url
    try:
        api_url = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as resp:
                res = await resp.json()
                if res.get("status") == "success": return res["shortenedUrl"]
    except Exception: pass
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
        except Exception: pass

# ================= ALL ADMIN COMMAND HANDLERS =================

@app.on_message(filters.command(["pratap", "stats"]) & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    count = await client.movies.count_documents({})
    users_count = await client.users.count_documents({})
    req_count = await client.requests.count_documents({})
    await msg.reply(
        f"📊 **Bot Status (Admin)**\n\n"
        f"🎬 Total Movies: `{count}`\n"
        f"👤 Total Users: `{users_count}`\n"
        f"📌 Pending Requests: `{req_count}`"
    )

@app.on_message(filters.command("requests") & filters.user(ADMIN_IDS))
async def list_requests_cmd(client, msg):
    reqs = await client.requests.find({}).to_list(length=500)
    if not reqs:
        return await msg.reply("✅ Koi pending requests nahi hain!")
        
    header = f"📥 **TOTAL PENDING REQUESTS ({len(reqs)}):**\n\n"
    chunks = []
    current_chunk = header

    for idx, r in enumerate(reqs, 1):
        u_name = r.get("user_name", "User")
        u_id = r.get("user_id", "N/A")
        movie = r.get("raw_query", r.get("query", "Unknown"))
        
        entry = (
            f"**{idx}.** 🎬 `{movie}`\n"
            f"   👤 [{u_name}](tg://user?id={u_id}) (`{u_id}`)\n\n"
        )
        
        if len(current_chunk) + len(entry) > 3800:
            chunks.append(current_chunk)
            current_chunk = entry
        else:
            current_chunk += entry

    if current_chunk:
        chunks.append(current_chunk)

    for chunk in chunks:
        await msg.reply(chunk, disable_web_page_preview=True)

@app.on_message(filters.command("delreq") & filters.user(ADMIN_IDS))
async def delete_single_request_cmd(client, msg):
    if len(msg.command) < 2:
        return await msg.reply("Usage:\n`/delreq movie_name`")
    
    query = clean_name(" ".join(msg.command[1:]))
    res = await client.requests.delete_many({
        "query": {"$regex": query, "$options": "i"}
    })
    await msg.reply(f"🗑️ Cleaned: Deleted `{res.deleted_count}` pending request(s) matching `{query}`.")

@app.on_message(filters.command("clearreq") & filters.user(ADMIN_IDS))
async def clear_all_requests_cmd(client, msg):
    res = await client.requests.delete_many({})
    await msg.reply(f"🗑️ MongoDB Cleaned! Removed all `{res.deleted_count}` pending requests.")

@app.on_message(filters.command("shortlink") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    choice = msg.command[1].lower() if len(msg.command) > 1 else ""
    if choice == "on": 
        SHORTLINK_ENABLED = True
        await msg.reply("✅ Shortlink Enabled")
    elif choice == "off": 
        SHORTLINK_ENABLED = False
        await msg.reply("❌ Shortlink Disabled")
    else:
        await msg.reply(f"Status: `{'ON' if SHORTLINK_ENABLED else 'OFF'}`\nUse `/shortlink on` or `/shortlink off`")

@app.on_message(filters.command(["del", "delall"]) & filters.user(ADMIN_IDS))
async def delete_movie_cmd(client, msg):
    if len(msg.command) < 2:
        return await msg.reply("Usage:\n/del movie_name")
    query = clean_name(" ".join(msg.command[1:]))
    result = await client.movies.delete_many({
        "title": {"$regex": query, "$options": "i"}
    })
    await msg.reply(f"🗑️ Deleted: {result.deleted_count} movie(s).")

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

async def _resolve_target(client, msg):
    """Reply, user id ya @username se target user nikalta hai. Return: (user_id, reason)"""
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return msg.reply_to_message.from_user.id, " ".join(msg.command[1:])
    if len(msg.command) > 1:
        arg = msg.command[1]
        reason = " ".join(msg.command[2:])
        if arg.lstrip("-").isdigit():
            return int(arg), reason
        try:
            u = await client.get_users(arg)
            return u.id, reason
        except Exception:
            return None, ""
    return None, ""

@app.on_message(filters.command("ban") & filters.user(ADMIN_IDS))
async def ban_cmd(client, msg):
    uid, reason = await _resolve_target(client, msg)
    if not uid:
        return await msg.reply("Usage:\n`/ban user_id [reason]`\n`/ban @username`\nYa kisi user ke message ko reply karke `/ban`")
    if uid in ADMIN_IDS:
        return await msg.reply("❌ Admin ko ban nahi kar sakte.")
    await client.banned.update_one(
        {"user_id": uid},
        {"$set": {"user_id": uid, "reason": reason or "N/A", "time": datetime.now()}},
        upsert=True
    )
    BANNED_USERS.add(uid)
    await msg.reply(f"🚫 User `{uid}` ban ho gaya.\n📝 Reason: {reason or 'N/A'}")

@app.on_message(filters.command("unban") & filters.user(ADMIN_IDS))
async def unban_cmd(client, msg):
    uid, _ = await _resolve_target(client, msg)
    if not uid:
        return await msg.reply("Usage:\n`/unban user_id`\n`/unban @username`\nYa kisi user ke message ko reply karke `/unban`")
    res = await client.banned.delete_one({"user_id": uid})
    BANNED_USERS.discard(uid)
    if res.deleted_count:
        await msg.reply(f"✅ User `{uid}` unban ho gaya.")
    else:
        await msg.reply(f"ℹ️ User `{uid}` banned list me nahi tha.")

@app.on_message(filters.command("banlist") & filters.user(ADMIN_IDS))
async def banlist_cmd(client, msg):
    users = await client.banned.find({}).to_list(length=1000)
    if not users:
        return await msg.reply("✅ Koi banned user nahi hai.")
    text = f"🚫 **BANNED USERS ({len(users)}):**\n\n"
    for i, u in enumerate(users, 1):
        text += f"**{i}.** [{u['user_id']}](tg://user?id={u['user_id']}) - {u.get('reason', 'N/A')}\n"
    await msg.reply(text[:4000], disable_web_page_preview=True)

# ================= CALLBACK QUERY HANDLER (PAGINATION) =================
@app.on_callback_query(filters.regex(r"^page_"))
async def page_callback(client, cb):
    try:
        if cb.from_user and cb.from_user.id in BANNED_USERS:
            return await cb.answer("🚫 Aap ban hain.", show_alert=True)
        await cb.answer()
        data = cb.data.split("_")
        offset = int(data[1])
        query = unquote("_".join(data[2:]))

        results = await smart_db_search(client, query)
        if not results:
            return await cb.answer("❌ Koi result nahi mila!", show_alert=True)

        reply_markup = await get_search_buttons(query, results, offset=offset)
        await cb.message.edit_reply_markup(reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Page Callback Error: {e}")

# ================= USER HANDLERS =================

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, msg):
    if msg.from_user.id in BANNED_USERS:
        return await msg.reply("🚫 Aapko is bot se ban kar diya gaya hai.")
    await client.users.update_one({"user_id": msg.from_user.id}, {"$set": {"user_id": msg.from_user.id}}, upsert=True)
    data = msg.command[1] if len(msg.command) > 1 else ""

    try:
        await client.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
    except UserNotParticipant:
        invite = (await client.get_chat(FSUB_CHANNEL)).invite_link or MAIN_CHANNEL_LINK
        me = await client.get_me()
        buttons = [[InlineKeyboardButton("📢 JOIN CHANNEL", url=invite)]]
        if data:
            try_again_link = f"https://t.me/{me.username}?start={data}"
            buttons.append([InlineKeyboardButton("🔄 TRY AGAIN / VERIFY 🔄", url=try_again_link)])
        btn = InlineKeyboardMarkup(buttons)
        return await msg.reply("❌ Pehle channel join karein!", reply_markup=btn)
    except Exception:
        pass

    if not data:
        group_link = MAIN_CHANNEL_LINK
        if SEARCH_CHAT and SEARCH_CHAT != 0:
            try:
                search_group = await client.get_chat(SEARCH_CHAT)
                group_link = search_group.invite_link or (f"https://t.me/{search_group.username}" if search_group.username else MAIN_CHANNEL_LINK)
            except Exception:
                group_link = MAIN_CHANNEL_LINK

        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔍 GO TO SEARCH GROUP 🔍", url=group_link)]])
        return await msg.reply(
            "👋 **Namaste!**\n\nMovies search karne ke liye niche diye gaye button par click karke hamare **Search Group** me jayein.",
            reply_markup=btn
        )

    if data.startswith("file_"):
        res = await client.movies.find_one({"_id": ObjectId(data.split("_")[1])})
        if res:
            # Full original title caption me dikhane ke liye
            raw_title = res.get('file_name') or res.get('original_title') or res.get('title', 'Movie')
            
            # TMDB search ke liye title clean karna (brackets hatayenge par YEAR bacha kar rakhenge)
            clean_text = str(raw_title).replace('.', ' ').replace('_', ' ').replace('(', '').replace(')', '').replace('[', '').replace(']', '')
            
            quality_pattern = r'(?i)\b(s\d+|e\d+|season|episode|360p|480p|720p|1080p|2160p|4k|mkv|mp4|avi|hd|webdl|web-dl|uncut|dual|hindi|bluray|x264|hevc)\b.*'
            clean_title = re.sub(quality_pattern, '', clean_text).strip()
            if not clean_title:
                clean_title = clean_text.strip()

            display_title = str(raw_title).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

            file_caption = (
                f"🎬 <a href='https://t.me/Movies2026Cinema'><b>{display_title}</b></a>\n"
                f"<b>JOIN ❤️: @Movies2026Cinema</b>"
            )

            # Poster dhoondna
            poster_url = res.get("poster") or res.get("poster_url")
            if not poster_url and TMDB_API_KEY:
                poster_url = await get_poster(clean_title)

            # Agar TMDB se poster na mile, toh aapka brand poster set ho jaye
            if not poster_url:
                poster_url = DEFAULT_POSTER

            sent_msgs = []
            
            # 1. Poster bhejna (Download & Send technique se taaki Telegram fail na ho)
            if poster_url:
                poster_path = f"poster_{msg.from_user.id}.jpg"
                try:
                    thumb_url = poster_url.replace('/w500/', '/w300/').replace('/original/', '/w300/')
                    async with aiohttp.ClientSession() as session:
                        async with session.get(thumb_url) as resp:
                            if resp.status == 200:
                                with open(poster_path, 'wb') as f:
                                    f.write(await resp.read())
                    
                    p_msg = await client.send_photo(chat_id=msg.chat.id, photo=poster_path)
                    sent_msgs.append(p_msg)
                except Exception as e:
                    print(f"Poster Error: {e}")
                finally:
                    if os.path.exists(poster_path):
                        os.remove(poster_path)

            # 2. Main Movie File
            try:
                sf = await client.send_cached_media(
                    chat_id=msg.chat.id, 
                    file_id=res["file_id"], 
                    caption=file_caption, 
                    parse_mode=enums.ParseMode.HTML
                )
                sent_msgs.append(sf)
            except Exception as e:
                print(f"File Send Error: {e}")

            # 3. Auto-delete Warning Message (5 minutes)
            warn_msg = await msg.reply_text(
                "⚠️ **DHYAN DEN:** Is file ko turant apne **Saved Messages** ya kisi chat me forward karein. File 5 minute me delete ho jayegi."
            )
            sent_msgs.append(warn_msg)

            asyncio.create_task(delete_after_delay(sent_msgs, 300))
        else:
            await msg.reply("❌ File database me nahi mili.")

    elif data.startswith("all_"):
        try:
            # Query nikalna (base64 ya plain text decode karna)
                raw_query = data.split("all_", 1)[1]
                try:
                    import base64
                    query = base64.urlsafe_b64decode(raw_query.encode('ascii') + b'==').decode('utf-8')
                except:
                    query = raw_query.replace("_", " ")

                sw = await msg.reply("⏳ **Saari files bhej raha hoon, thoda wait karein...**")
                
                # Database me search karna
                results = await asyncio.wait_for(smart_db_search(client, query), timeout=10.0)
                
                if not results:
                    return await sw.edit("❌ Mujhe ye movie database me nahi mili.")
                
                # Sabhi files ko ek ek karke loop me bhejna
                for res in results:
                    try:
                        await client.send_cached_media(
                            chat_id=msg.chat.id,
                            file_id=res["file_id"],
                            caption=f"🎬 **{res.get('original_title', res.get('title', 'Movie'))}**\n\n❤️ Join: @Movies2026Cinema"
                        )
                        await asyncio.sleep(0.5) # Telegram flood block se bachne ke liye
                    except Exception as e:
                        pass
                
                await sw.delete()
            
        except Exception as e:
            print(f"Get All Files Error: {e}")
            await msg.reply("❌ Kuch error aayi hai files nikalne me.")
            
@app.on_message(filters.private & filters.text & ~filters.regex(r"^/"))
async def pm_text_handler(client, msg):
    if msg.from_user and msg.from_user.id in BANNED_USERS:
        return
    group_link = MAIN_CHANNEL_LINK
    if SEARCH_CHAT and SEARCH_CHAT != 0:
        try:
            search_group = await client.get_chat(SEARCH_CHAT)
            group_link = search_group.invite_link or (f"https://t.me/{search_group.username}" if search_group.username else MAIN_CHANNEL_LINK)
        except Exception:
            group_link = MAIN_CHANNEL_LINK

    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔍 GO TO SEARCH GROUP 🔍", url=group_link)]])
    await msg.reply(
        "👋 **Namaste!**\n\n"
        "Movies search karne ke liye niche diye gaye button par click karke hamare **Search Group** me jayein aur wahan movie ka naam type karein.",
        reply_markup=btn
    )

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.regex(r"^/"))
async def search_movie(client, msg):
    is_admin = msg.from_user and msg.from_user.id in ADMIN_IDS
    query = clean_name(msg.text)
    if len(query) < 2: 
        return

    user_name = msg.from_user.first_name if msg.from_user else "User"
    user_id = msg.from_user.id if msg.from_user else 0
    user_mention = f"[{user_name}](tg://user?id={user_id})" if user_id else "User"

    if user_id in BANNED_USERS:
        return

    sw = await client.send_message(msg.chat.id, "🔍 Searching...")

    def format_date(d_str):
        if not d_str or d_str == "N/A":
            return "N/A"
        try:
            return datetime.strptime(d_str.strip(), "%Y-%m-%d").strftime("%d-%m-%Y")
        except Exception:
            return d_str

    results = []
    try:
        results = await asyncio.wait_for(smart_db_search(client, msg.text), timeout=10.0)
    except Exception:
        results = []

    if not results:
        await client.requests.update_one(
            {"user_id": user_id, "query": query},
            {"$set": {
                "user_id": user_id,
                "user_name": user_name,
                "query": query,
                "raw_query": msg.text,
                "time": datetime.now()
            }},
            upsert=True
        )

        asyncio.create_task(notify_admins_about_request(client, user_name, user_id, user_mention, msg.text))

        upcoming_info = None
        try:
            upcoming_info = await asyncio.wait_for(check_upcoming_movie(msg.text), timeout=4.0)
        except Exception:
            upcoming_info = None

        if upcoming_info:
            up_date = format_date(upcoming_info.get('release_date', 'N/A'))
            days_str = upcoming_info.get('days_remaining', 'N/A')
            status_str = upcoming_info.get('status', 'Upcoming')

            text = (
                f"🎬 **Movie:** `{upcoming_info['title']}`\n"
                f"👤 **Requested by:** {user_mention} (`{user_id}`)\n"
                f"📅 **Release Date:** `{up_date}`\n"
                f"📌 **Status:** `{status_str}`\n"
                f"⏳ **Info / Days Left:** `{days_str}`\n\n"
                f"ℹ️ _Ye movie release/available hote hi hamare database me add kar di jayegi!_"
            )
            try:
                await sw.delete()
            except Exception:
                pass

            res_msg = None
            if upcoming_info.get('poster'):
                try:
                    res_msg = await client.send_photo(msg.chat.id, photo=upcoming_info['poster'], caption=text)
                except Exception:
                    res_msg = await client.send_message(msg.chat.id, text=text)
            else:
                res_msg = await client.send_message(msg.chat.id, text=text)

            if not is_admin and res_msg:
                asyncio.create_task(delete_after_delay([res_msg], 300))
            return

        try:
            await sw.delete()
        except Exception:
            pass

        req_msg = await client.send_message(
            msg.chat.id,
            f"👤 **Requested by:** {user_mention} (`{user_id}`)\n\n"
            "Maaf kijiye, ye movie abhi hamare database me available nahi hai.\n\n"
            "Humne aapki request admin ko bhej di hai.\n"
            "Jaise hi movie database me add hogi, aapko automatically private message mil jayega."
        )

        if not is_admin:
            asyncio.create_task(delete_after_delay([req_msg, msg], 60))
        return

    try:
        top = results[0]
        top_year = re.search(r'\b(19\d{2}|20\d{2})\b', top.get("original_title", "") or "")
        q_year = re.search(r'\b(19\d{2}|20\d{2})\b', msg.text)
        year_hint = q_year.group(1) if q_year else (top_year.group(1) if top_year else None)
        poster = await get_poster(top.get("title") or query, year_hint)
        markup = await get_search_buttons(query, results, offset=0)
        
        text = (
            f"🎬 **Results for:** 📌 `{msg.text}`\n"
            f"👤 **Requested by:** {user_mention} (`{user_id}`)\n\n"
            f"⏳ _Ye result 5 minute mein delete ho jayega._"
        )
        
        if poster:
            try:
                res_msg = await client.send_photo(msg.chat.id, photo=poster, caption=text, reply_markup=markup)
            except Exception:
                res_msg = await client.send_message(msg.chat.id, text=text, reply_markup=markup)
        else:
            res_msg = await client.send_message(msg.chat.id, text=text, reply_markup=markup)
        
        try:
            await sw.delete()
        except Exception:
            pass
        
        if not is_admin:
            try:
                await msg.delete()
            except Exception:
                pass
            asyncio.create_task(delete_after_delay([res_msg], 300))
            
    except Exception as e:
        logger.error(f"Search Final Error: {e}")

# ================= ADD TO DB HANDLER =================
GENRE_MAP = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History",
    27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance", 878: "Sci-Fi",
    10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western"
}

async def post_channel_poster(client, search_title, year, raw_caption):
    group_link = MAIN_CHANNEL_LINK
    if SEARCH_CHAT and SEARCH_CHAT != 0:
        try:
            search_group = await client.get_chat(SEARCH_CHAT)
            group_link = search_group.invite_link or (f"https://t.me/{search_group.username}" if search_group.username else MAIN_CHANNEL_LINK)
        except Exception:
            group_link = MAIN_CHANNEL_LINK

    qualities = re.findall(r'(?i)\b(480p|720p|1080p|2160p|4k|2k|web-?dl|webrip|hdrip|bluray)\b', raw_caption)
    quality_str = ", ".join(list(dict.fromkeys([q.upper() for q in qualities]))) if qualities else "720p, 1080p, WEB-DL"
    audios = re.findall(r'(?i)\b(hindi|english|tamil|telugu|malayalam|kannada|dual|multi)\b', raw_caption)
    audio_str = ", ".join(list(dict.fromkeys([a.capitalize() for a in audios]))) if audios else "English, Hindi"

    poster_url = None
    rating = "N/A"
    title_display = search_title
    genres_str = "Action, Drama"
    runtime_str = "N/A"
    release_year = str(year) if year else "N/A"

    item = await tmdb_best_match(search_title, year)
    if item:
        title_display = item.get("title") or item.get("name") or search_title
        vote_avg = item.get("vote_average")
        rating = f"{round(vote_avg, 1)}" if vote_avg else "N/A"
        rel_date = item.get("release_date") or item.get("first_air_date")
        if rel_date and len(rel_date) >= 4:
            release_year = rel_date[:4]
        b_path, p_path = item.get("backdrop_path"), item.get("poster_path")
        if b_path:
            poster_url = f"https://image.tmdb.org/t/p/w780{b_path}"
        elif p_path:
            poster_url = f"https://image.tmdb.org/t/p/w500{p_path}"
        g_ids = item.get("genre_ids", [])
        matched_g = [GENRE_MAP[g] for g in g_ids if g in GENRE_MAP]
        if matched_g:
            genres_str = ", ".join(matched_g)
        if item.get("media_type") == "movie" and item.get("id"):
            try:
                d_url = f"https://api.themoviedb.org/3/movie/{item['id']}?api_key={TMDB_API_KEY}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(d_url, timeout=aiohttp.ClientTimeout(total=5)) as d_resp:
                        if d_resp.status == 200:
                            rt = (await d_resp.json()).get("runtime")
                            if rt:
                                runtime_str = f"{rt // 60}h {rt % 60}m" if rt // 60 else f"{rt % 60}m"
            except Exception:
                pass

    caption_text = (
        f"✨ **TITLE :** `{title_display}` ({release_year})\n"
        f"─────────────────────\n"
        f"🎭 **GENRES :** {genres_str}\n"
        f"⏱️ **RUNTIME :** {runtime_str}\n"
        f"🎬 **QUALITY :** {quality_str}\n"
        f"🔉 **AUDIO :** {audio_str}\n"
        f"🌟 **IMDB :** {rating}/10"
    )
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton("🔍 GET MOVIE HERE 🔍", url=group_link)]])
    target_channel = FSUB_CHANNEL or "@Movies2026Cinema"

    img_bytes = None
    if poster_url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(poster_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        img_bytes = await resp.read()
        except Exception:
            pass

    if img_bytes:
        photo_file = io.BytesIO(img_bytes)
        photo_file.name = "poster.jpg"
        await client.send_photo(target_channel, photo=photo_file, caption=caption_text, reply_markup=buttons)
    else:
        await client.send_message(target_channel, text=caption_text, reply_markup=buttons)

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document | filters.forwarded))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    if not file:
        return

    raw_caption = msg.caption or file.file_name or "Unknown Movie"

    clean_text = raw_caption.replace('.', ' ').replace('_', ' ')
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', clean_text)
    year = year_match.group(1) if year_match else None

    quality_pattern = r'(?i)\(.*?\)|\[.*?\]|\b(s\d+|e\d+|s\d+combined|s\d+complete|season\s*\d+|episode\s*\d+|combined|complete|part\s*\d+|360p|480p|720p|1080p|1080|2160p|4k|2k|hd|hdr|web-?dl|webrip|hdrip|bluray|hdtv|dvdrip|cam|hindi|tam|tel|eng|dual|multi|esub|sub|aac|ddp5?\.1|mkv|mp4|avi)\b.*'

    if year:
        search_title = clean_text.split(year)[0].strip()
    else:
        search_title = re.sub(quality_pattern, '', clean_text).strip()

    search_title = re.sub(quality_pattern, '', search_title).strip()
    if not search_title:
        search_title = clean_text.strip()

    # Same movie ki 480p/720p/1080p ek saath aayein to lock se sirf PEHLI file ko poster milega.
    # Database se movie delete karke dobara add karoge to count 0 hoga, isliye poster dobara milega.
    lock = TITLE_LOCKS.setdefault(search_title.lower(), asyncio.Lock())
    async with lock:
        existing = await client.movies.count_documents({
            "title": {"$regex": f"^{re.escape(search_title)}$", "$options": "i"}
        })
        await client.movies.insert_one({
            "title": search_title,
            "original_title": raw_caption,
            "file_id": file.file_id
        })

    status_msg = await msg.reply_text(f"📁 File DB me Add ho gayi!\nClean Name: `{search_title}`")

    if existing == 0:
        try:
            await post_channel_poster(client, search_title, year, raw_caption)
            await status_msg.edit_text(f"✅ File DB me Add ho gayi & Channel Poster Posted!\nClean Name: `{search_title}`")
        except Exception as e:
            logger.error(f"Auto Poster Error: {e}")
            await status_msg.edit_text(f"📁 File DB me Add ho gayi!\n❌ Channel Post Error: `{e}`")
    else:
        await status_msg.edit_text(
            f"📁 File DB me Add ho gayi!\nℹ️ `{search_title}` ka poster pehle post ho chuka hai, dobara nahi bheja."
        )

    try:
        all_requests = await client.requests.find({}).to_list(length=5000)
        for req in all_requests:
            req_q = req.get("query", "")
            if req_q and _doc_matches(req_q, {"title": search_title, "original_title": raw_caption}):
                user_id = req.get("user_id")
                try:
                    await client.send_message(
                        user_id,
                        f"🎉 Aapki requested movie **{raw_caption}** ab hamare database me add ho gayi hai!\n\n"
                        f"Search Group me jaakar download kar sakte hain."
                    )
                    await client.requests.delete_one({"_id": req["_id"]})
                except Exception as e:
                    logger.error(f"Failed notification to {user_id}: {e}")
    except Exception as e:
        logger.error(f"Request Notify Loop Error: {e}")

# ==================== RUNNER ====================
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
