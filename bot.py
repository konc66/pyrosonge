import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import base64
import re
import os
import json
from datetime import datetime
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
TIMEZONE = pytz.timezone("Europe/Paris")

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
    MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
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
                raise ValueError(f"Impossible de parser la réponse Gemini: {text}")
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
def get_previous_best(discord_id: int, event_type: str) -> tuple | None:
    """Retourne (intensite_rank, temps_seconds) du record actuel, ou None."""
    conn = sqlite3.connect("songe.db")
    c = conn.cursor()
    c.execute("""
        SELECT r.intensite_rank, r.temps_seconds
        FROM runs r JOIN run_players rp ON r.id = rp.run_id
        WHERE rp.discord_id = ? AND r.event_type = ? AND r.resultat = 'Victoire'
        ORDER BY r.intensite_rank DESC, r.temps_seconds ASC
        LIMIT 1
    """, (discord_id, event_type))
    result = c.fetchone()
    conn.close()
    return result

# --- Bot ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    init_db()
    print(f"Bot connecté: {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} commandes slash synchronisées")
    except Exception as e:
        print(f"Erreur sync: {e}")


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

        # Récupérer les records avant insertion
        previous_bests = {}
        for member in mentioned:
            previous_bests[member.id] = get_previous_best(member.id, event_type)

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

        await msg.edit(content=None, embed=embed)

        # Stocker le message_id pour suppression par reply
        conn = sqlite3.connect("songe.db")
        c = conn.cursor()
        c.execute("UPDATE runs SET message_id = ? WHERE id = ?", (msg.id, run_id))
        conn.commit()
        conn.close()

        # Vérifier les nouveaux records
        if resultat == "Victoire":
            record_holders = []
            for member in mentioned:
                prev = previous_bests[member.id]
                if prev is None:
                    # Premier run victorieux
                    record_holders.append(member)
                else:
                    prev_rank, prev_time = prev
                    if intensite_rank > prev_rank or (intensite_rank == prev_rank and temps_seconds < prev_time):
                        record_holders.append(member)

            if record_holders:
                names = ", ".join(m.mention for m in record_holders)
                await ctx.channel.send(
                    f"🚨 **NOUVEAU RECORD !** {names} vient(ent) de battre leur record personnel !\n"
                    f"**{intensite}** en **{temps_display}** 🔥"
                )

            # Streak
            for member in mentioned:
                streak = get_streak(member.id, event_type)
                if streak == 3:
                    await ctx.channel.send(f"🔥🔥 {member.mention} est en série de **3 victoires** d'affilée !")
                elif streak == 5:
                    await ctx.channel.send(f"🔥🔥🔥 {member.mention} est **INARRÊTABLE** — **5 victoires** d'affilée !")
                elif streak == 10:
                    await ctx.channel.send(f"👑 {member.mention} — **10 victoires d'affilée** ! Légendaire !")

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


@bot.command(name="aide")
async def aide(ctx: commands.Context):
    """!aide — Liste des commandes."""
    if ctx.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    embed = discord.Embed(
        title="🤖 Pyrosonge — Commandes",
        color=discord.Color.dark_gold()
    )
    embed.add_field(name="📸 Enregistrer une run", value="`!songe @joueurs` + screenshot\n`!bolgrot @joueurs` + screenshot", inline=False)
    embed.add_field(name="🏆 Classements", value="`!classement` — classement Songe\n`!classement bolgrot` — classement Bolgrot", inline=False)
    embed.add_field(name="📊 Stats", value="`!stats @joueur` — stats d'un joueur\n`!historique @joueur` — historique des runs", inline=False)
    embed.add_field(name="📅 Événements", value="`!event` — événements en cours\n`!inscrits` — liste des inscrits", inline=False)
    embed.add_field(name="🔧 Admin", value=f"**Répondre** au message d'une run + `!supprun` — supprimer cette run\n`!reset` — réinitialiser toutes les runs\n(rôle {ADMIN_ROLE} requis)", inline=False)

    await ctx.reply(embed=embed)


bot.run(DISCORD_TOKEN)
