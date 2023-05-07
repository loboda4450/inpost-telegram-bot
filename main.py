import asyncio
import logging
from typing import List, Dict

import yaml

from telethon import TelegramClient, Button
from telethon.events import NewMessage, CallbackQuery
import database

from inpost.static import ParcelStatus, ParcelShipmentType
from inpost.static.exceptions import *
from inpost.api import Inpost


async def reply(event: NewMessage.Event | CallbackQuery.Event, text: str, alert=True):
    if isinstance(event, CallbackQuery.Event):
        await event.answer(text, alert=alert)
    elif isinstance(event, NewMessage.Event):
        await event.reply(text)


async def send_pcgs(event, inp, status):
    packages: List[Parcel] = await inp[event.sender.id].get_parcels(status=status, parse=True)
    exclude = []
    if len(packages) > 0:
        for package in packages:
            if package.shipment_number in exclude:
                continue

            if package.is_multicompartment and not package.is_main_multicompartment:
                exclude.append(package.shipment_number)
                continue

            elif package.is_main_multicompartment:
                packages: List[Parcel] = await inp[event.sender.id].get_multi_compartment(
                    multi_uuid=package.multi_compartment.uuid, parse=True)
                package = next((parcel for parcel in packages if parcel.is_main_multicompartment), None)
                other = '\n'.join(f'📤 **Sender:** `{p.sender.sender_name}`\n'
                                  f'📦 **Shipment number:** `{p.shipment_number}\n`' for p in packages if
                                  not p.is_main_multicompartment)

                message = f'⚠️ **THIS IS MULTICOMPARTMENT CONTAINING {len(packages)} PARCELS!** ⚠\n️\n' \
                          f'📤 **Sender:** `{package.sender.sender_name}`\n' \
                          f'📦 **Shipment number:** `{package.shipment_number}`\n' \
                          f'📮 **Status:** `{package.status.value}`\n' \
                          f'📥 **Pick up point:** `{package.pickup_point}`\n\n' \
                          f'Other parcels inside:\n{other}'

            elif package.shipment_type == ParcelShipmentType.courier:
                message = f'📤 **Sender:** `{package.sender.sender_name}`\n' \
                          f'📦 **Shipment number:** `{package.shipment_number}`\n' \
                          f'📮 **Status:** `{package.status.value}`\n'
            else:
                message = f'📤 **Sender:** `{package.sender.sender_name}`\n' \
                          f'📦 **Shipment number:** `{package.shipment_number}`\n' \
                          f'📮 **Status:** `{package.status.value}`\n' \
                          f'📥 **Pick up point:** `{package.pickup_point}`'

            if package.status in (ParcelStatus.STACK_IN_BOX_MACHINE, ParcelStatus.STACK_IN_CUSTOMER_SERVICE_POINT):
                message = f'⚠️ **PARCEL IS IN SUBSTITUTIONARY PICK UP POINT!** ⚠\n️\n' + message

            match package.status:
                case ParcelStatus.READY_TO_PICKUP | ParcelStatus.STACK_IN_BOX_MACHINE | ParcelStatus.STACK_IN_CUSTOMER_SERVICE_POINT:
                    await event.reply(message + f'\n🫳 **Pick up until:** '
                                                f'`{package.expiry_date.to("local").format("DD.MM.YYYY HH:mm")}`',
                                      buttons=[
                                          [Button.inline('Open Code'), Button.inline('QR Code')],
                                          [Button.inline('Details'), Button.inline('Open Compartment')], ]
                                      )
                case _:
                    await event.reply(message,
                                      buttons=[Button.inline('Details'), ])

    else:
        if isinstance(event, CallbackQuery.Event):
            await event.answer('No parcels with specified status!', alert=True)
        elif isinstance(event, NewMessage.Event):
            await event.reply('No parcels with specified status!')

    return status


