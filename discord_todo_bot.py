# discord_todo_bot.py

import os
import logging
import asyncio
import random
from dotenv import load_dotenv
import discord
from discord.ext import commands, tasks
import openai
from datetime import datetime, timezone, timedelta
from dateutil import parser
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from contextlib import contextmanager

# ===========================
# 1. Environment Setup
# ===========================

# Load environment variables from .env file
load_dotenv()

# Retrieve API keys and tokens
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN_1')
DATABASE_URL = os.getenv('DATABASE_URL')  # e.g., sqlite:///discord_todo_bot.db

# Verify that the tokens are loaded
if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN is not set in the .env file.")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is not set in the .env file.")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in the .env file.")

# ===========================
# 2. Logging Configuration
# ===========================

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s:%(levelname)s:%(name)s: %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('discord_todo_bot')

# ===========================
# 3. OpenAI Initialization
# ===========================

# Initialize OpenAI
openai.api_key = OPENAI_API_KEY

# ===========================
# 4. Discord Bot Setup
# ===========================

# Initialize Discord intents
intents = discord.Intents.default()
intents.message_content = True  # Enable if your bot reads message content

# Initialize the bot with command prefix '!' and disable default help command
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ===========================
# 5. Database Setup with SQLAlchemy
# ===========================

# Setup SQLAlchemy
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)  # Internal ID
    discord_id = Column(String, unique=True, nullable=False)  # Discord User ID
    points = Column(Integer, default=0)
    streak = Column(Integer, default=0)
    last_completed = Column(DateTime, nullable=True)
    badges = relationship('Badge', back_populates='user', cascade='all, delete-orphan')
    tasks = relationship('Task', back_populates='user', cascade='all, delete-orphan')

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    description = Column(String, nullable=False)
    due_date = Column(DateTime, nullable=True)
    completed = Column(Boolean, default=False)
    priority = Column(String, default="Medium")
    added_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    edited_at = Column(DateTime, nullable=True)
    notified = Column(Boolean, default=False)  # To prevent repeated reminders
    user_id = Column(Integer, ForeignKey('users.id'))
    user = relationship('User', back_populates='tasks')

class Badge(Base):
    __tablename__ = 'badges'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'))
    user = relationship('User', back_populates='badges')

# Create the database engine
engine = create_engine(DATABASE_URL, echo=False)

# Create all tables
Base.metadata.create_all(engine)

# Create a configured "Session" class
Session = sessionmaker(bind=engine)

# ===========================
# 6. Session Management
# ===========================

@contextmanager
def get_session():
    """Provide a transactional scope around a series of operations."""
    session = Session()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Session rollback because of exception: {e}")
        raise
    finally:
        session.close()

# ===========================
# 7. Background Tasks
# ===========================

# Task: Check for due tasks every minute
@tasks.loop(minutes=1.0)
async def check_due_tasks():
    now = datetime.now(timezone.utc)
    with get_session() as session:
        due_tasks = session.query(Task).filter(
            Task.completed == False,
            Task.due_date <= now,
            Task.notified == False
        ).all()
        for task in due_tasks:
            user_id = task.user_id
            try:
                # Fetch the user within the session to ensure the relationship is loaded
                user = session.query(User).filter_by(id=user_id).first()
                if user:
                    discord_user = bot.get_user(int(user.discord_id))
                    if discord_user:
                        reminder_message = (
                            f"🔔 **Reminder:** Your task \"{task.description}\" is due now!"
                        )
                        await discord_user.send(reminder_message)
                        task.notified = True  # Update the notified flag to prevent repeated reminders
                        logger.info(f"Sent reminder for task ID {task.id} to user ID {user_id}")
            except discord.Forbidden:
                logger.warning(f"Cannot send DM to user ID {user_id}. They might have DMs disabled.")

# Task: Send daily motivational quotes at 9 AM UTC
@tasks.loop(hours=24.0)
async def send_motivational_quote():
    await bot.wait_until_ready()
    now = datetime.now(timezone.utc)
    target_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if now > target_time:
        # Schedule for the next day
        target_time += timedelta(days=1)
    wait_seconds = (target_time - now).total_seconds()
    await asyncio.sleep(wait_seconds)

    motivational_quotes = [
        "🌟 *Believe you can and you're halfway there.* – Theodore Roosevelt",
        "🔥 *Don't watch the clock; do what it does. Keep going.* – Sam Levenson",
        "🚀 *The future depends on what you do today.* – Mahatma Gandhi",
        "💡 *The only way to do great work is to love what you do.* – Steve Jobs",
        "🌱 *Start where you are. Use what you have. Do what you can.* – Arthur Ashe"
    ]

    with get_session() as session:
        users = session.query(User).all()
        for user in users:
            user_id = user.id
            try:
                discord_user = bot.get_user(int(user.discord_id))
                if discord_user:
                    quote = random.choice(motivational_quotes)
                    await discord_user.send(f"💪 **Daily Motivation:** {quote}")
                    logger.info(f"Sent motivational quote to user ID {user_id}")
            except discord.Forbidden:
                logger.warning(f"Cannot send DM to user ID {user_id}. They might have DMs disabled.")

