import discord
import sqlite3
import pytz
from datetime import datetime, timedelta, time
from discord.ext import commands, tasks
import os
import re
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()  # take environment variables from .env.

# Bot startup
intents = discord.Intents.all()
intents.messages = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Configurations
STREAK_TIME = time(14, 39)  # in PDT

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
                        user_id TEXT NOT NULL,
                        server_id TEXT NOT NULL,
                        current_streak INTEGER DEFAULT 0,
                        highest_streak INTEGER DEFAULT 0,
                        last_submission_date TIMESTAMP,
                        PRIMARY KEY (user_id, server_id)
                    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS reminders (
                        user_id TEXT NOT NULL,
                        server_id TEXT NOT NULL,
                        reminder_time TIME,
                        PRIMARY KEY (user_id, server_id)
                    )''')

# Database functions
def add_journal_entry(user_id, server_id, channel_id, message):
    with conn:
        conn.execute('INSERT INTO journals (user_id, server_id, channel_id, message) VALUES (?, ?, ?, ?)', 
                     (user_id, server_id, channel_id, message))

def update_streak(user_id, server_id):
    pacific_time = pytz.timezone('America/Los_Angeles')
    now_utc = datetime.now(pytz.utc)  # Current time in UTC
    start_of_today_pacific = datetime.combine(now_utc.astimezone(pacific_time).date(), STREAK_TIME).astimezone(pytz.utc)

    with conn:
        cur = conn.execute('SELECT last_submission_date, current_streak, highest_streak FROM streaks WHERE user_id = ? AND server_id = ?', (user_id, server_id))
        row = cur.fetchone()

        if row is None:
            # Initialize streak data for new user
            conn.execute('INSERT INTO streaks (user_id, server_id, current_streak, highest_streak, last_submission_date) VALUES (?, ?, ?, ?, ?)', 
                         (user_id, server_id, 1, 1, now_utc))
            return 1

        last_submission_date, current_streak, highest_streak = row

        # Use last_submission_date as is, assuming it's already a datetime object
        if not last_submission_date:
            last_submission_date = datetime.min.replace(tzinfo=pytz.utc)

        # Update streak
        if last_submission_date < start_of_today_pacific:
            current_streak = current_streak + 1 if last_submission_date >= start_of_today_pacific - timedelta(days=1) else 1
        highest_streak = max(highest_streak, current_streak)

        # Update the streaks table
        conn.execute('UPDATE streaks SET current_streak = ?, highest_streak = ?, last_submission_date = ? WHERE user_id = ? AND server_id = ?', 
                     (current_streak, highest_streak, now_utc, user_id, server_id))

    return current_streak



@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')
    check_reminders.start()
    print("Started reminder async")
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
        "!submit [message]: Submit a daily journal.\n"
        "!journals [number]: View your last [number] journals.\n"
        "!removelatest: Remove your latest journal.\n"
        "!streak: View your current and highest streak achieved.\n"
        "!remindme [time]: Set a daily reminder time (e.g., '8:30PM').\n"
        "!dontremindme: Remove your daily reminder.\n"
        "!export: Export all your journals as a JSON file.\n"
    )
    await ctx.send(help_text)

@bot.command(name='journals')
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
        embed = discord.Embed(color=0x3498db)
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else discord.Embed.Empty)
        embed.set_footer(text=f"Showing your last {number} journals")

        for entry in entries:
            utc_time = pytz.utc.localize(entry[1]) if entry[1] else None  # Ensure the datetime is UTC
            la_submission_time = utc_time.astimezone(pacific_time) if utc_time else None
            if la_submission_time:
                embed.add_field(name=la_submission_time.strftime("%A, %B %d %Y at %I:%M%p"), value=entry[0], inline=False)

        await ctx.send(embed=embed)
    else:
        await ctx.send("You have no journals.")


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
        # First, select the ID of the latest journal
        cur = conn.execute('SELECT id FROM journals WHERE user_id = ? AND server_id = ? ORDER BY submission_time DESC LIMIT 1', (user_id, server_id))
        row = cur.fetchone()

        # If an entry exists, delete it
        if row:
            conn.execute('DELETE FROM journals WHERE id = ?', (row[0],))
            await ctx.send("Your latest journal has been removed.")
        else:
            await ctx.send("No journals to remove.")


@bot.command(name='submit')
async def submit(ctx, *, arg=None):
    if arg is None or arg.strip() == "":
        await ctx.send("Please provide a journal to submit.")
        return

    user_id = str(ctx.author.id)
    server_id = str(ctx.guild.id)
    channel_id = str(ctx.channel.id)
    message = arg

    add_journal_entry(user_id, server_id, channel_id, message)
    new_streak = update_streak(user_id, server_id)
    embed = discord.Embed(description=message, color=0x3498db)
    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else discord.Embed.Empty)

    response = f"Thank you {ctx.author.display_name} for submitting your journal. Your current streak is {new_streak}."

    await ctx.send(response, embed=embed)

@bot.command(name='remindme')
async def remindme(ctx, time_str: str):
    # Regular expression to parse the time input
    match = re.match(r'(\d{1,2}):(\d{2})([APM]{2})', time_str.upper())
    if not match:
        await ctx.send("Invalid time format. Please use a format like '8:30PM' or '10:00AM'.")
        return

    hour, minute, meridiem = match.groups()
    hour, minute = int(hour), int(minute)

    # Convert 12-hour time to 24-hour time
    if meridiem == 'PM' and hour != 12:
        hour += 12
    elif meridiem == 'AM' and hour == 12:
        hour = 0

    # Create a datetime object in PDT timezone
    pacific_time = pytz.timezone('America/Los_Angeles')
    now = datetime.now(pacific_time)
    reminder_time_pdt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Convert reminder time to UTC
    reminder_time_utc = reminder_time_pdt.astimezone(pytz.utc)

    # Store reminder time in the database in UTC format
    user_id = str(ctx.author.id)
    server_id = str(ctx.guild.id)
    with conn:
        conn.execute('REPLACE INTO reminders (user_id, server_id, reminder_time) VALUES (?, ?, ?)', 
                     (user_id, server_id, reminder_time_utc.strftime('%H:%M:%S')))

    await ctx.send(f"You will be reminded to submit your journal daily at {time_str} PDT. To remove this reminder, enter the !dontremindme command.")

@bot.command(name='dontremindme')
async def dontremindme(ctx):
    user_id = str(ctx.author.id)
    server_id = str(ctx.guild.id)

    with conn:
        conn.execute('DELETE FROM reminders WHERE user_id = ? AND server_id = ?', (user_id, server_id))

    await ctx.send("Your daily reminder has been removed.")

@bot.command(name='export')
async def export(ctx):
    user_id = str(ctx.author.id)
    server_id = str(ctx.guild.id)

    with conn:
        entries = conn.execute('SELECT message, submission_time FROM journals WHERE user_id = ? AND server_id = ?', (user_id, server_id)).fetchall()

    if entries:
        journal_data = [{'message': entry[0], 'submission_time': entry[1].strftime("%Y-%m-%d %H:%M:%S")} for entry in entries]
        file_name = f'{ctx.author.id}_journals.json'
        with open(file_name, 'w') as file:
            json.dump(journal_data, file)

        await ctx.send(file=discord.File(file_name))
        os.remove(file_name)
    else:
        await ctx.send("You have no journals to export.")

@tasks.loop(minutes=1)
async def check_reminders():
    print("Checking reminders...")
    now_pacific = datetime.now(pytz.timezone('America/Los_Angeles'))
    now_utc = now_pacific.astimezone(pytz.utc)
    current_utc_time = now_utc.strftime("%H:%M:00")

    start_of_today_pacific = datetime.combine(now_pacific.date(), STREAK_TIME)
    if now_pacific.time() < STREAK_TIME:
        start_of_today_pacific -= timedelta(days=1)  # Go back one day if current time is before 6 AM

    with conn:
        # Fetch all reminders that match the current UTC time
        reminders = conn.execute('SELECT user_id, server_id FROM reminders WHERE reminder_time = ?', (current_utc_time,)).fetchall()

    print("Reminders at " + str(now_utc) + ": " + str(reminders))

    for reminder in reminders:
        user_id, server_id = reminder
        server = bot.get_guild(int(server_id))
        member = server.get_member(int(user_id)) if server else None

        if member:
            do_remind = False
            # Check if the user has already submitted a journal today in Los Angeles time
            with conn:
                last_entry = conn.execute('SELECT MAX(submission_time) FROM journals WHERE user_id = ? AND server_id = ?', (user_id, server_id)).fetchone()[0]
                if last_entry:
                    # Convert last_entry to a datetime object
                    last_entry_datetime = datetime.strptime(last_entry, "%Y-%m-%d %H:%M:%S")
                    last_entry_datetime = last_entry_datetime.replace(tzinfo=pytz.utc)

                    # Convert last_entry_datetime to Pacific Time
                    last_entry_datetime_pacific = last_entry_datetime.astimezone(pytz.timezone('America/Los_Angeles'))

                    if last_entry_datetime_pacific < start_of_today_pacific:
                        do_remind = True
                else:
                    do_remind = True

            print("do_remind with " + str(reminder) + " is " + str(do_remind))
            if do_remind:
                reminder_channel_id = 902831374506020874 # 1182855047143493772
                channel = server.get_channel(reminder_channel_id)
                if channel:
                    await channel.send(f"Hey {member.mention}, don't forget to submit your journal today!")
                    print("Sent reminder!")
                else:
                    print("Did not send reminder because channel does not exist")
            print("")


@check_reminders.before_loop
async def before_check_reminders():
    print("Waiting for bot to be ready to start reminder checks...")
    await bot.wait_until_ready()
    print("Starting reminder checks...")

# Bot token and run
bot_token = os.getenv('DISCORD_BOT_TOKEN')
bot.run(bot_token)