async def send_qrc(event, inp, shipment_number):
    p: Parcel = await inp[event.sender.id].get_parcel(shipment_number=shipment_number, parse=True)
    if p.status == ParcelStatus.READY_TO_PICKUP:
        await event.reply(file=p.generate_qr_image)
    else:
        await event.answer(f'Parcel not ready for pick up!\nStatus: {p.status.value}', alert=True)


async def show_oc(event, inp, shipment_number):
    p: Parcel = await inp[event.sender.id].get_parcel(shipment_number=shipment_number, parse=True)
    if p.status == ParcelStatus.READY_TO_PICKUP:
        await event.answer(f'This parcel open code is: {p.open_code}', alert=True)
    else:
        await event.answer(f'Parcel not ready for pick up!\nStatus: {p.status.value}', alert=True)


async def open_comp(event, inp, p: Parcel):
    await inp[event.sender.id].collect(parcel_obj=p)
    await event.reply(
        f'Compartment opened!\nLocation:\n   '
        f'Side: {p.compartment_location.side}\n   '
        f'Row: {p.compartment_location.row}\n   '
        f'Column: {p.compartment_location.column}')


async def send_details(event, inp, shipment_number):
    parcel: Parcel = await inp[event.sender.id].get_parcel(shipment_number=shipment_number, parse=True)

    if parcel.is_multicompartment:
        parcels = await inp[event.sender.id].get_multi_compartment(multi_uuid=parcel.multi_compartment.uuid, parse=True)
        message = ''

        for p in parcels:
            message = message + f'**Sender:** {p.sender}\n'
            events = "\n".join(
                f'{status.date.to("local").format("DD.MM.YYYY HH:mm"):>22}: {status.name.value}' for status in
                p.event_log)
            if p.status == ParcelStatus.READY_TO_PICKUP:
                message = message + f'**Shipment number**: {p.shipment_number}\n' \
                                    f'**Stored**: {p.stored_date.to("local").format("DD.MM.YYYY HH:mm")}\n' \
                                    f'**Open code**: {p.open_code}\n' \
                                    f'**Events**:\n{events}\n\n'

            elif p.status == ParcelStatus.DELIVERED:
                message = message + f'**Stored**: {p.stored_date.to("local").format("DD.MM.YYYY HH:mm")}\n' \
                                    f'**Events**:\n{events}\n\n'
            else:
                message = message + f'**Events**:\n{events}\n\n'

        await event.reply(message)
    else:
        events = "\n".join(
            f'{status.date.to("local").format("DD.MM.YYYY HH:mm"):>22}: {status.name.value}' for status in
            parcel.event_log)
        if parcel.status == ParcelStatus.READY_TO_PICKUP:
            await event.reply(f'**Stored**: {parcel.stored_date.to("local").format("DD.MM.YYYY HH:mm")}\n'
                              f'**Open code**: {parcel.open_code}\n'
                              f'**Events**:\n{events}'
                              )
        elif parcel.status == ParcelStatus.DELIVERED:
            await event.reply(f'**Picked up**: {parcel.pickup_date.to("local").format("DD.MM.YYYY HH:mm")}\n'
                              f'**Events**:\n{events}'
                              )
        else:
            await event.reply(f'**Events**:\n{events}')