# ===========================
# 8. Helper Functions
# ===========================

def get_or_create_user(discord_id):
    """Retrieve a user from the database or create one if not exists."""
    with get_session() as session:
        user = session.query(User).filter_by(discord_id=discord_id).first()
        if not user:
            user = User(discord_id=discord_id)
            session.add(user)
            session.flush()  # Assigns an ID
            logger.info(f"Created new user with Discord ID: {discord_id}")
        return user.id  # Return user_id instead of the user object

def award_points(user_id, points: int = 0):
    """Add points to a user's total."""
    with get_session() as session:
        user_db = session.query(User).filter_by(id=user_id).first()
        if user_db:
            user_db.points += points
            logger.info(f"Awarded {points} points to user ID {user_db.id}. Total points: {user_db.points}")
        else:
            logger.error(f"User ID {user_id} not found when awarding points.")

def update_streak_and_badges(user_id):
    """Update the user's streak and award badges based on streak length."""
    with get_session() as session:
        user_db = session.query(User).filter_by(id=user_id).first()
        if not user_db:
            logger.error(f"User ID {user_id} not found when updating streak and badges.")
            return []
        today = datetime.now(timezone.utc).date()
        if user_db.last_completed:
            days_diff = (today - user_db.last_completed.date()).days
            if days_diff == 1:
                user_db.streak += 1
                logger.info(f"Incremented streak to {user_db.streak} for user ID {user_db.id}")
            elif days_diff > 1:
                user_db.streak = 1
                logger.info(f"Reset streak to 1 for user ID {user_db.id}")
        else:
            user_db.streak = 1
            logger.info(f"Set initial streak to 1 for user ID {user_db.id}")
        user_db.last_completed = datetime.now(timezone.utc)
        
        # Assign badges based on streak
        badges_awarded = []
        if user_db.streak >= 3 and not session.query(Badge).filter_by(user_id=user_db.id, name='3-day-streak').first():
            badge = Badge(name='3-day-streak', user_id=user_db.id)
            session.add(badge)
            badges_awarded.append('3-day-streak')
        if user_db.streak >= 7 and not session.query(Badge).filter_by(user_id=user_db.id, name='7-day-streak').first():
            badge = Badge(name='7-day-streak', user_id=user_db.id)
            session.add(badge)
            badges_awarded.append('7-day-streak')
        if user_db.streak >= 14 and not session.query(Badge).filter_by(user_id=user_db.id, name='14-day-streak').first():
            badge = Badge(name='14-day-streak', user_id=user_db.id)
            session.add(badge)
            badges_awarded.append('14-day-streak')
        # Add more badges as needed
        
        if badges_awarded:
            logger.info(f"Awarded badges {badges_awarded} to user ID {user_db.id}")
        
        # Return badges_awarded
        return badges_awarded

# ===========================
# 9. Bot Events and Commands
# ===========================

# Event: Bot is ready
@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    
    # Start the check_due_tasks task if it's not already running
    if not check_due_tasks.is_running():
        try:
            check_due_tasks.start()
            logger.info("Started check_due_tasks loop.")
        except RuntimeError as e:
            logger.warning(f"check_due_tasks is already running: {e}")
    
    # Start the send_motivational_quote task if it's not already running
    if not send_motivational_quote.is_running():
        try:
            send_motivational_quote.start()
            logger.info("Started send_motivational_quote loop.")
        except RuntimeError as e:
            logger.warning(f"send_motivational_quote is already running: {e}")

