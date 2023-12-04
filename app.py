import discord
import sqlite3
import pytz
from datetime import datetime, timedelta, time
from discord.ext import commands, tasks
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()  # take environment variables from .env.

# Bot setup
intents = discord.Intents.all()
intents.messages = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Database connection
conn = sqlite3.connect('journals.db', detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)

# Ensure tables are created
with conn:
    conn.execute('''CREATE TABLE IF NOT EXISTS journals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        server_id TEXT NOT NULL,
                        channel_id TEXT NOT NULL,
                        message TEXT NOT NULL,
                        submission_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS streaks (
                        user_id TEXT PRIMARY KEY,
                        server_id TEXT NOT NULL,
                        current_streak INTEGER DEFAULT 0,
                        highest_streak INTEGER DEFAULT 0,
                        last_submission_date TIMESTAMP
                    )''')
    
def read_reminder_time():
    try:
        with open('config.txt', 'r') as file:
            time_str = file.read().strip()
            hour, minute = map(int, time_str.split(':'))
            return hour, minute
    except FileNotFoundError:
        return 20, 0  # Default time if file not found

async def get_users_without_submission(server_id, date):
    users_with_submission = {row[0] for row in conn.execute('SELECT DISTINCT user_id FROM journals WHERE server_id = ? AND DATE(submission_time) = ?', (server_id, date))}
    all_members = {member.id for member in bot.get_guild(server_id).members if not member.bot}
    return all_members - users_with_submission

# Database functions
def add_journal_entry(user_id, server_id, channel_id, message):
    with conn:
        conn.execute('INSERT INTO journals (user_id, server_id, channel_id, message) VALUES (?, ?, ?, ?)', 
                     (user_id, server_id, channel_id, message))

def update_streak(user_id, server_id):
    with conn:
        cur = conn.execute('SELECT last_submission_date, current_streak, highest_streak FROM streaks WHERE user_id = ? AND server_id = ?', (user_id, server_id))
        row = cur.fetchone()

        pacific_time = pytz.timezone('America/Los_Angeles')
        now = datetime.now(pacific_time).date()  # Get only the date part
        streak_updated = False  # Flag to indicate if the streak was updated

        if row and row[0] is not None:
            last_submission_date_str = str(row[0])
            last_submission_date = datetime.fromisoformat(last_submission_date_str).astimezone(pacific_time).date()
            current_streak, highest_streak = row[1], row[2]

            if last_submission_date == now - timedelta(days=1):
                # If the last submission was yesterday, increment the streak
                new_streak = current_streak + 1
            elif last_submission_date < now - timedelta(days=1):
                # If the last submission was before yesterday, reset the streak
                new_streak = 1
            else:
                # If the last submission was today or in the future, don't update the streak
                new_streak = current_streak

            # Update highest streak if new streak is higher
            if new_streak > highest_streak:
                highest_streak = new_streak

            # Update the database
            conn.execute('UPDATE streaks SET last_submission_date = ?, current_streak = ?, highest_streak = ? WHERE user_id = ? AND server_id = ?', (datetime.now(), new_streak, highest_streak, user_id, server_id))
            streak_updated = True
        else:
            # If there are no previous submissions, start the streak
            new_streak = 1
            highest_streak = 1
            conn.execute('INSERT INTO streaks (user_id, server_id, last_submission_date, current_streak, highest_streak) VALUES (?, ?, ?, ?, ?)', (user_id, server_id, datetime.now(), new_streak, highest_streak))
            streak_updated = True

        return streak_updated, new_streak

@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')
    print(f'Commands: {[command.name for command in bot.commands]}')

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandInvokeError):
        await ctx.send(f"An error occurred while executing the command: {error.original}")
    else:
        await ctx.send(f"An error occurred: {error}")


@bot.command(name='help')
async def help_command(ctx):
    help_text = (
        "!submit [message]: Submit a daily journal entry.\n"
        "!history [number]: View your last [number] journal entries.\n"
        "!removelatest: Remove your latest journal entry.\n"
        "!streak: View your current and highest streak achieved.\n"
        "!help: Shows this help message."
    )
    await ctx.send(help_text)

