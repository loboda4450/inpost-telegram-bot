import asyncio
import logging
from typing import List, Dict

import arrow
import yaml

from telethon import TelegramClient, Button
from telethon.events import NewMessage, CallbackQuery

import database
from utils import validate_number, get_phone_number, send_pcgs, send_qrc, show_oc, open_comp, \
    send_details, BotUserConfig, send_pcg, init_phone_number, confirm_location

from inpost.static import ParcelStatus
from inpost.static.exceptions import *
from inpost.api import Inpost

from constants import pending_statuses, welcome_message, friend_invitations_message_builder


async def reply(event: NewMessage.Event | CallbackQuery.Event, text: str, alert=True):
    if isinstance(event, CallbackQuery.Event):
        await event.answer(text, alert=alert)
    elif isinstance(event, NewMessage.Event):
        await event.reply(text)


async def main(config, inp: Dict):
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=config['log_level'])
    logger = logging.getLogger(__name__)
    client = TelegramClient(**config['telethon_settings'])
    print("Starting")

    if not config['bot_token']:
        raise Exception('No bot token provided')

    users = database.get_dict()
    for user in users:
        for phone_number in users[user]:
            inp[user] = dict()
            inp[user][phone_number] = dict()
            inp[user][phone_number]['inpost'] = Inpost()
            await inp[user][phone_number]['inpost'].set_phone_number(
                users[user][phone_number]['phone_number'])  # must do it that way, logger has to be initialized
            inp[user][phone_number]['inpost'].sms_code = users[user][phone_number]['sms_code']
            inp[user][phone_number]['inpost'].refr_token = users[user][phone_number]['refr_token']
            inp[user][phone_number]['inpost'].auth_token = users[user][phone_number]['auth_token']
            inp[user][phone_number]['config'] = BotUserConfig(
                **{'notifications': users[user][phone_number]['notifications'],
                   'default_parcel_machine': users[user][phone_number][
                       'default_parcel_machine'],
                   'geocheck': users[user][phone_number]['geocheck'],
                   'airquality': users[user][phone_number]['airquality'],
                   'location': (0, 0),
                   'location_time': arrow.get(2023, 1, 1)})

    await client.start(bot_token=config['bot_token'])
    print("Started")

    @client.on(NewMessage(pattern='/start'))
    async def start(event):
        await event.reply(welcome_message, buttons=[Button.request_phone('Log in via Telegram')])

    @client.on(NewMessage())
    async def new_message_handler(event):
        #  I know, that's bullshit, gotta figure out better solution
        phone_number = await init_phone_number(event=event)
        if phone_number is not None:
            if event.sender.id not in inp:
                database.add_user(event=event)
            elif phone_number in inp[event.sender.id]:
                await event.reply('You initialized this number before')
                return

            try:
                inp[event.sender.id][phone_number]['inpost'] = Inpost()
                inp[event.sender.id][phone_number]['config'] = {'airquality': True,
                                                                'default_parcel_machine': None,
                                                                'geocheck': True,
                                                                'notifications': True}

                await inp[event.sender.id][phone_number]['inpost'].set_phone_number(phone_number=phone_number)
                if await inp[event.sender.id][phone_number]['inpost'].send_sms_code():
                    database.add_phone_number_config(event=event, phone_number=phone_number)
                    await event.reply(
                        f'Initialized with phone number: {inp[event.sender.id][phone_number]["inpost"].phone_number}!'
                        f'\nSending sms code!', buttons=Button.clear())

            except PhoneNumberError as e:
                logger.exception(e)
                await event.reply(e.reason)
            except UnauthorizedError as e:
                logger.exception(e)
                await event.reply('You are not authorized')
            except UnidentifiedAPIError as e:
                logger.exception(e)
                await event.reply('Unexpected error occurred, call admin')
            except Exception as e:
                logger.exception(e)
                await event.reply('Bad things happened, call admin now!')
        elif event.message.geo:  # validate location
            phone_number = await get_phone_number(inp, event)
            if phone_number is None:
                await event.reply('No phone number provided!')
                return

            if event.sender.id not in inp:
                await event.reply('You are not initialized')
                return

            msg = await event.get_reply_message()
            msg = await msg.get_reply_message()
            inp[event.sender.id][phone_number]['config'].location_time = arrow.now(tz='Europe/Warsaw')
            inp[event.sender.id][phone_number]['config'].location = (event.message.geo.lat, event.message.geo.long)

            shipment_number = \
                (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()

            try:
                await confirm_location(event=event, inp=inp, phone_number=phone_number, shipment_number=shipment_number)

            except (NotAuthenticatedError, ParcelTypeError) as e:
                logger.exception(e)
                await event.reply(e.reason)
            except UnauthorizedError as e:
                logger.exception(e)
                await event.reply('You are not authorized, initialize first!')
            except NotFoundError:
                await event.reply('Parcel not found')
            except UnidentifiedAPIError as e:
                logger.exception(e)
                await event.reply('Unexpected error occurred, call admin')
            except Exception as e:
                logger.exception(e)
                await event.reply('Bad things happened, call admin now!')
        else:
            return

    @client.on(NewMessage(pattern='/confirm'))
    async def confirm_sms(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        sms_code = await validate_number(event=event, phone_number=False)

        try:
            if await inp[event.sender.id][phone_number]['inpost'].confirm_sms_code(sms_code=sms_code):
                database.edit_phone_number_config(event=event,
                                                  sms_code=sms_code,
                                                  refr_token=inp[event.sender.id][phone_number]['inpost'].refr_token,
                                                  auth_token=inp[event.sender.id][phone_number]['inpost'].auth_token)

                await event.reply(f'Succesfully verifed!', buttons=[Button.inline('Pending Parcels'),
                                                                    Button.inline('Delivered Parcels')])
            else:
                await event.reply('Could not confirm sms code!')

        except (PhoneNumberError, SmsCodeError) as e:
            logger.exception(e)
            await event.reply(e.reason)
        except UnauthorizedError as e:
            logger.exception(e)
            await event.reply('You are not authorized, initialize first!')
        except UnidentifiedAPIError as e:
            logger.exception(e)
            await event.reply('Unexpected error occurred, call admin')
        except Exception as e:
            logger.exception(e)
            await event.reply('Bad things happened, call admin now!')

    @client.on(NewMessage(pattern='/clear'))
    async def clear(event):
        await event.reply('You are welcome :D', buttons=Button.clear())

    @client.on(NewMessage(pattern='/refresh'))
    async def refresh_token(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        phone_number = await get_phone_number(inp, event)
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        try:
            if await inp[event.sender.id][phone_number]['inpost'].refresh_token():
                database.edit_phone_number_config(event=event,
                                                  refr_token=inp[event.sender.id][phone_number]['inpost'].refr_token)
                await event.reply('Token refreshed!')
            else:
                await event.reply('Could not refresh token')
        except RefreshTokenError as e:
            logger.exception(e)
            await event.reply(e.reason)
        except UnauthorizedError as e:
            logger.exception(e)
            await event.reply('You are not authorized, initialize again')
        except UnidentifiedAPIError as e:
            logger.exception(e)
            await event.reply('Unexpected error occurred, call admin')
        except Exception as e:
            logger.exception(e)
            await event.reply('Bad things happened, call admin now!')

    @client.on(NewMessage(pattern='/parcel'))
    async def get_parcel(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        phone_number = await get_phone_number(inp, event)
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        try:
            await send_pcg(event, inp, phone_number)

        except NotAuthenticatedError as e:
            logger.exception(e)
            await event.reply(e.reason)
        except UnauthorizedError as e:
            logger.exception(e)
            await event.reply('You are not authorized, initialize first!')
        except NotFoundError as e:
            logger.exception(e)
            await event.reply('This parcel does not exist or does not belong to you!')
        except UnidentifiedAPIError as e:
            logger.exception(e)
            await event.reply('Unexpected exception occurred, call admin')
        except Exception as e:
            logger.exception(e)
            await event.reply('Bad things happened, call admin now!')

    @client.on(NewMessage(pattern='/pending'))
    @client.on(NewMessage(pattern='/delivered'))
    @client.on(NewMessage(pattern='/all'))
    @client.on(CallbackQuery(pattern=b'Pending Parcels'))
    @client.on(CallbackQuery(pattern=b'Delivered Parcels'))
    async def get_packages(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        phone_number = await get_phone_number(inp, event)
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        status = None
        if isinstance(event, CallbackQuery.Event):
            if event.data == b'Pending Parcels':
                status = pending_statuses
            elif event.data == b'Delivered Parcels':
                status = ParcelStatus.DELIVERED
        elif isinstance(event, NewMessage.Event):
            if event.text == '/pending':
                status = pending_statuses
            elif event.text == '/delivered':
                status = ParcelStatus.DELIVERED
            elif event.text == '/all':
                status = None
            else:
                return

        try:
            await send_pcgs(event, inp, status, phone_number)

        except NotAuthenticatedError as e:
            await event.reply(e.reason)
        except ParcelTypeError as e:
            await event.reply(e.reason)
        except UnauthorizedError as e:
            logger.exception(e)
            await event.reply('You are not authorized, initialize first!')
        except NotFoundError:
            await event.reply('No parcels found!')
        except UnidentifiedAPIError as e:
            logger.exception(e)
            await event.reply('Unexpected error occurred, call admin')
        except Exception as e:
            logger.exception(e)
            await event.reply('Bad things happened, call admin now!')

    @client.on(NewMessage(pattern='/friends'))
    async def send_friends(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        phone_number = await get_phone_number(inp, event)
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        try:
            friends = await inp[event.sender.id][phone_number]['inpost'].get_friends()
            for f in friends['friends']:
                await event.reply(f'**Name**: {f["name"]}\n'
                                  f'**Phone number**: {f["phoneNumber"]}',
                                  buttons=[Button.inline('Remove')])

            for i in friends['invitations']:
                await event.reply(friend_invitations_message_builder(friend=i),
                                  buttons=[Button.inline('Remove')])

        except (NotAuthenticatedError, ParcelTypeError) as e:
            logger.exception(e)
            await event.reply(e.reason)
        except UnauthorizedError as e:
            logger.exception(e)
            await event.reply('You are not authorized, initialize first!')
        except NotFoundError as e:
            logger.exception(e)
            await event.reply('Parcel not found!')
        except UnidentifiedAPIError as e:
            logger.exception(e)
            await event.reply('Unexpected error occurred, call admin')
        except Exception as e:
            logger.exception(e)
            await event.reply('Bad things happened, call admin now!')

    @client.on(NewMessage(pattern='/share'))
    async def share_to_friend(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')

        if not event.message.is_reply:
            await event.reply('Wrong parcel to share!')

        phone_number = await get_phone_number(inp, event)
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        try:
            msg = await event.get_reply_message()
            shipment_number = \
                (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[
                    1].strip()

            friends = await inp[event.sender.id][phone_number]['inpost'].get_parcel_friends(
                shipment_number=shipment_number, parse=True)

            for f in friends['friends']:
                await event.reply(f'**Name**: {f.name}\n'
                                  f'**Phone number**: {f.phone_number}',
                                  buttons=[Button.inline('Share')])

        except (NotAuthenticatedError, ParcelTypeError) as e:
            logger.exception(e)
            await event.reply(e.reason)
        except UnauthorizedError as e:
            logger.exception(e)
            await event.reply('You are not authorized, initialize first!')
        except NotFoundError as e:
            logger.exception(e)
            await event.reply('Parcel not found!')
        except UnidentifiedAPIError as e:
            logger.exception(e)
            await event.reply('Unexpected error occurred, call admin')
        except Exception as e:
            logger.exception(e)
            await event.reply('Bad things happened, call admin now!')

    @client.on(NewMessage(pattern='/set_default_phone_number'))
    async def set_default_phone_number(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')

        phone_number = await get_phone_number(inp, event)
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        database.edit_default_phone_number(event=event, default_phone_number=phone_number)
        inp[event.sender.id][phone_number]['config'].default_phone_number = phone_number
        await event.reply('Default phone number is set!')

    @client.on(NewMessage(pattern='/set_geocheck'))
    async def set_geocheck(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')

        phone_number = await get_phone_number(inp, event)
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        if len(inp[event.sender.id]) == 1:
            if len(event.text.split(' ')) != 2:
                await event.reply('No option selected (available On/Off)')
                return

            geocheck = True if event.text.split(' ')[1].strip().lower() == 'on' else False

        else:
            if len(event.text.split(' ')) != 3:
                await event.reply('No option selected (available On/Off) or no phone number provided')
                return

            geocheck = True if event.text.split(' ')[2].strip().lower() == 'on' else False

        database.edit_phone_number_config(event=event,
                                          phone_number=phone_number,
                                          geocheck=geocheck)
        inp[event.sender.id][phone_number]['config'].geocheck = geocheck
        await event.reply('Geo checking is set!')

    @client.on(NewMessage(pattern='/set_airquality'))
    async def set_airquality(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')

        phone_number = await get_phone_number(inp, event)
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        if len(inp[event.sender.id]) == 1:
            if len(event.text.split(' ')) != 2:
                await event.reply('No option selected (available On/Off)')
                return

            airquality = True if event.text.split(' ')[1].strip().lower() == 'on' else False

        else:
            if len(event.text.split(' ')) != 3:
                await event.reply('No option selected (available On/Off) or no phone number provided')
                return

            airquality = True if event.text.split(' ')[2].strip().lower() == 'on' else False

        database.edit_phone_number_config(event=event,
                                          phone_number=phone_number,
                                          airquality=airquality)
        inp[event.sender.id][phone_number]['config'].airquality = airquality
        await event.reply('Airquality is set!')

    @client.on(NewMessage(pattern='/set_notifications'))
    async def set_notifications(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')

        phone_number = await get_phone_number(inp, event)
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        if len(inp[event.sender.id]) == 1:
            if len(event.text.split(' ')) != 2:
                await event.reply('No option selected (available On/Off)')
                return

            notifications = True if event.text.split(' ')[1].strip().lower() == 'on' else False

        else:
            if len(event.text.split(' ')) != 3:
                await event.reply('No option selected (available On/Off) or no phone number provided')
                return

            notifications = True if event.text.split(' ')[2].strip().lower() == 'on' else False

        database.edit_phone_number_config(event=event,
                                          phone_number=phone_number,
                                          notifications=notifications)
        inp[event.sender.id][phone_number]['config'].notifications = notifications
        await event.reply('Notifications are set!')

    @client.on(CallbackQuery(pattern=b'QR Code'))
    async def send_qr_code(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        msg = await event.get_message()
        shipment_number = \
            (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()
        try:
            await send_qrc(event, inp, shipment_number)

        except (NotAuthenticatedError, ParcelTypeError) as e:
            logger.exception(e)
            await event.reply(e.reason)
        except UnauthorizedError as e:
            logger.exception(e)
            await event.reply('You are not authorized, initialize first!')
        except NotFoundError:
            await event.reply('Parcel not found!')
        except UnidentifiedAPIError as e:
            logger.exception(e)
            await event.reply('Unexpected error occurred, call admin')
        except Exception as e:
            logger.exception(e)
            await event.reply('Bad things happened, call admin now!')

    @client.on(CallbackQuery(pattern=b'Open Code'))
    async def show_open_code(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        msg = await event.get_message()
        shipment_number = \
            (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()
        try:
            await show_oc(event, inp, shipment_number)
        except (NotAuthenticatedError, ParcelTypeError) as e:
            logger.exception(e)
            await event.reply(e.reason)
        except UnauthorizedError as e:
            logger.exception(e)
            await event.reply('You are not authorized, initialize first!')
        except NotFoundError:
            await event.reply('Parcel not found!')
        except UnidentifiedAPIError as e:
            logger.exception(e)
            await event.reply('Unexpected error occurred, call admin')
        except Exception as e:
            logger.exception(e)
            await event.reply('Bad things happened, call admin now!')

    @client.on(CallbackQuery(pattern=b'Open Compartment'))
    async def open_compartment(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')

        phone_number = await get_phone_number(inp, event)
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        if inp[event.sender.id][phone_number]['config'].location_time < (arrow.now(tz='Europe/Warsaw').shift(minutes=+2)):
            await event.reply('Please share your location so I can check whether you are near parcel machine or not.',
                              buttons=[Button.request_location('Confirm localization')])

        inp[event.sender.id][phone_number]['config'].location_time_lock = True  # gotta do this in case someone would want to hit 'open compartment' button just on the edge, otherwise hitting 'yes' button could be davson-insensitive
        await event.reply('Less than 2 minutes have passed since the last compartment opening, '
                          'skipping location verification.\nAre you sure to open?',
                          buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])

    @client.on(CallbackQuery(pattern=b'Yes!'))
    async def yes(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')

        phone_number = await get_phone_number(inp, event)
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        if inp[event.sender.id][phone_number]['config'].location_time_lock:
            msg = await event.get_message()
            msg = await msg.get_reply_message()
            msg = await msg.get_reply_message()
            inp[event.sender.id][phone_number]['config'].location_time_lock = False
        else:
            msg = await event.get_message()
            msg = await msg.get_reply_message()
            msg = await msg.get_reply_message()
            msg = await msg.get_reply_message()  # ffs gotta move 3 messages upwards

        shipment_number = \
            (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()
        p: Parcel = await inp[event.sender.id][phone_number]['inpost'].get_parcel(shipment_number=shipment_number,
                                                                                  parse=True)

        try:
            await open_comp(event, inp, p)

        except (NotAuthenticatedError, ParcelTypeError) as e:
            logger.exception(e)
            await event.reply(e.reason)
        except UnauthorizedError as e:
            logger.exception(e)
            await event.reply('You are not authorized, initialize first!')
        except NotFoundError:
            await event.reply('Parcel not found')
        except UnidentifiedAPIError as e:
            logger.exception(e)
            await event.reply('Unexpected error occurred, call admin')
        except Exception as e:
            logger.exception(e)
            await event.reply('Bad things happened, call admin now!')

    @client.on(CallbackQuery(pattern=b'Hell no!'))
    async def no(event):
        await event.reply('Fine, compartment remains closed!')

    @client.on(CallbackQuery(pattern=b'Details'))
    async def details(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        msg = await event.get_message()
        shipment_number = \
            (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()
        try:
            await send_details(event, inp, shipment_number)

        except (NotAuthenticatedError, ParcelTypeError) as e:
            logger.exception(e)
            await event.reply(e.reason)
        except UnauthorizedError as e:
            logger.exception(e)
            await event.reply('You are not authorized, initialize first!')
        except NotFoundError:
            await event.reply('Parcel not found!')
        except UnidentifiedAPIError as e:
            logger.exception(e)
            await event.reply('Unexpected error occurred, call admin')
        except Exception as e:
            logger.exception(e)
            await event.reply('Bad things happened, call admin now!')

    @client.on(CallbackQuery(pattern=b'Share'))
    async def share_parcel(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')

        phone_number = await get_phone_number(inp, event)

        try:
            friend = await event.get_message()
            msg = await friend.get_reply_message()
            msg = await msg.get_reply_message()
            friend = friend.raw_text.split('\n')
            friend = [friend[0].split(':')[1].strip(), friend[1].split(':')[1].strip()]

            shipment_number = \
                (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[
                    1].strip()

            friends = await inp[event.sender.id][phone_number]['inpost'].get_parcel_friends(
                shipment_number=shipment_number, parse=True)
            uuid = (next((f for f in friends['friends'] if (f.name == friend[0] and f.phone_number == friend[1])))).uuid
            if await inp[event.sender.id][phone_number]['inpost'].share_parcel(uuid=uuid,
                                                                               shipment_number=shipment_number):
                await event.reply('Parcel shared!')
            else:
                await event.reply('Not shared!')

        except (NotAuthenticatedError, ParcelTypeError) as e:
            logger.exception(e)
            await event.reply(e.reason)
        except UnauthorizedError as e:
            logger.exception(e)
            await event.reply('You are not authorized, initialize first!')
        except NotFoundError:
            await event.reply('Parcel not found!')
        except UnidentifiedAPIError as e:
            logger.exception(e)
            await event.reply('Unexpected error occurred, call admin')
        except Exception as e:
            logger.exception(e)
            await event.reply('Bad things happened, call admin now!')

    async with client:
        print("Good morning!")
        await client.run_until_disconnected()


if __name__ == '__main__':
    with open("config.yml", 'r') as f:
        config = yaml.safe_load(f)
        asyncio.run(main(config=config, inp=dict()))