# Command: !start
@bot.command(name='start')
async def start_command(ctx):
    """Initialize interaction with the To-Do Bot."""
    user_id = get_or_create_user(str(ctx.author.id))
    welcome_message = (
        f"Hello {ctx.author.mention}, I'm your AI-powered To-Do Bot! 🎉\n"
        "Here are the commands you can use:\n"
        "`!addtask` - Add a new task.\n"
        "`!viewtasks` - View your pending tasks.\n"
        "`!edittask` - Edit an existing task.\n"
        "`!donetask` - Mark a task as complete.\n"
        "`!badges` - View your achievements and badges.\n"
        "`!points` - View your points.\n"
        "`!generate` - Generate a response using OpenAI's ChatGPT.\n"
        "`!chat` - Chat with the AI assistant.\n"
        "`!help` - Show this help message."
    )
    await ctx.send(welcome_message)

# Command: !help
@bot.command(name='help')
async def help_command(ctx):
    """Display help information."""
    help_embed = discord.Embed(
        title="📚 Discord To-Do Bot Commands",
        color=discord.Color.blue()
    )
    help_embed.add_field(name="!start", value="Initialize interaction with the To-Do Bot.", inline=False)
    help_embed.add_field(name="!addtask", value="Add a new task.\n**Usage:** `!addtask Finish the report by Friday [Priority: High]`", inline=False)
    help_embed.add_field(name="!viewtasks", value="View your pending tasks.", inline=False)
    help_embed.add_field(name="!edittask", value="Edit an existing task.\n**Usage:** `!edittask 1 Update the report deadline to next Monday [Priority: High]`", inline=False)
    help_embed.add_field(name="!donetask", value="Mark a task as complete.\n**Usage:** `!donetask 1`", inline=False)
    help_embed.add_field(name="!badges", value="View your achievements and badges.", inline=False)
    help_embed.add_field(name="!points", value="View your accumulated points.", inline=False)
    help_embed.add_field(name="!generate", value="Generate a response using OpenAI's ChatGPT.\n**Usage:** `!generate Tell me a joke.`", inline=False)
    help_embed.add_field(name="!chat", value="Chat with the AI assistant.\n**Usage:** `!chat How can I improve my productivity?`", inline=False)
    help_embed.add_field(name="!help", value="Show this help message.", inline=False)
    await ctx.send(embed=help_embed)

# Command: !addtask
@bot.command(name='addtask')
async def add_task(ctx, *, task_description: str = None):
    """Add a new task with an optional due date and priority."""
    if not task_description:
        await ctx.send("📝 Please provide the task description. **Usage:** `!addtask Finish the report by Friday [Priority: High]`")
        return

    # Default priority
    priority = "Medium"

    # Parse for priority in the format [Priority: High]
    if "[Priority:" in task_description:
        try:
            parts = task_description.split("[Priority:")
            task_description = parts[0].strip()
            priority_part = parts[1].strip().rstrip(']')
            if priority_part.capitalize() in ["High", "Medium", "Low"]:
                priority = priority_part.capitalize()
        except Exception as e:
            logger.error(f"Error parsing priority: {e}")
            await ctx.send("⚠️ I couldn't parse the priority. Please specify it as `[Priority: High]`, `[Priority: Medium]`, or `[Priority: Low]`.")
            return

    # Parse due date from the task description
    try:
        due_date = None
        if 'by' in task_description:
            parts = task_description.split('by')
            task = parts[0].strip()
            date_str = 'by'.join(parts[1:]).strip()  # Handle multiple 'by's in the description
            due_date = parser.parse(date_str, fuzzy=True)
            task_description = task

            # Ensure due_date is timezone-aware (UTC)
            if due_date.tzinfo is None:
                due_date = due_date.replace(tzinfo=timezone.utc)
            else:
                due_date = due_date.astimezone(timezone.utc)
    except Exception as e:
        logger.error(f"Error parsing due date: {e}")
        await ctx.send("⚠️ I couldn't parse the due date. Please specify it clearly. **Usage:** `!addtask Finish the report by Friday [Priority: High]`")
        return

    # Assign a unique ID to the task
    user_id = get_or_create_user(str(ctx.author.id))

    # Create a new task
    with get_session() as session:
        new_task = Task(
            description=task_description,
            due_date=due_date,
            priority=priority,
            user_id=user_id
        )
        session.add(new_task)
        session.flush()  # Flush to assign an ID without committing
        task_id = new_task.id

    # Confirm task addition
    response = f"✅ **Task Added:** {task_description}\n**ID:** {task_id}\n**Priority:** {priority}"
    if due_date:
        response += f"\n📅 **Due Date:** {due_date.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    await ctx.send(response)

