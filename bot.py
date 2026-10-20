import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
import base64
import re
import os
import json
import logging
import asyncio
from datetime import datetime, UTC
from dotenv import load_dotenv

load_dotenv()

# --- Config ---
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ALLOWED_CHANNEL_IDS = [int(x.strip()) for x in os.environ["ALLOWED_CHANNEL_IDS"].split(",")]
ADMIN_ROLE_NAME = os.environ.get("ADMIN_ROLE_NAME", "Pyrosonge Admin")
GEMINI_TIMEOUT = 60
DB_PATH = "songe.db"

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("pyrosonge.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("pyrosonge")

# --- DB (async) ---
async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                submitted_by INTEGER NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS run_players (
                run_id INTEGER NOT NULL,
                discord_id INTEGER NOT NULL,
                discord_name TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(id),
                UNIQUE(run_id, discord_id)
            )
        """)
        await db.commit()

# --- Intensité ranking (plus haut = mieux) ---
INTENSITE_ORDER = {
    "normal": 0,
    "ardu i": 1, "ardu ii": 2, "ardu iii": 3, "ardu iv": 4, "ardu v": 5,
    "difficile i": 6, "difficile ii": 7, "difficile iii": 8, "difficile iv": 9, "difficile v": 10,
    "cauchemar i": 11, "cauchemar ii": 12, "cauchemar iii": 13, "cauchemar iv": 14, "cauchemar v": 15,
    "paradoxe i": 16, "paradoxe ii": 17, "paradoxe iii": 18, "paradoxe iv": 19, "paradoxe v": 20,
}

INTENSITE_NAMES = {v: k.title() for k, v in INTENSITE_ORDER.items()}


def get_intensite_rank(intensite: str) -> int:
    return INTENSITE_ORDER.get(intensite.lower().strip(), -1)


def parse_time_to_seconds(time_str: str) -> int:
    parts = time_str.strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0


def is_allowed(ctx: commands.Context) -> bool:
    return ctx.channel.id in ALLOWED_CHANNEL_IDS


def is_admin(ctx: commands.Context) -> bool:
    if ctx.author.guild_permissions.administrator:
        return True
    return any(r.name == ADMIN_ROLE_NAME for r in ctx.author.roles)


# --- Gemini Vision (REST API, async, avec timeout) ---
async def analyze_screenshot(image_bytes: bytes, media_type: str) -> dict:
    import aiohttp

    prompt = """Analyse ce screenshot de fin de Songe Dofus. Extrais les données suivantes en JSON strict :
{
  "intensite": "ex: Paradoxe IV, Cauchemar I, Ardu III...",
  "temps": "le temps affiché, format HH:MM:SS ou MM:SS",
  "resultat": "Victoire ou Défaite",
  "palier": "le palier atteint si visible, sinon null",
  "score": le score numérique si visible sinon null,
  "tours": le nombre de tours si visible sinon null,
  "songeur": "le nom du songeur si visible, sinon null"
}

Réponds UNIQUEMENT avec le JSON, rien d'autre."""

    image_b64 = base64.b64encode(image_bytes).decode()

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": media_type, "data": image_b64}}
            ]
        }]
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={GEMINI_API_KEY}"
    timeout = aiohttp.ClientTimeout(total=GEMINI_TIMEOUT)

    max_retries = 3
    for attempt in range(max_retries):
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, ssl=False) as resp:
                if resp.status == 503 and attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status != 200:
                    body = await resp.text()
                    raise ValueError(f"Gemini API erreur {resp.status}: {body[:200]}")
                result = await resp.json()
        break

    text = result["candidates"][0]["content"]["parts"][0]["text"].strip()

    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"Impossible de parser la réponse Gemini: {text[:200]}")


# --- Bot ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


# =====================
# EVENTS
# =====================

@bot.event
async def on_ready():
    await init_db()
    log.info(f"Bot connecté: {bot.user}")
    try:
        synced = await bot.tree.sync()
        log.info(f"{len(synced)} commandes slash synchronisées")
    except Exception as e:
        log.error(f"Erreur sync: {e}")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    log.error(f"Erreur commande {ctx.command}: {error}", exc_info=error)
    await ctx.reply(f"❌ Une erreur est survenue: {error}")


# =====================
# !songe — Enregistrer une run
# =====================

