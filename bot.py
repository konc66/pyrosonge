import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import base64
import re
import os
import json
import random
import asyncio
from datetime import datetime, timedelta
import pytz

# Charger .env si présent (local), sinon utilise les variables système (Railway)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- Config ---
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]

# Support multi-clés Gemini (rotation si quota dépassé)
_gemini_keys_raw = os.environ.get("GEMINI_API_KEYS", os.environ.get("GEMINI_API_KEY", ""))
GEMINI_API_KEYS = [k.strip() for k in _gemini_keys_raw.split(",") if k.strip()]
_current_key_index = 0

# Support multi-channels
_channels_raw = os.environ.get("ALLOWED_CHANNEL_IDS", os.environ.get("ALLOWED_CHANNEL_ID", ""))
ALLOWED_CHANNEL_IDS = [int(c.strip()) for c in _channels_raw.split(",") if c.strip()]

ADMIN_ROLE = os.environ.get("ADMIN_ROLE_NAME", os.environ.get("ADMIN_ROLE", "PyroAdmin"))
BACKUP_CHANNEL_ID = int(os.environ.get("BACKUP_CHANNEL_ID", "0")) or None
CASINO_CHANNEL_ID = int(os.environ.get("CASINO_CHANNEL_ID", "0")) or None
SHOP_CHANNEL_ID = int(os.environ.get("SHOP_CHANNEL_ID", "0")) or None
ADMIN_NOTIFY_CHANNEL_ID = int(os.environ.get("ADMIN_NOTIFY_CHANNEL_ID", "0")) or None
GUILD_MEMBER_ROLE_ID = int(os.environ.get("GUILD_MEMBER_ROLE_ID", "0")) or None
FUN_CHANNEL_ID = int(os.environ.get("FUN_CHANNEL_ID", "0")) or None
ENTRAIDE_CHANNEL_ID = int(os.environ.get("ENTRAIDE_CHANNEL_ID", "0")) or None
ENTRAIDE_CATEGORY_ID = int(os.environ.get("ENTRAIDE_CATEGORY_ID", "0")) or None
FLAMTON = os.environ.get("FLAMTON_EMOJI", "🪙")
TIMEZONE = pytz.timezone("Europe/Paris")

# --- Casino config ---
COIN_PER_TICK = 1
TICK_MINUTES = 2
AFK_MUTE_TIMEOUT = 15 * 60
DAILY_CAP = 750
SLOTS_MIN_BET = 10
SLOTS_MAX_BET = 500
ROULETTE_MIN_BET = 10
ROULETTE_MAX_BET = 500
COINFLIP_MAX_BET = 500
CASINO_DAILY_LOSS_CAP = 2000

# --- Don config ---
DON_MIN = 50
DON_MAX_DAILY = 500
ENTRAIDE_MIN = 50
ENTRAIDE_MAX = 200
ENTRAIDE_THRESHOLD = 200

# --- Raid & Event rewards ---
RAID_REWARD = 15
EVENT_BASE_PARTICIPATION = 15
EVENT_VICTORY_BONUS = 20
EVENT_DIFFICULTY_MULTIPLIERS = {
    "paradoxe iii": 1.0,
    "paradoxe iv": 1.25,
    "cauchemar i": 1.5,
    "cauchemar ii": 1.75,
    "cauchemar iii": 2.0,
}

# --- Shop config ---
SHOP_ROLES = {
    "bronze": {"price": 300, "days": 30, "discount": 0.05},
    "argent": {"price": 750, "days": 30, "discount": 0.10},
    "or": {"price": 1500, "days": 30, "discount": 0.20},
    "diamant": {"price": 3000, "days": 30, "discount": 0.30},
}
SHOP_ITEMS = {
    "8ball": 50, "devinette": 75, "rename": 100, "avatar": 150,
    "immunity": 250, "banner": 500, "nidas": 5000, "kamas1m": 6000, "kamas5m": 25000,
}

# --- Events config ---
EVENTS = {
    "songe": {
        "name": "Speed Rush Songe",
        "start": os.environ.get("SONGE_START", "2026-10-26"),
        "end": os.environ.get("SONGE_END", "2026-11-01"),
        "prompt": """Analyse ce screenshot de fin de Songe Dofus. Extrais les données suivantes en JSON strict :
{
  "intensite": "ex: Paradoxe IV, Cauchemar I, Ardu III...",
  "temps": "le temps affiché sur le récap, format MM:SS ou HH:MM:SS",
  "resultat": "Victoire ou Défaite",
  "palier": "le palier atteint si visible, sinon null",
  "score": null,
  "tours": le nombre de tours si visible sinon null,
  "songeur": "le nom du songeur si visible, sinon null",
  "date_ingame": "la date/heure visible dans le tchat en bas de l'écran (via /time ou /whois), sinon null"
}
Réponds UNIQUEMENT avec le JSON, rien d'autre.""",
    },
    "bolgrot": {
        "name": "Speed Rush Bolgrot",
        "start": os.environ.get("BOLGROT_START", "2099-01-01"),
        "end": os.environ.get("BOLGROT_END", "2099-01-07"),
        "prompt": """Analyse ce screenshot de fin de combat Dofus (donjon Bolgrot ou autre). Extrais les données suivantes en JSON strict :
{
  "intensite": "le niveau de difficulté ou mode si visible, sinon 'Normal'",
  "temps": "le temps de combat affiché, format MM:SS ou HH:MM:SS",
  "resultat": "Victoire ou Défaite",
  "palier": null,
  "score": null,
  "tours": le nombre de tours si visible sinon null,
  "songeur": null,
  "date_ingame": "la date/heure visible dans le tchat en bas de l'écran (via /time ou /whois), sinon null"
}
Réponds UNIQUEMENT avec le JSON, rien d'autre.""",
    },
}