# Command: !viewtasks
@bot.command(name='viewtasks')
async def view_tasks(ctx):
    """View all pending tasks with details."""
    user_id = get_or_create_user(str(ctx.author.id))

    with get_session() as session:
        tasks = session.query(Task).filter_by(user_id=user_id, completed=False).all()
        task_data = []
        for task in tasks:
            data = {
                'id': task.id,
                'description': task.description,
                'due_date': task.due_date.strftime('%Y-%m-%d %H:%M:%S UTC') if task.due_date else None,
                'priority': task.priority
            }
            task_data.append(data)

    if not task_data:
        await ctx.send("📭 You have no pending tasks. Use `!addtask` to add a new task.")
        return

    embed = discord.Embed(
        title="📋 Your Pending Tasks",
        color=discord.Color.green()
    )

    for task in task_data:
        description = task['description']
        if task['due_date']:
            description += f"\n📅 **Due:** {task['due_date']}"
        description += f"\n🔺 **Priority:** {task['priority']}"
        embed.add_field(name=f"ID: {task['id']}", value=description, inline=False)

    await ctx.send(embed=embed)

# Command: !edittask
@bot.command(name='edittask')
async def edit_task(ctx, task_id: int = None, *, new_description: str = None):
    """Edit an existing task's description and/or due date and/or priority."""
    if not task_id or not new_description:
        await ctx.send("✏️ Please provide the task ID and the new details.\n**Usage:** `!edittask 1 Update the report deadline to next Monday [Priority: High]`")
        return

    user_id = get_or_create_user(str(ctx.author.id))

    with get_session() as session:
        task = session.query(Task).filter_by(id=task_id, user_id=user_id, completed=False).first()

        if not task:
            await ctx.send(f"⚠️ No pending task found with ID {task_id}.")
            return

        # Default priority remains unchanged
        priority = task.priority

        # Parse for priority in the format [Priority: High]
        if "[Priority:" in new_description:
            try:
                parts = new_description.split("[Priority:")
                new_description = parts[0].strip()
                priority_part = parts[1].strip().rstrip(']')
                if priority_part.capitalize() in ["High", "Medium", "Low"]:
                    priority = priority_part.capitalize()
            except Exception as e:
                logger.error(f"Error parsing priority during edit: {e}")
                await ctx.send("⚠️ I couldn't parse the priority. Please specify it as `[Priority: High]`, `[Priority: Medium]`, or `[Priority: Low]`.")
                return

        # Parse due date from the new_description
        try:
            due_date = task.due_date
            if 'by' in new_description:
                parts = new_description.split('by')
                new_task_description = parts[0].strip()
                date_str = 'by'.join(parts[1:]).strip()
                due_date = parser.parse(date_str, fuzzy=True)

                # Ensure due_date is timezone-aware (UTC)
                if due_date.tzinfo is None:
                    due_date = due_date.replace(tzinfo=timezone.utc)
                else:
                    due_date = due_date.astimezone(timezone.utc)

                new_description = new_task_description
            else:
                new_description = new_description.strip()
                due_date = None  # Remove due date if not specified
        except Exception as e:
            logger.error(f"Error parsing due date during edit: {e}")
            await ctx.send("⚠️ I couldn't parse the due date. Please specify it clearly. **Usage:** `!edittask 1 Update the report deadline to next Monday [Priority: High]`")
            return

        # Update the task details
        task.description = new_description
        task.due_date = due_date
        task.priority = priority
        task.edited_at = datetime.now(timezone.utc)

    # Confirm task edit
    response = f"📝 **Task ID {task_id} Updated:** {new_description}\n**Priority:** {priority}"
    if due_date:
        response += f"\n📅 **Due Date:** {due_date.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    else:
        response += f"\n📅 **Due Date:** Not Set"
    await ctx.send(response)

# Command: !donetask
@bot.command(name='donetask')
async def done_task(ctx, task_id: int = None):
    """Mark a task as complete."""
    if not task_id:
        await ctx.send("✅ Please provide the **ID** of the task you've completed. **Usage:** `!donetask 1`")
        return

    user_id = get_or_create_user(str(ctx.author.id))

    with get_session() as session:
        task = session.query(Task).filter_by(id=task_id, user_id=user_id, completed=False).first()

        if not task:
            await ctx.send(f"⚠️ No pending task found with ID {task_id}.")
            return

        # Capture necessary attributes before session closes
        task_description = task.description

        # Mark the task as complete
        task.completed = True
        task.completed_at = datetime.now(timezone.utc)

    # Update user streak and badges
    badges_awarded = update_streak_and_badges(user_id)

    # Award points for completing a task
    award_points(user_id, points=10)

    # Send motivational message using the captured description
    motivational_gif_url = "https://media.giphy.com/media/5GoVLqeAOo6PK/giphy.gif"
    response = (
        f"🎉 **Great job** on completing your task: **{task_description}**!\n"
        f"![Celebration GIF]({motivational_gif_url})\n"
        f"🏆 **You've earned 10 points!**\n"
        f"Use `!points` to view your total points."
    )
    await ctx.send(response)

    # Notify about new badges, if any
    if badges_awarded:
        badges_list = ', '.join(badges_awarded)
        badge_notification = f"🏆 **Congratulations! You've earned new badge(s):**\n**{badges_list}**"
        await ctx.send(badge_notification)