@bot.command(name="songe")
async def songe(ctx: commands.Context):
    """!songe @joueur1 @joueur2 ... + screenshot attaché"""
    if not is_allowed(ctx):
        return

    if not ctx.message.attachments:
        await ctx.reply("❌ Attache un screenshot de fin de Songe.\nUsage: `!songe @joueur1 @joueur2` + image")
        return

    attachment = ctx.message.attachments[0]
    if not attachment.content_type or not attachment.content_type.startswith("image/"):
        await ctx.reply("❌ Le fichier attaché n'est pas une image.")
        return

    mentioned = ctx.message.mentions
    if not mentioned:
        await ctx.reply("❌ Mentionne les joueurs du Songe.\nUsage: `!songe @joueur1 @joueur2` + image")
        return

    msg = await ctx.reply("🔍 Analyse du screenshot en cours...")

    try:
        image_bytes = await attachment.read()
        media_type = attachment.content_type

        try:
            data = await analyze_screenshot(image_bytes, media_type)
        except asyncio.TimeoutError:
            await msg.edit(content="❌ Gemini n'a pas répondu à temps. Réessaie dans quelques secondes.")
            return
        except ValueError as e:
            await msg.edit(content=f"❌ Erreur d'analyse: {e}")
            return

        intensite = data.get("intensite", "Inconnu")
        temps_display = data.get("temps", "00:00")
        resultat = data.get("resultat", "Inconnu")
        palier = data.get("palier")
        score = data.get("score")
        tours = data.get("tours")
        songeur = data.get("songeur")

        intensite_rank = get_intensite_rank(intensite)
        temps_seconds = parse_time_to_seconds(temps_display)

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                INSERT INTO runs (timestamp, intensite, intensite_rank, temps_seconds, temps_display,
                                resultat, palier, score, tours, songeur, submitted_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now(UTC).isoformat(), intensite, intensite_rank, temps_seconds,
                temps_display, resultat, palier, score, tours, songeur, ctx.author.id
            ))
            run_id = cursor.lastrowid

            for member in mentioned:
                await db.execute("INSERT OR IGNORE INTO run_players (run_id, discord_id, discord_name) VALUES (?, ?, ?)",
                                 (run_id, member.id, member.display_name))
            await db.commit()

        embed = discord.Embed(
            title=f"{'✅ Victoire' if resultat == 'Victoire' else '❌ Défaite'} — Songe enregistré",
            color=discord.Color.green() if resultat == "Victoire" else discord.Color.red()
        )
        embed.add_field(name="Intensité", value=intensite, inline=True)
        embed.add_field(name="Temps", value=temps_display, inline=True)
        if tours:
            embed.add_field(name="Tours", value=str(tours), inline=True)
        if songeur:
            embed.add_field(name="Songeur", value=songeur, inline=True)
        embed.add_field(name="Joueurs", value=" ".join(m.mention for m in mentioned), inline=False)
        embed.set_footer(text=f"Run #{run_id}")

        await msg.edit(content=None, embed=embed)
        log.info(f"Run #{run_id} enregistrée: {intensite} {resultat} par {ctx.author}")

    except Exception as e:
        log.error(f"Erreur commande songe: {e}", exc_info=True)
        await msg.edit(content=f"❌ Erreur inattendue. Réessaie ou contacte un admin.")


# =====================
# !classement — Top global
# =====================

