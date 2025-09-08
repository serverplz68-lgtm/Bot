# Main.py
# Discord moderation bot with:
# - Kick & Ban commands
# - Automatic slowdown (anti-spam protection)

import os
import asyncio
from collections import deque, defaultdict
from datetime import datetime, timedelta

import discord
from discord.ext import commands
from dotenv import load_dotenv

# --------------------
# Load config
# --------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("BOT_PREFIX", "!")
MOD_ROLE_NAME = os.getenv("MOD_ROLE_NAME", "Moderator")

# Auto slowdown settings
SLOW_THRESHOLD = int(os.getenv("SLOW_THRESHOLD", "5"))          # messages
SLOW_WINDOW = int(os.getenv("SLOW_WINDOW", "7"))                # seconds
SLOW_MODE_DURATION = int(os.getenv("SLOW_MODE_DURATION", "10")) # seconds
SLOW_RESET_SECONDS = int(os.getenv("SLOW_RESET_SECONDS", "60")) # seconds

if not TOKEN:
    raise RuntimeError("❌ DISCORD_TOKEN missing in .env file")

# --------------------
# Bot setup
# --------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Track spam
message_history = defaultdict(lambda: defaultdict(lambda: defaultdict(deque)))
slow_channels = {}

# --------------------
# Helpers
# --------------------
def is_mod(member: discord.Member) -> bool:
    """Check if member is a moderator."""
    if member.guild_permissions.kick_members or member.guild_permissions.ban_members:
        return True
    return any(role.name == MOD_ROLE_NAME for role in member.roles)

async def reset_slowmode(channel: discord.TextChannel, original: int, delay: int):
    """Restore channel slowmode after delay."""
    await asyncio.sleep(delay)
    try:
        await channel.edit(slowmode_delay=original, reason="Auto slowmode reset")
    except Exception:
        pass
    slow_channels.pop(channel.id, None)

# --------------------
# Events
# --------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")

@bot.event
async def on_message(message: discord.Message):
    if not message.guild or message.author.bot:
        return

    now = datetime.utcnow()
    history = message_history[message.guild.id][message.channel.id][message.author.id]
    history.append(now)

    # Keep only recent messages
    cutoff = now - timedelta(seconds=SLOW_WINDOW)
    while history and history[0] < cutoff:
        history.popleft()

    # If spam detected
    if len(history) >= SLOW_THRESHOLD and message.channel.id not in slow_channels:
        original = message.channel.slowmode_delay
        try:
            await message.channel.edit(
                slowmode_delay=SLOW_MODE_DURATION,
                reason="Auto slowdown (spam detected)"
            )
            msg = await message.channel.send(
                f"⚠️ Slowmode enabled ({SLOW_MODE_DURATION}s) due to spam. "
                f"Resets in {SLOW_RESET_SECONDS}s."
            )
            await asyncio.sleep(8)
            await msg.delete()
        except discord.Forbidden:
            pass

        # Schedule reset
        slow_channels[message.channel.id] = True
        bot.loop.create_task(reset_slowmode(message.channel, original, SLOW_RESET_SECONDS))

    await bot.process_commands(message)

# --------------------
# Kick & Ban commands
# --------------------
@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """Kick a member from the server."""
    try:
        await member.kick(reason=reason)
        await ctx.send(f"✅ {member.mention} was kicked. Reason: {reason}")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to kick this user.")
    except Exception as e:
        await ctx.send(f"❌ Failed to kick: {e}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """Ban a member from the server."""
    try:
        await member.ban(reason=reason, delete_message_days=1)
        await ctx.send(f"✅ {member.mention} was banned. Reason: {reason}")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to ban this user.")
    except Exception as e:
        await ctx.send(f"❌ Failed to ban: {e}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def unban(ctx, user: discord.User, *, reason: str = "No reason provided"):
    """Unban a previously banned user."""
    try:
        await ctx.guild.unban(user, reason=reason)
        await ctx.send(f"✅ {user.mention} was unbanned. Reason: {reason}")
    except discord.NotFound:
        await ctx.send("❌ This user is not banned.")
    except Exception as e:
        await ctx.send(f"❌ Failed to unban: {e}")

# --------------------
# Run
# --------------------
if __name__ == "__main__":
    bot.run(TOKEN)
