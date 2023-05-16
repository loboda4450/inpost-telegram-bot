import asyncio
import logging
from typing import List, Dict

import yaml

from telethon import TelegramClient, Button
from telethon.events import NewMessage, CallbackQuery

import database
from utils import validate_number, get_phone_number, get_shipment_number, send_pcgs, send_qrc, show_oc, open_comp, \
    send_details

from inpost.static import ParcelStatus
from inpost.static.exceptions import *
from inpost.api import Inpost


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
            inp[user][phone_number]['config'] = {'notifications': users[user][phone_number]['notifications'],
                                                 'default_parcel_machine': users[user][phone_number][
                                                     'default_parcel_machine'],
                                                 'geocheck': users[user][phone_number]['geocheck'],
                                                 'airquality': users[user][phone_number]['airquality']}

    await client.start(bot_token=config['bot_token'])
    print("Started")

    @client.on(NewMessage(pattern='/start'))
    async def start(event):
        await event.reply('Hello!\nThis is a bot helping you to manage your InPost parcels!\n'
                          'If you want to contribute to Inpost development you can find us there: '
                          '[Inpost](https://github.com/IFOSSA/inpost-python)\n\n'
                          'Log in using button that just shown up below the text box '
                          'or by typing `/init <phone_number>`!\n\n'
                          '**List of commands:**\n'
                          '/start - display start message and allow user to login with Telegram\n'
                          '/init - login using phone number `/init <phone_number>`\n'
                          '/confirm - confirm login with sms code `/confirm <sms_code>`\n'
                          '/refresh - refresh authorization token\n'
                          '/pending - return pending parcels\n'
                          '/delivered - return delivered parcels\n'
                          '/parcel - return parcel `/parcel <shipment_number>`\n'
                          '/friends - list all known inpost friends \n'
                          '/share <reply to parcel message> - share parcel to listed friend\n'
                          '/all - return all available parcels\n'
                          '/clear - if you accidentally invoked `/start` and annoying box sprang up',
                          buttons=[Button.request_phone('Log in via Telegram')])

    @client.on(NewMessage())
    async def init(event):
        if event.message.contact:  # first check if NewMessage contains contact field
            phone_number = event.message.contact.phone_number[-9:]  # cut the region part, 9 last digits
        elif not event.text.startswith('/init'):  # then check if starts with /init, if so proceed
            return
        elif len(event.text.split(' ')) == 2 and event.text.split()[1].strip().isdigit():
            phone_number = event.text.split()[1].strip()
        else:
            await event.reply('Something is wrong with provided phone number')
            return

        if event.sender.id not in inp:
            database.add_user(event=event)
        elif phone_number in inp[event.sender.id]:
            await event.reply('You initialized this number before')
            return

        try:
            inp[event.sender.id][phone_number]['inpost'] = Inpost()
            inp[event.sender.id][phone_number]['config'] = {'airquality': True,
                                                            'default_parcel_machine': '',
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

        try:
            if await inp[event.sender.id][phone_number]['inpost'].refresh_token():
                database.edit_phone_number_config(event=event, refr_token=inp[event.sender.id][phone_number]['inpost'].refr_token)
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

        try:
            package: Parcel = await inp[event.sender.id][phone_number]['inpost'].get_parcel(
                shipment_number=(get_shipment_number(event)), parse=True)

            if package.is_multicompartment:
                packages: List[Parcel] = await inp[event.sender.id][phone_number]['inpost'].get_multi_compartment(
                    multi_uuid=package.multi_compartment.uuid, parse=True)
                package = next((parcel for parcel in packages if parcel.is_main_multicompartment), None)
                other = '\n'.join(f'📤 **Sender:** `{p.sender.sender_name}`\n'
                                  f'📦 **Shipment number:** `{p.shipment_number}`' for p in packages if
                                  not p.is_main_multicompartment)

                message = f'⚠️ **THIS IS MULTICOMPARTMENT CONTAINING {len(packages)} PARCELS!** ⚠\n️\n' \
                          f'📤 **Sender:** `{package.sender.sender_name}`\n' \
                          f'📦 **Shipment number:** `{package.shipment_number}`\n' \
                          f'📮 **Status:** `{package.status.value}`\n' \
                          f'📥 **Pick up point:** `{package.pickup_point}, {package.pickup_point.city} ' \
                          f'{package.pickup_point.street} {package.pickup_point.building_number}`\n\n' \
                          f'Other parcels inside:\n{other}'
            elif package.status == ParcelStatus.DELIVERED:
                message = f'📤 **Sender:** `{package.sender.sender_name}`\n' \
                          f'📦 **Shipment number:** `{package.shipment_number}`\n' \
                          f'📮 **Status:** `{package.status.value}`'
            else:
                message = f'📤 **Sender:** `{package.sender.sender_name}`\n' \
                          f'📦 **Shipment number:** `{package.shipment_number}`\n' \
                          f'📮 **Status:** `{package.status.value}`\n' \
                          f'📥 **Pick up point:** `{package.pickup_point}, {package.pickup_point.city} ' \
                          f'{package.pickup_point.street} {package.pickup_point.building_number}`'

            match package.status:
                case ParcelStatus.READY_TO_PICKUP:
                    await event.reply(message,
                                      buttons=[
                                          [Button.inline('Open Code'), Button.inline('QR Code')],
                                          [Button.inline('Details'), Button.inline('Open Compartment')]
                                      ]
                                      )
                case _:
                    await event.reply(message,
                                      buttons=[Button.inline('Details')])

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
            await send_pcgs(event, inp, status)

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

        try:
            friends = await inp[event.sender.id][phone_number]['inpost'].get_friends()
            for f in friends['friends']:
                await event.reply(f'**Name**: {f["name"]}\n'
                                  f'**Phone number**: {f["phoneNumber"]}',
                                  buttons=[Button.inline('Remove')])

            for i in friends['invitations']:
                await event.reply(f'**Name**: {i["friend"]["name"]}\n'
                                  f'**Phone number**: {i["friend"]["phoneNumber"]}\n'
                                  f'**Invitation code**: `{i["invitationCode"]}`\n'
                                  f'**Expiry date**: {i["expiryDate"]}',
                                  buttons=[Button.inline('Remove')])

        except NotAuthenticatedError as e:
            await event.reply(e.reason)
        except ParcelTypeError as e:
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

        try:
            msg = await event.get_reply_message()
            shipment_number = \
                (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[
                    1].strip()

            friends = await inp[event.sender.id][phone_number]['inpost'].get_parcel_friends(shipment_number=shipment_number, parse=True)

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

        await event.reply('Please share your location so I can check whether you are near parcel machine or not.',
                          buttons=[Button.request_location('Confirm localization')])

    @client.on(NewMessage())
    async def location_confirmation(event):
        if not event.message.geo:
            return

        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return
        msg = await event.get_reply_message()
        msg = await msg.get_reply_message()

        phone_number = await get_phone_number(inp, event)

        shipment_number = \
            (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()

        try:
            p: Parcel = await inp[event.sender.id][phone_number]['inpost'].get_parcel(shipment_number=shipment_number, parse=True)

            match p.status:
                case ParcelStatus.DELIVERED:
                    await event.answer('Parcel already delivered!', alert=True)
                case ParcelStatus.READY_TO_PICKUP:
                    if (p.pickup_point.latitude - 0.0005 <= event.message.geo.lat <= p.pickup_point.latitude + 0.0005) \
                            and \
                            (
                                    p.pickup_point.longitude - 0.0005 <= event.message.geo.long <= p.pickup_point.longitude + 0.0005):
                        await event.reply('You are within the range, open?',
                                          buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])
                    else:
                        await event.reply(
                            f'Your location is outside the range that is allowed to open this parcel machine. '
                            f'Confirm that you are standing nearby, there is description:'
                            f'\n\n**Name: {p.pickup_point.name}**'
                            f'\n**Address: {p.pickup_point.post_code} {p.pickup_point.city}, '
                            f'{p.pickup_point.street} {p.pickup_point.building_number}**\n'
                            f'**Description: {p.pickup_point.description}**\n\n'
                            f'Do you still want me to open it for you?',
                            buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])
                case _:
                    await event.answer(f'Parcel not ready for pick up!\nStatus: {p.status.value}', alert=True)

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

    @client.on(CallbackQuery(pattern=b'Yes!'))
    async def yes(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')

        msg = await event.get_message()
        msg = await msg.get_reply_message()
        msg = await msg.get_reply_message()
        msg = await msg.get_reply_message()  # ffs gotta move 3 messages upwards
        phone_number = await get_phone_number(inp, event)
        shipment_number = \
            (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()
        p: Parcel = await inp[event.sender.id][phone_number]['inpost'].get_parcel(shipment_number=shipment_number, parse=True)

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

            friends = await inp[event.sender.id][phone_number]['inpost'].get_parcel_friends(shipment_number=shipment_number, parse=True)
            uuid = (next((f for f in friends['friends'] if (f.name == friend[0] and f.phone_number == friend[1])))).uuid
            if await inp[event.sender.id][phone_number]['inpost'].share_parcel(uuid=uuid, shipment_number=shipment_number):
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
