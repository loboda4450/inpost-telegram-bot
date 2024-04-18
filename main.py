import asyncio
import logging

import yaml
from inpost import Inpost
from inpost.static import ParcelStatus, ParcelType, PhoneNumberError, UnauthorizedError, UnidentifiedAPIError, \
    NotAuthenticatedError, NotFoundError, ParcelTypeError, Parcel
from pony.orm import count
from telethon import TelegramClient, Button
from telethon.events import NewMessage, CallbackQuery

from constants import pending_statuses, welcome_message
from database import PhoneNumberConfig, add_user, add_phone_number_config, \
    edit_default_phone_number, get_inpost_obj, edit_phone_number_config, get_default_phone_number, \
    count_user_phone_numbers, get_user_phone_numbers, db_session, user_exists, get_user_last_parcel_with_shipment_number
from utils import send_pcgs, send_qrc, show_oc, send_details, send_pcg, init_phone_number, share_parcel, \
    open_compartment, \
    get_shipment_number_from_button


async def main(config):
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=config['log_level'])
    logger = logging.getLogger(__name__)
    client = TelegramClient(**config['telethon_settings'])
    print("Starting")

    if not config['bot_token']:
        raise Exception('No bot token provided')

    await client.start(bot_token=config['bot_token'])
    print("Started")

    @client.on(CallbackQuery(pattern='Me'))
    async def get_me(event):
        if not user_exists(userid=event.sender.id):
            await event.reply('You are not initialized')
            return

        await event.reply('Sorry, it is not implemented yet :<')

        # for phone_number in PhoneNumberConfig.select(user=event.sender.id):
        #     await event.reply(
        #         f'**Phone number**: `{phone_number.prefix} '
        #         f'{str(phone_number.phone_number)[:3] + "***" + str(phone_number.phone_number)[6:]}`'
        #         f'\n**Default parcel machine**: `'
        #         f'{phone_number.default_parcel_machine if phone_number.default_parcel_machine != "" else "Not set"}`'
        #         f'\n**Notifications**: `{phone_number.notifications}`'
        #         f'\n**Geo checking**: `{phone_number.geocheck}`'
        #         f'\n**Air quality**: `{phone_number.airquality}`')

    @client.on(NewMessage(func=lambda e: e.text.startswith('/init') or e.message.contact is not None))
    async def init_user(event):
        with db_session:
            async with client.conversation(event.sender.id) as convo:
                prefix, phone_number = await init_phone_number(event=event)
                try:
                    if phone_number is None is prefix:
                        await convo.send_message(
                            'Something is wrong with provided phone number. Start initialization again.',
                            buttons=Button.clear())
                        convo.cancel()
                        return

                    if not user_exists(userid=event.sender.id):
                        add_user(event=event)

                    pn: PhoneNumberConfig = PhoneNumberConfig.get(phone_number=phone_number)

                    if pn is not None:
                        if not event.sender.id == pn.user.userid:
                            await convo.send_message(
                                "Phone number already exist and you are not it's owner, cancelling!",
                                buttons=Button.clear())
                            convo.cancel()
                            return

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
                        add_phone_number_config(event=event, prefix=prefix, phone_number=phone_number)

                        if count(s for s in PhoneNumberConfig if s.user.userid == event.sender.id) == 1:
                            edit_default_phone_number(event=event, default_phone_number=phone_number)

                    inp = Inpost(**get_inpost_obj(userid=event.sender.id, phone_number=phone_number))

                    if not await inp.send_sms_code():
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

                    if not await inp.confirm_sms_code(sms_code=sms_code.text.strip()):
                        await convo.send_message('Something went wrong! Start initialization again.',
                                                 buttons=Button.clear())

                        return

                    edit_phone_number_config(event=event,
                                             phone_number=phone_number,
                                             sms_code=sms_code.text.strip(),
                                             refr_token=inp.refr_token,
                                             auth_token=inp.auth_token)
                    await convo.send_message(
                        f'Congrats, you have successfully verified yourself. '
                        f'If this was your first time, `{prefix} {phone_number}` is now your default one!'
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
                finally:
                    convo.cancel()
                    await inp.sess.close()
                    del inp
                    return

    @client.on(NewMessage(pattern='/start'))
    @client.on(NewMessage(pattern='/help'))
    async def start(event):
        await event.reply(welcome_message, buttons=[Button.request_phone('Log in via Telegram')])

    @client.on(NewMessage(pattern='/clear'))
    async def clear(event):
        await event.reply('You are welcome :D', buttons=Button.clear())

    @client.on(NewMessage(pattern='/menu'))
    async def send_menu(event):
        if not user_exists(userid=event.sender.id):
            await event.reply('You are not initialized')
            return

        await event.reply('Hello, what you want to do? :)',
                          buttons=[[Button.inline('Parcels'), Button.inline('Friends')],
                                   [Button.inline('Me'), Button.inline('Consent')]])

    @client.on(CallbackQuery(pattern=b'Parcels'))
    async def send_menu_parcels(event):
        if not user_exists(userid=event.sender.id):
            await event.reply('You are not initialized')
            return

        await event.reply('Select parcel type',
                          buttons=[[Button.inline('Pending'), Button.inline('Sent')],
                                   [Button.inline('Returns'), Button.inline('All')],
                                   [Button.inline('From shipment number')]
                                   ])

    @client.on(CallbackQuery(pattern=b'Friends'))
    async def send_menu_friends(event):
        if not user_exists(userid=event.sender.id):
            await event.reply('You are not initialized')
            return

        await event.reply('Sorry, it is not implemented yet :<')

    @client.on(CallbackQuery(pattern='From shipment number'))
    async def get_parcel(event):
        if not user_exists(userid=event.sender.id):
            await event.reply('You are not initialized')
            return

        async with client.conversation(event.sender.id) as convo:
            try:
                if count_user_phone_numbers(userid=event.sender.id) == 1:
                    phone_number = get_default_phone_number(userid=event.sender.id).phone_number
                else:
                    await convo.send_message('Please choose phone number',
                                             buttons=[Button.inline(f'{phone.phone_number}') for phone in
                                                      get_user_phone_numbers(userid=event.sender.id)])

                    phone_number = await convo.wait_event(event=CallbackQuery(), timeout=30)
                    phone_number = phone_number.data.decode("utf-8")

                await convo.send_message('Please send me a shipment number within 60 seconds')
                shipment_number = await convo.wait_event(event=NewMessage(), timeout=60)

            except asyncio.TimeoutError as e:
                logger.exception(e)
                await convo.send_message('Time has ran out, please start opening compartment again!')
                convo.cancel()

                return

            try:
                inp = Inpost(**get_inpost_obj(userid=event.sender.id, phone_number=phone_number))
                await send_pcg(shipment_number, inp, phone_number, ParcelType.TRACKED)

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
            finally:
                convo.cancel()
                await inp.sess.close()
                del inp
                return

    @client.on(CallbackQuery(pattern=b'Pending'))
    @client.on(CallbackQuery(pattern=b'Delivered'))
    @client.on(CallbackQuery(pattern=b'Sent'))
    @client.on(CallbackQuery(pattern=b'Returns'))
    @client.on(CallbackQuery(pattern=b'All'))
    async def get_packages(event):
        if not user_exists(userid=event.sender.id):
            await event.reply('You are not initialized')
            return

        match event.data:
            case b'Pending':
                status = pending_statuses
                parcel_type = ParcelType.TRACKED
            case b'Delivered':
                status = ParcelStatus.DELIVERED
                parcel_type = ParcelType.TRACKED
            case b'Sent':
                status = None
                parcel_type = ParcelType.SENT
            case b'Returns':
                status = None
                parcel_type = ParcelType.RETURNS
            case b'All':
                status = None
                parcel_type = ParcelType.TRACKED
            case _:
                await event.reply('Unreckognized option selected')
                return

        async with client.conversation(event.sender.id) as convo:
            try:
                if count_user_phone_numbers(userid=event.sender.id) == 1:
                    phone_number = get_default_phone_number(userid=event.sender.id).phone_number
                else:
                    await convo.send_message('Please choose phone number',
                                             buttons=[Button.inline(f'{phone.phone_number}') for phone in
                                                      get_user_phone_numbers(userid=event.sender.id)])

                    phone_number = await convo.wait_event(event=CallbackQuery(), timeout=30)
                    phone_number = phone_number.data.decode("utf-8").strip()

                inp = Inpost(**get_inpost_obj(userid=event.sender.id, phone_number=phone_number))

                await send_pcgs(event, inp, status, phone_number, parcel_type)

            except asyncio.TimeoutError as e:
                logger.exception(e)
                await convo.send_message('Time has ran out, please start opening compartment again!',
                                         buttons=[Button.clear()])

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
            finally:
                convo.cancel()
                await inp.sess.close()
                del inp
                return

    @client.on(CallbackQuery(pattern='Open Code'))
    @client.on(CallbackQuery(pattern='QR Code'))
    @client.on(CallbackQuery(pattern='Details'))
    @client.on(CallbackQuery(pattern='Share'))
    @client.on(CallbackQuery(pattern='Open Compartment'))
    async def handle_parcel(event):
        async with client.conversation(event.sender.id) as convo:
            try:
                shipment_number = await get_shipment_number_from_button(event)
                raw_parcel = get_user_last_parcel_with_shipment_number(event.sender.id, shipment_number)
                inp = Inpost(**get_inpost_obj(userid=event.sender.id,
                                              phone_number=raw_parcel.phone_number.phone_number))
                parcel = Parcel(raw_parcel.parcel, logging.getLogger('Inpost'))
                match event.data:
                    case b'Open Code':
                        await show_oc(event, parcel, inp)
                    case b'QR Code':
                        await send_qrc(event, parcel, inp)
                    case b'Details':
                        await send_details(event, inp, parcel)
                    case b'Share':
                        await share_parcel(event, convo, inp, parcel.shipment_number)
                    case b'Open Compartment':
                        await open_compartment(event, convo, inp, parcel, ParcelType[raw_parcel.ptype])
                    case _:
                        await convo.send_message('Time has ran out, please start opening compartment again!',
                                                 buttons=[Button.clear()])

            except asyncio.TimeoutError as e:
                logger.exception(e)
                await convo.send_message('Time has ran out, please start opening compartment again!',
                                         buttons=[Button.clear()])

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
            finally:
                convo.cancel()
                await inp.sess.close()
                del inp
                return

    # @client.on(NewMessage(pattern='/friends'))
    # async def send_friends(event):
    #     if event.sender.id not in inp:
    #         await event.reply('You are not initialized')
    #         return
    #
    #     if inp[event.sender.id].consent is None:
    #         await event.reply('You did not set your data collecting consent.'
    #                           '\n\nSend `/consent yes` if you want your data to be collected '
    #                           'in order to reduce data collected from inpost services and to help us develop this app.'
    #                           'If you refuse send `/consent no`.')
    #
    #         return
    #
    #     async with client.conversation(event.sender.id) as convo:
    #         match len(event.text.strip().split(' ')):
    #             case 1:
    #                 phone_number = inp[event.sender.id].default_phone_number.phone_number
    #             case 2:
    #                 phone_number = inp[event.sender.id][event.text.strip().split(' ')[1].strip()].inpost.phone_number
    #             case _:
    #                 await event.reply(not_enough_parameters_provided)
    #                 return
    #
    #         try:
    #             friends = await inp[event.sender.id][int(phone_number)].inpost.get_friends()
    #             for f in friends['friends']:
    #                 await convo.send_message(f'**Name**: {f["name"]}\n'
    #                                          f'**Phone number**: {f["phoneNumber"]}',
    #                                          buttons=[Button.inline('Remove')])  # TODO: implement
    #
    #             for i in friends['invitations']:
    #                 await convo.send_message(friend_invitations_message_builder(friend=i),
    #                                          buttons=[Button.inline('Accept')])  # TODO: implement
    #
    #         except asyncio.TimeoutError as e:
    #             logger.exception(e)
    #             await convo.send_message('Time has ran out, start initialization again!')
    #             convo.cancel()
    #         except PhoneNumberError as e:
    #             logger.exception(e)
    #             await convo.send_message(e.reason)
    #         except UnauthorizedError as e:
    #             logger.exception(e)
    #             await convo.send_message('You are not authorized')
    #         except UnidentifiedAPIError as e:
    #             logger.exception(e)
    #             await convo.send_message('Unexpected error occurred, call admin')
    #         except Exception as e:
    #             logger.exception(e)
    #             await convo.send_message('Bad things happened, call admin now!')
    #
    #
    # @client.on(NewMessage(pattern='/set_default_phone_number'))
    # async def set_default_phone_number(event):
    #     if event.sender.id not in inp:
    #         await event.reply('You are not initialized')
    #         return
    #
    #     if inp[event.sender.id].consent is None:
    #         await event.reply('You did not set your data collecting consent.'
    #                           '\n\nSend `/consent yes` if you want your data to be collected '
    #                           'in order to reduce data collected from inpost services and to help us develop this app.'
    #                           'If you refuse send `/consent no`.')
    #
    #         return
    #
    #     msg = event.text.strip().split(' ')
    #
    #     match len(msg):
    #         case 2:
    #             if not msg[1].strip().isdigit() or len(msg[1].strip()) != 9:
    #                 await event.reply("Provided phone number contains non digit characters or is not 9 digits long")
    #                 return
    #
    #             phone_number = int(msg[1].strip())
    #             database.edit_default_phone_number(event=event, default_phone_number=phone_number)
    #             inp[event.sender.id].default_phone_number = phone_number
    #             await event.reply(f'Default phone number is set to {phone_number}!')
    #         case _:
    #             await event.reply(not_enough_parameters_provided)
    #             return
    #
    # @client.on(NewMessage(pattern='/set_default_parcel_machine'))
    # async def set_default_phone_number(event):
    #     if event.sender.id not in inp:
    #         await event.reply('You are not initialized')
    #         return
    #
    #     if inp[event.sender.id].consent is None:
    #         await event.reply('You did not set your data collecting consent.'
    #                           '\n\nSend `/consent yes` if you want your data to be collected '
    #                           'in order to reduce data collected from inpost services and to help us develop this app.'
    #                           'If you refuse send `/consent no`.')
    #
    #         return
    #
    #     msg = event.text.strip().split(' ')
    #
    #     match len(msg):
    #         case 2:
    #             phone_number = inp[event.sender.id].default_phone_number.phone_number
    #             default_parcel_machine = msg[1].strip().upper()
    #         case 3:
    #             phone_number = inp[event.sender.id][event.text.strip().split(' ')[1].strip()].inpost.phone_number
    #             default_parcel_machine = msg[2].strip().upper()
    #         case _:
    #             await event.reply(not_enough_parameters_provided)
    #             return
    #
    #     database.edit_default_parcel_machine(event=event, phone_number=phone_number,
    #                                          default_parcel_machine=default_parcel_machine)
    #     inp[event.sender.id][int(phone_number)].default_parcel_machine = default_parcel_machine
    #     await event.reply(f'Default parcel machine is set to {default_parcel_machine}! Remember, there is no '
    #                       f'verification to provided parcel machine code, so if typed incorrectly it just will not '
    #                       f'work!')
    #
    # @client.on(NewMessage(pattern='/set_geocheck'))
    # async def set_geocheck(event):
    #     if event.sender.id not in inp:
    #         await event.reply('You are not initialized')
    #         return
    #
    #     if inp[event.sender.id].consent is None:
    #         await event.reply('You did not set your data collecting consent.'
    #                           '\n\nSend `/consent yes` if you want your data to be collected '
    #                           'in order to reduce data collected from inpost services and to help us develop this app.'
    #                           'If you refuse send `/consent no`.')
    #
    #         return
    #
    #     msg = event.text.strip().split(' ')
    #
    #     match len(msg):
    #         case 2:
    #             phone_number = inp[event.sender.id].default_phone_number.phone_number
    #             geocheck = True if msg[1].strip().lower() == 'on' else False
    #         case 3:
    #             phone_number = inp[event.sender.id][event.text.strip().split(' ')[1].strip()].inpost.phone_number
    #             geocheck = True if msg[2].strip().lower() == 'on' else False
    #         case _:
    #             await event.reply(not_enough_parameters_provided)
    #             return
    #
    #     database.edit_phone_number_config(event=event,
    #                                       phone_number=phone_number,
    #                                       geocheck=geocheck)
    #     inp[event.sender.id][int(phone_number)].geocheck = geocheck
    #     await event.reply('Geo checking is set!')
    #
    # @client.on(NewMessage(pattern='/set_airquality'))
    # async def set_airquality(event):
    #     if event.sender.id not in inp:
    #         await event.reply('You are not initialized')
    #         return
    #
    #     if inp[event.sender.id].consent is None:
    #         await event.reply('You did not set your data collecting consent.'
    #                           '\n\nSend `/consent yes` if you want your data to be collected '
    #                           'in order to reduce data collected from inpost services and to help us develop this app.'
    #                           'If you refuse send `/consent no`.')
    #
    #         return
    #
    #     msg = event.text.strip().split(' ')
    #
    #     match len(msg):
    #         case 2:
    #             phone_number = inp[event.sender.id].default_phone_number.phone_number
    #             airquality = True if msg[1].strip().lower() == 'on' else False
    #         case 3:
    #             phone_number = inp[event.sender.id][event.text.strip().split(' ')[1].strip()].inpost.phone_number
    #             airquality = True if msg[2].strip().lower() == 'on' else False
    #         case _:
    #             await event.reply(not_enough_parameters_provided)
    #             return
    #
    #     database.edit_phone_number_config(event=event,
    #                                       phone_number=phone_number,
    #                                       airquality=airquality)
    #     inp[event.sender.id][int(phone_number)].airquality = airquality
    #     await event.reply('Airquality is set!')
    #
    # @client.on(NewMessage(pattern='/set_notifications'))
    # async def set_notifications(event):
    #     if event.sender.id not in inp:
    #         await event.reply('You are not initialized')
    #         return
    #
    #     if inp[event.sender.id].consent is None:
    #         await event.reply('You did not set your data collecting consent.'
    #                           '\n\nSend `/consent yes` if you want your data to be collected '
    #                           'in order to reduce data collected from inpost services and to help us develop this app.'
    #                           'If you refuse send `/consent no`.')
    #
    #         return
    #
    #     msg = event.text.strip().split(' ')[1].strip()
    #
    #     match len(event.text.strip().split(' ')):
    #         case 2:
    #             phone_number = inp[event.sender.id].default_phone_number.phone_number
    #             notifications = True if msg.lower() == 'on' else False
    #         case 3:
    #             phone_number = inp[event.sender.id][int(event.text.strip().split(' ')[1].strip())].inpost.phone_number
    #             notifications = True if msg.lower() == 'on' else False
    #         case _:
    #             await event.reply(not_enough_parameters_provided)
    #             return
    #
    #     database.edit_phone_number_config(event=event,
    #                                       phone_number=phone_number,
    #                                       notifications=notifications)
    #     inp[event.sender.id][int(phone_number)].notifications = notifications
    #     await event.reply(f'Notifications are set to {msg.upper()}!')
    #

    async with client:
        print("Good morning!")
        await client.run_until_disconnected()


if __name__ == '__main__':
    with open("config.yml", 'r') as f:
        config = yaml.safe_load(f)
        asyncio.run(main(config=config))
