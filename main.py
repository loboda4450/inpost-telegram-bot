import asyncio
import logging
from typing import Dict

import arrow
import yaml
from inpost.static import ParcelStatus
from inpost.static.exceptions import *
from telethon import TelegramClient, Button
from telethon.events import NewMessage, CallbackQuery

import database
from constants import pending_statuses, welcome_message, friend_invitations_message_builder, \
    out_of_range_message_builder, open_comp_message_builder
from utils import get_phone_number, send_pcgs, send_qrc, show_oc, open_comp, \
    send_details, BotUserPhoneNumberConfig, BotUserConfig, send_pcg, init_phone_number, confirm_location


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

    inp = {user: BotUserConfig(default_phone_number=database.get_default_phone_number(userid=user).phone_number,
                         phone_numbers=users[user]) for user in users}

    await client.start(bot_token=config['bot_token'])
    print("Started")

    @client.on(NewMessage(func=lambda e: e.text.startswith('/init') or e.message.contact is not None))
    async def init_user(event):
        async with client.conversation(event.sender.id) as convo:
            phone_number = await init_phone_number(event=event)
            if phone_number is None:
                await convo.send_message('Something is wrong with provided phone number. Start initialization again.',
                                         buttons=Button.clear())
                convo.cancel()
                return

            if event.sender.id not in inp:
                inp.update({event.sender.id: BotUserConfig()})
                database.add_user(event=event)

            if phone_number in inp[event.sender.id]:
                await convo.send_message('You have initialized this phone number before, cancelling!',
                                         buttons=Button.clear())
                convo.cancel()
                return

            inp[event.sender.id].phone_numbers.update({phone_number: BotUserPhoneNumberConfig(**{
                'airquality': True,
                'default_parcel_machine': None,
                'geocheck': True,
                'notifications': True})})
            inp[event.sender.id][phone_number].inpost.set_phone_number(phone_number=phone_number)

            try:
                if database.phone_number_exists(phone_number=phone_number):
                    await convo.send_message('Phone number already exist!')
                    return

                database.add_phone_number_config(event=event, phone_number=phone_number)

                if not await inp[event.sender.id][phone_number].inpost.send_sms_code():
                    await convo.send_message('Could not send sms code! Start initializing again!')
                    return

                await convo.send_message('Phone number accepted, send me sms code that InPost '
                                         'sent to provided phone number! You have 60 seconds from now!')
                sms_code = await convo.get_response(timeout=60)

                if not (len(sms_code.text.strip()) == 6 and sms_code.text.strip().isdigit()):
                    await convo.send_message(
                        'Something is wrong with provided sms code! Start initialization again.',
                        buttons=Button.clear())
                    return

                if not await inp[event.sender.id][phone_number].inpost.confirm_sms_code(
                        sms_code=sms_code.text.strip()):
                    await convo.send_message('Something went wrong! Start initialization again.')
                    return

                database.edit_phone_number_config(event=event,
                                                  phone_number=phone_number,
                                                  sms_code=sms_code.text.strip(),
                                                  refr_token=inp[event.sender.id][phone_number].inpost.refr_token,
                                                  auth_token=inp[event.sender.id][phone_number].inpost.auth_token)
                await convo.send_message(
                    'Congrats, you have successfully verified yourself. Have fun using InPost services there!')
                return

            except asyncio.TimeoutError as e:
                logger.exception(e)
                await convo.send_message('Time has ran out, start initialization again!')
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
            finally:
                convo.cancel()  # no need to add convo cancellation in every case inside try statement

    @client.on(NewMessage(pattern='/start'))
    async def start(event):
        await event.reply(welcome_message, buttons=[Button.request_phone('Log in via Telegram')])

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
            if await inp[event.sender.id][phone_number].inpost.refresh_token():
                database.edit_phone_number_config(event=event,
                                                  refr_token=inp[event.sender.id][phone_number].inpost.refr_token)
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
    @client.on(CallbackQuery(pattern=b'Delivered Parcels'))  # TODO: Because of multiple patterns and request types gotta think about phone number selection
    async def get_packages(event):
        if event.sender.id not in inp:
            await event.reply('You are not initialized')
            return

        if len(event.text.strip().split(' ')) == 1:
            phone_number = inp[event.sender.id].default_phone_number.inpost.phone_number
        elif len(event.text.strip().split(' ')) == 2:
            phone_number = inp[event.sender.id][event.text.strip().split(' ')[1]].inpost.phone_number
        else:
            await event.reply('Something is wrong with phone number!')
            return

        if phone_number is None:
            await event.reply('This phone number does not exist or does not belong to you!')
            return

        status = None
        if isinstance(event, CallbackQuery.Event):
            if event.data == b'Pending Parcels':
                status = pending_statuses
            elif event.data == b'Delivered Parcels':
                status = ParcelStatus.DELIVERED
        elif isinstance(event, NewMessage.Event):
            if '/pending' in event.text:
                status = pending_statuses
            elif '/delivered' in event.text:
                status = ParcelStatus.DELIVERED
            elif '/all' in event.text:
                status = None
            else:
                return

        try:
            await send_pcgs(event, inp, status, phone_number)

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

        phone_number = await get_phone_number(inp, event)
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        try:
            friends = await inp[event.sender.id][phone_number].inpost.get_friends()
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

            friends = await inp[event.sender.id][phone_number].inpost.get_parcel_friends(
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

        phone_number = event.text.split()[1].strip()
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        if not database.user_is_phone_number_owner(event=event):
            await event.reply(f'You are not the owner of {phone_number}')
            return

        database.edit_default_phone_number(event=event, default_phone_number=phone_number)
        inp[event.sender.id].default_phone_number = phone_number
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

    @client.on(CallbackQuery(pattern=b'QR Code'))  # TODO: add support for NewMessage type (pattern /qrcode <phone_number> <shipment_number>)
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
            return

        phone_number = await get_phone_number(inp, event)
        if phone_number is None:
            await event.reply('No phone number provided!')
            return

        try:
            msg = await event.get_message()
            shipment_number = \
                (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()
            p: Parcel = await inp[event.sender.id][phone_number].inpost.get_parcel(shipment_number=shipment_number,
                                                                                      parse=True)

            async with client.conversation(event.sender.id) as convo:
                if inp[event.sender.id][phone_number]['config']['geocheck']:
                    if inp[event.sender.id][phone_number]['config'].location_time.shift(minutes=+2) < arrow.now(
                            tz='Europe/Warsaw'):
                        await convo.send_message(
                            'Please share your location so I can check whether you are near parcel machine or not.',
                            buttons=[Button.request_location('Confirm localization')])

                        geo = await convo.get_response(timeout=30)
                        if not geo.message.geo:
                            await convo.send_message('Your message does not contain geolocation, start opening again!')
                            return

                        inp[event.sender.id][phone_number]['config'].location_time = arrow.now(tz='Europe/Warsaw')
                        inp[event.sender.id][phone_number]['config'].location = (
                            geo.message.geo.lat, geo.message.geo.long)

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

                    else:
                        inp[event.sender.id][phone_number][
                            'config'].location_time_lock = True  # gotta do this in case someone would want to hit 'open compartment' button just on the edge, otherwise hitting 'yes' button could be davson-insensitive
                        await convo.send_message('Less than 2 minutes have passed since the last compartment opening, '
                                                 'skipping location verification.\nAre you sure to open?',
                                                 buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])
                else:
                    await convo.send_message(f'You have location checking off, skipping! '
                                             f'You can turn it on by sending `/set_geocheck {phone_number} On`!\n\n'
                                             f'Are you sure to open?',
                                             buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])

                decision = await convo.wait_event(event=CallbackQuery.Event, timeout=30)

                match decision.data:
                    case b'Yes!':
                        await open_comp(event, inp, p)
                        await convo.send_message(open_comp_message_builder(parcel=p), buttons=Button.clear())
                    case b'Hell no!':
                        await convo.send_message('Fine, compartment remains closed!', buttons=Button.clear())

                return

        except asyncio.TimeoutError as e:
            logger.exception(e)
            await convo.send_message('Time has ran out, please start opening compartment again!')
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
        finally:
            convo.cancel()  # no need to add convo cancellation in every case inside try statement

    @client.on(CallbackQuery(pattern=b'Details'))  # TODO: Add support for NewMessage type (pattern /details <phone_number> <shipment_number>)
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

    @client.on(CallbackQuery(pattern=b'Share'))  # TODO: Refactor to conversation, remember about can_share attribute inside parcel
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

            friends = await inp[event.sender.id][phone_number].inpost.get_parcel_friends(
                shipment_number=shipment_number, parse=True)
            uuid = (next((f for f in friends['friends'] if (f.name == friend[0] and f.phone_number == friend[1])))).uuid
            if await inp[event.sender.id][phone_number].inpost.share_parcel(uuid=uuid,
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
