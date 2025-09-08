# Main.py
# Discord moderation bot with ticket system + automatic slowdown (anti-spam)
# Requirements: discord.py (v2.x), python-dotenv
# Fill in your bot token and adjust settings in the .env file.

import os
import asyncio
import logging
from collections import deque, defaultdict
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# --- Load configuration ---
load_dotenv()  # Loads variables from .env into environment

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN or TOKEN == "YOUR_DISCORD_TOKEN_HERE":
    raise RuntimeError("Please set DISCORD_TOKEN in your .env file.")

PREFIX = os.getenv("BOT_PREFIX", "!")
MOD_ROLE_NAME = os.getenv("MOD_ROLE_NAME", "Moderator")
TICKET_CATEGORY_NAME = os.getenv("TICKET_CATEGORY_NAME", "Tickets")

# Anti-spam / slowdown settings (integers, can be changed in .env)
SLOW_THRESHOLD = int(os.getenv("SLOW_THRESHOLD", "5"))          # messages
SLOW_WINDOW = int(os.getenv("SLOW_WINDOW", "7"))                # seconds window to count messages
SLOW_MODE_DURATION = int(os.getenv("SLOW_MODE_DURATION", "10")) # slowmode seconds to set
SLOW_RESET_SECONDS = int(os.getenv("SLOW_RESET_SECONDS", "60")) # how long slowmode stays set before restoring

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('bot')

# Intents (we need message content for anti-spam)
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=commands.DefaultHelpCommand(no_category="Commands"))

# In-memory counters and state
# user_message_times[guild_id][channel_id][user_id] = deque of timestamps
user_message_times = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: deque())))
# channel_slowstate[channel_id] = { "original": <int slowmode>, "reset_task": <asyncio.Task> }
channel_slowstate = {}

# --------------------
# Helper utilities
# --------------------
def is_mod(member: discord.Member):
    """Return True if member has the mod role or guild permissions manage_messages."""
    if member.guild_permissions.manage_messages or member.guild_permissions.manage_channels or member.guild_permissions.kick_members:
        return True
    for role in member.roles:
        if role.name == MOD_ROLE_NAME:
            return True
    return False

async def ensure_ticket_category(guild: discord.Guild):
    """Ensure a 'Tickets' category exists and return it."""
    for cat in guild.categories:
        if cat.name == TICKET_CATEGORY_NAME:
            return cat
    # not found -> create (requires manage_channels)
    try:
        cat = await guild.create_category(TICKET_CATEGORY_NAME, reason="Ticket system initialization")
        logger.info(f"Created ticket category in guild {guild.name}")
        return cat
    except discord.Forbidden:
        logger.warning("Lacking permissions to create ticket category. Tickets might fail.")
        return None

async def schedule_slowmode_reset(channel: discord.TextChannel, original_slowmode: int, delay_seconds: int):
    """Wait delay_seconds then restore channel slowmode to original_slowmode."""
    async def _reset():
        try:
            await asyncio.sleep(delay_seconds)
            # If channel still exists, restore
            ch = channel
            try:
                await ch.edit(slowmode_delay=original_slowmode, reason="Automatic slowdown reset")
                logger.info(f"Restored slowmode for #{ch.name} to {original_slowmode}")
            except Exception as e:
                logger.warning(f"Could not restore slowmode for channel {channel.id}: {e}")
        finally:
            # cleanup tracking entry
            channel_slowstate.pop(channel.id, None)

    task = asyncio.create_task(_reset())
    channel_slowstate[channel.id] = {"original": original_slowmode, "reset_task": task}

# --------------------
# Events
# --------------------
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info("Bot is ready.")
    # start any persistent tasks if needed

@bot.event
async def on_message(message: discord.Message):
    # Ignore bot messages
    if message.author.bot or not message.guild:
        return

    guild_id = message.guild.id
    channel_id = message.channel.id
    user_id = message.author.id

    # Record message timestamp for anti-spam
    dq = user_message_times[guild_id][channel_id][user_id]
    now = datetime.utcnow()
    dq.append(now)

    # Remove timestamps older than SLOW_WINDOW
    cutoff = now - timedelta(seconds=SLOW_WINDOW)
    while dq and dq[0] < cutoff:
        dq.popleft()

    # Check threshold
    if len(dq) >= SLOW_THRESHOLD:
        # If channel already in auto-slow state, do nothing
        if channel_id not in channel_slowstate:
            # Try to set slowmode on the channel
            channel = message.channel
            try:
                original = channel.slowmode_delay or 0
                await channel.edit(slowmode_delay=SLOW_MODE_DURATION, reason="Automatic slowdown due to rapid messaging")
                logger.info(f"Set slowmode {SLOW_MODE_DURATION}s in #{channel.name} (guild: {message.guild.name}) due to spam.")
                # schedule restore after SLOW_RESET_SECONDS
                await schedule_slowmode_reset(channel, original, SLOW_RESET_SECONDS)

                # inform the channel briefly (ephemeral-ish) - send a message and delete after a few secs
                info = await channel.send(f":rotating_light: Slowmode enabled for {SLOW_MODE_DURATION}s due to rapid messages. It will auto-reset in {SLOW_RESET_SECONDS}s.")
                await asyncio.sleep(8)
                try:
                    await info.delete()
                except Exception:
                    pass

                # clear the records for that channel to avoid repeated triggers
                user_message_times[guild_id].pop(channel_id, None)
            except discord.Forbidden:
                logger.warning("Missing permission to edit channel slowmode. Please give Manage Channels permission.")
            except Exception as e:
                logger.exception(f"Failed to apply automatic slowmode: {e}")

    # Allow commands to process
    await bot.process_commands(message)

