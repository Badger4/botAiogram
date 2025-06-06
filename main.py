import asyncio
import logging
import os
from pyexpat.errors import messages

from aiogram import Dispatcher, Router, types, filters, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.methods import SendMessage
from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from sqlalchemy import false, select

from bot import bot
from database import Parts3d, get_session
from filters import Registered, Review3d, Edit3d
from functions import send_register_query, allow_register_query, generate_rights_keyboard, parts_keyboard_constructor, \
    get_part_info, construct_part_info_keyboard, upload_3mf, send_3mf, get_image
from keyboards_makets import MainMenuMarkUp

main_router = Router()

dp = Dispatcher()


async def start():
    dp.include_routers(main_router)
    await dp.start_polling(bot)

class MainStates(StatesGroup):
    property_edit = State()
    in_part3d_info = State()
    in_parts3d = State()
    prepairing_quote = State()
    accepting_quote = State()
    in_main_menu = State()

class PartCreating(StatesGroup):
    name = State()
    image = State()
    count = State()
    weight = State()
    time_on_A1mini = State()
    time_on_P1S = State()
    filling = State()
    three_mf = State()

# РЕГІСТРАЦІЯ ---------

@main_router.message(~Registered(), filters.CommandStart(),
                     filters.StateFilter(None))
async def prepare_registration_quote(message: types.Message, state: FSMContext):
    await message.answer("Підготовка до надсилання запиту. Введіть імя")
    await state.set_state(MainStates.prepairing_quote)

@main_router.message(filters.StateFilter(MainStates.prepairing_quote))
async def send_quote(message: types.Message, state: FSMContext):
    if any(i in message.text for i in "'\";!@#$%^&*()_/|+=-`~1234567890"):
        await message.answer("Введи імя нормально")
    else:
        await send_register_query(message.text,
                                  message.from_user.url, message.from_user.id)
        await message.answer("Запит на реєстрацію надіслано успішно!")
    await state.clear()

@main_router.callback_query(F.data == "user_registration_allow")
async def accept_query(query: types.CallbackQuery, state: FSMContext):
    print("fff")
    message: types.Message = query.message
    text = message.text.split()
    user_id, userurl, username = text[1], text[2], text[3]
    rights = [False]*4
    markup = ReplyKeyboardBuilder()
    markup.button(text="Підтвердити")
    markup.adjust(1)
    await state.set_state(MainStates.accepting_quote)
    await state.update_data(user_id=user_id, userurl=userurl, username=username, rights=rights)
    await message.answer(".", reply_markup=markup.as_markup(resize_keyboard=True))
    await message.answer("Налаштуйте права користувача:", reply_markup=await generate_rights_keyboard(*rights))


@main_router.callback_query(StateFilter(MainStates.accepting_quote))
async def set_right(query: types.CallbackQuery, state: FSMContext):
    message: types.Message = query.message
    rights = await state.get_value("rights")
    match query.data:
        case "review_3d":
            rights[0] = not rights[0]
        case "edit_3d":
            rights[1] = not rights[1]
        case "review_electronics":
            rights[2] = not rights[2]
        case "edit_electronics":
            rights[3] = not rights[3]
    await state.update_data(rights=rights)
    await message.edit_reply_markup(reply_markup=await generate_rights_keyboard(*rights))

@main_router.message(StateFilter(MainStates.accepting_quote), F.text == "Підтвердити")
async def register_user_from_query(message: types.Message, state: FSMContext):
    data = await state.get_data()
    rights = data["rights"]
    await allow_register_query(data['username'], data['user_id'],
                               review_3d=rights[0], edit_3d=rights[1],
                               review_electronics=rights[2], edit_electronics=rights[3])
    await message.answer("Успішно!")
    await state.clear()

@main_router.callback_query(F.data == "user_registration_deny")
async def decline_query(query: types.CallbackQuery, state: FSMContext):
    await query.message.delete()

# --------------------------

# ОСНОВНЕ МЕНЮ

@main_router.message(StateFilter(None), Registered())
async def send_main_menu(message: types.Message, state: FSMContext):
    await state.set_state(MainStates.in_main_menu)
    await message.answer("Головне меню!", reply_markup=MainMenuMarkUp.as_markup())

# 3Д --------
@main_router.message(Review3d(), StateFilter(MainStates.in_main_menu), F.text == MainMenuMarkUp.modules3d.value)
async def open_3d_model(message: types.Message, state: FSMContext):
    await state.set_state(MainStates.in_parts3d)
    text, text_keyboard, inline_keyboard = await parts_keyboard_constructor(Parts3d)
    await message.answer(".", reply_markup=text_keyboard)
    await message.answer(text, reply_markup=inline_keyboard)

@main_router.callback_query(StateFilter(MainStates.in_parts3d))
async def get_options(query: types.CallbackQuery, state: FSMContext):
    await state.set_state(MainStates.in_part3d_info)
    await state.update_data(part_name=query.data, part_query=query)
    keyboard = await construct_part_info_keyboard(query.data, Parts3d)
    text_keyboard = ReplyKeyboardBuilder().button(text="Назад").button(text="Видалити").as_markup(resize_keyboard=True)
    await query.message.answer('.', reply_markup=text_keyboard)
    print(await get_image(query.data, Parts3d))
    await query.message.answer_photo(await get_image(query.data, Parts3d), caption=query.data, reply_markup=keyboard)
    await send_3mf(query.message, query.data, Parts3d)