# --- DB ---
def init_db():
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL DEFAULT 'songe',
            timestamp TEXT NOT NULL,
            intensite TEXT NOT NULL,
            intensite_rank INTEGER NOT NULL,
            temps_seconds INTEGER NOT NULL,
            temps_display TEXT NOT NULL,
            resultat TEXT NOT NULL,
            palier TEXT,
            score INTEGER,
            tours INTEGER,
            songeur TEXT,
            date_ingame TEXT,
            submitted_by INTEGER NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS run_players (
            run_id INTEGER NOT NULL,
            discord_id INTEGER NOT NULL,
            discord_name TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(id),
            UNIQUE(run_id, discord_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            discord_id INTEGER PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS coin_daily (
            discord_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            earned INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (discord_id, date)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS active_roles (
            discord_id INTEGER NOT NULL,
            role_key TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            PRIMARY KEY (discord_id, role_key)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS active_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            data TEXT,
            expires_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS casino_daily_loss (
            discord_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            lost INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (discord_id, date)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS don_daily (
            discord_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            donated INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (discord_id, date)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Migration : ajouter event_type si la colonne n'existe pas
    try:
        c.execute("ALTER TABLE runs ADD COLUMN event_type TEXT NOT NULL DEFAULT 'songe'")
    except:
        pass
    try:
        c.execute("ALTER TABLE runs ADD COLUMN date_ingame TEXT")
    except:
        pass
    try:
        c.execute("ALTER TABLE runs ADD COLUMN message_id INTEGER")
    except:
        pass
    conn.commit()
    conn.close()

# --- Intensité ranking ---
INTENSITE_ORDER = {
    "normal": 0,
    "ardu i": 1, "ardu ii": 2, "ardu iii": 3, "ardu iv": 4, "ardu v": 5,
    "difficile i": 6, "difficile ii": 7, "difficile iii": 8, "difficile iv": 9, "difficile v": 10,
    "cauchemar i": 11, "cauchemar ii": 12, "cauchemar iii": 13, "cauchemar iv": 14, "cauchemar v": 15,
    "paradoxe i": 16, "paradoxe ii": 17, "paradoxe iii": 18, "paradoxe iv": 19, "paradoxe v": 20,
}

def get_intensite_rank(intensite: str) -> int:
    return INTENSITE_ORDER.get(intensite.lower().strip(), -1)

def parse_time_to_seconds(time_str: str) -> int:
    parts = time_str.strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0

def format_seconds(s: int) -> str:
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"

# --- Titres auto ---
def get_title(temps_seconds: int, victoires: int, streak: int) -> str:
    titles = []
    if temps_seconds > 0 and temps_seconds < 600:
        titles.append("⚡ Le Flash")
    elif temps_seconds > 0 and temps_seconds < 1200:
        titles.append("🏃 Le Rapide")
    elif temps_seconds > 3600:
        titles.append("🐢 Le Patient")

    if victoires >= 20:
        titles.append("👑 Le Vétéran")
    elif victoires >= 10:
        titles.append("⭐ L'Expérimenté")
    elif victoires >= 5:
        titles.append("🔥 L'Assidu")

    if streak >= 5:
        titles.append("🔥🔥🔥 L'Inarrêtable")
    elif streak >= 3:
        titles.append("🔥🔥 En Série")

    return " | ".join(titles) if titles else "🌱 Débutant"

def get_streak(discord_id: int, event_type: str = "songe") -> int:
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("""
        SELECT r.resultat FROM runs r
        JOIN run_players rp ON r.id = rp.run_id
        WHERE rp.discord_id = ? AND r.event_type = ?
        ORDER BY r.id DESC
    """, (discord_id, event_type))
    streak = 0
    for (resultat,) in c.fetchall():
        if resultat == "Victoire":
            streak += 1
        else:
            break
    conn.close()
    return streak

# --- Gemini Vision ---
def analyze_screenshot(image_bytes: bytes, media_type: str, event_type: str = "songe") -> dict:
    global _current_key_index
    import google.generativeai as genai
    import PIL.Image
    import io

    prompt = EVENTS[event_type]["prompt"]
    image = PIL.Image.open(io.BytesIO(image_bytes))

    # Rotation clés + modèles Gemini (quota/dispo)
    MODELS = ["gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]
    last_error = None
    for model_name in MODELS:
        for attempt in range(len(GEMINI_API_KEYS)):
            key_index = (_current_key_index + attempt) % len(GEMINI_API_KEYS)
            try:
                genai.configure(api_key=GEMINI_API_KEYS[key_index])
                model = genai.GenerativeModel(model_name)
                response = model.generate_content([prompt, image])
                text = response.text.strip()
                _current_key_index = key_index
                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    return json.loads(match.group())
                raise ValueError("Impossible de parser la réponse de l'IA.")
            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                if "quota" in err_str or "429" in str(e) or "resource" in err_str:
                    _current_key_index = (key_index + 1) % len(GEMINI_API_KEYS)
                    continue
                if "404" in str(e) or "not found" in err_str or "not available" in err_str:
                    break  # modèle indispo, passer au suivant
                raise
    raise last_error

# --- Check event dates ---
def check_event_dates(event_type: str) -> str | None:
    """Retourne un message d'erreur si hors dates, None si OK."""
    event = EVENTS.get(event_type)
    if not event:
        return "❌ Événement inconnu."
    now_paris = datetime.now(TIMEZONE)
    start = TIMEZONE.localize(datetime.strptime(event["start"], "%Y-%m-%d"))
    end = TIMEZONE.localize(datetime.strptime(event["end"], "%Y-%m-%d").replace(hour=23, minute=59, second=59))
    if now_paris < start:
        return f"❌ L'événement **{event['name']}** n'a pas encore commencé ! Début le {event['start']}."
    if now_paris > end:
        return f"❌ L'événement **{event['name']}** est terminé depuis le {event['end']}."
    return None

# --- Check previous record ---
def get_previous_best_time(discord_id: int, event_type: str) -> int | None:
    """Retourne le meilleur temps (en secondes) du joueur, ou None si aucune victoire."""
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("""
        SELECT MIN(r.temps_seconds)
        FROM runs r JOIN run_players rp ON r.id = rp.run_id
        WHERE rp.discord_id = ? AND r.event_type = ? AND r.resultat = 'Victoire'
    """, (discord_id, event_type))
    result = c.fetchone()
    conn.close()
    return result[0] if result and result[0] is not None else None

# --- Wallet helpers ---
def get_balance(discord_id: int) -> int:
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("SELECT balance FROM wallets WHERE discord_id = ?", (discord_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def add_coins(discord_id: int, amount: int) -> int:
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("INSERT INTO wallets (discord_id, balance) VALUES (?, ?) ON CONFLICT(discord_id) DO UPDATE SET balance = balance + ?",
              (discord_id, max(amount, 0), amount))
    c.execute("SELECT balance FROM wallets WHERE discord_id = ?", (discord_id,))
    new_balance = c.fetchone()[0]
    conn.commit()
    conn.close()
    return new_balance


def get_daily_earned(discord_id: int) -> int:
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("SELECT earned FROM coin_daily WHERE discord_id = ? AND date = ?", (discord_id, today))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def add_daily_earned(discord_id: int, amount: int):
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("INSERT INTO coin_daily (discord_id, date, earned) VALUES (?, ?, ?) ON CONFLICT(discord_id, date) DO UPDATE SET earned = earned + ?",
              (discord_id, today, amount, amount))
    conn.commit()
    conn.close()


# --- Casino loss tracking ---
def get_daily_loss(discord_id: int) -> int:
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("SELECT lost FROM casino_daily_loss WHERE discord_id = ? AND date = ?", (discord_id, today))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def add_daily_loss(discord_id: int, amount: int):
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("INSERT INTO casino_daily_loss (discord_id, date, lost) VALUES (?, ?, ?) ON CONFLICT(discord_id, date) DO UPDATE SET lost = lost + ?",
              (discord_id, today, amount, amount))
    conn.commit()
    conn.close()


# --- Donation tracking ---
def get_daily_donated(discord_id: int) -> int:
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("SELECT donated FROM don_daily WHERE discord_id = ? AND date = ?", (discord_id, today))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def add_daily_donated(discord_id: int, amount: int):
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("INSERT INTO don_daily (discord_id, date, donated) VALUES (?, ?, ?) ON CONFLICT(discord_id, date) DO UPDATE SET donated = donated + ?",
              (discord_id, today, amount, amount))
    conn.commit()
    conn.close()


# --- Role/shop helpers ---
def get_user_discount(discord_id: int) -> float:
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    now = datetime.now(TIMEZONE).isoformat()
    c.execute("SELECT role_key FROM active_roles WHERE discord_id = ? AND expires_at > ?", (discord_id, now))
    rows = c.fetchall()
    conn.close()
    best = 0
    for (role_key,) in rows:
        d = SHOP_ROLES.get(role_key, {}).get("discount", 0)
        if d > best:
            best = d
    return best


def apply_discount(base_price: int, discord_id: int) -> tuple[int, float]:
    discount = get_user_discount(discord_id)
    final = int(base_price * (1 - discount))
    return final, discount


def has_active_item(discord_id: int, item_type: str) -> bool:
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    now = datetime.now(TIMEZONE).isoformat()
    c.execute("SELECT 1 FROM active_items WHERE discord_id = ? AND item_type = ? AND expires_at > ?",
              (discord_id, item_type, now))
    result = c.fetchone() is not None
    conn.close()
    return result


def add_active_item(discord_id: int, item_type: str, data: str, hours: int):
    expires = (datetime.now(TIMEZONE) + timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("INSERT INTO active_items (discord_id, item_type, data, expires_at) VALUES (?, ?, ?, ?)",
              (discord_id, item_type, data, expires))
    conn.commit()
    conn.close()


# --- Gemini text generation ---
def generate_text_gemini(prompt: str) -> str:
    global _current_key_index
    import google.generativeai as genai

    MODELS = ["gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]
    last_error = None
    for model_name in MODELS:
        for attempt in range(len(GEMINI_API_KEYS)):
            key_index = (_current_key_index + attempt) % len(GEMINI_API_KEYS)
            try:
                genai.configure(api_key=GEMINI_API_KEYS[key_index])
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                _current_key_index = key_index
                return response.text.strip()
            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                if "quota" in err_str or "429" in str(e) or "resource" in err_str:
                    _current_key_index = (key_index + 1) % len(GEMINI_API_KEYS)
                    continue
                if "404" in str(e) or "not found" in err_str or "not available" in err_str:
                    break
                raise
    raise last_error


# --- Vocal tracking ---
# {user_id: {"channel_id": int, "joined_at": float, "last_active": float, "mute_since": float|None}}
voice_sessions = {}


def get_voice_multiplier(channel) -> float:
    if not channel:
        return 0
    active_members = []
    for m in channel.members:
        if m.bot:
            continue
        session = voice_sessions.get(m.id)
        if session and session.get("afk"):
            continue
        active_members.append(m)
    count = len(active_members)
    if count < 2:
        return 0
    if count >= 5:
        return 2.0
    if count >= 3:
        return 1.5
    return 1.0


# --- Bot ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def auto_restore():
    """Cherche le dernier backup dans le channel dédié et restaure la DB."""
    search_channels = [BACKUP_CHANNEL_ID] if BACKUP_CHANNEL_ID else ALLOWED_CHANNEL_IDS
    for channel_id in search_channels:
        channel = bot.get_channel(channel_id)
        if not channel:
            continue
        try:
            async for message in channel.history(limit=200):
                if message.author != bot.user:
                    continue
                for att in message.attachments:
                    if att.filename == "songe.db":
                        print(f"[AUTO-RESTORE] Backup trouvé dans #{channel.name} ({att.filename}, {att.size} bytes)")
                        db_bytes = await att.read()
                        with open("songe.db", "wb") as f:
                            f.write(db_bytes)
                        conn_test = sqlite3.connect("songe.db")
                        conn_test.execute("SELECT count(*) FROM runs")
                        conn_test.close()
                        print("[AUTO-RESTORE] Base restaurée avec succès.")
                        return True
        except Exception as e:
            print(f"[AUTO-RESTORE] Erreur sur channel {channel_id}: {e}")
    print("[AUTO-RESTORE] Aucun backup trouvé, utilisation de la DB locale.")
    return False


async def auto_backup(source_channel):
    """Envoie la DB dans le channel backup dédié, puis purge les anciens backups."""
    target = bot.get_channel(BACKUP_CHANNEL_ID) if BACKUP_CHANNEL_ID else source_channel
    if not target:
        print("[AUTO-BACKUP] Channel backup introuvable.")
        return
    try:
        old_backups = []
        async for message in target.history(limit=200):
            if message.author == bot.user and any(att.filename == "songe.db" for att in message.attachments):
                old_backups.append(message)

        await target.send(
            "💾 Backup auto",
            file=discord.File("songe.db", filename="songe.db")
        )

        for old_msg in old_backups:
            try:
                await old_msg.delete()
            except Exception:
                pass
    except Exception as e:
        print(f"[AUTO-BACKUP] Erreur: {e}")


@tasks.loop(hours=6)
async def periodic_backup():
    """Backup automatique toutes les 6h."""
    target = bot.get_channel(BACKUP_CHANNEL_ID) if BACKUP_CHANNEL_ID else None
    if not target:
        print("[PERIODIC-BACKUP] BACKUP_CHANNEL_ID non configuré, skip.")
        return
    print("[PERIODIC-BACKUP] Lancement du backup périodique...")
    await auto_backup(target)
    print("[PERIODIC-BACKUP] Backup périodique terminé.")


@periodic_backup.before_loop
async def before_periodic_backup():
    await bot.wait_until_ready()


async def first_boot_intro():
    """Envoie un message d'intro dans chaque salon dédié — une seule fois, jamais après."""
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("SELECT value FROM bot_meta WHERE key='first_boot_done'")
    row = c.fetchone()
    if row:
        conn.close()
        return
    c.execute("INSERT INTO bot_meta (key, value) VALUES ('first_boot_done', '1')")
    conn.commit()
    conn.close()

    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    channels_info = {
        CASINO_CHANNEL_ID: {
            "title": "🎰 Bienvenue au Casino Pyrosonge !",
            "desc": (
                "**Commandes disponibles ici :**\n"
                f"🎰 `!slots <mise>` — Machine à sous ({SLOTS_MIN_BET}-{SLOTS_MAX_BET} {FLAMTON})\n"
                f"{FLAMTON} `!coinflip @joueur <mise>` — Pile ou face PvP (10-{COINFLIP_MAX_BET} {FLAMTON})\n"
                f"🎡 `!roulette <mise> <choix>` — Roulette ({ROULETTE_MIN_BET}-{ROULETTE_MAX_BET} {FLAMTON})\n"
                f"💰 `!solde` — Voir ton solde\n"
                f"🏆 `!classement-coins` — Top 15 des fortunes\n\n"
                f"⚠️ **Limite de perte : {CASINO_DAILY_LOSS_CAP} {FLAMTON}/jour** (slots/roulette)\n\n"
                f"🚫 Toute tentative de génération abusive de {FLAMTON} (AFK, exploit, multicompte...) sera sanctionnée."
            ),
            "color": discord.Color.gold(),
        },
        SHOP_CHANNEL_ID: {
            "title": "🛒 Boutique Pyrosonge",
            "desc": (
                "**Commandes disponibles ici :**\n"
                "🛒 `!shop` — Voir le catalogue et les prix\n"
                "🛍️ `!buy <item>` — Acheter un article\n"
                f"🎁 `!don @joueur <montant>` — Don direct (max {DON_MAX_DAILY}/jour)\n\n"
                "Tape `!shop` pour voir tous les articles !\n\n"
                f"🚫 Toute tentative de génération abusive de {FLAMTON} (AFK, exploit, multicompte...) sera sanctionnée."
            ),
            "color": discord.Color.purple(),
        },
        FUN_CHANNEL_ID: {
            "title": "🎉 Salon Fun — Roast & Compliment",
            "desc": (
                "**Ce qui se passe ici :**\n"
                "🔥 **Roast du jour** — Tous les jours à **12h**, un membre aléatoire se fait vanner par le bot\n"
                "💖 **Compliment du jour** — Tous les jours à **18h**, un membre reçoit un compliment\n"
                "📊 `!sondage \"question\"` — Crée un sondage\n\n"
                "C'est 100% automatique et gratuit. Prépare-toi 😈\n\n"
                f"🚫 Toute tentative de génération abusive de {FLAMTON} (AFK, exploit, multicompte...) sera sanctionnée."
            ),
            "color": discord.Color.orange(),
        },
        ENTRAIDE_CHANNEL_ID: {
            "title": "🤝 Entraide Guilde",
            "desc": (
                "**Besoin d'aide ? Crée un ticket :**\n"
                "`!entraide <ta demande>`\n\n"
                "**Exemple :** `!entraide Besoin d'aide pour Nidas, lvl 180 enu`\n\n"
                "Le bot crée un ticket privé. Un membre tape `!prendre` pour te rejoindre.\n"
                "Le créateur, le helper ou un admin peut fermer avec `!fermer`.\n\n"
                f"🚫 Toute tentative de génération abusive de {FLAMTON} (AFK, exploit, faux tickets, multicompte...) sera sanctionnée."
            ),
            "color": discord.Color.teal(),
        },
    }

    for channel_id, info in channels_info.items():
        if not channel_id:
            continue
        channel = guild.get_channel(channel_id)
        if not channel:
            continue
        embed = discord.Embed(title=info["title"], description=info["desc"], color=info["color"])
        embed.set_footer(text="Pyrosonge Bot — tape !aide n'importe où pour la liste complète")
        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[FIRST-BOOT] Erreur envoi {channel.name}: {e}")

    print("[FIRST-BOOT] Messages d'intro envoyés dans tous les salons.")


@bot.event
async def on_ready():
    await auto_restore()
    init_db()
    print(f"Bot connecté: {bot.user}")
    await first_boot_intro()
    if not periodic_backup.is_running():
        periodic_backup.start()
        print("[PERIODIC-BACKUP] Tâche planifiée toutes les 6h.")
    if not vocal_coin_tick.is_running():
        vocal_coin_tick.start()
        print(f"[VOCAL-COINS] Tick toutes les {TICK_MINUTES} min.")
    if not check_expired_roles.is_running():
        check_expired_roles.start()
        print("[ROLES] Vérification expiration toutes les heures.")
    if not daily_roast.is_running():
        daily_roast.start()
        print("[ROAST] Roast quotidien activé (12h).")
    if not daily_compliment.is_running():
        daily_compliment.start()
        print("[COMPLIMENT] Compliment quotidien activé (18h).")
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} commandes slash synchronisées")
    except Exception as e:
        print(f"Erreur sync: {e}")


# --- Vocal coin tracking ---
@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    now = datetime.now(TIMEZONE).timestamp()
    guild = member.guild
    afk_channel_id = guild.afk_channel.id if guild.afk_channel else None

    # Quitte le vocal ou rejoint le salon AFK
    if after.channel is None or (afk_channel_id and after.channel.id == afk_channel_id):
        if member.id in voice_sessions:
            del voice_sessions[member.id]
        return

    # Rejoint un vocal
    if before.channel is None or (afk_channel_id and before.channel and before.channel.id == afk_channel_id):
        voice_sessions[member.id] = {
            "channel_id": after.channel.id,
            "joined_at": now,
            "last_active": now,
            "mute_since": now if (after.self_mute or after.self_deaf) else None,
            "afk": False,
        }
        return

    # Changement d'état (mute/unmute/deaf/undeaf/changement de salon)
    session = voice_sessions.get(member.id)
    if not session:
        voice_sessions[member.id] = {
            "channel_id": after.channel.id,
            "joined_at": now,
            "last_active": now,
            "mute_since": now if (after.self_mute or after.self_deaf) else None,
            "afk": False,
        }
        return

    session["channel_id"] = after.channel.id

    if after.self_mute or after.self_deaf:
        if session["mute_since"] is None:
            session["mute_since"] = now
    else:
        session["mute_since"] = None
        session["last_active"] = now
        session["afk"] = False


@tasks.loop(minutes=2)
async def vocal_coin_tick():
    """Crédite les Flamtons pour les membres actifs en vocal."""
    now = datetime.now(TIMEZONE).timestamp()
    to_remove = []

    for user_id, session in voice_sessions.items():
        # Vérifier timeout mute/deaf → AFK
        if session["mute_since"] and (now - session["mute_since"]) >= AFK_MUTE_TIMEOUT:
            session["afk"] = True

        if session["afk"]:
            continue

        # Trouver le channel
        channel = bot.get_channel(session["channel_id"])
        if not channel:
            to_remove.append(user_id)
            continue

        multiplier = get_voice_multiplier(channel)
        if multiplier <= 0:
            continue

        # Vérifier le cap journalier
        daily = get_daily_earned(user_id)
        if daily >= DAILY_CAP:
            continue

        coins = int(COIN_PER_TICK * multiplier)
        remaining = DAILY_CAP - daily
        coins = min(coins, remaining)

        if coins > 0:
            add_coins(user_id, coins)
            add_daily_earned(user_id, coins)

    for uid in to_remove:
        del voice_sessions[uid]


@vocal_coin_tick.before_loop
async def before_vocal_coin_tick():
    await bot.wait_until_ready()


@tasks.loop(hours=1)
async def check_expired_roles():
    now = datetime.now(TIMEZONE).isoformat()
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("SELECT discord_id, role_key FROM active_roles WHERE expires_at <= ?", (now,))
    expired = c.fetchall()
    for discord_id, role_key in expired:
        c.execute("DELETE FROM active_roles WHERE discord_id = ? AND role_key = ?", (discord_id, role_key))
        for guild in bot.guilds:
            member = guild.get_member(discord_id)
            if not member:
                continue
            role_name = SHOP_ROLES.get(role_key, {}).get("discount", role_key)
            role = discord.utils.get(guild.roles, name=role_key.capitalize())
            if role and role in member.roles:
                try:
                    await member.remove_role(role)
                    print(f"[ROLES] Rôle {role_key} expiré retiré de {member.display_name}")
                except Exception as e:
                    print(f"[ROLES] Erreur retrait rôle {role_key} de {member.display_name}: {e}")
    # Nettoyage items expirés
    c.execute("DELETE FROM active_items WHERE expires_at <= ?", (now,))
    conn.commit()
    conn.close()


@check_expired_roles.before_loop
async def before_check_expired_roles():
    await bot.wait_until_ready()


# --- Commande générique pour enregistrer une run ---
async def handle_run(ctx: commands.Context, event_type: str):
    if ctx.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    if not ctx.message.attachments:
        await ctx.reply("❌ Attache un screenshot de fin de combat.")
        return

    attachment = ctx.message.attachments[0]
    if not attachment.content_type or not attachment.content_type.startswith("image/"):
        await ctx.reply("❌ Le fichier attaché n'est pas une image.")
        return

    mentioned = ctx.message.mentions
    if not mentioned:
        await ctx.reply(f"❌ Mentionne les joueurs. Ex: `!{event_type} @joueur1 @joueur2`")
        return

    # Vérifier les dates
    date_error = check_event_dates(event_type)
    if date_error:
        await ctx.reply(date_error)
        return

    msg = await ctx.reply("🔍 Analyse du screenshot en cours...")

    try:
        image_bytes = await attachment.read()
        media_type = attachment.content_type
        data = analyze_screenshot(image_bytes, media_type, event_type)

        intensite = data.get("intensite", "Inconnu")
        temps_display = data.get("temps", "00:00")
        resultat = data.get("resultat", "Inconnu")
        palier = data.get("palier")
        score = data.get("score")
        tours = data.get("tours")
        songeur = data.get("songeur")
        date_ingame = data.get("date_ingame")

        intensite_rank = get_intensite_rank(intensite)
        temps_seconds = parse_time_to_seconds(temps_display)

        # Vérifier que la date in-game est visible (obligatoire pour songe)
        if event_type == "songe" and (not date_ingame or date_ingame.lower() in ["null", "none", ""]):
            await msg.edit(
                content="❌ **Run refusée** — La date et l'heure in-game ne sont pas visibles sur le screenshot.\n\n"
                        "📋 **Règle** : tu dois avoir le `/time` ou `/whois` visible dans le tchat en bas de ton écran "
                        "pour prouver que la run a été faite pendant la période de l'événement (26/10 → 01/11).\n\n"
                        "👉 Refais un screen avec la date visible et retente !"
            )
            return

        # Récupérer les meilleurs temps avant insertion
        previous_best_times = {}
        for member in mentioned:
            previous_best_times[member.id] = get_previous_best_time(member.id, event_type)

        # Sauvegarder en DB
        conn = sqlite3.connect("songe.db")
        c = conn.cursor()
        c.execute("""
            INSERT INTO runs (event_type, timestamp, intensite, intensite_rank, temps_seconds, temps_display,
                            resultat, palier, score, tours, songeur, date_ingame, submitted_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_type, datetime.now(TIMEZONE).isoformat(), intensite, intensite_rank, temps_seconds,
            temps_display, resultat, palier, score, tours, songeur, date_ingame, ctx.author.id
        ))
        run_id = c.lastrowid

        for member in mentioned:
            c.execute("INSERT OR IGNORE INTO run_players (run_id, discord_id, discord_name) VALUES (?, ?, ?)",
                      (run_id, member.id, member.display_name))

        conn.commit()
        conn.close()

        # Embed de réponse
        event_name = EVENTS[event_type]["name"]
        embed = discord.Embed(
            title=f"{'✅ Victoire' if resultat == 'Victoire' else '❌ Défaite'} — {event_name}",
            color=discord.Color.green() if resultat == "Victoire" else discord.Color.red()
        )
        embed.add_field(name="Intensité", value=intensite, inline=True)
        embed.add_field(name="Temps", value=temps_display, inline=True)
        if palier:
            embed.add_field(name="Palier", value=palier, inline=True)
        if tours:
            embed.add_field(name="Tours", value=str(tours), inline=True)
        if songeur:
            embed.add_field(name="Songeur", value=songeur, inline=True)
        if date_ingame:
            embed.add_field(name="Date in-game", value=date_ingame, inline=True)
        embed.add_field(name="Joueurs", value=" ".join(m.mention for m in mentioned), inline=False)
        embed.set_footer(text=f"Run #{run_id}")

        # Récompense Flamtons pour event
        difficulty_key = intensite.lower().strip()
        multiplier = EVENT_DIFFICULTY_MULTIPLIERS.get(difficulty_key)
        if multiplier is not None:
            reward = int(EVENT_BASE_PARTICIPATION * multiplier)
            if resultat == "Victoire":
                reward += EVENT_VICTORY_BONUS
            for member in mentioned:
                add_coins(member.id, reward)
            reward_text = f"\n\n{FLAMTON} **+{reward} Flamtons** par joueur"
            if resultat == "Victoire":
                reward_text += f" (participation x{multiplier} + victoire)"
            else:
                reward_text += f" (participation, x{multiplier})"
            embed.add_field(name="Récompense", value=reward_text, inline=False)

        await msg.edit(content=None, embed=embed)

        # Stocker le message_id pour suppression par reply
        conn = sqlite3.connect("songe.db")
        c = conn.cursor()
        c.execute("UPDATE runs SET message_id = ? WHERE id = ?", (msg.id, run_id))
        conn.commit()
        conn.close()

        # Vérifier les nouveaux records de temps
        if resultat == "Victoire":
            record_holders = []
            for member in mentioned:
                prev_time = previous_best_times[member.id]
                if prev_time is not None and temps_seconds < prev_time:
                    record_holders.append(member)

            if record_holders:
                if len(record_holders) == 1:
                    names = record_holders[0].mention
                    msg_record = f"🚨 **NOUVEAU RECORD !** {names} vient de battre son record personnel ! **{temps_display}** 🔥"
                else:
                    names = ", ".join(m.mention for m in record_holders)
                    msg_record = f"🚨 **NOUVEAU RECORD !** {names} viennent de battre leur record personnel ! **{temps_display}** 🔥"
                await ctx.channel.send(msg_record)

        await auto_backup(ctx.channel)

    except Exception as e:
        await msg.edit(content=f"❌ Erreur: {e}")


@bot.command(name="songe")
async def songe(ctx: commands.Context):
    """!songe @joueur1 @joueur2 ... + screenshot"""
    await handle_run(ctx, "songe")


@bot.command(name="bolgrot")
async def bolgrot(ctx: commands.Context):
    """!bolgrot @joueur1 @joueur2 ... + screenshot"""
    await handle_run(ctx, "bolgrot")


@bot.command(name="raid")
async def raid(ctx: commands.Context):
    """!raid @joueur1 @joueur2 ... — Récompense les participants d'un raid (admin only)."""
    allowed = list(ALLOWED_CHANNEL_IDS)
    if SHOP_CHANNEL_ID:
        allowed.append(SHOP_CHANNEL_ID)
    if ctx.channel.id not in allowed:
        return
    is_admin = any(role.name == ADMIN_ROLE for role in ctx.author.roles) or ctx.author.guild_permissions.administrator
    if not is_admin:
        await ctx.reply(f"❌ Seul un **{ADMIN_ROLE}** ou administrateur peut utiliser `!raid`.")
        return
    mentioned = [m for m in ctx.message.mentions if not m.bot]
    if not mentioned:
        await ctx.reply("❌ Mentionne les participants. Ex: `!raid @joueur1 @joueur2 @joueur3`")
        return
    for member in mentioned:
        add_coins(member.id, RAID_REWARD)
    names = ", ".join(m.mention for m in mentioned)
    embed = discord.Embed(
        title="⚔️ Récompense Raid",
        description=(
            f"**+{RAID_REWARD} {FLAMTON}** distribués à {len(mentioned)} participant{'s' if len(mentioned) > 1 else ''} !\n\n"
            f"👥 {names}"
        ),
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"Par {ctx.author.display_name}")
    await ctx.reply(embed=embed)


@bot.command(name="classement")
async def classement(ctx: commands.Context, event_type: str = "songe"):
    """!classement [songe|bolgrot]"""
    if ctx.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    if event_type not in EVENTS:
        await ctx.reply(f"❌ Événement inconnu. Choix : {', '.join(EVENTS.keys())}")
        return

    conn = sqlite3.connect("songe.db")
    c = conn.cursor()

    c.execute("""
        SELECT rp.discord_id, rp.discord_name,
               r.intensite, r.temps_display, r.temps_seconds, r.intensite_rank, r.id
        FROM run_players rp
        JOIN runs r ON r.id = rp.run_id
        WHERE r.resultat = 'Victoire' AND r.event_type = ?
          AND (rp.discord_id, r.intensite_rank, r.temps_seconds) IN (
              SELECT rp2.discord_id, MAX(r2.intensite_rank), MIN(r2.temps_seconds)
              FROM run_players rp2
              JOIN runs r2 ON r2.id = rp2.run_id
              WHERE r2.resultat = 'Victoire' AND r2.event_type = ?
                AND r2.intensite_rank = (
                    SELECT MAX(r3.intensite_rank)
                    FROM run_players rp3
                    JOIN runs r3 ON r3.id = rp3.run_id
                    WHERE rp3.discord_id = rp2.discord_id AND r3.resultat = 'Victoire' AND r3.event_type = ?
                )
              GROUP BY rp2.discord_id
          )
        GROUP BY rp.discord_id
        ORDER BY r.intensite_rank DESC, r.temps_seconds ASC
        LIMIT 15
    """, (event_type, event_type, event_type))
    rows = c.fetchall()
    conn.close()

    if not rows:
        await ctx.reply("📭 Aucune run enregistrée.")
        return

    event_name = EVENTS[event_type]["name"]
    embed = discord.Embed(
        title=f"🏆 Classement {event_name}",
        description="Meilleure intensité puis meilleur temps par joueur",
        color=discord.Color.gold()
    )

    for i, (did, dname, intensite, temps, _, _, run_id) in enumerate(rows, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"**{i}.**"
        embed.add_field(
            name=f"{medal} {dname}",
            value=f"{intensite} — {temps}\n`Run #{run_id}`",
            inline=False
        )

    await ctx.reply(embed=embed)


@bot.command(name="stats")
async def stats(ctx: commands.Context):
    """!stats @joueur"""
    if ctx.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    if not ctx.message.mentions:
        await ctx.reply("❌ Mentionne un joueur. Ex: `!stats @joueur`")
        return

    target = ctx.message.mentions[0]
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()

    # Stats globales
    c.execute("""
        SELECT COUNT(*), SUM(CASE WHEN r.resultat='Victoire' THEN 1 ELSE 0 END)
        FROM runs r JOIN run_players rp ON r.id = rp.run_id
        WHERE rp.discord_id = ?
    """, (target.id,))
    total, victoires = c.fetchone()
    victoires = victoires or 0

    # Meilleure run toutes catégories
    c.execute("""
        SELECT r.intensite, r.temps_display, r.temps_seconds, r.event_type
        FROM runs r JOIN run_players rp ON r.id = rp.run_id
        WHERE rp.discord_id = ? AND r.resultat = 'Victoire'
        ORDER BY r.intensite_rank DESC, r.temps_seconds ASC
        LIMIT 1
    """, (target.id,))
    best = c.fetchone()

    # Temps moyen
    c.execute("""
        SELECT AVG(r.temps_seconds)
        FROM runs r JOIN run_players rp ON r.id = rp.run_id
        WHERE rp.discord_id = ? AND r.resultat = 'Victoire'
    """, (target.id,))
    avg_time = c.fetchone()[0]

    conn.close()

    streak = get_streak(target.id)
    title_str = get_title(
        best[2] if best else 0,
        victoires,
        streak
    )

    embed = discord.Embed(title=f"📊 Stats de {target.display_name}", color=discord.Color.blue())
    embed.add_field(name="Titre", value=title_str, inline=False)
    embed.add_field(name="Runs", value=str(total), inline=True)
    embed.add_field(name="Victoires", value=f"{victoires}/{total}", inline=True)
    if total > 0:
        embed.add_field(name="Winrate", value=f"{victoires/total*100:.0f}%", inline=True)
    if best:
        embed.add_field(name="Meilleure run", value=f"{best[0]} en {best[1]} ({best[3]})", inline=False)
    if avg_time:
        embed.add_field(name="Temps moyen", value=format_seconds(int(avg_time)), inline=True)
    if streak > 0:
        embed.add_field(name="Série actuelle", value=f"{'🔥' * min(streak, 5)} {streak} victoire(s)", inline=True)

    await ctx.reply(embed=embed)


@bot.command(name="historique")
async def historique(ctx: commands.Context):
    """!historique @joueur — Toutes les runs d'un joueur."""
    if ctx.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    if not ctx.message.mentions:
        await ctx.reply("❌ Mentionne un joueur. Ex: `!historique @joueur`")
        return

    target = ctx.message.mentions[0]
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()

    c.execute("""
        SELECT r.id, r.event_type, r.intensite, r.temps_display, r.resultat, r.timestamp
        FROM runs r JOIN run_players rp ON r.id = rp.run_id
        WHERE rp.discord_id = ?
        ORDER BY r.id DESC
        LIMIT 20
    """, (target.id,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        await ctx.reply(f"📭 Aucune run pour {target.display_name}.")
        return

    embed = discord.Embed(
        title=f"📜 Historique de {target.display_name}",
        description=f"Dernières {len(rows)} runs",
        color=discord.Color.purple()
    )

    for run_id, etype, intensite, temps, resultat, ts in rows:
        icon = "✅" if resultat == "Victoire" else "❌"
        date_str = ts[:10] if ts else "?"
        embed.add_field(
            name=f"{icon} Run #{run_id} — {etype}",
            value=f"{intensite} — {temps} ({date_str})",
            inline=False
        )

    await ctx.reply(embed=embed)


reset_pending = {}

@bot.command(name="reset")
async def reset(ctx: commands.Context):
    """!reset — Réinitialise toutes les runs (admin, double confirmation)."""
    if ctx.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    is_admin = any(role.name == ADMIN_ROLE for role in ctx.author.roles) or ctx.author.guild_permissions.administrator
    if not is_admin:
        await ctx.reply(f"❌ Tu dois avoir le rôle **{ADMIN_ROLE}** ou être administrateur.")
        return

    if reset_pending.get(ctx.author.id):
        # Deuxième !reset = confirmation
        conn = sqlite3.connect("songe.db")
        c = conn.cursor()
        c.execute("DELETE FROM run_players")
        c.execute("DELETE FROM runs")
        conn.commit()
        conn.close()
        reset_pending[ctx.author.id] = False
        await ctx.reply("🗑️ **Toutes les runs ont été supprimées.** Base remise à zéro.")
    else:
        reset_pending[ctx.author.id] = True
        await ctx.reply("⚠️ Es-tu sûr ? **Toutes les runs seront supprimées.** Retape `!reset` pour confirmer.")


@bot.command(name="supprun")
async def supprun(ctx: commands.Context):
    """!supprun — Réponds au message d'une run pour la supprimer (admin)."""
    if ctx.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    # Vérifier que c'est une réponse à un message
    ref = ctx.message.reference
    if not ref or not ref.message_id:
        await ctx.reply("❌ Réponds au message de la run que tu veux supprimer avec `!supprun`.")
        return

    target_msg_id = ref.message_id

    conn = sqlite3.connect("songe.db")
    c = conn.cursor()

    c.execute("SELECT id, intensite, temps_display, resultat, submitted_by FROM runs WHERE message_id = ?", (target_msg_id,))
    run = c.fetchone()
    if not run:
        conn.close()
        await ctx.reply("❌ Aucune run liée à ce message.")
        return

    run_id = run[0]
    submitted_by = run[4]

    # Autoriser si admin OU auteur de la run
    is_admin = any(role.name == ADMIN_ROLE for role in ctx.author.roles) or ctx.author.guild_permissions.administrator
    if not is_admin and ctx.author.id != submitted_by:
        await ctx.reply("❌ Tu peux supprimer uniquement tes propres runs (ou être **PyroAdmin**).")
        return
    c.execute("DELETE FROM run_players WHERE run_id = ?", (run_id,))
    c.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    conn.commit()
    conn.close()

    await ctx.reply(f"🗑️ Run #{run_id} supprimée ({run[1]} — {run[2]} — {run[3]}).")


@bot.command(name="event")
async def event_list(ctx: commands.Context):
    """!event — Liste des événements."""
    if ctx.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    now_paris = datetime.now(TIMEZONE)
    embed = discord.Embed(title="📅 Événements", color=discord.Color.blue())

    for etype, edata in EVENTS.items():
        start = datetime.strptime(edata["start"], "%Y-%m-%d")
        end = datetime.strptime(edata["end"], "%Y-%m-%d")

        if now_paris.date() < start.date():
            status = "⏳ À venir"
        elif now_paris.date() > end.date():
            status = "🏁 Terminé"
        else:
            status = "🟢 En cours"

        # Compter les runs
        conn = sqlite3.connect("songe.db")
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM runs WHERE event_type = ?", (etype,))
        nb_runs = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT rp.discord_id) FROM run_players rp JOIN runs r ON r.id = rp.run_id WHERE r.event_type = ?", (etype,))
        nb_players = c.fetchone()[0]
        conn.close()

        embed.add_field(
            name=f"{status} {edata['name']}",
            value=f"📅 {edata['start']} → {edata['end']}\n🎮 {nb_runs} runs | 👥 {nb_players} joueurs\nCommande : `!{etype}`",
            inline=False
        )

    await ctx.reply(embed=embed)


@bot.command(name="inscrits")
async def inscrits(ctx: commands.Context):
    """!inscrits — Liste des inscrits (réaction 🔥)."""
    if ctx.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    inscription_msg = None
    async for message in ctx.channel.history(limit=100):
        for reaction in message.reactions:
            if str(reaction.emoji) == "🔥":
                inscription_msg = message
                break
        if inscription_msg:
            break

    if not inscription_msg:
        await ctx.reply("❌ Aucun message avec une réaction 🔥 trouvé dans le salon.")
        return

    reaction = None
    for r in inscription_msg.reactions:
        if str(r.emoji) == "🔥":
            reaction = r
            break

    users = []
    async for user in reaction.users():
        if not user.bot:
            users.append(user)

    if not users:
        await ctx.reply("📭 Aucun inscrit pour le moment.")
        return

    embed = discord.Embed(
        title="🔥 Inscrits Speed Rush Songe",
        description=f"**{len(users)} participant(s)**\n\n" + "\n".join(f"• {u.mention}" for u in users),
        color=discord.Color.orange()
    )
    await ctx.reply(embed=embed)


# --- Casino commands ---
SLOT_SYMBOLS = ["💎", "⭐", "🔥", "🌱", "💀"]
SLOT_WEIGHTS = [1, 2, 3, 4, 5]  # 💎 rare, 💀 commun
SLOT_PAYOUTS = {"💎": 20, "⭐": 8, "🔥": 4, "🌱": 2, "💀": 0}

ROULETTE_NUMBERS = list(range(0, 37))  # 0-36
ROULETTE_REDS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
ROULETTE_BLACKS = {2,4,6,8,10,11,13,15,17,20,22,24,26,28,29,31,33,35}


def spin_slots():
    return random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)


def calculate_slot_payout(reels, bet):
    if reels[0] == reels[1] == reels[2]:
        multiplier = SLOT_PAYOUTS.get(reels[0], 0)
        if multiplier == 0:
            return 0, "Triple 💀 — pas de gains !"
        return bet * multiplier, f"**TRIPLE {reels[0]}** — x{multiplier} !"
    if reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        payout = int(bet * 1.5)
        return payout, "Paire — x1.5"
    return 0, "Rien..."


@bot.command(name="solde")
async def solde(ctx: commands.Context):
    """!solde — Voir son solde de Flamtons."""
    if CASINO_CHANNEL_ID and ctx.channel.id != CASINO_CHANNEL_ID:
        if ctx.channel.id not in ALLOWED_CHANNEL_IDS:
            return
    balance = get_balance(ctx.author.id)
    daily = get_daily_earned(ctx.author.id)
    session = voice_sessions.get(ctx.author.id)
    status = "🟢 En vocal (actif)" if session and not session.get("afk") else "🔴 Hors vocal" if not session else "💤 AFK"
    embed = discord.Embed(title=f"💰 Solde de {ctx.author.display_name}", color=discord.Color.gold())
    embed.add_field(name="Flamtons", value=f"**{balance}** {FLAMTON}", inline=True)
    embed.add_field(name="Gagné aujourd'hui", value=f"{daily}/{DAILY_CAP} {FLAMTON}", inline=True)
    embed.add_field(name="Statut vocal", value=status, inline=True)
    await ctx.reply(embed=embed)


@bot.command(name="classement-coins")
async def classement_coins(ctx: commands.Context):
    """!classement-coins — Top 15 des plus riches."""
    if CASINO_CHANNEL_ID and ctx.channel.id != CASINO_CHANNEL_ID:
        if ctx.channel.id not in ALLOWED_CHANNEL_IDS:
            return
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("SELECT discord_id, balance FROM wallets WHERE balance > 0 ORDER BY balance DESC LIMIT 15")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await ctx.reply("📭 Personne n'a encore de Flamtons.")
        return
    embed = discord.Embed(title="💰 Classement des fortunes", color=discord.Color.gold())
    for i, (did, bal) in enumerate(rows, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"**{i}.**"
        member = ctx.guild.get_member(did)
        name = member.display_name if member else f"ID:{did}"
        embed.add_field(name=f"{medal} {name}", value=f"**{bal}** {FLAMTON}", inline=False)
    await ctx.reply(embed=embed)


@bot.command(name="slots")
async def slots(ctx: commands.Context, mise: int = 0):
    """!slots <mise> — Machine à sous."""
    if CASINO_CHANNEL_ID and ctx.channel.id != CASINO_CHANNEL_ID:
        return
    if not mise:
        await ctx.reply(f"❌ Usage : `!slots <mise>` (min {SLOTS_MIN_BET}, max {SLOTS_MAX_BET})")
        return
    if mise < SLOTS_MIN_BET or mise > SLOTS_MAX_BET:
        await ctx.reply(f"❌ Mise entre **{SLOTS_MIN_BET}** et **{SLOTS_MAX_BET}** {FLAMTON}")
        return
    balance = get_balance(ctx.author.id)
    if balance < mise:
        await ctx.reply(f"❌ Solde insuffisant. Tu as **{balance}** {FLAMTON}")
        return
    daily_loss = get_daily_loss(ctx.author.id)
    if daily_loss >= CASINO_DAILY_LOSS_CAP:
        await ctx.reply(f"🛑 Tu as atteint la limite de perte journalière (**{CASINO_DAILY_LOSS_CAP}** {FLAMTON}). Reviens demain !")
        return

    add_coins(ctx.author.id, -mise)

    reels = spin_slots()
    msg = await ctx.reply("🎰 **|** ❓ **|** ❓ **|** ❓ **|** — *Les rouleaux tournent...*")

    # Animation : chaque rouleau "tourne" avec des faux symboles avant de s'arrêter
    for reel_idx in range(3):
        # 3 faux symboles qui défilent
        fake_symbols = random.sample([s for s in SLOT_SYMBOLS if s != reels[reel_idx]], 3)
        for fake in fake_symbols:
            revealed = " **|** ".join(reels[:reel_idx]) + (" **|** " if reel_idx > 0 else "")
            spinning = f"{fake}"
            remaining = " **|** ".join(["❓"] * (2 - reel_idx))
            await asyncio.sleep(0.25)
            await msg.edit(content=f"🎰 **|** {revealed}{spinning} **|** {remaining} **|** — *Rouleau {reel_idx+1}...*")
        # Symbole final
        revealed_all = " **|** ".join(reels[:reel_idx+1])
        remaining = " **|** ".join(["❓"] * (2 - reel_idx))
        remaining_part = f" **|** {remaining} **|**" if reel_idx < 2 else " **|**"
        await asyncio.sleep(0.4)
        await msg.edit(content=f"🎰 **|** {revealed_all}{remaining_part} — *Arrêté !*")

    payout, desc = calculate_slot_payout(reels, mise)
    if payout > 0:
        add_coins(ctx.author.id, payout)
    else:
        add_daily_loss(ctx.author.id, mise)

    new_balance = get_balance(ctx.author.id)
    result_line = f"🎰 **|** {reels[0]} **|** {reels[1]} **|** {reels[2]} **|**"

    if payout > 0:
        gain = payout - mise
        embed = discord.Embed(title="🎰 Machine à sous", color=discord.Color.green())
        embed.description = f"{result_line}\n\n✅ {desc}\n💰 **+{gain}** {FLAMTON} (gain net)"
    else:
        embed = discord.Embed(title="🎰 Machine à sous", color=discord.Color.red())
        embed.description = f"{result_line}\n\n❌ {desc}\n💸 **-{mise}** {FLAMTON}"

    embed.set_footer(text=f"Solde : {new_balance} {FLAMTON}")
    await msg.edit(content=None, embed=embed)


@bot.command(name="coinflip")
async def coinflip(ctx: commands.Context, target: discord.Member = None, mise: int = 0):
    """!coinflip @joueur <mise> — Pile ou face PvP."""
    if CASINO_CHANNEL_ID and ctx.channel.id != CASINO_CHANNEL_ID:
        return
    if not target or not mise:
        await ctx.reply("❌ Usage : `!coinflip @joueur <mise>`")
        return
    if target.id == ctx.author.id:
        await ctx.reply("❌ Tu ne peux pas jouer contre toi-même.")
        return
    if target.bot:
        await ctx.reply("❌ Tu ne peux pas jouer contre un bot.")
        return
    if mise < 10 or mise > COINFLIP_MAX_BET:
        await ctx.reply(f"❌ Mise entre **10** et **{COINFLIP_MAX_BET}** {FLAMTON}")
        return

    bal_author = get_balance(ctx.author.id)
    bal_target = get_balance(target.id)
    if bal_author < mise:
        await ctx.reply(f"❌ Solde insuffisant. Tu as **{bal_author}** {FLAMTON}")
        return
    if bal_target < mise:
        await ctx.reply(f"❌ {target.display_name} n'a que **{bal_target}** {FLAMTON}")
        return

    msg = await ctx.reply(f"{FLAMTON} {target.mention}, {ctx.author.display_name} te défie pour **{mise}** {FLAMTON} ! Réagis ✅ pour accepter (30s).")
    await msg.add_reaction("✅")

    def check(reaction, user):
        return user.id == target.id and str(reaction.emoji) == "✅" and reaction.message.id == msg.id

    try:
        await bot.wait_for("reaction_add", timeout=30.0, check=check)
    except Exception:
        await msg.edit(content=f"⏰ {target.display_name} n'a pas accepté le défi.")
        return

    # Vérifier les soldes à nouveau
    if get_balance(ctx.author.id) < mise or get_balance(target.id) < mise:
        await msg.edit(content="❌ Solde insuffisant après acceptation.")
        return

    flip_msg = await ctx.send(f"{FLAMTON} La pièce est lancée...")
    animation = ["🪙", "💫", "✨", "💫", "🪙", "💫"]
    for frame in animation:
        await asyncio.sleep(0.4)
        await flip_msg.edit(content=f"{frame} La pièce tourne...")

    result = random.choice(["pile", "face"])
    winner = ctx.author if result == "pile" else target
    loser = target if result == "pile" else ctx.author

    add_coins(winner.id, mise)
    add_coins(loser.id, -mise)

    result_emoji = "🟡" if result == "pile" else "⚪"
    embed = discord.Embed(title=f"{FLAMTON} Coinflip — {result.upper()} !", color=discord.Color.gold())
    embed.description = (
        f"{result_emoji} La pièce retombe sur **{result}** !\n\n"
        f"🏆 {winner.mention} gagne **+{mise}** {FLAMTON}\n"
        f"💸 {loser.mention} perd **-{mise}** {FLAMTON}"
    )
    await flip_msg.edit(content=None, embed=embed)


@bot.command(name="roulette")
async def roulette(ctx: commands.Context, mise: int = 0, *, choix: str = ""):
    """!roulette <mise> <choix> — Roulette de casino."""
    if CASINO_CHANNEL_ID and ctx.channel.id != CASINO_CHANNEL_ID:
        return
    if not mise or not choix:
        await ctx.reply(
            f"❌ Usage : `!roulette <mise> <choix>`\n\n"
            f"**Choix possibles :**\n"
            f"• Un numéro (0-36) → paye **x35**\n"
            f"• `rouge` ou `noir` → paye **x2**\n"
            f"• `pair` ou `impair` → paye **x2**\n"
            f"• `1-18` ou `19-36` → paye **x2**\n"
            f"• `douzaine1`, `douzaine2`, `douzaine3` → paye **x3**\n\n"
            f"Mise : {ROULETTE_MIN_BET}-{ROULETTE_MAX_BET} {FLAMTON}"
        )
        return
    if mise < ROULETTE_MIN_BET or mise > ROULETTE_MAX_BET:
        await ctx.reply(f"❌ Mise entre **{ROULETTE_MIN_BET}** et **{ROULETTE_MAX_BET}** {FLAMTON}")
        return
    balance = get_balance(ctx.author.id)
    if balance < mise:
        await ctx.reply(f"❌ Solde insuffisant. Tu as **{balance}** {FLAMTON}")
        return
    daily_loss = get_daily_loss(ctx.author.id)
    if daily_loss >= CASINO_DAILY_LOSS_CAP:
        await ctx.reply(f"🛑 Tu as atteint la limite de perte journalière (**{CASINO_DAILY_LOSS_CAP}** {FLAMTON}). Reviens demain !")
        return

    choix = choix.lower().strip()
    result_num = random.choice(ROULETTE_NUMBERS)
    is_red = result_num in ROULETTE_REDS
    is_black = result_num in ROULETTE_BLACKS
    color_emoji = "🔴" if is_red else "⚫" if is_black else "🟢"
    color_name = "Rouge" if is_red else "Noir" if is_black else "Vert"

    payout = 0
    bet_desc = ""

    # Numéro exact
    if choix.isdigit() and 0 <= int(choix) <= 36:
        bet_desc = f"Numéro {choix}"
        if int(choix) == result_num:
            payout = mise * 35
    elif choix == "rouge":
        bet_desc = "Rouge"
        if is_red:
            payout = mise * 2
    elif choix == "noir":
        bet_desc = "Noir"
        if is_black:
            payout = mise * 2
    elif choix == "pair":
        bet_desc = "Pair"
        if result_num != 0 and result_num % 2 == 0:
            payout = mise * 2
    elif choix == "impair":
        bet_desc = "Impair"
        if result_num % 2 == 1:
            payout = mise * 2
    elif choix == "1-18":
        bet_desc = "1-18"
        if 1 <= result_num <= 18:
            payout = mise * 2
    elif choix == "19-36":
        bet_desc = "19-36"
        if 19 <= result_num <= 36:
            payout = mise * 2
    elif choix == "douzaine1":
        bet_desc = "Douzaine 1 (1-12)"
        if 1 <= result_num <= 12:
            payout = mise * 3
    elif choix == "douzaine2":
        bet_desc = "Douzaine 2 (13-24)"
        if 13 <= result_num <= 24:
            payout = mise * 3
    elif choix == "douzaine3":
        bet_desc = "Douzaine 3 (25-36)"
        if 25 <= result_num <= 36:
            payout = mise * 3
    else:
        await ctx.reply("❌ Choix invalide. Tape `!roulette` pour voir les options.")
        return

    add_coins(ctx.author.id, -mise)

    msg = await ctx.reply("🎡 La roulette tourne...")
    fake_nums = random.sample([n for n in ROULETTE_NUMBERS if n != result_num], 4)
    for i, fake in enumerate(fake_nums):
        fake_red = fake in ROULETTE_REDS
        fake_emoji = "🔴" if fake_red else "⚫" if fake in ROULETTE_BLACKS else "🟢"
        await asyncio.sleep(0.6)
        await msg.edit(content=f"🎡 La bille rebondit... {fake_emoji} **{fake}** ?")
    await asyncio.sleep(0.8)
    await msg.edit(content=f"🎡 La bille ralentit... {color_emoji} **{result_num}** !")
    await asyncio.sleep(1.0)

    if payout > 0:
        add_coins(ctx.author.id, payout)
        gain = payout - mise
        embed = discord.Embed(title="🎡 Roulette", color=discord.Color.green())
        embed.description = (
            f"{color_emoji} **{result_num}** — {color_name}\n\n"
            f"Mise : {bet_desc}\n"
            f"✅ **Gagné ! +{gain}** {FLAMTON}"
        )
    else:
        add_daily_loss(ctx.author.id, mise)
        embed = discord.Embed(title="🎡 Roulette", color=discord.Color.red())
        embed.description = (
            f"{color_emoji} **{result_num}** — {color_name}\n\n"
            f"Mise : {bet_desc}\n"
            f"❌ **Perdu ! -{mise}** {FLAMTON}"
        )

    new_balance = get_balance(ctx.author.id)
    embed.set_footer(text=f"Solde : {new_balance} {FLAMTON}")
    await msg.edit(content=None, embed=embed)


# --- Shop commands ---
@bot.command(name="shop")
async def shop(ctx: commands.Context):
    """!shop — Affiche la boutique."""
    if SHOP_CHANNEL_ID and ctx.channel.id != SHOP_CHANNEL_ID:
        await ctx.reply(f"❌ Utilise <#{SHOP_CHANNEL_ID}> pour la boutique.", delete_after=5)
        return

    discount = get_user_discount(ctx.author.id)
    disc_pct = f" (tu as **-{int(discount*100)}%** sur le Tier 3)" if discount > 0 else ""

    embed = discord.Embed(title="🛒 Boutique Pyrosonge", color=discord.Color.purple(),
                          description=f"Solde : **{get_balance(ctx.author.id)}** {FLAMTON}{disc_pct}")

    embed.add_field(name="🎲 Tier 1 — Fun Discord", value=(
        f"`!buy 8ball \"question\"` — 50 {FLAMTON}\n"
        f"`!buy devinette` — 75 {FLAMTON}\n"
        f"`!buy rename @joueur \"surnom\"` — 100 {FLAMTON}\n"
        f"`!buy avatar` — 150 {FLAMTON}\n"
        f"`!buy immunity` — 250 {FLAMTON}"
    ), inline=False)

    roles_lines = []
    for key, info in SHOP_ROLES.items():
        roles_lines.append(f"`!buy role {key}` — {info['price']} {FLAMTON} (1 mois, -{int(info['discount']*100)}% Tier 3)")
    roles_lines.append(f"`!buy banner \"texte\"` — 500 {FLAMTON} (7 jours)")
    embed.add_field(name="👑 Tier 2 — Rôles & Cosmétiques", value="\n".join(roles_lines), inline=False)

    t3_lines = []
    for item, base in [("nidas", 5000), ("kamas1m", 6000), ("kamas5m", 25000)]:
        if discount > 0:
            final = int(base * (1 - discount))
            t3_lines.append(f"`!buy {item}` — ~~{base}~~ **{final}** {FLAMTON}")
        else:
            t3_lines.append(f"`!buy {item}` — {base} {FLAMTON}")
    embed.add_field(name="⚔️ Tier 3 — In-Game Dofus", value="\n".join(t3_lines), inline=False)

    embed.add_field(name="🤝 Social", value=(
        f"`!don @joueur <montant>` — Don direct, max {DON_MAX_DAILY}/jour"
    ), inline=False)

    await ctx.reply(embed=embed)


@bot.command(name="buy")
async def buy(ctx: commands.Context, *, args: str = ""):
    """!buy <item> [params] — Acheter un article."""
    if SHOP_CHANNEL_ID and ctx.channel.id != SHOP_CHANNEL_ID:
        await ctx.reply(f"❌ Utilise <#{SHOP_CHANNEL_ID}> pour la boutique.", delete_after=5)
        return

    if not args:
        await ctx.reply("❌ Usage : `!buy <item>`. Tape `!shop` pour voir le catalogue.")
        return

    parts = args.split(None, 1)
    item = parts[0].lower()
    extra = parts[1] if len(parts) > 1 else ""

    # --- ROLE ---
    if item == "role":
        role_key = extra.lower().strip()
        if role_key not in SHOP_ROLES:
            await ctx.reply(f"❌ Rôles disponibles : {', '.join(SHOP_ROLES.keys())}")
            return
        info = SHOP_ROLES[role_key]
        price = info["price"]
        bal = get_balance(ctx.author.id)
        if bal < price:
            await ctx.reply(f"❌ Solde insuffisant. Il te faut **{price}** {FLAMTON}, tu as **{bal}** {FLAMTON}")
            return

        role = discord.utils.get(ctx.guild.roles, name=role_key.capitalize())
        if not role:
            await ctx.reply(f"❌ Le rôle **{role_key.capitalize()}** n'existe pas sur ce serveur. Un admin doit le créer.")
            return

        add_coins(ctx.author.id, -price)
        expires = (datetime.now(TIMEZONE) + timedelta(days=info["days"])).isoformat()
        conn = sqlite3.connect("songe.db")
        c = conn.cursor()
        c.execute("INSERT INTO active_roles (discord_id, role_key, expires_at) VALUES (?, ?, ?) ON CONFLICT(discord_id, role_key) DO UPDATE SET expires_at = ?",
                  (ctx.author.id, role_key, expires, expires))
        conn.commit()
        conn.close()

        try:
            await ctx.author.add_roles(role)
        except Exception as e:
            await ctx.reply(f"⚠️ Achat enregistré mais impossible d'ajouter le rôle Discord : {e}")
            return

        await ctx.reply(f"✅ Rôle **{role_key.capitalize()}** activé pour {info['days']} jours ! (-{int(info['discount']*100)}% sur le Tier 3)\n💰 -{price} {FLAMTON} | Solde : **{get_balance(ctx.author.id)}** {FLAMTON}")
        return

    # --- 8BALL ---
    if item == "8ball":
        question = extra.strip().strip('"')
        if not question:
            await ctx.reply("❌ Usage : `!buy 8ball \"ta question\"`")
            return
        price = SHOP_ITEMS["8ball"]
        bal = get_balance(ctx.author.id)
        if bal < price:
            await ctx.reply(f"❌ Il te faut **{price}** {FLAMTON}, tu as **{bal}** {FLAMTON}")
            return
        add_coins(ctx.author.id, -price)
        responses = [
            "🔮 C'est certain.", "🔮 Sans aucun doute.", "🔮 Oui, absolument.",
            "🔮 Les astres disent oui.", "🔮 Probablement.", "🔮 Les signes sont bons.",
            "🔮 Demande plus tard...", "🔮 Je ne peux pas répondre maintenant.",
            "🔮 N'y compte pas.", "🔮 Les chances sont faibles.", "🔮 Non.", "🔮 Très douteux.",
            "🔮 Ça sent le cramé.", "🔮 Dofus dit non.", "🔮 Les Songes sont flous...",
        ]
        embed = discord.Embed(title="🔮 Boule Magique", color=discord.Color.dark_purple())
        embed.add_field(name="Question", value=question, inline=False)
        embed.add_field(name="Réponse", value=random.choice(responses), inline=False)
        embed.set_footer(text=f"-{price} {FLAMTON} | Solde : {get_balance(ctx.author.id)} {FLAMTON}")
        await ctx.reply(embed=embed)
        return

    # --- DEVINETTE ---
    if item == "devinette":
        price = SHOP_ITEMS["devinette"]
        bal = get_balance(ctx.author.id)
        if bal < price:
            await ctx.reply(f"❌ Il te faut **{price}** {FLAMTON}, tu as **{bal}** {FLAMTON}")
            return
        add_coins(ctx.author.id, -price)
        try:
            result = generate_text_gemini(
                "Génère une devinette sur l'univers Dofus (monstres, donjons, classes, sorts, zones). "
                "Format : une ligne pour la devinette, puis 'Réponse: ...' sur la ligne suivante. "
                "La devinette doit être fun et pas trop difficile. En français."
            )
            lines = result.strip().split("\n")
            riddle = lines[0].strip()
            answer = ""
            for l in lines[1:]:
                if l.lower().startswith("réponse") or l.lower().startswith("reponse"):
                    answer = l.split(":", 1)[-1].strip()
                    break
        except Exception:
            riddle = "Quel monstre de Dofus ressemble à un arbre et fait pleurer les énis ? (Abraknyde)"
            answer = "Abraknyde"

        embed = discord.Embed(title="❓ Devinette Dofus", description=riddle, color=discord.Color.orange())
        embed.set_footer(text=f"Premier à répondre gagne 50 {FLAMTON} ! (-{price} {FLAMTON} pour {ctx.author.display_name})")
        quiz_msg = await ctx.reply(embed=embed)

        def check_answer(m):
            return m.channel.id == ctx.channel.id and m.author.id != ctx.author.id and not m.author.bot and answer.lower() in m.content.lower()

        if answer:
            try:
                winner_msg = await bot.wait_for("message", timeout=60.0, check=check_answer)
                add_coins(winner_msg.author.id, 50)
                await ctx.channel.send(f"✅ Bravo {winner_msg.author.mention} ! La réponse était **{answer}** — +50 {FLAMTON} !")
            except Exception:
                await ctx.channel.send(f"⏰ Temps écoulé ! La réponse était : **{answer}**")
        return

    # --- RENAME ---
    if item == "rename":
        price = SHOP_ITEMS["rename"]
        bal = get_balance(ctx.author.id)
        if bal < price:
            await ctx.reply(f"❌ Il te faut **{price}** {FLAMTON}, tu as **{bal}** {FLAMTON}")
            return

        if not ctx.message.mentions:
            await ctx.reply("❌ Usage : `!buy rename @joueur \"surnom\"`")
            return
        target = ctx.message.mentions[0]
        # Extraire le surnom (après la mention)
        surnom_match = re.search(r'"([^"]+)"', extra)
        if not surnom_match:
            surnom_parts = extra.split(None, 1)
            surnom = surnom_parts[1].strip() if len(surnom_parts) > 1 else ""
            surnom = re.sub(r'<@!?\d+>', '', surnom).strip().strip('"')
        else:
            surnom = surnom_match.group(1)

        if not surnom or len(surnom) > 32:
            await ctx.reply("❌ Surnom requis (max 32 caractères).")
            return

        if has_active_item(target.id, "immunity"):
            await ctx.reply(f"🛡️ {target.display_name} a une **immunité** active ! Impossible de le renommer.")
            return

        # Anti-vulgarité automatique
        try:
            check = generate_text_gemini(
                f"Le surnom suivant est-il vulgaire, offensant, discriminatoire ou inapproprié ? "
                f"Surnom : \"{surnom}\". Réponds uniquement par OUI ou NON."
            )
            if "oui" in check.lower():
                await ctx.reply("❌ Ce surnom a été refusé par la modération automatique.")
                return
        except Exception:
            pass

        add_coins(ctx.author.id, -price)
        old_nick = target.display_name
        try:
            await target.edit(nick=surnom)
            add_active_item(target.id, "rename", old_nick, 1)
            await ctx.reply(f"✏️ **{old_nick}** a été renommé en **{surnom}** pour 1h !\n💰 -{price} {FLAMTON} | Solde : {get_balance(ctx.author.id)} {FLAMTON}")
            await asyncio.sleep(3600)
            try:
                await target.edit(nick=old_nick)
            except Exception:
                pass
        except Exception as e:
            add_coins(ctx.author.id, price)
            await ctx.reply(f"❌ Impossible de renommer : {e}")
        return

    # --- AVATAR (Joueur du jour) ---
    if item == "avatar":
        price = SHOP_ITEMS["avatar"]
        bal = get_balance(ctx.author.id)
        if bal < price:
            await ctx.reply(f"❌ Il te faut **{price}** {FLAMTON}, tu as **{bal}** {FLAMTON}")
            return
        add_coins(ctx.author.id, -price)
        embed = discord.Embed(
            title="⭐ Joueur du Jour",
            description=f"Aujourd'hui on met à l'honneur **{ctx.author.display_name}** !",
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.set_footer(text="Épinglé pour 24h")
        pin_msg = await ctx.reply(embed=embed)
        try:
            await pin_msg.pin()
        except Exception:
            pass
        await asyncio.sleep(86400)
        try:
            await pin_msg.unpin()
        except Exception:
            pass
        return

    # --- IMMUNITY ---
    if item == "immunity":
        price = SHOP_ITEMS["immunity"]
        bal = get_balance(ctx.author.id)
        if bal < price:
            await ctx.reply(f"❌ Il te faut **{price}** {FLAMTON}, tu as **{bal}** {FLAMTON}")
            return
        if has_active_item(ctx.author.id, "immunity"):
            await ctx.reply("❌ Tu as déjà une immunité active !")
            return
        add_coins(ctx.author.id, -price)
        add_active_item(ctx.author.id, "immunity", None, 24)
        await ctx.reply(f"🛡️ **Immunité activée** pour 24h ! Personne ne peut te renommer.\n💰 -{price} {FLAMTON} | Solde : {get_balance(ctx.author.id)} {FLAMTON}")
        return

    # --- BANNER ---
    if item == "banner":
        texte = extra.strip().strip('"')
        if not texte:
            await ctx.reply("❌ Usage : `!buy banner \"ton texte\"`")
            return
        price = SHOP_ITEMS["banner"]
        bal = get_balance(ctx.author.id)
        if bal < price:
            await ctx.reply(f"❌ Il te faut **{price}** {FLAMTON}, tu as **{bal}** {FLAMTON}")
            return
        add_coins(ctx.author.id, -price)
        embed = discord.Embed(
            title="📢 Annonce de " + ctx.author.display_name,
            description=texte,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Bannière (7 jours) | -{price} {FLAMTON}")
        banner_msg = await ctx.reply(embed=embed)
        try:
            await banner_msg.pin()
        except Exception:
            pass
        await asyncio.sleep(7 * 86400)
        try:
            await banner_msg.unpin()
        except Exception:
            pass
        return

    # --- NIDAS ---
    if item == "nidas":
        base_price = SHOP_ITEMS["nidas"]
        price, discount = apply_discount(base_price, ctx.author.id)
        bal = get_balance(ctx.author.id)
        if bal < price:
            disc_txt = f" (réduc -{int(discount*100)}%)" if discount > 0 else ""
            await ctx.reply(f"❌ Il te faut **{price}** {FLAMTON}{disc_txt}, tu as **{bal}** {FLAMTON}")
            return
        add_coins(ctx.author.id, -price)

        # Créer un ticket (salon privé)
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        for role in ctx.guild.roles:
            if role.name == ADMIN_ROLE:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        try:
            ticket = await ctx.guild.create_text_channel(
                f"pl-nidas-{ctx.author.display_name}".lower().replace(" ", "-"),
                overwrites=overwrites,
                reason="Achat PL Nidas boutique"
            )
            embed = discord.Embed(title="⚔️ Demande de PL Nidas", color=discord.Color.red())
            embed.description = (
                f"**Joueur :** {ctx.author.mention}\n"
                f"**Pack :** PL complet (10 combats)\n"
                f"**Prix payé :** {price} {FLAMTON}"
                + (f" (réduc -{int(discount*100)}%)" if discount > 0 else "") +
                f"\n\n📋 Merci de préciser tes disponibilités ici.\nUn porteur te contactera."
            )
            await ticket.send(embed=embed)

            # Notifier les admins
            if ADMIN_NOTIFY_CHANNEL_ID:
                notif = bot.get_channel(ADMIN_NOTIFY_CHANNEL_ID)
                if notif:
                    await notif.send(f"🎫 **Nouveau ticket PL Nidas** de {ctx.author.mention} — {ticket.mention}")

            await ctx.reply(f"✅ Ticket créé : {ticket.mention}\n💰 -{price} {FLAMTON} | Solde : {get_balance(ctx.author.id)} {FLAMTON}")
        except Exception as e:
            add_coins(ctx.author.id, price)
            await ctx.reply(f"❌ Erreur création ticket : {e}")
        return

    # --- KAMAS ---
    if item in ("kamas1m", "kamas5m"):
        label = "1M kamas" if item == "kamas1m" else "5M kamas"
        base_price = SHOP_ITEMS[item]
        price, discount = apply_discount(base_price, ctx.author.id)
        bal = get_balance(ctx.author.id)
        if bal < price:
            await ctx.reply(f"❌ Il te faut **{price}** {FLAMTON}, tu as **{bal}** {FLAMTON}")
            return
        add_coins(ctx.author.id, -price)

        embed = discord.Embed(title=f"💰 Achat {label}", color=discord.Color.gold())
        embed.description = (
            f"**Joueur :** {ctx.author.mention}\n"
            f"**Récompense :** {label}\n"
            f"**Prix payé :** {price} {FLAMTON}"
            + (f" (réduc -{int(discount*100)}%)" if discount > 0 else "")
        )

        if ADMIN_NOTIFY_CHANNEL_ID:
            notif = bot.get_channel(ADMIN_NOTIFY_CHANNEL_ID)
            if notif:
                await notif.send(embed=embed)
                await notif.send(f"⚠️ {label} à donner à {ctx.author.mention} en jeu !")

        await ctx.reply(f"✅ Achat de **{label}** enregistré ! Un admin te contactera pour le transfert in-game.\n💰 -{price} {FLAMTON} | Solde : {get_balance(ctx.author.id)} {FLAMTON}")
        return

    await ctx.reply(f"❌ Article inconnu : `{item}`. Tape `!shop` pour voir le catalogue.")


# --- Don & Entraide ---
@bot.command(name="don")
async def don(ctx: commands.Context, target: discord.Member = None, montant: int = 0):
    """!don @joueur <montant> — Donner des Flamtons."""
    if SHOP_CHANNEL_ID and ctx.channel.id != SHOP_CHANNEL_ID:
        await ctx.reply(f"❌ Utilise <#{SHOP_CHANNEL_ID}> pour les dons.", delete_after=5)
        return
    if not target or not montant:
        await ctx.reply(f"❌ Usage : `!don @joueur <montant>` (min {DON_MIN}, max {DON_MAX_DAILY}/jour)")
        return
    if target.id == ctx.author.id:
        await ctx.reply("❌ Tu ne peux pas te donner des Flamtons à toi-même.")
        return
    if target.bot:
        await ctx.reply("❌ Tu ne peux pas donner à un bot.")
        return
    if montant < DON_MIN:
        await ctx.reply(f"❌ Don minimum : **{DON_MIN}** {FLAMTON}")
        return
    daily = get_daily_donated(ctx.author.id)
    if daily + montant > DON_MAX_DAILY:
        reste = DON_MAX_DAILY - daily
        await ctx.reply(f"❌ Limite de don journalière : **{DON_MAX_DAILY}** {FLAMTON}. Il te reste **{reste}** {FLAMTON} de don aujourd'hui.")
        return
    bal = get_balance(ctx.author.id)
    if bal < montant:
        await ctx.reply(f"❌ Solde insuffisant. Tu as **{bal}** {FLAMTON}")
        return

    add_coins(ctx.author.id, -montant)
    add_coins(target.id, montant)
    add_daily_donated(ctx.author.id, montant)

    embed = discord.Embed(title="🎁 Don", color=discord.Color.green())
    embed.description = (
        f"{ctx.author.mention} → {target.mention}\n"
        f"**{montant}** {FLAMTON}"
    )
    await ctx.reply(embed=embed)


@bot.command(name="entraide")
async def entraide(ctx: commands.Context, *, description: str = ""):
    """!entraide <description> — Crée un ticket d'entraide privé."""
    if ENTRAIDE_CHANNEL_ID and ctx.channel.id != ENTRAIDE_CHANNEL_ID:
        await ctx.reply(f"❌ Utilise <#{ENTRAIDE_CHANNEL_ID}> pour l'entraide.", delete_after=5)
        return
    if not description:
        await ctx.reply("❌ Usage : `!entraide <ta demande>`\nEx: `!entraide Besoin d'aide pour le donjon Nidas, je suis lvl 180 enu`")
        return

    # Permissions : créateur + admins + bot seulement (pas la guilde entière)
    overwrites = {
        ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    for role in ctx.guild.roles:
        if role.name == ADMIN_ROLE:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    # Créer dans la catégorie dédiée si configurée
    category = ctx.guild.get_channel(ENTRAIDE_CATEGORY_ID) if ENTRAIDE_CATEGORY_ID else None

    try:
        ticket = await ctx.guild.create_text_channel(
            f"entraide-{ctx.author.display_name}".lower().replace(" ", "-")[:50],
            category=category,
            overwrites=overwrites,
            reason="Demande d'entraide"
        )

        # Message dans le ticket
        ticket_embed = discord.Embed(title="🤝 Demande d'entraide", color=discord.Color.teal())
        ticket_embed.description = (
            f"**Joueur :** {ctx.author.mention}\n"
            f"**Demande :** {description}\n\n"
            f"⏳ En attente d'un helper... Quelqu'un doit taper `!prendre` dans <#{ENTRAIDE_CHANNEL_ID}> pour te rejoindre.\n"
            f"Le créateur, le helper ou un admin peut fermer avec `!fermer`.\n\n"
            f"⚠️ **L'entraide existe pour jouer ensemble, pas pour profiter des autres.** "
            f"Tout abus sera sanctionné par les admins.\n\n"
            f"🚫 **Toute tentative de génération abusive de {FLAMTON} est interdite** : "
            f"AFK farming, exploitation du système vocal, faux tickets, multicompte, "
            f"ou tout autre moyen détourné. Les admins surveillent et sanctionnent."
        )
        await ticket.send(embed=ticket_embed)

        # Annonce dans #entraide pour que quelqu'un prenne en charge
        annonce_embed = discord.Embed(title="🆘 Nouvelle demande d'entraide", color=discord.Color.teal())
        annonce_embed.description = (
            f"**Joueur :** {ctx.author.mention}\n"
            f"**Demande :** {description}\n\n"
            f"👉 **Réponds à ce message avec `!prendre` pour prendre en charge cette demande.**"
        )
        annonce_embed.set_footer(text=f"Ticket : {ticket.name}")
        annonce_msg = await ctx.reply(embed=annonce_embed)

        # Stocker le lien annonce → ticket dans bot_meta
        conn = sqlite3.connect("songe.db")
        c = conn.cursor()
        c.execute("INSERT INTO bot_meta (key, value) VALUES (?, ?)",
                  (f"entraide_ticket_{annonce_msg.id}", f"{ticket.id}|{ctx.author.id}"))
        conn.commit()
        conn.close()

    except Exception as e:
        await ctx.reply(f"❌ Erreur création ticket : {e}")


@bot.command(name="prendre")
async def prendre(ctx: commands.Context):
    """!prendre — Réponds à une annonce d'entraide pour prendre en charge la demande."""
    if ENTRAIDE_CHANNEL_ID and ctx.channel.id != ENTRAIDE_CHANNEL_ID:
        return

    ref = ctx.message.reference
    if not ref or not ref.message_id:
        await ctx.reply("❌ Réponds au message d'annonce d'entraide avec `!prendre`.", delete_after=5)
        return

    # Récupérer le ticket lié
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("SELECT value FROM bot_meta WHERE key = ?", (f"entraide_ticket_{ref.message_id}",))
    row = c.fetchone()
    conn.close()

    if not row:
        await ctx.reply("❌ Ce message n'est pas une annonce d'entraide.", delete_after=5)
        return

    parts = row[0].split("|")
    ticket_id = int(parts[0])
    creator_id = int(parts[1])

    if ctx.author.id == creator_id:
        await ctx.reply("❌ Tu ne peux pas prendre en charge ta propre demande.", delete_after=5)
        return

    ticket = ctx.guild.get_channel(ticket_id)
    if not ticket:
        await ctx.reply("❌ Le ticket a été supprimé.", delete_after=5)
        return

    # Vérifier si déjà pris en charge
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("SELECT value FROM bot_meta WHERE key = ?", (f"entraide_helper_{ticket_id}",))
    existing = c.fetchone()
    conn.close()
    if existing:
        await ctx.reply("❌ Cette demande est déjà prise en charge.", delete_after=5)
        return

    # Confirmation (2 min)
    creator = ctx.guild.get_member(creator_id)
    creator_name = creator.display_name if creator else "le joueur"
    confirm_msg = await ctx.reply(
        f"✋ {ctx.author.mention}, tu confirmes prendre en charge la demande de **{creator_name}** ?\n"
        f"Réagis ✅ pour confirmer (2 min)."
    )
    await confirm_msg.add_reaction("✅")

    def check(reaction, user):
        return user.id == ctx.author.id and str(reaction.emoji) == "✅" and reaction.message.id == confirm_msg.id

    try:
        await bot.wait_for("reaction_add", timeout=120.0, check=check)
    except Exception:
        await confirm_msg.edit(content=f"⏰ {ctx.author.display_name} n'a pas confirmé à temps.")
        return

    # Ajouter le helper au ticket
    try:
        await ticket.set_permissions(ctx.author, read_messages=True, send_messages=True)
    except Exception as e:
        await confirm_msg.edit(content=f"❌ Erreur d'ajout au ticket : {e}")
        return

    # Enregistrer le helper
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO bot_meta (key, value) VALUES (?, ?)",
              (f"entraide_helper_{ticket_id}", str(ctx.author.id)))
    conn.commit()
    conn.close()

    # Message dans le ticket
    await ticket.send(
        f"✅ **{ctx.author.mention} prend en charge cette demande !**\n"
        f"Vous pouvez discuter ici. Tapez `!fermer` quand c'est terminé."
    )

    # Mettre à jour l'annonce
    try:
        annonce_msg = await ctx.channel.fetch_message(ref.message_id)
        updated_embed = annonce_msg.embeds[0].copy()
        updated_embed.description = (
            f"**Joueur :** <@{creator_id}>\n"
            f"**Demande :** {updated_embed.description.split('Demande :** ')[1].split(chr(10))[0]}\n\n"
            f"✅ **Pris en charge par {ctx.author.mention}**"
        )
        updated_embed.color = discord.Color.green()
        await annonce_msg.edit(embed=updated_embed)
    except Exception:
        pass

    await confirm_msg.edit(content=f"✅ Tu as rejoint le ticket {ticket.mention} !")


@bot.command(name="fermer")
async def fermer(ctx: commands.Context):
    """!fermer — Ferme un ticket (entraide/nidas). Créateur, helper ou admin."""
    if not ctx.channel.name.startswith(("entraide-", "pl-nidas-")):
        await ctx.reply("❌ Cette commande ne fonctionne que dans un ticket.")
        return

    is_admin = any(role.name == ADMIN_ROLE for role in ctx.author.roles) or ctx.author.guild_permissions.administrator

    # Trouver le créateur (premier mentionné dans le premier embed)
    creator_id = None
    async for msg in ctx.channel.history(limit=1, oldest_first=True):
        if msg.embeds:
            desc = msg.embeds[0].description or ""
            mention_match = re.search(r'<@!?(\d+)>', desc)
            if mention_match:
                creator_id = int(mention_match.group(1))

    # Trouver le helper
    helper_id = None
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("SELECT value FROM bot_meta WHERE key = ?", (f"entraide_helper_{ctx.channel.id}",))
    row = c.fetchone()
    conn.close()
    if row:
        helper_id = int(row[0])

    is_creator = ctx.author.id == creator_id
    is_helper = ctx.author.id == helper_id

    if not is_admin and not is_creator and not is_helper:
        await ctx.reply(f"❌ Seul le créateur, le helper ou un admin ({ADMIN_ROLE}) peut fermer ce ticket.")
        return

    # Confirmation de fermeture
    confirm_msg = await ctx.reply(
        f"🔒 {ctx.author.mention}, tu confirmes la fermeture de ce ticket ?\n"
        f"Réagis ✅ pour confirmer (2 min)."
    )
    await confirm_msg.add_reaction("✅")

    def check(reaction, user):
        return user.id == ctx.author.id and str(reaction.emoji) == "✅" and reaction.message.id == confirm_msg.id

    try:
        await bot.wait_for("reaction_add", timeout=120.0, check=check)
    except Exception:
        await confirm_msg.edit(content="⏰ Fermeture annulée (pas de confirmation).")
        return

    await ctx.reply("🔒 **Ticket fermé.** Suppression dans 10 secondes...")

    # Nettoyage bot_meta
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("DELETE FROM bot_meta WHERE key LIKE ?", (f"entraide_%_{ctx.channel.id}",))
    c.execute("DELETE FROM bot_meta WHERE key LIKE ? AND value LIKE ?",
              (f"entraide_ticket_%", f"{ctx.channel.id}|%"))
    conn.commit()
    conn.close()

    await asyncio.sleep(10)
    try:
        await ctx.channel.delete(reason="Ticket fermé")
    except Exception as e:
        await ctx.reply(f"❌ Erreur suppression : {e}")


# --- Roast & Compliment automatiques (1x/jour chacun) ---
@tasks.loop(hours=24)
async def daily_roast():
    """Poste un roast automatique sur un membre aléatoire chaque jour."""
    if not FUN_CHANNEL_ID or not GUILD_MEMBER_ROLE_ID:
        return
    for guild in bot.guilds:
        channel = guild.get_channel(FUN_CHANNEL_ID)
        role = guild.get_role(GUILD_MEMBER_ROLE_ID)
        if not channel or not role:
            continue
        members = [m for m in role.members if not m.bot]
        if not members:
            continue
        target = random.choice(members)
        try:
            text = generate_text_gemini(
                f"Tu es un bot Discord humoristique dans une guilde gaming. Génère une vanne drôle et bon enfant "
                f"(pas méchant, pas vulgaire, pas discriminatoire) sur le joueur \"{target.display_name}\". "
                f"Tu peux parler de gaming, de ses habitudes imaginaires, de stéréotypes de joueurs, d'humour absurde, "
                f"de MMO, de rage quit, de tryhard, de tout ce qui est drôle dans la culture gaming et internet. "
                f"Maximum 2 phrases. En français. Sois créatif et varié."
            )
            embed = discord.Embed(title=f"🔥 Roast du jour — {target.display_name}", description=text, color=discord.Color.orange())
            embed.set_footer(text="Roast automatique par Pyrosonge")
            await channel.send(target.mention, embed=embed)
        except Exception as e:
            print(f"[ROAST] Erreur Gemini : {e}")

@daily_roast.before_loop
async def before_daily_roast():
    await bot.wait_until_ready()
    now = datetime.now(TIMEZONE)
    target_time = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now >= target_time:
        target_time += timedelta(days=1)
    wait_seconds = (target_time - now).total_seconds()
    print(f"[ROAST] Premier roast dans {wait_seconds/3600:.1f}h (à 12h)")
    await asyncio.sleep(wait_seconds)


@tasks.loop(hours=24)
async def daily_compliment():
    """Poste un compliment automatique sur un membre aléatoire chaque jour."""
    if not FUN_CHANNEL_ID or not GUILD_MEMBER_ROLE_ID:
        return
    for guild in bot.guilds:
        channel = guild.get_channel(FUN_CHANNEL_ID)
        role = guild.get_role(GUILD_MEMBER_ROLE_ID)
        if not channel or not role:
            continue
        members = [m for m in role.members if not m.bot]
        if not members:
            continue
        target = random.choice(members)
        try:
            text = generate_text_gemini(
                f"Tu es un bot Discord bienveillant dans une guilde gaming. Génère un compliment sympa et original "
                f"sur le joueur \"{target.display_name}\". Tu peux parler de gaming, d'entraide, de skill, "
                f"de sa personnalité imaginaire, de sa valeur dans l'équipe, d'humour positif. "
                f"Maximum 2 phrases. En français. Sois créatif et varié."
            )
            embed = discord.Embed(title=f"💖 Compliment du jour — {target.display_name}", description=text, color=discord.Color.magenta())
            embed.set_footer(text="Compliment automatique par Pyrosonge")
            await channel.send(target.mention, embed=embed)
        except Exception as e:
            print(f"[COMPLIMENT] Erreur Gemini : {e}")

@daily_compliment.before_loop
async def before_daily_compliment():
    await bot.wait_until_ready()
    now = datetime.now(TIMEZONE)
    target_time = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if now >= target_time:
        target_time += timedelta(days=1)
    wait_seconds = (target_time - now).total_seconds()
    print(f"[COMPLIMENT] Premier compliment dans {wait_seconds/3600:.1f}h (à 18h)")
    await asyncio.sleep(wait_seconds)


# --- Sondage (gratuit) ---
@bot.command(name="sondage")
async def sondage(ctx: commands.Context, *, question: str = ""):
    """!sondage "question" — Crée un sondage avec réactions."""
    if FUN_CHANNEL_ID and ctx.channel.id != FUN_CHANNEL_ID:
        await ctx.reply(f"❌ Utilise <#{FUN_CHANNEL_ID}> pour les sondages.", delete_after=5)
        return
    if not question:
        await ctx.reply("❌ Usage : `!sondage \"ta question\"`")
        return
    question = question.strip().strip('"')
    embed = discord.Embed(title="📊 Sondage", description=question, color=discord.Color.blue())
    embed.set_footer(text=f"Par {ctx.author.display_name} — Réagis pour voter !")
    poll = await ctx.reply(embed=embed)
    await poll.add_reaction("👍")
    await poll.add_reaction("👎")


@bot.command(name="aide")
async def aide(ctx: commands.Context):
    """!aide — Liste des commandes."""
    allowed = set(ALLOWED_CHANNEL_IDS)
    for cid in (CASINO_CHANNEL_ID, SHOP_CHANNEL_ID, FUN_CHANNEL_ID, ENTRAIDE_CHANNEL_ID):
        if cid: allowed.add(cid)
    if ctx.channel.id not in allowed:
        return

    embed = discord.Embed(
        title="🤖 Pyrosonge — Commandes",
        color=discord.Color.dark_gold()
    )
    embed.add_field(name="📸 Enregistrer une run", value="`!songe @joueurs` + screenshot\n`!bolgrot @joueurs` + screenshot", inline=False)
    embed.add_field(name="🏆 Classements", value="`!classement` — classement Songe\n`!classement bolgrot` — classement Bolgrot", inline=False)
    embed.add_field(name="📊 Stats", value="`!stats @joueur` — stats d'un joueur\n`!historique @joueur` — historique des runs", inline=False)
    embed.add_field(name="📅 Événements", value="`!event` — événements en cours\n`!inscrits` — liste des inscrits", inline=False)
    embed.add_field(name="🎰 Casino", value=(
        "`!solde` — voir ton solde\n"
        "`!classement-coins` — top 15 des fortunes\n"
        f"`!slots <mise>` — machine à sous ({SLOTS_MIN_BET}-{SLOTS_MAX_BET} {FLAMTON})\n"
        f"`!coinflip @joueur <mise>` — pile ou face PvP (10-{COINFLIP_MAX_BET} {FLAMTON})\n"
        f"`!roulette <mise> <choix>` — roulette ({ROULETTE_MIN_BET}-{ROULETTE_MAX_BET} {FLAMTON})\n"
        f"⚠️ Limite perte : {CASINO_DAILY_LOSS_CAP}/jour (slots/roulette)"
    ), inline=False)
    embed.add_field(name="🛒 Boutique", value=(
        "`!shop` — voir la boutique\n"
        "`!buy <item>` — acheter un article\n"
        f"`!don @joueur <montant>` — don direct (max {DON_MAX_DAILY}/jour)"
    ), inline=False)
    embed.add_field(name="🤝 Entraide", value=(
        "`!entraide <demande>` — crée un ticket privé\n"
        "`!prendre` — réponds à une annonce pour aider\n"
        "`!fermer` — ferme un ticket (créateur, helper ou admin)"
    ), inline=False)
    embed.add_field(name="🎉 Fun", value=(
        "🔥 **Roast auto** — tous les jours à 12h\n"
        "💖 **Compliment auto** — tous les jours à 18h\n"
        "`!sondage \"question\"` — sondage avec réactions"
    ), inline=False)
    embed.add_field(name="🔧 Admin", value=f"`!raid @joueurs` — récompense les participants d'un raid (+{RAID_REWARD} {FLAMTON})\n`!coins add/remove @joueur <montant>` — créditer/retirer des {FLAMTON}\n**Répondre** au message d'une run + `!supprun` — supprimer cette run\n`!reset` — réinitialiser toutes les runs\n`!backup` — sauvegarder la base\n`!restore` — restaurer depuis un backup\n`!purge <id_debut> <id_fin>` — supprimer les messages entre deux IDs\n(rôle {ADMIN_ROLE} requis)", inline=False)

    await ctx.reply(embed=embed)


@bot.command(name="coins")
async def coins_admin(ctx: commands.Context, action: str = "", target: discord.Member = None, montant: int = 0):
    """!coins add/remove @joueur <montant> — Créditer ou retirer des Flamtons (admin)."""
    is_admin = any(role.name == ADMIN_ROLE for role in ctx.author.roles) or ctx.author.guild_permissions.administrator
    if not is_admin:
        await ctx.reply(f"❌ Réservé aux **{ADMIN_ROLE}** / administrateurs.")
        return
    if action not in ("add", "remove") or not target or not montant or montant <= 0:
        await ctx.reply("❌ Usage : `!coins add @joueur <montant>` ou `!coins remove @joueur <montant>`")
        return
    if action == "add":
        new_bal = add_coins(target.id, montant)
        await ctx.reply(f"✅ **+{montant}** {FLAMTON} crédités à {target.mention}. Nouveau solde : **{new_bal}** {FLAMTON}")
    else:
        bal = get_balance(target.id)
        to_remove = min(montant, bal)
        new_bal = add_coins(target.id, -to_remove)
        await ctx.reply(f"✅ **-{to_remove}** {FLAMTON} retirés à {target.mention}. Nouveau solde : **{new_bal}** {FLAMTON}")


@bot.command(name="backup")
async def backup(ctx: commands.Context):
    """!backup — Sauvegarde la DB en fichier sur Discord (admin)."""
    if ctx.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    is_admin = any(role.name == ADMIN_ROLE for role in ctx.author.roles) or ctx.author.guild_permissions.administrator
    if not is_admin:
        await ctx.reply(f"❌ Tu dois avoir le rôle **{ADMIN_ROLE}** ou être administrateur.")
        return

    try:
        await ctx.reply(
            "💾 **Backup de la base de données**\nGarde ce fichier précieusement ! Utilise `!restore` en répondant à ce message pour restaurer.",
            file=discord.File("songe.db", filename="songe_backup.db")
        )
    except Exception as e:
        await ctx.reply(f"❌ Erreur backup: {e}")


@bot.command(name="restore")
async def restore(ctx: commands.Context):
    """!restore — Restaure la DB depuis un backup (admin). Réponds au message du backup."""
    if ctx.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    is_admin = any(role.name == ADMIN_ROLE for role in ctx.author.roles) or ctx.author.guild_permissions.administrator
    if not is_admin:
        await ctx.reply(f"❌ Tu dois avoir le rôle **{ADMIN_ROLE}** ou être administrateur.")
        return

    # Chercher le fichier : soit en pièce jointe directe, soit dans le message répondu
    attachment = None
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
    elif ctx.message.reference and ctx.message.reference.message_id:
        try:
            ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            if ref_msg.attachments:
                attachment = ref_msg.attachments[0]
        except:
            pass

    if not attachment or not attachment.filename.endswith(".db"):
        await ctx.reply("❌ Réponds au message du backup (celui avec le fichier `.db`) avec `!restore`, ou attache directement le fichier.")
        return

    try:
        db_bytes = await attachment.read()
        with open("songe.db", "wb") as f:
            f.write(db_bytes)
        # Réinitialiser la DB pour appliquer les migrations
        init_db()
        conn = sqlite3.connect("songe.db")
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM runs")
        nb_runs = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT rp.discord_id) FROM run_players rp")
        nb_players = c.fetchone()[0]
        conn.close()
        await ctx.reply(f"✅ **Base restaurée !** {nb_runs} runs, {nb_players} joueurs récupérés.")
    except Exception as e:
        await ctx.reply(f"❌ Erreur restore: {e}")


@bot.command(name="purge")
async def purge(ctx: commands.Context, id_debut: int = 0, id_fin: int = 0):
    """!purge <id_debut> <id_fin> — Supprime les messages entre deux IDs (admin)."""
    if ctx.channel.id not in ALLOWED_CHANNEL_IDS and ctx.channel.id != BACKUP_CHANNEL_ID:
        return

    is_admin = any(role.name == ADMIN_ROLE for role in ctx.author.roles) or ctx.author.guild_permissions.administrator
    if not is_admin:
        await ctx.reply(f"❌ Tu dois avoir le rôle **{ADMIN_ROLE}** ou être administrateur.")
        return

    if not id_debut or not id_fin:
        await ctx.reply("❌ Usage : `!purge <id_message_debut> <id_message_fin>`\n"
                         "💡 Active le mode développeur Discord pour copier les IDs (clic droit → Copier l'identifiant).")
        return

    if id_debut > id_fin:
        id_debut, id_fin = id_fin, id_debut

    try:
        after = discord.Object(id=id_debut - 1)
        before = discord.Object(id=id_fin + 1)
        messages = [m async for m in ctx.channel.history(after=after, before=before, limit=500)]

        if not messages:
            await ctx.reply("❌ Aucun message trouvé entre ces deux IDs.")
            return

        msg = await ctx.reply(f"🗑️ Suppression de {len(messages)} message(s) en cours...")

        now = datetime.now(pytz.utc)
        recent = [m for m in messages if (now - m.created_at).days < 14]
        old = [m for m in messages if (now - m.created_at).days >= 14]

        deleted = 0
        errors = 0

        # Bulk delete pour les messages < 14 jours (par lots de 100)
        for i in range(0, len(recent), 100):
            batch = recent[i:i+100]
            try:
                await ctx.channel.delete_messages(batch)
                deleted += len(batch)
            except Exception:
                for m in batch:
                    try:
                        await m.delete()
                        deleted += 1
                    except Exception:
                        errors += 1

        # Un par un pour les messages > 14 jours
        for i, message in enumerate(old):
            try:
                await message.delete()
                deleted += 1
            except Exception:
                errors += 1
            if (i + 1) % 10 == 0:
                await msg.edit(content=f"🗑️ Suppression en cours... {deleted}/{len(messages)}")

        summary = f"✅ **Purge terminée** — {deleted}/{len(messages)} message(s) supprimé(s)."
        if errors:
            summary += f"\n⚠️ {errors} message(s) n'ont pas pu être supprimés (permissions manquantes ?)."
        if old:
            summary += f"\n📌 {len(old)} message(s) avaient +14 jours (suppression lente)."
        await msg.edit(content=summary)
    except discord.Forbidden:
        await ctx.reply("❌ Le bot n'a pas la permission **Gérer les messages** dans ce salon.")
    except Exception as e:
        await ctx.reply(f"❌ Erreur purge: {e}")


bot.run(DISCORD_TOKEN)
