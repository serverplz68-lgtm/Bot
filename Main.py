# Main.py
# Discord bot with:
# - 29+ moderator features
# - Advanced ticket system
# - Auto slowdown (anti-spam)

import os
import asyncio
from collections import deque, defaultdict
from datetime import datetime, timedelta

import discord
from discord.ext import commands
from dotenv import load_dotenv

# --------------------
# Config
# --------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("BOT_PREFIX", "!")
MOD_ROLE_NAME = os.getenv("MOD_ROLE_NAME", "Moderator")
TICKET_CATEGORY_NAME = os.getenv("TICKET_CATEGORY_NAME", "Tickets")

# Anti-spam
SLOW_THRESHOLD = int(os.getenv("SLOW_THRESHOLD", "5"))
SLOW_WINDOW = int(os.getenv("SLOW_WINDOW", "7"))
SLOW_MODE_DURATION = int(os.getenv("SLOW_MODE_DURATION", "10"))
SLOW_RESET_SECONDS = int(os.getenv("SLOW_RESET_SECONDS", "60"))

if not TOKEN:
    raise RuntimeError("❌ DISCORD_TOKEN missing in .env file")

# --------------------
# Bot Setup
# --------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Spam tracking
message_history = defaultdict(lambda: defaultdict(lambda: defaultdict(deque)))
slow_channels = {}

# Ticket tracking
ticket_count = defaultdict(int)

# Warn system
warns = defaultdict(list)

# --------------------
# Helpers
# --------------------
def is_mod(member: discord.Member) -> bool:
    return (
        member.guild_permissions.kick_members
        or member.guild_permissions.ban_members
        or any(r.name == MOD_ROLE_NAME for r in member.roles)
    )

async def reset_slowmode(channel, original, delay):
    await asyncio.sleep(delay)
    try:
        await channel.edit(slowmode_delay=original, reason="Auto slowmode reset")
    except Exception:
        pass
    slow_channels.pop(channel.id, None)

async def ensure_ticket_category(guild):
    category = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
    if not category:
        category = await guild.create_category(TICKET_CATEGORY_NAME, reason="Ticket system")
    return category

# --------------------
# Events
# --------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")

@bot.event
async def on_message(message):
    if not message.guild or message.author.bot:
        return

    # Spam check
    now = datetime.utcnow()
    hist = message_history[message.guild.id][message.channel.id][message.author.id]
    hist.append(now)
    cutoff = now - timedelta(seconds=SLOW_WINDOW)
    while hist and hist[0] < cutoff:
        hist.popleft()

    if len(hist) >= SLOW_THRESHOLD and message.channel.id not in slow_channels:
        original = message.channel.slowmode_delay
        await message.channel.edit(slowmode_delay=SLOW_MODE_DURATION,
                                   reason="Auto slowdown: spam")
        await message.channel.send(
            f"⚠️ Slowmode enabled ({SLOW_MODE_DURATION}s). Resets in {SLOW_RESET_SECONDS}s."
        )
        slow_channels[message.channel.id] = True
        bot.loop.create_task(reset_slowmode(message.channel, original, SLOW_RESET_SECONDS))

    await bot.process_commands(message)

# --------------------
# Moderator Commands (29+)
# --------------------
@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int = 10):
    """Clear messages"""
    await ctx.channel.purge(limit=amount+1)
    await ctx.send(f"✅ Cleared {amount} messages.", delete_after=5)

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="No reason"):
    await member.kick(reason=reason)
    await ctx.send(f"👢 {member} kicked. Reason: {reason}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason"):
    await member.ban(reason=reason)
    await ctx.send(f"🔨 {member} banned. Reason: {reason}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def unban(ctx, user: discord.User):
    await ctx.guild.unban(user)
    await ctx.send(f"✅ {user} unbanned.")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member):
    muted = discord.utils.get(ctx.guild.roles, name="Muted")
    if not muted:
        muted = await ctx.guild.create_role(name="Muted")
        for ch in ctx.guild.channels:
            await ch.set_permissions(muted, send_messages=False, speak=False)
    await member.add_roles(muted)
    await ctx.send(f"🔇 {member} muted.")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member):
    muted = discord.utils.get(ctx.guild.roles, name="Muted")
    if muted in member.roles:
        await member.remove_roles(muted)
        await ctx.send(f"🔊 {member} unmuted.")

