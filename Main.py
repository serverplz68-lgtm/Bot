# Main.py
# Discord moderation bot with:
# - Ticket system (open/close)
# - Automatic slowdown (anti-spam)
# - Admin slowmode tools
#
# Requirements: discord.py, python-dotenv

import os
import asyncio
from collections import deque, defaultdict
from datetime import datetime, timedelta

import discord
from discord.ext import commands
from dotenv import load_dotenv

# --------------------
# Load .env config
# --------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("BOT_PREFIX", "!")
MOD_ROLE_NAME = os.getenv("MOD_ROLE_NAME", "Moderator")
TICKET_CATEGORY_NAME = os.getenv("TICKET_CATEGORY_NAME", "Tickets")

SLOW_THRESHOLD = int(os.getenv("SLOW_THRESHOLD", "5"))          # messages
SLOW_WINDOW = int(os.getenv("SLOW_WINDOW", "7"))                # seconds
SLOW_MODE_DURATION = int(os.getenv("SLOW_MODE_DURATION", "10")) # seconds
SLOW_RESET_SECONDS = int(os.getenv("SLOW_RESET_SECONDS", "60")) # seconds

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN missing in .env file")

# --------------------
# Bot setup
# --------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Track messages for spam
message_history = defaultdict(lambda: defaultdict(lambda: defaultdict(deque)))
# Track auto-slow state
slow_channels = {}

# --------------------
# Helpers
# --------------------
def is_mod(member: discord.Member) -> bool:
    """Check if a member is a moderator."""
    if member.guild_permissions.manage_messages or member.guild_permissions.kick_members:
        return True
    return any(role.name == MOD_ROLE_NAME for role in member.roles)

async def ensure_ticket_category(guild: discord.Guild) -> discord.CategoryChannel | None:
    """Ensure a category for tickets exists."""
    category = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
    if not category:
        try:
            category = await guild.create_category(TICKET_CATEGORY_NAME, reason="Ticket system")
        except discord.Forbidden:
            return None
    return category

async def reset_slowmode(channel: discord.TextChannel, original: int, delay: int):
    """Restore original slowmode after delay."""
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

    # Keep only messages in the last SLOW_WINDOW seconds
    cutoff = now - timedelta(seconds=SLOW_WINDOW)
    while history and history[0] < cutoff:
        history.popleft()

    if len(history) >= SLOW_THRESHOLD and message.channel.id not in slow_channels:
        original = message.channel.slowmode_delay
        try:
            await message.channel.edit(
                slowmode_delay=SLOW_MODE_DURATION,
                reason="Auto slowdown due to spam"
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
# Ticket system
# --------------------
@bot.group(invoke_without_command=True)
async def ticket(ctx: commands.Context):
    """Ticket system commands"""
    await ctx.send("Use `!ticket open <reason>` or `!ticket close`")

@ticket.command()
async def open(ctx: commands.Context, *, reason: str = "No reason provided"):
    category = await ensure_ticket_category(ctx.guild)
    if not category:
        return await ctx.send("❌ Cannot create ticket category. Missing permissions.")

    name = f"ticket-{ctx.author.name}".lower().replace(" ", "-")
    if discord.utils.get(category.channels, name=name):
        name = f"{name}-{ctx.author.discriminator}"

    overwrites = {
        ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }

    mod_role = discord.utils.get(ctx.guild.roles, name=MOD_ROLE_NAME)
    if mod_role:
        overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    channel = await ctx.guild.create_text_channel(
        name=name,
        category=category,
        overwrites=overwrites,
        topic=f"Ticket owner: {ctx.author.id}"
    )

    embed = discord.Embed(
        title="🎫 Ticket Opened",
        description=f"Reason: {reason}",
        color=discord.Color.blurple(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="User", value=ctx.author.mention)
    embed.set_footer(text="Staff: use !ticket close to close this ticket.")
    await channel.send(embed=embed)
    await ctx.send(f"✅ Ticket created: {channel.mention}")

@ticket.command()
async def close(ctx: commands.Context):
    if not ctx.channel.category or ctx.channel.category.name != TICKET_CATEGORY_NAME:
        return await ctx.send("❌ This command can only be used inside a ticket channel.")

    # Extract ticket owner ID from topic
    owner_id = None
    if ctx.channel.topic and ctx.channel.topic.startswith("Ticket owner: "):
        try:
            owner_id = int(ctx.channel.topic.replace("Ticket owner: ", ""))
        except ValueError:
            pass

    if ctx.author.id != owner_id and not is_mod(ctx.author):
        return await ctx.send("❌ Only the ticket owner or staff can close this ticket.")

    await ctx.send("⏳ Closing ticket in 5s...")
    await asyncio.sleep(5)
    try:
        await ctx.channel.delete(reason=f"Ticket closed by {ctx.author}")
    except discord.Forbidden:
        await ctx.send("❌ Missing permissions to delete this channel.")

# --------------------
# Slowmode commands
# --------------------
@bot.command()
async def slowstatus(ctx: commands.Context):
    """Check if current channel is slowed automatically"""
    if ctx.channel.id in slow_channels:
        await ctx.send("⚠️ This channel is under automatic slowdown.")
    else:
        await ctx.send("✅ This channel is not slowed.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def force_slow(ctx: commands.Context, seconds: int = SLOW_MODE_DURATION, duration: int = SLOW_RESET_SECONDS):
    """Force slowmode in current channel"""
    original = ctx.channel.slowmode_delay
    await ctx.channel.edit(slowmode_delay=seconds, reason=f"Forced by {ctx.author}")
    await ctx.send(f"⏳ Forced slowmode {seconds}s for {duration}s.")
    bot.loop.create_task(reset_slowmode(ctx.channel, original, duration))

# --------------------
# Run
# --------------------
if __name__ == "__main__":
    bot.run(TOKEN)
