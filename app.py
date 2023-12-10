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
            la_submission_time = entry[1].astimezone(pacific_time) if entry[1] else None
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
    streak_updated, new_streak = update_streak(user_id, server_id)
    embed = discord.Embed(description=message, color=0x3498db)
    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else discord.Embed.Empty)

    if streak_updated:
        response = f"Thank you {ctx.author.display_name} for submitting your journal. Your current streak is {new_streak}."
    else:
        response = f"Thank you for sending your journal, {ctx.author.display_name}. You've already submitted one today, so your streak still stands at {new_streak}."

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
                print("last_entry with " + str(reminder) + " is " + str(last_entry))
                if last_entry:
                    # Convert last_entry to a datetime object
                    last_entry_datetime = datetime.strptime(last_entry, "%Y-%m-%d %H:%M:%S")

                    # Make last_entry_datetime offset-aware by setting it to UTC timezone
                    last_entry_datetime = last_entry_datetime.replace(tzinfo=pytz.utc)

                    # Determine the start of the current day in Pacific Time and convert it to UTC
                    start_of_today_pacific = datetime.combine(now_pacific.date(), time(0, 0))
                    start_of_today_utc = start_of_today_pacific.astimezone(pytz.utc)

                    if last_entry_datetime < start_of_today_utc:
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