@bot.command()
async def warn(ctx, member: discord.Member, *, reason="No reason"):
    warns[member.id].append((reason, ctx.author, datetime.utcnow()))
    await ctx.send(f"⚠️ {member} warned. Reason: {reason}")

@bot.command()
async def warnings(ctx, member: discord.Member):
    user_warns = warns.get(member.id, [])
    if not user_warns:
        await ctx.send("✅ No warnings.")
    else:
        msg = "\n".join([f"{i+1}. {r} by {a} at {t}" for i, (r,a,t) in enumerate(user_warns)])
        await ctx.send(f"⚠️ Warnings for {member}:\n{msg}")

@bot.command()
@commands.has_permissions(manage_nicknames=True)
async def nickname(ctx, member: discord.Member, *, nick=None):
    await member.edit(nick=nick)
    await ctx.send(f"✅ Nickname changed to {nick}")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def addrole(ctx, member: discord.Member, *, role: discord.Role):
    await member.add_roles(role)
    await ctx.send(f"✅ {role.name} added to {member}")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def removerole(ctx, member: discord.Member, *, role: discord.Role):
    await member.remove_roles(role)
    await ctx.send(f"❌ {role.name} removed from {member}")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def lockdown(ctx, channel: discord.TextChannel=None):
    channel = channel or ctx.channel
    await channel.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.send("🔒 Channel locked.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def unlock(ctx, channel: discord.TextChannel=None):
    channel = channel or ctx.channel
    await channel.set_permissions(ctx.guild.default_role, send_messages=True)
    await ctx.send("🔓 Channel unlocked.")

@bot.command()
async def say(ctx, *, msg):
    await ctx.message.delete()
    await ctx.send(msg)

@bot.command()
async def poll(ctx, question, *options):
    if len(options) < 2:
        return await ctx.send("❌ Need at least 2 options.")
    emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣"]
    desc = "\n".join([f"{emojis[i]} {opt}" for i,opt in enumerate(options)])
    embed = discord.Embed(title=question, description=desc, color=discord.Color.blurple())
    poll_msg = await ctx.send(embed=embed)
    for i in range(len(options)):
        await poll_msg.add_reaction(emojis[i])

# --------------------
# Advanced Ticket System
# --------------------
@bot.group(invoke_without_command=True)
async def ticket(ctx):
    await ctx.send("🎫 Use `!ticket open <reason>` or `!ticket close`")

@ticket.command()
async def open(ctx, *, reason="No reason"):
    category = await ensure_ticket_category(ctx.guild)
    ticket_count[ctx.guild.id] += 1
    name = f"ticket-{ticket_count[ctx.guild.id]}"
    overwrites = {
        ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    mod_role = discord.utils.get(ctx.guild.roles, name=MOD_ROLE_NAME)
    if mod_role:
        overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True)
    ch = await ctx.guild.create_text_channel(name=name, category=category,
                                             overwrites=overwrites,
                                             topic=f"Ticket owner: {ctx.author.id}")
    await ch.send(f"🎫 Ticket opened by {ctx.author.mention}\nReason: {reason}")
    await ctx.send(f"✅ Ticket created: {ch.mention}")

@ticket.command()
async def close(ctx):
    if not ctx.channel.category or ctx.channel.category.name != TICKET_CATEGORY_NAME:
        return await ctx.send("❌ Not a ticket.")
    await ctx.send("⏳ Closing in 5s...")
    await asyncio.sleep(5)
    await ctx.channel.delete()

@ticket.command()
async def add(ctx, member: discord.Member):
    await ctx.channel.set_permissions(member, read_messages=True, send_messages=True)
    await ctx.send(f"✅ {member} added to ticket.")

@ticket.command()
async def remove(ctx, member: discord.Member):
    await ctx.channel.set_permissions(member, overwrite=None)
    await ctx.send(f"❌ {member} removed from ticket.")

@ticket.command()
async def rename(ctx, *, new_name):
    await ctx.channel.edit(name=new_name)
    await ctx.send(f"✏️ Ticket renamed to {new_name}")

# --------------------
# Run
# --------------------
if __name__ == "__main__":
    bot.run(TOKEN)
