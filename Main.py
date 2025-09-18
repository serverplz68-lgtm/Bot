import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Set prefix
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# Preloaded responses
responses = {
    "hello": "Hi there 👋",
    "bye": "Goodbye 👋",
    "ping": "Pong 🏓",
    "joke": "Why don’t skeletons fight each other? Because they don’t have the guts 😂",
    # 👉 Add up to 50+ responses here
}

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    content = message.content.lower().lstrip("!")

    if content in responses:
        await message.channel.send(responses[content])
    elif message.content.startswith("!"):
        await message.channel.send("❌ I didn’t understand that command.")

    await bot.process_commands(message)

# Run bot using token from .env
bot.run(TOKEN)