# --------------------
# Ticket commands (open/close)
# --------------------
@bot.group(invoke_without_command=True)
async def ticket(ctx: commands.Context, *args):
    """Ticket command group. Use !ticket open <reason> or !ticket close"""
    await ctx.send_help(ctx.command)

@ticket.command(name="open")
async def ticket_open(ctx: commands.Context, *, reason: str = "No reason provided"):
    """Open a private ticket channel in the server."""
    guild = ctx.guild
    author = ctx.author

    # Ensure category exists
    category = await ensure_ticket_category(guild)
    if category is None:
        await ctx.send("Ticket category not found and I can't create one (missing permissions). Please ask an admin to create a category named: " + TICKET_CATEGORY_NAME)
        return

    # Channel name sanitization
    chan_name = f"ticket-{author.name}".lower().replace(" ", "-")
    # avoid name collision by appending short id if exists
    existing = discord.utils.get(category.channels, name=chan_name)
    if existing:
        chan_name = f"{chan_name}-{author.discriminator}"

    # Permissions: only author, mods, and bot can see
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=False),
        author: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True)
    }

    # Allow mod role to view if present
    mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
    if mod_role:
        overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True, manage_channels=False)

    # Create channel
    try:
        channel = await guild.create_text_channel(name=chan_name, overwrites=overwrites, category=category, reason=f"Ticket opened by {author}")
    except discord.Forbidden:
        await ctx.send("I don't have permission to create channels. Please give me Manage Channels permission.")
        return
    except Exception as e:
        await ctx.send("Failed to create ticket channel: " + str(e))
        return

    # Send initial message with instructions
    embed = discord.Embed(title="Ticket Created", color=discord.Color.blurple(), timestamp=datetime.utcnow())
    embed.add_field(name="User", value=f"{author.mention} ({author})", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text="Staff: Use !ticket close to close this ticket.")
    await channel.send(content=f"{author.mention} Thank you — a staff member will be with you shortly.", embed=embed)
    await ctx.reply(f"Your ticket has been created: {channel.mention}", mention_author=False)

@ticket.command(name="close")
async def ticket_close(ctx: commands.Context):
    """Close the ticket. Can be used by the ticket author or moderators."""
    channel = ctx.channel
    guild = ctx.guild

    # Only allow in ticket category channels
    if not channel.category or channel.category.name != TICKET_CATEGORY_NAME:
        await ctx.send("This command can only be used inside a ticket channel.")
        return

    # Check permission: author or mod
    # If author, allow. If mod (has mod role / manage_messages), allow.
    # Otherwise deny.
    # Attempt to find ticket owner by looking for first message embed user field (best-effort)
    can_close = False
    if is_mod(ctx.author):
        can_close = True
    else:
        # try to check if invoking user is the ticket owner by checking channel name
        # channel name expected like ticket-username or ticket-username-1234
        if ctx.author.name.lower() in channel.name:
            can_close = True

    if not can_close:
        await ctx.send("Only the ticket opener or staff can close this ticket.")
        return

    # Archive/close: delete channel after a short delay so logs can be read, or optionally lock
    try:
        await channel.send("This ticket will be closed in 5 seconds...")
        await asyncio.sleep(5)
        await channel.delete(reason=f"Ticket closed by {ctx.author}")
    except discord.Forbidden:
        await ctx.send("I don't have permission to delete this channel. Ask an admin to remove it.")
    except Exception as e:
        await ctx.send("Failed to close ticket: " + str(e))

# --------------------
# Admin / utility commands
# --------------------
@bot.command(name="slowstatus")
async def slowstatus(ctx: commands.Context):
    """Check if the current channel is in automatic slow state (admin/mod only)."""
    if not is_mod(ctx.author):
        await ctx.send("You must be a moderator to use this command.")
        return
    ch = ctx.channel
    state = channel_slowstate.get(ch.id)
    if state:
        original = state.get("original", 0)
        await ctx.send(f"Channel is currently in auto-slow state. Original slowmode: {original}s.")
    else:
        await ctx.send("Channel is not currently in auto-slow state.")

@bot.command(name="force_slow")
@commands.has_permissions(manage_channels=True)
async def force_slow(ctx: commands.Context, slow_seconds: int = SLOW_MODE_DURATION, duration_seconds: int = SLOW_RESET_SECONDS):
    """Force-enable slowmode in the current channel for a given period (admin-only)."""
    ch = ctx.channel
    original = ch.slowmode_delay or 0
    try:
        await ch.edit(slowmode_delay=slow_seconds, reason=f"Forced slow by {ctx.author}")
        # cancel existing reset if present
        if ch.id in channel_slowstate:
            # cancel previous task
            try:
                prev_task = channel_slowstate[ch.id].get("reset_task")
                if prev_task and not prev_task.done():
                    prev_task.cancel()
            except Exception:
                pass
        await schedule_slowmode_reset(ch, original, duration_seconds)
        await ctx.send(f"Forced slowmode {slow_seconds}s for {duration_seconds}s (original: {original}s).")
    except discord.Forbidden:
        await ctx.send("Missing permission to edit channel slowmode.")
    except Exception as e:
        await ctx.send("Failed to set slowmode: " + str(e))

# --------------------
# Error handlers
# --------------------
@force_slow.error
async def force_slow_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need Manage Channels permission to use this command.")
    else:
        await ctx.send(f"Error: {error}")

# --------------------
# Run
# --------------------
if __name__ == "__main__":
    bot.run(TOKEN)