@bot.command(name='history')
async def history(ctx, number: str = None):
    if number is None or not number.isdigit():
        number = 10
    else:
        number = int(number)

    user_id = str(ctx.author.id)
    server_id = str(ctx.guild.id)
    pacific_time = pytz.timezone('America/Los_Angeles')

    with conn:
        entries = conn.execute('SELECT message, submission_time FROM journals WHERE user_id = ? AND server_id = ? ORDER BY submission_time DESC LIMIT ?', (user_id, server_id, number)).fetchall()

    if entries:
        for entry in entries:
            # Convert the submission time to Los Angeles timezone
            utc_submission_time = datetime.fromisoformat(entry[1])
            la_submission_time = utc_submission_time.astimezone(pacific_time)

            embed = discord.Embed(description=entry[0], color=0x3498db)
            embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else discord.Embed.Empty)
            embed.set_footer(text=la_submission_time.strftime("%A, %B %d %Y at %I:%M%p"))
            await ctx.send(embed=embed)
    else:
        await ctx.send("You have no journal entries.")


@bot.command(name='streak')
async def streak(ctx):
    user_id = str(ctx.author.id)
    server_id = str(ctx.guild.id)

    with conn:
        cur = conn.execute('SELECT current_streak, highest_streak FROM streaks WHERE user_id = ? AND server_id = ?', (user_id, server_id))
        row = cur.fetchone()

    if row:
        await ctx.send(f"Your current streak is {row[0]} and your highest streak is {row[1]}.")
    else:
        await ctx.send("You don't have a streak yet.")

@bot.command(name='removelatest')
async def removelatest(ctx):
    user_id = str(ctx.author.id)
    server_id = str(ctx.guild.id)

    with conn:
        # First, select the ID of the latest journal entry
        cur = conn.execute('SELECT id FROM journals WHERE user_id = ? AND server_id = ? ORDER BY submission_time DESC LIMIT 1', (user_id, server_id))
        row = cur.fetchone()

        # If an entry exists, delete it
        if row:
            conn.execute('DELETE FROM journals WHERE id = ?', (row[0],))
            await ctx.send("Your latest journal entry has been removed.")
        else:
            await ctx.send("No journal entries to remove.")


@bot.command(name='submit')
async def submit(ctx, *, arg=None):
    if arg is None or arg.strip() == "":
        await ctx.send("Please provide a journal entry to submit.")
        return

    print("Sending daily journal")
    user_id = str(ctx.author.id)
    server_id = str(ctx.guild.id)
    channel_id = str(ctx.channel.id)
    message = arg

    # Save the journal entry
    add_journal_entry(user_id, server_id, channel_id, message)
    # Update and get the new streak
    streak_updated, new_streak = update_streak(user_id, server_id)

    if streak_updated:
        await ctx.send(f"Thank you {ctx.author.display_name} for sending your daily journal. Your daily journal streak is now {new_streak}.")
    else:
        await ctx.send(f"Thank you for sending your journal entry, {ctx.author.display_name}. You've already submitted one today, so your streak still stands at {new_streak}.")

@bot.command(name='setreminder')
@commands.has_permissions(administrator=True)  # Ensure only admins can set the reminder time
async def set_reminder(ctx, hour: int, minute: int):
    with open('config.txt', 'w') as file:
        file.write(f'{hour}:{minute}')
    await ctx.send(f'Reminder time set to {hour:02d}:{minute:02d} PDT.')

    # Restart the daily_check loop to update the time
    daily_check.restart()
        
@tasks.loop(hours=24)
async def daily_check():
    server_id = 816083336836939776  # Server to check
    channel_id = 902831374506020874  # Channel to send message
    channel = bot.get_channel(channel_id)

    if channel is None:
        print("Channel not found")
        return
    
    hour, minute = read_reminder_time()
    now = datetime.now(pytz.timezone('America/Los_Angeles'))
    reminder_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if now < reminder_time:
        # Wait until the reminder time
        await asyncio.sleep((reminder_time - now).total_seconds())

    date_to_check = (datetime.now(pytz.timezone('America/Los_Angeles')) - timedelta(hours=20)).date()
    users_to_remind = await get_users_without_submission(server_id, date_to_check)

    if users_to_remind:
        mentions = ' '.join([f'<@{user_id}>' for user_id in users_to_remind])
        reminder_message = f"{mentions}\nMake sure you submit your journal entry before the end of the day!"
        await channel.send(reminder_message)

@daily_check.before_loop
async def before_daily_check():
    await bot.wait_until_ready()
    hour, minute = read_reminder_time()
    now = datetime.now(pytz.timezone('America/Los_Angeles'))
    first_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if now >= first_run:
        first_run += timedelta(days=1)  # Schedule for the next day if time has passed

    await asyncio.sleep((first_run - now).total_seconds())

daily_check.start()

bot_token = os.getenv('DISCORD_BOT_TOKEN')
bot.run(bot_token)