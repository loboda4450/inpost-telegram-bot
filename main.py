import asyncio
import logging
from typing import Dict

import arrow
import yaml
from inpost.static import ParcelStatus, ParcelType, PhoneNumberError, UnauthorizedError, UnidentifiedAPIError, \
    NotAuthenticatedError, NotFoundError, ParcelTypeError, Parcel
from telethon import TelegramClient, Button
from telethon.events import NewMessage, CallbackQuery

import database
from constants import pending_statuses, welcome_message, friend_invitations_message_builder, \
    out_of_range_message_builder, open_comp_message_builder, use_command_as_reply_message_builder, \
    not_enough_parameters_provided
from utils import get_shipment_and_phone_number_from_button, send_pcgs, send_qrc, show_oc, open_comp, \
    send_details, BotUserPhoneNumberConfig, BotUserConfig, send_pcg, init_phone_number, confirm_location, \
    get_shipment_and_phone_number_from_reply, is_parcel_owner


async def main(config, inp: Dict):
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=config['log_level'])
    logger = logging.getLogger(__name__)
    client = TelegramClient(**config['telethon_settings'])
    print("Starting")

    if not config['bot_token']:
        raise Exception('No bot token provided')

    users = database.get_dict()

    inp = {user: BotUserConfig(default_phone_number=database.get_default_phone_number(userid=user).phone_number,
                               consent=database.get_user_consent(userid=user),
                               phone_numbers=users[user]) for user in users}

    await client.start(bot_token=config['bot_token'])
    print("Started")

    @client.on(NewMessage(pattern='/me'))
    async def get_me(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        for phone_number in inp[event.sender.id].phone_numbers.values():
            await event.reply(
                f'**Phone number**: `{str(phone_number.phone_number)[:3] + "***" + str(phone_number.phone_number)[6:]}`'
                f'\n**Default parcel machine**: `{phone_number.default_parcel_machine if phone_number.default_parcel_machine != "" else "Not set"}`'
                f'\n**Notifications**: `{phone_number.notifications}`'
                f'\n**Geo checking**: `{phone_number.geocheck}`'
                f'\n**Air quality**: `{phone_number.airquality}`')

    @client.on(NewMessage(func=lambda e: e.text.startswith('/init') or e.message.contact is not None))
    async def init_user(event):
        async with client.conversation(event.sender.id) as convo:
            phone_number = await init_phone_number(event=event)
            try:
                if phone_number is None:
                    await convo.send_message(
                        'Something is wrong with provided phone number. Start initialization again.',
                        buttons=Button.clear())
                    convo.cancel()
                    return

                if event.sender.id not in inp:
                    inp.update({event.sender.id: BotUserConfig()})
                    database.add_user(event=event)

                if database.phone_number_exists(phone_number=phone_number):
                    if phone_number in inp[event.sender.id]:
                        await convo.send_message(
                            'You have initialized this phone number before, do you want to do it again? '
                            'All defaults remains!', buttons=[Button.inline('Do it'), Button.inline('Cancel')])
                        resp = await convo.wait_event(CallbackQuery())

                        match resp.data:
                            case b'Do it':
                                await resp.reply('Fine, moving on to sending sms code!')
                            case b'Cancel':
                                await resp.reply('Fine, cancelling!')
                                convo.cancel()
                                return

                    else:
                        await convo.send_message("Phone number already exist and you are not it's owner, cancelling!",
                                                 buttons=Button.clear())
                        convo.cancel()
                        return

                else:
                    inp[event.sender.id].phone_numbers.update({phone_number: BotUserPhoneNumberConfig(**{
                        'airquality': True,
                        'default_parcel_machine': None,
                        'geocheck': True,
                        'notifications': True,
                        'phone_number': phone_number})})

                    database.add_phone_number_config(event=event, phone_number=phone_number)

                    if len(inp[event.sender.id].phone_numbers) == 1:
                        inp[event.sender.id].default_phone_number = phone_number
                        database.edit_default_phone_number(event=event, default_phone_number=phone_number)

                if not await inp[event.sender.id][phone_number].inpost.send_sms_code():
                    await convo.send_message('Could not send sms code! Start initializing again!',
                                             buttons=Button.clear())
                    return

                await convo.send_message('Phone number accepted, send me sms code that InPost '
                                         'sent to provided phone number! You have 60 seconds from now!',
                                         buttons=Button.clear())
                sms_code = await convo.get_response(timeout=60)

                if not (len(sms_code.text.strip()) == 6 and sms_code.text.strip().isdigit()):
                    await convo.send_message(
                        'Something is wrong with provided sms code! Start initialization again.',
                        buttons=Button.clear())
                    return

                if not await inp[event.sender.id][phone_number].inpost.confirm_sms_code(
                        sms_code=sms_code.text.strip()):
                    await convo.send_message('Something went wrong! Start initialization again.',
                                             buttons=Button.clear())
                    return

                database.edit_phone_number_config(event=event,
                                                  phone_number=phone_number,
                                                  sms_code=sms_code.text.strip(),
                                                  refr_token=inp[event.sender.id][phone_number].inpost.refr_token,
                                                  auth_token=inp[event.sender.id][phone_number].inpost.auth_token)
                await convo.send_message(
                    f'Congrats, you have successfully verified yourself. '
                    f'If this was your first time, {phone_number} is now your default one, '
                    f'if you want to change your current one to this just send `/set_default_phone_number {phone_number}`!'
                    f'\n\nHave fun using InPost services there!', buttons=Button.clear())
                return

            except asyncio.TimeoutError as e:
                logger.exception(e)
                await convo.send_message('Time has ran out, start initialization again!')
                convo.cancel()
            except PhoneNumberError as e:
                logger.exception(e)
                await convo.send_message(e.reason)
            except UnauthorizedError as e:
                logger.exception(e)
                await convo.send_message('You are not authorized')
            except UnidentifiedAPIError as e:
                logger.exception(e)
                await convo.send_message('Unexpected error occurred, call admin')
            except Exception as e:
                logger.exception(e)
                await convo.send_message('Bad things happened, call admin now!')

    @client.on(NewMessage(pattern='/start'))
    @client.on(NewMessage(pattern='/help'))
    async def start(event):
        await event.reply(welcome_message, buttons=[Button.request_phone('Log in via Telegram')])

    @client.on(NewMessage(pattern='/clear'))
    async def clear(event):
        await event.reply('You are welcome :D', buttons=Button.clear())

    @client.on(NewMessage(pattern='/parcel'))
    async def get_parcel(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if inp[event.sender.id].consent is None:
            await event.reply('You did not set your data collecting consent.'
                              '\n\nSend `/consent yes` if you want your data to be collected '
                              'in order to reduce data collected from inpost services and to help us develop this app.'
                              'If you refuse send `/consent no`.')

            return

        match len(event.text.strip().split(' ')):
            case 2:
                phone_number = inp[event.sender.id].default_phone_number.phone_number
            case 3:
                phone_number = inp[event.sender.id][event.text.strip().split(' ')[1].strip()].inpost.phone_number
            case _:
                await event.reply(not_enough_parameters_provided)
                return

        try:
            await send_pcg(event, inp, phone_number, ParcelType.TRACKED)

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
    @client.on(NewMessage(pattern='/sent'))
    @client.on(NewMessage(pattern='/returns'))
    @client.on(CallbackQuery(pattern=b'Pending Parcels'))
    @client.on(CallbackQuery(pattern=b'Delivered Parcels'))
    async def get_packages(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if inp[event.sender.id].consent is None:
            await event.reply('You did not set your data collecting consent.'
                              '\n\nSend `/consent yes` if you want your data to be collected '
                              'in order to reduce data collected from inpost services and to help us develop this app.'
                              'If you refuse send `/consent no`.')

            return

        status = None
        parcel_type = None

        match event:
            case CallbackQuery.Event():
                phone_number = inp[event.sender.id].default_phone_number.phone_number
                if phone_number is None:
                    await event.reply(f'Buttons works only with default phone number. '
                                      f'Please set up one before using them or type following command: '
                                      f'\n`/set_default_phone_number <phone_number>')
                    return

                if event.data == b'Pending Parcels':
                    status = pending_statuses
                    parcel_type = ParcelType.TRACKED
                elif event.data == b'Delivered Parcels':
                    status = ParcelStatus.DELIVERED
                    parcel_type = ParcelType.TRACKED

            case NewMessage.Event():
                if '/pending' in event.text:
                    status = pending_statuses
                    parcel_type = ParcelType.TRACKED
                elif '/delivered' in event.text:
                    status = ParcelStatus.DELIVERED
                    parcel_type = ParcelType.TRACKED
                elif '/all' in event.text:
                    status = None
                    parcel_type = ParcelType.TRACKED
                elif '/sent' in event.text:
                    status = None
                    parcel_type = ParcelType.SENT
                elif '/returns' in event.text:
                    status = None
                    parcel_type = ParcelType.RETURNS
                else:
                    return

                match len(event.text.strip().split(' ')):
                    case 1:
                        phone_number = inp[event.sender.id].default_phone_number.phone_number
                    case 2:
                        phone_number = inp[event.sender.id][
                            event.text.strip().split(' ')[1].strip()].inpost.phone_number
                    case _:
                        await event.reply(not_enough_parameters_provided)
                        return

                if phone_number is None:
                    await event.reply('This phone number does not exist or does not belong to you!')
                    return

            case _:
                logger.warning('Obtained other type of event than expected')
                await event.reply('Bad things happened, call admin now!')
                return

        try:
            await send_pcgs(event, inp, status, phone_number, parcel_type)

        except (NotAuthenticatedError, ParcelTypeError) as e:
            logger.exception(e)
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

        if inp[event.sender.id].consent is None:
            await event.reply('You did not set your data collecting consent.'
                              '\n\nSend `/consent yes` if you want your data to be collected '
                              'in order to reduce data collected from inpost services and to help us develop this app.'
                              'If you refuse send `/consent no`.')

            return

        async with client.conversation(event.sender.id) as convo:
            match len(event.text.strip().split(' ')):
                case 1:
                    phone_number = inp[event.sender.id].default_phone_number.phone_number
                case 2:
                    phone_number = inp[event.sender.id][event.text.strip().split(' ')[1].strip()].inpost.phone_number
                case _:
                    await event.reply(not_enough_parameters_provided)
                    return

            try:
                friends = await inp[event.sender.id][int(phone_number)].inpost.get_friends()
                for f in friends['friends']:
                    await convo.send_message(f'**Name**: {f["name"]}\n'
                                             f'**Phone number**: {f["phoneNumber"]}',
                                             buttons=[Button.inline('Remove')])  # TODO: implement

                for i in friends['invitations']:
                    await convo.send_message(friend_invitations_message_builder(friend=i),
                                             buttons=[Button.inline('Accept')])  # TODO: implement

            except asyncio.TimeoutError as e:
                logger.exception(e)
                await convo.send_message('Time has ran out, start initialization again!')
                convo.cancel()
            except PhoneNumberError as e:
                logger.exception(e)
                await convo.send_message(e.reason)
            except UnauthorizedError as e:
                logger.exception(e)
                await convo.send_message('You are not authorized')
            except UnidentifiedAPIError as e:
                logger.exception(e)
                await convo.send_message('Unexpected error occurred, call admin')
            except Exception as e:
                logger.exception(e)
                await convo.send_message('Bad things happened, call admin now!')

    @client.on(NewMessage(pattern='/consent'))
    async def consent(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if event.text[-2:] == 'no':
            database.set_user_consent(event=event, consent=False)
            inp[event.sender.id].consent = False
            await event.reply(f'Your data will not be collected from this moment. '
                              f'Anytime you change your mind just type\n\n`/consent yes`.')
        elif event.text[-3:] == 'yes':
            database.set_user_consent(event=event, consent=True)
            inp[event.sender.id].consent = True
            await event.reply(f'Your data will be collected from this moment.')
        else:
            await event.reply('Please specify your answer, available options are `yes` and `no`')

    @client.on(NewMessage(pattern='/set_default_phone_number'))
    async def set_default_phone_number(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if inp[event.sender.id].consent is None:
            await event.reply('You did not set your data collecting consent.'
                              '\n\nSend `/consent yes` if you want your data to be collected '
                              'in order to reduce data collected from inpost services and to help us develop this app.'
                              'If you refuse send `/consent no`.')

            return

        msg = event.text.strip().split(' ')

        match len(msg):
            case 2:
                if not msg[1].strip().isdigit() or len(msg[1].strip()) != 9:
                    await event.reply("Provided phone number contains non digit characters or is not 9 digits long")
                    return

                phone_number = int(msg[1].strip())
                database.edit_default_phone_number(event=event, default_phone_number=phone_number)
                inp[event.sender.id].default_phone_number = phone_number
                await event.reply(f'Default phone number is set to {phone_number}!')
            case _:
                await event.reply(not_enough_parameters_provided)
                return

    @client.on(NewMessage(pattern='/set_default_parcel_machine'))
    async def set_default_phone_number(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if inp[event.sender.id].consent is None:
            await event.reply('You did not set your data collecting consent.'
                              '\n\nSend `/consent yes` if you want your data to be collected '
                              'in order to reduce data collected from inpost services and to help us develop this app.'
                              'If you refuse send `/consent no`.')

            return

        msg = event.text.strip().split(' ')

        match len(msg):
            case 2:
                phone_number = inp[event.sender.id].default_phone_number.phone_number
                default_parcel_machine = msg[1].strip().upper()
            case 3:
                phone_number = inp[event.sender.id][event.text.strip().split(' ')[1].strip()].inpost.phone_number
                default_parcel_machine = msg[2].strip().upper()
            case _:
                await event.reply(not_enough_parameters_provided)
                return

        database.edit_default_parcel_machine(event=event, phone_number=phone_number,
                                             default_parcel_machine=default_parcel_machine)
        inp[event.sender.id][int(phone_number)].default_parcel_machine = default_parcel_machine
        await event.reply(f'Default parcel machine is set to {default_parcel_machine}! Remember, there is no '
                          f'verification to provided parcel machine code, so if typed incorrectly it just will not '
                          f'work!')

    @client.on(NewMessage(pattern='/set_geocheck'))
    async def set_geocheck(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if inp[event.sender.id].consent is None:
            await event.reply('You did not set your data collecting consent.'
                              '\n\nSend `/consent yes` if you want your data to be collected '
                              'in order to reduce data collected from inpost services and to help us develop this app.'
                              'If you refuse send `/consent no`.')

            return

        msg = event.text.strip().split(' ')

        match len(msg):
            case 2:
                phone_number = inp[event.sender.id].default_phone_number.phone_number
                geocheck = True if msg[1].strip().lower() == 'on' else False
            case 3:
                phone_number = inp[event.sender.id][event.text.strip().split(' ')[1].strip()].inpost.phone_number
                geocheck = True if msg[2].strip().lower() == 'on' else False
            case _:
                await event.reply(not_enough_parameters_provided)
                return

        database.edit_phone_number_config(event=event,
                                          phone_number=phone_number,
                                          geocheck=geocheck)
        inp[event.sender.id][int(phone_number)].geocheck = geocheck
        await event.reply('Geo checking is set!')

    @client.on(NewMessage(pattern='/set_airquality'))
    async def set_airquality(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if inp[event.sender.id].consent is None:
            await event.reply('You did not set your data collecting consent.'
                              '\n\nSend `/consent yes` if you want your data to be collected '
                              'in order to reduce data collected from inpost services and to help us develop this app.'
                              'If you refuse send `/consent no`.')

            return

        msg = event.text.strip().split(' ')

        match len(msg):
            case 2:
                phone_number = inp[event.sender.id].default_phone_number.phone_number
                airquality = True if msg[1].strip().lower() == 'on' else False
            case 3:
                phone_number = inp[event.sender.id][event.text.strip().split(' ')[1].strip()].inpost.phone_number
                airquality = True if msg[2].strip().lower() == 'on' else False
            case _:
                await event.reply(not_enough_parameters_provided)
                return

        database.edit_phone_number_config(event=event,
                                          phone_number=phone_number,
                                          airquality=airquality)
        inp[event.sender.id][int(phone_number)].airquality = airquality
        await event.reply('Airquality is set!')

    @client.on(NewMessage(pattern='/set_notifications'))
    async def set_notifications(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if inp[event.sender.id].consent is None:
            await event.reply('You did not set your data collecting consent.'
                              '\n\nSend `/consent yes` if you want your data to be collected '
                              'in order to reduce data collected from inpost services and to help us develop this app.'
                              'If you refuse send `/consent no`.')

            return

        msg = event.text.strip().split(' ')[1].strip()

        match len(event.text.strip().split(' ')):
            case 2:
                phone_number = inp[event.sender.id].default_phone_number.phone_number
                notifications = True if msg.lower() == 'on' else False
            case 3:
                phone_number = inp[event.sender.id][int(event.text.strip().split(' ')[1].strip())].inpost.phone_number
                notifications = True if msg.lower() == 'on' else False
            case _:
                await event.reply(not_enough_parameters_provided)
                return

        database.edit_phone_number_config(event=event,
                                          phone_number=phone_number,
                                          notifications=notifications)
        inp[event.sender.id][int(phone_number)].notifications = notifications
        await event.reply(f'Notifications are set to {msg.upper()}!')

    @client.on(NewMessage(pattern='/qrcode'))
    @client.on(CallbackQuery(pattern=b'QR Code'))
    async def send_qr_code(event):
        # TODO: Add database qr code get if user consent
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if inp[event.sender.id].consent is None:
            await event.reply('You did not set your data collecting consent.'
                              '\n\nSend `/consent yes` if you want your data to be collected '
                              'in order to reduce data collected from inpost services and to help us develop this app.'
                              'If you refuse send `/consent no`.')

            return

        match event:
            case NewMessage.Event():
                if not event.message.is_reply:
                    await event.reply('You must reply to message with desired parcel!')
                    return

                shipment_number, phone_number = await get_shipment_and_phone_number_from_reply(event, inp)

                if phone_number is None:
                    await event.reply('This phone number does not exist or does not belong to you!')
                    return

            case CallbackQuery.Event():
                if inp[event.sender.id].default_phone_number is None:
                    await event.reply(use_command_as_reply_message_builder("/qrcode"))
                    return

                shipment_number, phone_number = await get_shipment_and_phone_number_from_button(event, inp)
            case _:
                logger.warning('Obtained other type of event than expected')
                await event.reply('Bad things happened, call admin now!')
                return

        if shipment_number is None:
            await event.reply('No shipment number!')
            return

        try:
            await send_qrc(event, inp, phone_number, shipment_number)

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
    @client.on(NewMessage(pattern='/opencode'))
    async def show_open_code(event):
        # TODO: Add database open code get if user consent
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if inp[event.sender.id].consent is None:
            await event.reply('You did not set your data collecting consent.'
                              '\n\nSend `/consent yes` if you want your data to be collected '
                              'in order to reduce data collected from inpost services and to help us develop this app.'
                              'If you refuse send `/consent no`.')

            return

        match event:
            case NewMessage.Event():
                if not event.message.is_reply:
                    await event.reply('You must reply to message with desired parcel!')
                    return

                shipment_number, phone_number = await get_shipment_and_phone_number_from_reply(event, inp)

                if phone_number is None:
                    await event.reply('This phone number does not exist or does not belong to you!')
                    return

            case CallbackQuery.Event():
                if inp[event.sender.id].default_phone_number is None:
                    await event.reply(use_command_as_reply_message_builder("/opencode"))
                    return
                shipment_number, phone_number = await get_shipment_and_phone_number_from_button(event, inp)
            case _:
                logger.warning('Obtained other type of event than expected')
                await event.reply('Bad things happened, call admin now!')
                return

        if shipment_number is None:
            await event.reply('No shipment number!')
            return

        try:
            await show_oc(event, inp, phone_number, shipment_number)
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
    @client.on(NewMessage(pattern='/open'))
    async def open_compartment(event):
        # TODO: Add database check if user consent if parcel is ParcelType.TRACKED using /open instead of button
        # TODO: Add database parcel get if user consent
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if inp[event.sender.id].consent is None:
            await event.reply('You did not set your data collecting consent.'
                              '\n\nSend `/consent yes` if you want your data to be collected '
                              'in order to reduce data collected from inpost services and to help us develop this app.'
                              'If you refuse send `/consent no`.')

            return

        match event:
            case NewMessage.Event():
                if not event.message.is_reply:
                    await event.reply('You must reply to message with desired parcel!')
                    return

                shipment_number, phone_number = await get_shipment_and_phone_number_from_reply(event, inp)
            case CallbackQuery.Event():
                if inp[event.sender.id].default_phone_number is None:
                    await event.reply(use_command_as_reply_message_builder("/open"))
                    return
                shipment_number, phone_number = await get_shipment_and_phone_number_from_button(event, inp)
            case _:
                await event.reply('Bad things happened, call admin now!')
                return

        try:
            p: Parcel = await inp[event.sender.id][phone_number].inpost.get_parcel(shipment_number=shipment_number,
                                                                                   parse=True)

            if p.status == ParcelStatus.DELIVERED:
                await event.reply('Parcel already delivered!')
                return

            async with client.conversation(event.sender.id) as convo:
                if inp[event.sender.id][phone_number].geocheck or inp[event.sender.id][phone_number].default_parcel_machine != p.pickup_point.name:
                    if inp[event.sender.id][phone_number].location_time.shift(minutes=+2) < arrow.now(tz='Europe/Warsaw'):
                        await convo.send_message(
                            'Please share your location so I can check whether you are near parcel machine or not.',
                            buttons=[Button.request_location('Confirm localization')])

                        geo = await convo.get_response(timeout=30)
                        if not geo.geo:
                            await convo.send_message('Your message does not contain geolocation, start opening again!')
                            return

                        inp[event.sender.id][phone_number].location_time = arrow.now(tz='Europe/Warsaw')
                        inp[event.sender.id][phone_number].location = (geo.geo.lat, geo.geo.long)

                        status = await confirm_location(event=geo, inp=inp, parcel_obj=p)

                        match status:
                            case 'IN RANGE':
                                await convo.send_message('You are in range. Are you sure to open?',
                                                         buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])
                            case 'OUT OF RANGE':
                                await convo.send_message(out_of_range_message_builder(parcel=p),
                                                         buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])
                            case 'NOT READY':
                                await convo.send_message(f'Parcel is not ready for pick up! Status: {p.status}')
                            case 'DELIVERED':
                                await convo.send_message('Parcel has been already delivered!')
                                return

                    else:
                        inp[event.sender.id][phone_number].location_time_lock = True  # gotta do this in case someone would want to hit 'open compartment' button just on the edge, otherwise hitting 'yes' button could be davson-insensitive
                        await convo.send_message(f'Less than 2 minutes have passed since the last compartment opening, '
                                                 f'you were in range of {p.pickup_point.name} parcel machine, '
                                                 f'assuming you still are and skipping location verification.'
                                                 f'\nAre you sure to open?',
                                                 buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])
                else:
                    await convo.send_message(f'You have location checking off or this parcel is in default parcel '
                                             f'machine, skipping! You can turn location checking on by sending:\n '
                                             f'`/set_geocheck {phone_number} On`!\n\nAre you sure to open?',
                                             buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])

                decision = await convo.wait_event(event=CallbackQuery(), timeout=30)

                match decision.data:
                    case b'Yes!':
                        if p_ := await open_comp(event, inp, phone_number, p):
                            await decision.reply(open_comp_message_builder(parcel=p_), buttons=Button.clear())
                    case b'Hell no!':
                        await decision.reply('Fine, compartment remains closed!', buttons=Button.clear())
                    case _:
                        await decision.reply('Unrecognizable decision made, please start opening compartment '
                                             'again!')

                return

        except asyncio.TimeoutError as e:
            logger.exception(e)
            await convo.send_message('Time has ran out, please start opening compartment again!')
            convo.cancel()
        except PhoneNumberError as e:
            logger.exception(e)
            await convo.send_message(e.reason)
        except UnauthorizedError as e:
            logger.exception(e)
            await convo.send_message('You are not authorized')
        except UnidentifiedAPIError as e:
            logger.exception(e)
            await convo.send_message('Unexpected error occurred, call admin')
        except Exception as e:
            logger.exception(e)
            await convo.send_message('Bad things happened, call admin now!')

    @client.on(CallbackQuery(pattern=b'Details'))
    @client.on(NewMessage(pattern='/details'))
    async def details(event):
        # TODO: Add database check if user consent what ParcelType is parcel
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if inp[event.sender.id].consent is None:
            await event.reply('You did not set your data collecting consent.'
                              '\n\nSend `/consent yes` if you want your data to be collected '
                              'in order to reduce data collected from inpost services and to help us develop this app.'
                              'If you refuse send `/consent no`.')

            return

        match event:
            case NewMessage.Event():
                if not event.message.is_reply:
                    await event.reply('You must reply to message with desired parcel!')
                    return

                shipment_number, phone_number = await get_shipment_and_phone_number_from_reply(event, inp)

                if phone_number is None:
                    await event.reply('This phone number does not exist or does not belong to you!')
                    return

            case CallbackQuery.Event():
                if inp[event.sender.id].default_phone_number is None:
                    await event.reply(use_command_as_reply_message_builder("/details"))
                    return

                shipment_number, phone_number = await get_shipment_and_phone_number_from_button(event, inp)
            case _:
                logger.warning('Obtained other type of event than expected')
                await event.reply('Bad things happened, call admin now!')
                return

        if shipment_number is None:
            await event.reply('No shipment number!')
            return

        try:
            await send_details(event, inp, shipment_number, ParcelType.TRACKED)

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
    @client.on(NewMessage(pattern='/share'))
    async def share_parcel(event):
        # TODO: Add database check if user consent what ParcelType is parcel
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if inp[event.sender.id].consent is None:
            await event.reply('You did not set your data collecting consent.'
                              '\n\nSend `/consent yes` if you want your data to be collected '
                              'in order to reduce data collected from inpost services and to help us develop this app.'
                              'If you refuse send `/consent no`.')

            return

        match event:
            case NewMessage.Event():
                if not event.message.is_reply:
                    await event.reply('You must reply to message with desired parcel!')
                    return

                shipment_number, phone_number = await get_shipment_and_phone_number_from_reply(event, inp)

                if phone_number is None:
                    await event.reply('This phone number does not exist or does not belong to you!')
                    return

            case CallbackQuery.Event():
                if inp[event.sender.id].default_phone_number is None:
                    await event.reply(use_command_as_reply_message_builder("/share"))
                    return

                shipment_number, phone_number = await get_shipment_and_phone_number_from_button(event, inp)
            case _:
                logger.warning('Obtained other type of event than expected')
                await event.reply('Bad things happened, call admin now!')
                return

        if shipment_number is None:
            await event.reply('No shipment number!')
            return

        async with client.conversation(event.sender.id) as convo:
            try:
                friends = await inp[event.sender.id][phone_number].inpost.get_parcel_friends(
                    shipment_number=shipment_number, parse=True)

                if not await is_parcel_owner(inp=inp, shipment_number=shipment_number, phone_number=phone_number,
                                             event=event,
                                             parcel_type=ParcelType.TRACKED):
                    await event.reply('This parcel does not belong to you, cannot share it')
                    return

                if len(friends['friends']) == 0:
                    await event.reply('This parcel has no people it can be shared with!')
                    return

                if isinstance(event, CallbackQuery.Event):
                    for f in friends['friends']:
                        await convo.send_message(f'**Name**: {f.name}\n'
                                                 f'**Phone number**: {f.phone_number}',
                                                 buttons=[Button.inline('Dispatch')])

                    await event.reply('Fine, now pick a friend to share parcel to and press `Dispatch` button')
                    friend = await convo.wait_event(CallbackQuery(pattern='Dispatch'), timeout=30)
                    friend_event = friend
                    friend = await friend.get_message()

                elif isinstance(event, NewMessage.Event):
                    for f in friends['friends']:
                        await convo.send_message(f'**Name**: {f.name}\n'
                                                 f'**Phone number**: {f.phone_number}')

                    await convo.send_message('Fine, now pick a friend to share parcel to and '
                                             'send a reply to him/her with `/dispatch`')
                    friend = await convo.get_response(timeout=30)
                    if not friend.is_reply:
                        await friend.reply('You must reply to message with desired friend, start sharing again!')
                        return

                    friend_event = friend
                    friend = await friend.get_reply_message()

                friend = friend.raw_text.split('\n')
                friend = [friend[0].split(':')[1].strip(), friend[1].split(':')[1].strip()]

                uuid = (
                    next((f for f in friends['friends'] if (f.name == friend[0] and f.phone_number == friend[1])))).uuid
                if await inp[event.sender.id][phone_number].inpost.share_parcel(uuid=uuid,
                                                                                shipment_number=shipment_number):
                    await friend_event.reply('Parcel shared!')
                else:
                    await friend_event.reply('Not shared, try again!')

            except asyncio.TimeoutError as e:
                logger.exception(e)
                await convo.send_message('Time has ran out, please start sharing parcel again!')
                convo.cancel()
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
            finally:
                convo.cancel()

    async with client:
        print("Good morning!")
        await client.run_until_disconnected()


if __name__ == '__main__':
    with open("config.yml", 'r') as f:
        config = yaml.safe_load(f)
        asyncio.run(main(config=config, inp=dict()))