@bot.command(name="classement")
async def classement(ctx: commands.Context):
    """!classement — Classement des meilleurs temps par intensité."""
    if not is_allowed(ctx):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("""
            SELECT rp.discord_id, rp.discord_name,
                   r.intensite, r.temps_display, r.temps_seconds, r.intensite_rank, r.id
            FROM run_players rp
            JOIN runs r ON r.id = rp.run_id
            WHERE r.resultat = 'Victoire'
              AND (rp.discord_id, r.intensite_rank, r.temps_seconds) IN (
                  SELECT rp2.discord_id, MAX(r2.intensite_rank), MIN(r2.temps_seconds)
                  FROM run_players rp2
                  JOIN runs r2 ON r2.id = rp2.run_id
                  WHERE r2.resultat = 'Victoire'
                    AND r2.intensite_rank = (
                        SELECT MAX(r3.intensite_rank)
                        FROM run_players rp3
                        JOIN runs r3 ON r3.id = rp3.run_id
                        WHERE rp3.discord_id = rp2.discord_id AND r3.resultat = 'Victoire'
                    )
                  GROUP BY rp2.discord_id
              )
            GROUP BY rp.discord_id
            ORDER BY r.intensite_rank DESC, r.temps_seconds ASC
            LIMIT 15
        """)

    if not rows:
        await ctx.reply("📭 Aucune run enregistrée.")
        return

    embed = discord.Embed(
        title="🏆 Classement Songe — Records personnels",
        description="Meilleure intensité puis meilleur temps",
        color=discord.Color.gold()
    )

    for i, row in enumerate(rows, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"**{i}.**"
        embed.add_field(
            name=f"{medal} {row[1]}",
            value=f"{row[2]} — {row[3]}\n`Run #{row[6]}`",
            inline=False
        )

    await ctx.reply(embed=embed)


# =====================
# !top <intensité> — Classement par intensité
# =====================

@bot.command(name="top")
async def top(ctx: commands.Context, *, intensite: str = None):
    """!top <intensité> — Meilleurs temps sur une intensité. Ex: !top paradoxe v"""
    if not is_allowed(ctx):
        return

    if not intensite:
        await ctx.reply("❌ Précise l'intensité.\nEx: `!top paradoxe v`, `!top cauchemar iii`")
        return

    rank = get_intensite_rank(intensite)
    if rank == -1:
        valid = ", ".join(f"`{k.title()}`" for k in INTENSITE_ORDER)
        await ctx.reply(f"❌ Intensité inconnue. Valeurs possibles:\n{valid}")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        rows = await db.execute_fetchall("""
            SELECT rp.discord_name, r.temps_display, r.temps_seconds, r.tours, r.id
            FROM run_players rp
            JOIN runs r ON r.id = rp.run_id
            WHERE r.resultat = 'Victoire' AND r.intensite_rank = ?
            ORDER BY r.temps_seconds ASC
            LIMIT 10
        """, (rank,))

    if not rows:
        await ctx.reply(f"📭 Aucune victoire en **{intensite.title()}**.")
        return

    embed = discord.Embed(
        title=f"⚔️ Top {intensite.title()}",
        description="Classé par temps le plus rapide",
        color=discord.Color.purple()
    )

    for i, row in enumerate(rows, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"**{i}.**"
        tours_str = f" ({row[3]} tours)" if row[3] else ""
        embed.add_field(
            name=f"{medal} {row[0]}",
            value=f"{row[1]}{tours_str} — `Run #{row[4]}`",
            inline=False
        )

    await ctx.reply(embed=embed)


# =====================
# !stats @joueur — Stats détaillées
# =====================

@bot.command(name="stats")
async def stats(ctx: commands.Context):
    """!stats @joueur — Stats d'un joueur."""
    if not is_allowed(ctx):
        return

    if not ctx.message.mentions:
        await ctx.reply("❌ Mentionne un joueur.\nEx: `!stats @joueur`")
        return

    target = ctx.message.mentions[0]

    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchall("""
            SELECT COUNT(*), SUM(CASE WHEN r.resultat='Victoire' THEN 1 ELSE 0 END)
            FROM runs r JOIN run_players rp ON r.id = rp.run_id
            WHERE rp.discord_id = ?
        """, (target.id,))
        total, victoires = row[0][0], row[0][1] or 0

        best = await db.execute_fetchall("""
            SELECT r.intensite, r.temps_display
            FROM runs r JOIN run_players rp ON r.id = rp.run_id
            WHERE rp.discord_id = ? AND r.resultat = 'Victoire'
            ORDER BY r.intensite_rank DESC, r.temps_seconds ASC
            LIMIT 1
        """, (target.id,))

        intensites = await db.execute_fetchall("""
            SELECT r.intensite, COUNT(*)
            FROM runs r JOIN run_players rp ON r.id = rp.run_id
            WHERE rp.discord_id = ? AND r.resultat = 'Victoire'
            GROUP BY r.intensite
            ORDER BY r.intensite_rank DESC
            LIMIT 5
        """, (target.id,))

    embed = discord.Embed(title=f"📊 Stats de {target.display_name}", color=discord.Color.blue())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Runs", value=str(total), inline=True)
    embed.add_field(name="Victoires", value=f"{victoires}/{total}", inline=True)
    if total > 0:
        embed.add_field(name="Winrate", value=f"{victoires/total*100:.0f}%", inline=True)
    if best:
        embed.add_field(name="Meilleure run", value=f"{best[0][0]} en {best[0][1]}", inline=False)
    if intensites:
        breakdown = "\n".join(f"• {row[0]}: {row[1]} victoire{'s' if row[1] > 1 else ''}" for row in intensites)
        embed.add_field(name="Victoires par intensité", value=breakdown, inline=False)

    await ctx.reply(embed=embed)


# =====================
# !historique @joueur — Toutes les runs d'un joueur
# =====================

@bot.command(name="historique")
async def historique(ctx: commands.Context):
    """!historique @joueur — Les 15 dernières runs d'un joueur."""
    if not is_allowed(ctx):
        return

    if not ctx.message.mentions:
        await ctx.reply("❌ Mentionne un joueur.\nEx: `!historique @joueur`")
        return

    target = ctx.message.mentions[0]

    async with aiosqlite.connect(DB_PATH) as db:
        rows = await db.execute_fetchall("""
            SELECT r.id, r.intensite, r.temps_display, r.resultat, r.tours, r.timestamp
            FROM runs r JOIN run_players rp ON r.id = rp.run_id
            WHERE rp.discord_id = ?
            ORDER BY r.id DESC
            LIMIT 15
        """, (target.id,))

    if not rows:
        await ctx.reply(f"📭 Aucune run enregistrée pour {target.display_name}.")
        return

    embed = discord.Embed(
        title=f"📜 Historique de {target.display_name}",
        description="15 dernières runs",
        color=discord.Color.dark_grey()
    )

    for row in rows:
        run_id, intensite, temps, resultat, tours, timestamp = row
        icon = "✅" if resultat == "Victoire" else "❌"
        date = timestamp[:10]
        tours_str = f" • {tours}T" if tours else ""
        embed.add_field(
            name=f"{icon} Run #{run_id} — {date}",
            value=f"{intensite} — {temps}{tours_str}",
            inline=False
        )

    await ctx.reply(embed=embed)


# =====================
# !vs @joueur1 @joueur2 — Comparaison
# =====================

@bot.command(name="vs")
async def vs(ctx: commands.Context):
    """!vs @joueur1 @joueur2 — Compare deux joueurs."""
    if not is_allowed(ctx):
        return

    mentions = ctx.message.mentions
    if len(mentions) < 2:
        await ctx.reply("❌ Mentionne deux joueurs.\nEx: `!vs @joueur1 @joueur2`")
        return

    p1, p2 = mentions[0], mentions[1]

    async with aiosqlite.connect(DB_PATH) as db:
        async def get_player_stats(player_id):
            row = await db.execute_fetchall("""
                SELECT COUNT(*), SUM(CASE WHEN r.resultat='Victoire' THEN 1 ELSE 0 END)
                FROM runs r JOIN run_players rp ON r.id = rp.run_id
                WHERE rp.discord_id = ?
            """, (player_id,))
            total, wins = row[0][0], row[0][1] or 0

            best = await db.execute_fetchall("""
                SELECT r.intensite, r.temps_display, r.intensite_rank
                FROM runs r JOIN run_players rp ON r.id = rp.run_id
                WHERE rp.discord_id = ? AND r.resultat = 'Victoire'
                ORDER BY r.intensite_rank DESC, r.temps_seconds ASC
                LIMIT 1
            """, (player_id,))

            return {
                "total": total,
                "wins": wins,
                "winrate": f"{wins/total*100:.0f}%" if total > 0 else "N/A",
                "best_intensite": best[0][0] if best else "—",
                "best_temps": best[0][1] if best else "—",
                "best_rank": best[0][2] if best else -1,
            }

        s1 = await get_player_stats(p1.id)
        s2 = await get_player_stats(p2.id)

    embed = discord.Embed(title=f"⚔️ {p1.display_name} vs {p2.display_name}", color=discord.Color.orange())

    embed.add_field(name=p1.display_name, value=(
        f"**Runs:** {s1['total']}\n"
        f"**Victoires:** {s1['wins']}\n"
        f"**Winrate:** {s1['winrate']}\n"
        f"**Record:** {s1['best_intensite']} en {s1['best_temps']}"
    ), inline=True)

    embed.add_field(name=p2.display_name, value=(
        f"**Runs:** {s2['total']}\n"
        f"**Victoires:** {s2['wins']}\n"
        f"**Winrate:** {s2['winrate']}\n"
        f"**Record:** {s2['best_intensite']} en {s2['best_temps']}"
    ), inline=True)

    if s1["best_rank"] > s2["best_rank"]:
        verdict = f"🏆 **{p1.display_name}** a la meilleure intensité !"
    elif s2["best_rank"] > s1["best_rank"]:
        verdict = f"🏆 **{p2.display_name}** a la meilleure intensité !"
    else:
        verdict = "🤝 Même niveau d'intensité !"
    embed.add_field(name="Verdict", value=verdict, inline=False)

    await ctx.reply(embed=embed)


# =====================
# !equipe — Classement par équipe
# =====================

@bot.command(name="equipe")
async def equipe(ctx: commands.Context):
    """!equipe — Classement des meilleures équipes."""
    if not is_allowed(ctx):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        rows = await db.execute_fetchall("""
            SELECT r.id, r.intensite, r.temps_display, r.intensite_rank, r.temps_seconds,
                   GROUP_CONCAT(rp.discord_name, ', ') as players
            FROM runs r
            JOIN run_players rp ON r.id = rp.run_id
            WHERE r.resultat = 'Victoire'
            GROUP BY r.id
            ORDER BY r.intensite_rank DESC, r.temps_seconds ASC
            LIMIT 10
        """)

    if not rows:
        await ctx.reply("📭 Aucune victoire enregistrée.")
        return

    embed = discord.Embed(
        title="👥 Classement par équipe",
        description="Meilleures runs tous joueurs confondus",
        color=discord.Color.teal()
    )

    for i, row in enumerate(rows, 1):
        run_id, intensite, temps, _, _, players = row
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"**{i}.**"
        embed.add_field(
            name=f"{medal} {intensite} — {temps}",
            value=f"{players}\n`Run #{run_id}`",
            inline=False
        )

    await ctx.reply(embed=embed)


# =====================
# !supprimer <id> — Supprimer une run
# =====================

@bot.command(name="supprimer")
async def supprimer(ctx: commands.Context, run_id: int = None):
    """!supprimer <id> — Supprime une run (admin ou auteur)."""
    if not is_allowed(ctx):
        return

    if run_id is None:
        await ctx.reply("❌ Précise l'ID de la run.\nEx: `!supprimer 5`")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchall("SELECT submitted_by FROM runs WHERE id = ?", (run_id,))
        if not row:
            await ctx.reply(f"❌ Run #{run_id} introuvable.")
            return

        if row[0][0] != ctx.author.id and not is_admin(ctx):
            await ctx.reply("❌ Tu ne peux supprimer que tes propres runs (ou être admin).")
            return

        await db.execute("DELETE FROM run_players WHERE run_id = ?", (run_id,))
        await db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        await db.commit()

    await ctx.reply(f"🗑️ Run #{run_id} supprimée.")
    log.info(f"Run #{run_id} supprimée par {ctx.author}")


# =====================
# !reset — Reset toutes les données
# =====================

@bot.command(name="reset")
async def reset(ctx: commands.Context):
    """!reset — Supprime toutes les données (admin seulement)."""
    if not is_allowed(ctx):
        return

    if not is_admin(ctx):
        await ctx.reply(f"❌ Seuls les administrateurs ou le rôle **{ADMIN_ROLE_NAME}** peuvent reset.")
        return

    if not hasattr(reset, "_pending") or reset._pending != ctx.author.id:
        reset._pending = ctx.author.id
        await ctx.reply("⚠️ Es-tu sûr ? **Toutes les runs seront supprimées.** Retape `!reset` pour confirmer.")
        return

    reset._pending = None

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM run_players")
        await db.execute("DELETE FROM runs")
        await db.execute("DELETE FROM sqlite_sequence WHERE name IN ('runs',)")
        await db.commit()

    await ctx.reply("🗑️ Toutes les données ont été supprimées. Le classement repart à zéro.")
    log.info(f"Reset complet par {ctx.author}")


# =====================
# !aide — Liste des commandes
# =====================

@bot.command(name="aide")
async def aide(ctx: commands.Context):
    """!aide — Affiche toutes les commandes disponibles."""
    if not is_allowed(ctx):
        return

    embed = discord.Embed(
        title="🔥 Pyrosonge — Commandes",
        color=discord.Color.orange()
    )
    embed.add_field(name="📸 Enregistrer", value=(
        "`!songe @joueur1 @joueur2` + screenshot\nAnalyse et enregistre une run"
    ), inline=False)
    embed.add_field(name="🏆 Classements", value=(
        "`!classement` — Records personnels\n"
        "`!top <intensité>` — Top par intensité\n"
        "`!equipe` — Meilleures équipes"
    ), inline=False)
    embed.add_field(name="📊 Joueurs", value=(
        "`!stats @joueur` — Stats détaillées\n"
        "`!historique @joueur` — 15 dernières runs\n"
        "`!vs @joueur1 @joueur2` — Comparaison"
    ), inline=False)
    embed.add_field(name="🔧 Admin", value=(
        "`!supprimer <id>` — Supprimer une run\n"
        "`!reset` — Tout supprimer (admin)"
    ), inline=False)

    await ctx.reply(embed=embed)


bot.run(DISCORD_TOKEN)