@main_router.callback_query(Edit3d(), StateFilter(MainStates.in_part3d_info))
async def edit_option(query: types.CallbackQuery, state: FSMContext):
    await state.set_state(MainStates.property_edit)
    await state.update_data(part_property=query.data)
    await query.message.answer('Введіть/надіслати нове значення/файл:')

@main_router.message(StateFilter(MainStates.property_edit))
async def confirm_edit(message: types.Message, state: FSMContext):
    session = await get_session()
    async with session.begin() as session:
        part = await session.scalar(select(Parts3d).where(Parts3d.name == await state.get_value('part_name')))
        match await state.get_value('part_property'):
            case 'name':
                part.name = message.text
            case 'image':
                part.image = message.photo[0].file_id
            case 'count':
                part.count = message.text
            case 'weight':
                part.weight = message.text
            case 'A1mini':
                part.time_on_A1mini = message.text
            case 'P1S':
                part.time_on_P1S = message.text
            case 'three_mf':
                await upload_3mf(message.document.file_id, part.name, Parts3d)
            case 'filling':
                part.filling = message.text
        await session.commit()
    await message.answer('Успішно!')
    if await state.get_value('part_property') == "name":
        await open_3d_model(message, state)
    else:
        await get_options(await state.get_value('part_query'), state)

@main_router.message(StateFilter(MainStates.in_parts3d), F.text == 'Добавити')
async def add_part(message: types.Message, state: FSMContext):
    await state.set_state(PartCreating.name)
    await message.answer('Введи імя:')


@main_router.message(StateFilter(PartCreating.name))
async def add_name(message: types.Message, state: FSMContext):
    await state.set_state(PartCreating.image)
    await state.update_data(name=message.text)
    await message.answer('Фото: ')

@main_router.message(StateFilter(PartCreating.image))
async def add_image(message: types.Message, state: FSMContext):
    await state.set_state(PartCreating.count)
    await state.update_data(image=message.photo[0].file_id)
    await message.answer('Кількість: ')


@main_router.message(StateFilter(PartCreating.count))
async def add_count(message: types.Message, state: FSMContext):
    await state.set_state(PartCreating.weight)
    await state.update_data(count=message.text)
    await message.answer('Вага: ')

@main_router.message(StateFilter(PartCreating.weight))
async def add_weight(message: types.Message, state: FSMContext):
    await state.set_state(PartCreating.time_on_A1mini)
    await state.update_data(weight=message.text)
    await message.answer('Час на A1mini: ')

@main_router.message(StateFilter(PartCreating.time_on_A1mini))
async def add_A1mini(message: types.Message, state: FSMContext):
    await state.set_state(PartCreating.time_on_P1S)
    await state.update_data(A1mini=message.text)
    await message.answer('Час на P1S')

@main_router.message(StateFilter(PartCreating.time_on_P1S))
async def add_P1S(message: types.Message, state: FSMContext):
    await state.set_state(PartCreating.filling)
    await state.update_data(P1S=message.text)
    await message.answer('Заповнення: ')

@main_router.message(StateFilter(PartCreating.filling))
async def add_filling(message: types.Message, state: FSMContext):
    await state.set_state(PartCreating.three_mf)
    await state.update_data(filling=message.text)
    await message.answer('.3mf Файл: ')

@main_router.message(StateFilter(PartCreating.three_mf))
async def add_full_part(message: types.Message, state: FSMContext):
    session = await get_session()
    async with session.begin() as session:
        part = Parts3d(
            name=await state.get_value('name'),
            image=await state.get_value('image'),
            count=await state.get_value('count'),
            weight=await state.get_value('weight'),
            time_on_A1mini=await state.get_value('A1mini'),
            time_on_P1S=await state.get_value('P1S'),
            filling=await state.get_value('filling'),
            three_mf=message.document.file_id,)
        session.add(part)
        await session.commit()
    await message.answer('Успішно!')
    await open_3d_model(message, state)

@main_router.message(StateFilter(MainStates.in_part3d_info), F.text == "Видалити")
async def delete_part(message: types.Message, state: FSMContext):
    async with (await get_session()).begin() as session:
        part = await session.scalar(select(Parts3d).where(Parts3d.name == await state.get_value('part_name')))
        await session.delete(part)
        await session.commit()
    await message.answer("Видалення деталі пройшло успішно!")
    await open_3d_model(message, state)


@main_router.message(StateFilter(MainStates.in_part3d_info,
                                 MainStates.in_parts3d), F.text == 'Назад')
async def back_handler(message: types.Message, state: FSMContext):
    match await state.get_state():
        case 'MainStates:in_part3d_info':
            await open_3d_model(message, state)
        case 'MainStates:in_parts3d':
            await send_main_menu(message, state)
# --------
@main_router.message(StateFilter(MainStates.in_main_menu), F.text == MainMenuMarkUp.electronics.value)
async def open_electronics(message: types.Message, state: FSMContext):
    pass

@main_router.message(StateFilter(MainStates.in_main_menu), F.text == MainMenuMarkUp.ded.value)
async def open_ded(message: types.Message, state: FSMContext):
    pass


if __name__ == '__main__':
    asyncio.run(start())
