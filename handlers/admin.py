import os
import asyncio
from datetime import datetime, timedelta

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import FSInputFile
from sqlalchemy import select, update, func

from database.connection import AsyncSessionLocal
from database.models import User
from config import ADMIN_IDS

router = Router()

# 1. State Management for Scaling, Broadcasts, and Search
class AdminStates(StatesGroup):
    waiting_for_search_query = State()
    waiting_for_broadcast_content = State()

@router.message(Command("admin_panel"))
async def admin_panel_root(message: types.Message):
    """The main entry point for the boss. 💅"""
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply("💅 Nice try, honey. You aren't my boss.")

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🔍 Search User", callback_data="admin_search"))
    builder.row(types.InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast"))
    builder.row(types.InlineKeyboardButton(text="📊 Stats", callback_data="admin_stats"))
    builder.row(types.InlineKeyboardButton(text="💾 Download DB", callback_data="admin_download_db"))

    await message.answer(
        "🛠 <b>Alice's High-Level Control</b>\n\nHow do you want to manage your empire today? 🥱",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "admin_cancel")
async def cancel_admin_action(callback: types.CallbackQuery, state: FSMContext):
    """Alice stops what she's doing 🙄"""
    await state.clear()
    await callback.message.edit_text("🔄 <b>Action cancelled.</b> Back to the panel. 💅", parse_mode="HTML")
    # Show panel again after cancellation
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🔍 Search User", callback_data="admin_search"))
    builder.row(types.InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast"))
    builder.row(types.InlineKeyboardButton(text="📊 Stats", callback_data="admin_stats"))
    builder.row(types.InlineKeyboardButton(text="💾 Download DB", callback_data="admin_download_db"))
    await callback.message.answer(
        "🛠 <b>Alice's High-Level Control</b>\n\nHow do you want to manage your empire today? 🥱",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

# --- DATABASE BACKUP LOGIC --- 💾
@router.callback_query(F.data == "admin_download_db")
async def download_db_file(callback: types.CallbackQuery):
    """Alice packs up her memory for the boss. 🥱"""
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("💅 Hands off.")

    db_path = "bot_database.db" 
    
    if os.path.exists(db_path):
        await callback.message.answer_document(
            document=FSInputFile(db_path),
            caption=f"💾 <b>Database Backup</b>\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nKeep this safe, Ali. 💅"
        )
    else:
        await callback.message.answer("🙄 No local .db file found. Check your Cloud settings.")
    
    await callback.answer()

# --- BROADCAST LOGIC (SUPPORT FOR TEXT/IMAGE/VIDEO) --- 📢
@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    """Alice prepares to shout at everyone. 🥱"""
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="❌ Cancel Broadcast", callback_data="admin_cancel"))

    await callback.message.answer(
        "📣 <b>Broadcast Mode</b>\n\nSend me <b>ANYTHING</b> (Text, Photo, or Video) to send to everyone. "
        "I will copy it exactly as you send it, including captions! 💅",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await state.set_state(AdminStates.waiting_for_broadcast_content)
    await callback.answer()

@router.message(AdminStates.waiting_for_broadcast_content)
async def execute_broadcast(message: types.Message, state: FSMContext):
    """Alice delivers the news (Media or Text) globally. 🧠"""
    status_msg = await message.answer("🚀 <b>Starting Broadcast...</b> Sit back and let me work. 💅")
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User.tg_id))
        user_ids = result.scalars().all()

    success_count = 0
    fail_count = 0

    for uid in user_ids:
        try:
            # .copy_to handles ALL media types automatically 💅
            await message.copy_to(chat_id=uid)
            success_count += 1
            
            # Anti-Flood Protection: Sleep 1 second every 25 messages ⏱️
            if success_count % 25 == 0:
                await asyncio.sleep(1.0)
        except Exception:
            fail_count += 1
    
    await status_msg.delete()
    await message.answer(
        f"🏁 <b>Broadcast Finished!</b>\n\n"
        f"✅ Delivered: <b>{success_count}</b>\n"
        f"❌ Failed/Blocked: <b>{fail_count}</b>\n\n"
        "Everyone has been notified. 🥱💅",
        parse_mode="HTML"
    )
    await state.clear()

# --- USER SEARCH & MANAGEMENT --- 🔍
@router.callback_query(F.data == "admin_search")
async def start_search(callback: types.CallbackQuery, state: FSMContext):
    """Alice waits for a name or ID 🥱"""
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="❌ Cancel Search", callback_data="admin_cancel"))

    await callback.message.answer(
        "⌨️ <b>Send me their Telegram ID or @Username:</b>", 
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await state.set_state(AdminStates.waiting_for_search_query)
    await callback.answer()

@router.message(AdminStates.waiting_for_search_query)
async def process_admin_search(message: types.Message, state: FSMContext):
    """Alice searches her memory for the user 🧠"""
    query = message.text.replace("@", "").strip()
    
    async with AsyncSessionLocal() as session:
        if query.isdigit():
            stmt = select(User).where(User.tg_id == int(query))
        else:
            stmt = select(User).where(User.username.ilike(query))
        
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

    if not user:
        # Provide feedback instead of silence
        return await message.answer(
            "🤷‍♂️ <b>User not found in my memory.</b>\nTry another ID or type /admin_panel to reset. 🥱",
            parse_mode="HTML"
        )

    builder = InlineKeyboardBuilder()
    status_text = "PRO ✅" if user.is_pro else "FREE 🆓"
    
    builder.row(types.InlineKeyboardButton(text="💎 Grant 30 Days Pro", callback_data=f"setpro_{user.tg_id}_30"))
    builder.row(types.InlineKeyboardButton(text="🔙 Back to Panel", callback_data="admin_cancel"))

    await message.answer(
        f"👤 <b>User Found:</b>\n"
        f"ID: <code>{user.tg_id}</code>\n"
        f"User: @{user.username if user.username else 'N/A'}\n"
        f"Status: <b>{status_text}</b>\n"
        f"Used Today: <b>{user.daily_requests}/10</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await state.clear()

@router.callback_query(F.data.startswith("setpro_"))
async def execute_pro_grant(callback: types.CallbackQuery):
    """Alice officially grants the Pro status 💅"""
    data = callback.data.split("_")
    user_id = int(data[1])
    days = int(data[2])
    expire_at = datetime.utcnow() + timedelta(days=days)

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User)
            .where(User.tg_id == user_id)
            .values(is_pro=True, expiry_date=expire_at)
        )
        await session.commit()

    await callback.answer(f"✅ Success! User {user_id} is now Pro.", show_alert=True)
    
    try:
        await callback.bot.send_message(
            user_id, 
            "❤️ <b>Payment Confirmed!</b>\n\nYour Pro status is active for 30 days. Enjoy unlimited RCs! 💅",
            parse_mode="HTML"
        )
    except Exception:
        pass

@router.callback_query(F.data == "admin_stats")
async def show_stats(callback: types.CallbackQuery):
    """Alice checks the books 📊"""
    async with AsyncSessionLocal() as session:
        total_res = await session.execute(select(func.count(User.id)))
        pro_res = await session.execute(select(func.count(User.id)).where(User.is_pro == True))
        
        text = (
            "📊 <b>Lazy Alice Business Stats</b>\n\n"
            f"👥 Total Users: <b>{total_res.scalar()}</b>\n"
            f"💎 Pro Users: <b>{pro_res.scalar()}</b>\n\n"
            "Alice is getting popular, honey. 🥱💅"
        )
        await callback.message.answer(text, parse_mode="HTML")
        await callback.answer()