async def main(config, inp: Dict):
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=config['log_level'])
    logger = logging.getLogger(__name__)
    client = TelegramClient(**config['telethon_settings'])
    print("Starting")

    if not config['bot_token']:
        raise Exception('No bot token provided')

    data = database.get_dict()
    for d in data:
        inp[d] = Inpost()
        await inp[d].set_phone_number(data[d]['phone_number'])
        inp[d].sms_code = data[d]['sms_code']
        inp[d].refr_token = data[d]['refr_token']
        inp[d].auth_token = data[d]['auth_token']

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
        elif len(event.text.split(' ')) == 2:
            phone_number = event.text.split()[1].strip()
        else:
            await event.reply('Something is wrong with provided phone number')
            return

        if event.sender.id in inp:
            del inp[event.sender.id]
            database.delete_user(event=event)
            await event.reply('You were initialized before, reinitializing')

        try:
            inp[event.sender.id] = Inpost()
            await inp[event.sender.id].set_phone_number(phone_number=phone_number)
            if await inp[event.sender.id].send_sms_code():
                database.add_user(event=event, phone_number=phone_number)
                await event.reply(f'Initialized with phone number: {inp[event.sender.id].phone_number}!'
                                  f'\nSending sms code!', buttons=Button.clear())

        except PhoneNumberError as e:
            await event.reply(e.reason)
        except UnauthorizedError:
            await event.reply('You are not authorized')
        except UnidentifiedAPIError as e:
            logger.exception(e)
            await event.reply('Unexpected error occurred, call admin')
        except Exception as e:
            logger.exception(e)
            await event.reply('Bad things happened, call admin now!')

    @client.on(NewMessage(pattern='/confirm'))
    async def confirm_sms(event):
        if event.sender.id in inp and len(event.text.split()) == 2:
            try:
                if await inp[event.sender.id].confirm_sms_code(event.text.split()[1].strip()):
                    database.edit_user(event=event,
                                       sms_code=event.text.split()[1].strip(),
                                       refr_token=inp[event.sender.id].refr_token,
                                       auth_token=inp[event.sender.id].auth_token)

                    await event.reply(f'Succesfully verifed!', buttons=[Button.inline('Pending Parcels'),
                                                                        Button.inline('Delivered Parcels')])
                else:
                    await event.reply('Could not confirm sms code!')

            except PhoneNumberError as e:
                await event.reply(e.reason)
            except SmsCodeError as e:
                await event.reply(e.reason)
            except UnauthorizedError:
                await event.reply('You are not authorized, initialize first!')
            except UnidentifiedAPIError as e:
                logger.exception(e)
                await event.reply('Unexpected error occurred, call admin')
            except Exception as e:
                logger.exception(e)
                await event.reply('Bad things happened, call admin now!')
        else:
            await event.reply('No sms code provided or not initialized')

    @client.on(NewMessage(pattern='/clear'))
    async def clear(event):
        await event.reply('You are welcome :D', buttons=Button.clear())

    @client.on(NewMessage(pattern='/refresh'))
    async def refresh_token(event):
        if event.sender.id in inp:
            try:
                if await inp[event.sender.id].refresh_token():
                    database.edit_user(event=event, refr_token=inp[event.sender.id].refr_token)
                    await event.reply('Token refreshed!')
                else:
                    await event.reply('Could not refresh token')
            except RefreshTokenError as e:
                await event.reply(e.reason)
            except UnauthorizedError:
                await event.reply('You are not authorized, initialize again')
            except UnidentifiedAPIError as e:
                logger.exception(e)
                await event.reply('Unexpected error occurred, call admin')
            except Exception as e:
                logger.exception(e)
                await event.reply('Bad things happened, call admin now!')

    @client.on(NewMessage(pattern='/parcel'))
    async def get_parcel(event):
        if event.sender.id in inp and len(event.text.split(' ')) == 2:
            try:
                package: Parcel = await inp[event.sender.id].get_parcel(
                    shipment_number=
                    (next((data for data in event.raw_text.split('\n') if 'Shipment number' in data))).split(':')[
                        1].strip(),
                    parse=True)

                if package.is_multicompartment:
                    packages: List[Parcel] = await inp[event.sender.id].get_multi_compartment(
                        multi_uuid=package.multi_compartment.uuid, parse=True)
                    package = next((parcel for parcel in packages if parcel.is_main_multicompartment), None)
                    other = '\n'.join(f'📤 **Sender:** `{p.sender.sender_name}`\n'
                                      f'📦 **Shipment number:** `{p.shipment_number}`' for p in packages if
                                      not p.is_main_multicompartment)

                    message = f'⚠️ **THIS IS MULTICOMPARTMENT CONTAINING {len(packages)} PARCELS!** ⚠\n️\n' \
                              f'📤 **Sender:** `{package.sender.sender_name}`\n' \
                              f'📦 **Shipment number:** `{package.shipment_number}`\n' \
                              f'📮 **Status:** `{package.status.value}`\n' \
                              f'📥 **Pick up point:** `{package.pickup_point}`\n\n' \
                              f'Other parcels inside:\n{other}'
                else:
                    message = f'📤 **Sender:** `{package.sender.sender_name}`\n' \
                              f'📦 **Shipment number:** `{package.shipment_number}`\n' \
                              f'📮 **Status:** `{package.status.value}`\n' \
                              f'📥 **Pick up point:** `{package.pickup_point}`'

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
                await event.reply(e.reason)
            except UnauthorizedError:
                if await inp[event.sender.id].refresh_token():
                    try:
                        package: Parcel = await inp[event.sender.id].get_parcel(
                            shipment_number=
                            (next((data for data in event.raw_text.split('\n') if 'Shipment number' in data))).split(
                                ':')[1].strip(),
                            parse=True)

                        if package.is_multicompartment:
                            packages: List[Parcel] = await inp[event.sender.id].get_multi_compartment(
                                multi_uuid=package.multi_compartment.uuid, parse=True)
                            package = next((parcel for parcel in packages if parcel.is_main_multicompartment), None)
                            other = '\n'.join(f'📤 **Sender:** `{p.sender.sender_name}`\n'
                                              f'📦 **Shipment number:** `{p.shipment_number}`' for p in packages if
                                              not p.is_main_multicompartment)

                            message = f'⚠️ **THIS IS MULTICOMPARTMENT CONTAINING {len(packages)} PARCELS!** ⚠\n️\n' \
                                      f'📤 **Sender:** `{package.sender.sender_name}`\n' \
                                      f'📦 **Shipment number:** `{package.shipment_number}`\n' \
                                      f'📮 **Status:** `{package.status.value}`\n' \
                                      f'📥 **Pick up point:** `{package.pickup_point}`\n\n' \
                                      f'Other parcels inside:\n{other}'
                        else:
                            message = f'📤 **Sender:** `{package.sender.sender_name}`\n' \
                                      f'📦 **Shipment number:** `{package.shipment_number}`\n' \
                                      f'📮 **Status:** `{package.status.value}`\n' \
                                      f'📥 **Pick up point:** `{package.pickup_point}`'

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

                    except NotFoundError:
                        await event.reply('This parcel does not exist or does not belong to you!')
                    except Exception as e:
                        logger.exception(e)
                        await event.reply('Bad things happened, call admin now!')
                else:
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
        else:
            await event.reply('No shipment number provided or not initialized')

    @client.on(NewMessage(pattern='/pending'))
    @client.on(NewMessage(pattern='/delivered'))
    @client.on(NewMessage(pattern='/all'))
    @client.on(CallbackQuery(pattern=b'Pending Parcels'))
    @client.on(CallbackQuery(pattern=b'Delivered Parcels'))
    async def get_packages(event):
        if event.sender.id in inp:
            status = None
            if isinstance(event, CallbackQuery.Event):
                if event.data == b'Pending Parcels':
                    status = [ParcelStatus.READY_TO_PICKUP, ParcelStatus.CONFIRMED,
                              ParcelStatus.ADOPTED_AT_SORTING_CENTER, ParcelStatus.ADOPTED_AT_SOURCE_BRANCH,
                              ParcelStatus.COLLECTED_FROM_SENDER, ParcelStatus.DISPATCHED_BY_SENDER,
                              ParcelStatus.DISPATCHED_BY_SENDER_TO_POK, ParcelStatus.OUT_FOR_DELIVERY,
                              ParcelStatus.OUT_FOR_DELIVERY_TO_ADDRESS, ParcelStatus.SENT_FROM_SOURCE_BRANCH,
                              ParcelStatus.TAKEN_BY_COURIER, ParcelStatus.TAKEN_BY_COURIER_FROM_POK,
                              ParcelStatus.STACK_IN_BOX_MACHINE, ParcelStatus.STACK_IN_CUSTOMER_SERVICE_POINT]
                elif event.data == b'Delivered Parcels':
                    status = ParcelStatus.DELIVERED
            elif isinstance(event, NewMessage.Event):
                if event.text == '/pending':
                    status = [ParcelStatus.READY_TO_PICKUP, ParcelStatus.CONFIRMED,
                              ParcelStatus.ADOPTED_AT_SORTING_CENTER, ParcelStatus.ADOPTED_AT_SOURCE_BRANCH,
                              ParcelStatus.COLLECTED_FROM_SENDER, ParcelStatus.DISPATCHED_BY_SENDER,
                              ParcelStatus.DISPATCHED_BY_SENDER_TO_POK, ParcelStatus.OUT_FOR_DELIVERY,
                              ParcelStatus.OUT_FOR_DELIVERY_TO_ADDRESS, ParcelStatus.SENT_FROM_SOURCE_BRANCH,
                              ParcelStatus.TAKEN_BY_COURIER, ParcelStatus.TAKEN_BY_COURIER_FROM_POK,
                              ParcelStatus.STACK_IN_BOX_MACHINE, ParcelStatus.STACK_IN_CUSTOMER_SERVICE_POINT]
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
            except UnauthorizedError:
                if await inp[event.sender.id].refresh_token():
                    try:
                        await send_pcgs(event, inp, status)

                    except Exception as e:
                        logger.exception(e)
                        await event.reply('Bad things happened, call admin now!')
                else:
                    await event.reply('You are not authorized, initialize first!')

            except NotFoundError:
                await event.reply('No parcels found!')
            except UnidentifiedAPIError as e:
                logger.exception(e)
                await event.reply('Unexpected error occurred, call admin')
            except Exception as e:
                logger.exception(e)
                await event.reply('Bad things happened, call admin now!')

        else:
            await event.reply('You are not initialized')

    @client.on(NewMessage(pattern='/friends'))
    async def send_friends(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')

        try:
            friends = await inp[event.sender.id].get_friends()
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
        except UnauthorizedError:
            if await inp[event.sender.id].refresh_token():
                try:
                    friends = await inp[event.sender.id].get_friends()
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

                except Exception as e:
                    logger.exception(e)
                    await event.reply('Bad things happened, call admin now!')
            else:
                await event.reply('You are not authorized, initialize first!')

        except NotFoundError:
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

        try:
            msg = await event.get_reply_message()
            shipment_number = \
                (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[
                    1].strip()

            friends = await inp[event.sender.id].get_parcel_friends(shipment_number=shipment_number, parse=True)

            for f in friends['friends']:
                await event.reply(f'**Name**: {f.name}\n'
                                  f'**Phone number**: {f.phone_number}',
                                  buttons=[Button.inline('Share')])

        except NotAuthenticatedError as e:
            await event.reply(e.reason)
        except ParcelTypeError as e:
            await event.reply(e.reason)
        except UnauthorizedError:
            if await inp[event.sender.id].refresh_token():
                try:
                    msg = await event.get_reply_message()
                    shipment_number = \
                        (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[
                            1].strip()
                    friends = await inp[event.sender.id].get_parcel_friends(shipment_number=shipment_number,
                                                                            parse=True)

                    for f in friends['friends']:
                        await event.reply(f'**Name**: {f.name}\n'
                                          f'**Phone number**: {f.phone_number}',
                                          buttons=[Button.inline('Share')])

                except Exception as e:
                    logger.exception(e)
                    await event.reply('Bad things happened, call admin now!')

    @client.on(CallbackQuery(pattern=b'QR Code'))
    async def send_qr_code(event):
        if event.sender.id in inp:
            msg = await event.get_message()
            shipment_number = \
                (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()
            try:
                await send_qrc(event, inp, shipment_number)

            except NotAuthenticatedError as e:
                await event.reply(e.reason)
            except ParcelTypeError as e:
                await event.reply(e.reason)
            except UnauthorizedError:
                if await inp[event.sender.id].refresh_token():
                    try:
                        await send_qrc(event, inp, shipment_number)

                    except Exception as e:
                        logger.exception(e)
                        await event.reply('Bad things happened, call admin now!')
                else:
                    await event.reply('You are not authorized, initialize first!')

            except NotFoundError:
                await event.reply('Parcel not found!')
            except UnidentifiedAPIError as e:
                logger.exception(e)
                await event.reply('Unexpected error occurred, call admin')
            except Exception as e:
                logger.exception(e)
                await event.reply('Bad things happened, call admin now!')
        else:
            await event.reply('You are not initialized')

    @client.on(CallbackQuery(pattern=b'Open Code'))
    async def show_open_code(event):
        if event.sender.id in inp:
            msg = await event.get_message()
            shipment_number = \
                (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()
            try:
                await show_oc(event, inp, shipment_number)
            except NotAuthenticatedError as e:
                await event.reply(e.reason)
            except ParcelTypeError as e:
                await event.reply(e.reason)
            except UnauthorizedError:
                if await inp[event.sender.id].refresh_token():
                    try:
                        await show_oc(event, inp, shipment_number)

                    except Exception as e:
                        logger.exception(e)
                        await event.reply('Bad things happened, call admin now!')
                else:
                    await event.reply('You are not authorized, initialize first!')

            except NotFoundError:
                await event.reply('Parcel not found!')
            except UnidentifiedAPIError as e:
                logger.exception(e)
                await event.reply('Unexpected error occurred, call admin')
            except Exception as e:
                logger.exception(e)
                await event.reply('Bad things happened, call admin now!')

        else:
            await event.reply('You are not initialized')

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

        shipment_number = \
            (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()
        try:
            p: Parcel = await inp[event.sender.id].get_parcel(shipment_number=shipment_number, parse=True)
            # (51.96333351198862, 19.69786269448417)
            # (51.96399, 19.69657)

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
                            f'Confirm that you are standing near by, there is description:'
                            f'\n\n**Name: {p.pickup_point.name}**'
                            f'\n**Address: {p.pickup_point.post_code} {p.pickup_point.city}, '
                            f'{p.pickup_point.street} {p.pickup_point.building_number}**\n'
                            f'**Description: {p.pickup_point.description}**\n\n'
                            f'Do you still want me to open it for you?',
                            buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])
                case _:
                    await event.answer(f'Parcel not ready for pick up!\nStatus: {p.status.value}', alert=True)

        except NotAuthenticatedError as e:
            await event.reply(e.reason)
        except ParcelTypeError as e:
            await event.reply(e.reason)
        except UnauthorizedError:
            if await inp[event.sender.id].refresh_token():
                try:
                    p: Parcel = await inp[event.sender.id].get_parcel(shipment_number=shipment_number, parse=True)

                    match p.status:
                        case ParcelStatus.DELIVERED:
                            await event.answer('Parcel already delivered!', alert=True)
                        case ParcelStatus.READY_TO_PICKUP:
                            if (p.pickup_point.latitude - 0.0005 <= event.message.geo.lat <= p.pickup_point.latitude + 0.0005) \
                                    and \
                               (p.pickup_point.longitude - 0.0005 <= event.message.geo.long <= p.pickup_point.longitude + 0.0005):
                                await event.reply('Your location is within the range, should I open?',
                                                  buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])
                            else:
                                await event.reply(
                                    f'Your location is outside the range that is allowed to open this parcel machine. '
                                    f'Confirm that you are standing near by, there is description:'
                                    f'\n\n**Name: {p.pickup_point.name}**'
                                    f'\n**Address: {p.pickup_point.post_code} {p.pickup_point.city}, '
                                    f'{p.pickup_point.street} {p.pickup_point.building_number}**\n'
                                    f'**Description: {p.pickup_point.description}**\n\n'
                                    f'Do you still want me to open it for you?',
                                    buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])
                        case _:
                            await event.answer(f'Parcel not ready for pick up!\nStatus: {p.status.value}', alert=True)

                except Exception as e:
                    logger.exception(e)
                    await event.reply('Bad things happened, call admin now!')
            else:
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
        shipment_number = \
            (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()
        p: Parcel = await inp[event.sender.id].get_parcel(shipment_number=shipment_number, parse=True)

        try:
            await open_comp(event, inp, p)

        except NotAuthenticatedError as e:
            await event.reply(e.reason)
        except ParcelTypeError as e:
            await event.reply(e.reason)
        except UnauthorizedError:
            if await inp[event.sender.id].refresh_token():
                try:
                    await open_comp(event, inp, p)

                except Exception as e:
                    logger.exception(e)
                    await event.reply('Bad things happened, call admin now!')
            else:
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
        if event.sender.id in inp:
            msg = await event.get_message()
            shipment_number = \
                (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()
            try:
                await send_details(event, inp, shipment_number)
            except NotAuthenticatedError as e:
                await event.reply(e.reason)
            except ParcelTypeError as e:
                await event.reply(e.reason)
            except UnauthorizedError:
                if await inp[event.sender.id].refresh_token():
                    try:
                        await send_details(event, inp, shipment_number)

                    except Exception as e:
                        logger.exception(e)
                        await event.reply('Bad things happened, call admin now!')
                else:
                    await event.reply('You are not authorized, initialize first!')

            except NotFoundError:
                await event.reply('Parcel not found!')
            except UnidentifiedAPIError as e:
                logger.exception(e)
                await event.reply('Unexpected error occurred, call admin')
            except Exception as e:
                logger.exception(e)
                await event.reply('Bad things happened, call admin now!')

        else:
            await event.reply('You are not initialized')

    @client.on(CallbackQuery(pattern=b'Share'))
    async def share_parcel(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')

        try:
            friend = await event.get_message()
            msg = await friend.get_reply_message()
            msg = await msg.get_reply_message()
            friend = friend.raw_text.split('\n')
            friend = [friend[0].split(':')[1].strip(), friend[1].split(':')[1].strip()]

            shipment_number = \
                (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[
                    1].strip()

            friends = await inp[event.sender.id].get_parcel_friends(shipment_number=shipment_number, parse=True)
            uuid = (next((f for f in friends['friends'] if (f.name == friend[0] and f.phone_number == friend[1])))).uuid
            if await inp[event.sender.id].share_parcel(uuid=uuid, shipment_number=shipment_number):
                await event.reply('Parcel shared!')
            else:
                await event.reply('Not shared!')

        except NotAuthenticatedError as e:
            await event.reply(e.reason)
        except ParcelTypeError as e:
            await event.reply(e.reason)
        except UnauthorizedError:
            if await inp[event.sender.id].refresh_token():
                try:
                    friend = await event.get_message()
                    msg = await friend.get_reply_message()
                    msg = await msg.get_reply_message()
                    friend = friend.raw_text.split('\n')
                    friend = [friend[0].split(':')[1].strip(), friend[1].split(':')[1].strip()]

                    shipment_number = \
                        (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[
                            1].strip()

                    friends = await inp[event.sender.id].get_parcel_friends(shipment_number=shipment_number, parse=True)
                    uuid = (next(
                        (f for f in friends['friends'] if (f.name == friend[0] and f.phone_number == friend[1])))).uuid
                    if await inp[event.sender.id].share_parcel(uuid=uuid, shipment_number=shipment_number):
                        await event.reply('Parcel shared!')
                    else:
                        await event.reply('Not shared!')

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