# Command: !badges
@bot.command(name='badges')
async def badges_command(ctx):
    """View your achievements and badges."""
    user_id = get_or_create_user(str(ctx.author.id))

    with get_session() as session:
        user_db = session.query(User).filter_by(id=user_id).first()
        if not user_db:
            await ctx.send("⚠️ User not found.")
            return
        streak = user_db.streak
        badges = session.query(Badge).filter_by(user_id=user_id).all()
        badge_names = [badge.name for badge in badges]

    embed = discord.Embed(
        title="🏅 Your Achievements and Badges",
        color=discord.Color.gold()
    )
    embed.add_field(name="📈 Current Streak", value=f"{streak} day(s)", inline=False)
    if badge_names:
        embed.add_field(name="🏆 Badges Earned", value=', '.join(badge_names), inline=False)
    else:
        embed.add_field(name="🏆 Badges Earned", value="None yet. Complete tasks to earn badges!", inline=False)

    await ctx.send(embed=embed)

# Command: !points
@bot.command(name='points')
async def points_command(ctx):
    """View your accumulated points."""
    user_id = get_or_create_user(str(ctx.author.id))

    with get_session() as session:
        user_db = session.query(User).filter_by(id=user_id).first()
        if not user_db:
            await ctx.send("⚠️ User not found.")
            return
        points = user_db.points

    embed = discord.Embed(
        title="⭐ Your Points",
        description=f"You have accumulated **{points}** points!",
        color=discord.Color.purple()
    )
    if ctx.author.avatar:
        embed.set_thumbnail(url=ctx.author.avatar.url)
    await ctx.send(embed=embed)

# Command: !generate (Using OpenAI ChatCompletion)
@bot.command(name='generate')
async def generate(ctx, *, prompt: str = None):
    """Generate a response using OpenAI's ChatGPT."""
    if not prompt:
        await ctx.send("📝 Please provide a prompt for me to generate a response. **Usage:** `!generate Tell me a joke.`")
        return

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.7,
        )

        reply = response.choices[0].message['content'].strip()
        await ctx.send(reply)

    except Exception as e:
        logger.error(f"OpenAI API error in !generate command: {e}")
        await ctx.send("❌ Sorry, I couldn't process your request. Please try again later.")

# Command: !chat (AI Chatbot Integration)
@bot.command(name='chat')
async def chat_command(ctx, *, user_input: str = None):
    """Chat with the AI assistant."""
    if not user_input:
        await ctx.send("🗣️ Please provide a message to chat with me. **Usage:** `!chat How can I improve my productivity?`")
        return

    try:
        # Send the user's message to OpenAI ChatCompletion
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an AI productivity coach. You help users organize their tasks, improve their workflow, and stay motivated."},
                {"role": "user", "content": user_input}
            ],
            max_tokens=300,
            temperature=0.7,
        )

        # Extract the assistant's reply
        ai_reply = response.choices[0].message['content'].strip()

        # Send the reply back to the user
        await ctx.send(ai_reply)

    except Exception as e:
        logger.error(f"OpenAI API error in !chat command: {e}")
        await ctx.send("❌ Sorry, I couldn't process your request. Please try again later.")

# ===========================
# 10. Error Handling
# ===========================

# Error Handling: Handle command errors gracefully
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("❓ I didn't understand that command. Use `!help` to see available commands.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("⚠️ Missing arguments for that command. Please check the usage.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("⚠️ Invalid argument type. Please check the usage.")
    elif isinstance(error, commands.CommandInvokeError):
        logger.error(f"CommandInvokeError: {error.original}")
        await ctx.send("⚠️ An error occurred while executing the command. Please try again later.")
    else:
        logger.error(f"Unhandled error: {error}")
        await ctx.send("⚠️ An unexpected error occurred. Please try again later.")

# ===========================
# 11. Run the Bot
# ===========================

# Run the bot
bot.run(DISCORD_BOT_TOKEN)
