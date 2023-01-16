import asyncio
import logging
from typing import List, Dict

import yaml

from telethon import TelegramClient, Button
from telethon.events import NewMessage, CallbackQuery

from inpost.static import Parcel, ParcelStatus
from inpost.static.exceptions import *
from inpost.api import Inpost


async def main(config, inp: Dict):
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=config['log_level'])
    logger = logging.getLogger(__name__)
    client = TelegramClient(**config['telethon_settings'])
    print("Starting")

    if not config['bot_token']:
        raise Exception('No bot token provided')

    await client.start(bot_token=config['bot_token'])
    print("Started")

    @client.on(NewMessage(pattern='/start'))
    async def start(event):
        await event.reply('Hello!\nThis is a bot helping you to manage your InPost parcels!\n\n'
                          'Log in using button that just shown up below the text box '
                          'or by typing `/init <phone_number>`!\n\n'
                          'List of commands:\n'
                          'start - display start message and allow user to login with Telegram\n'
                          '/init - login using phone number /init <phone_number>\n'
                          '/confirm - confirm login with sms code /confirm <sms_code>\n'
                          '/pending - return pending parcels\n'
                          '/delivered - return delivered parcels\n'
                          '/all - return all available parcels',
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
            await event.reply('You were initialized before, reinitializing')
        try:
            inp[event.sender.id] = Inpost()
            await inp[event.sender.id].set_phone_number(phone_number=phone_number)
            if await inp[event.sender.id].send_sms_code():
                await event.reply(f'Initialized with phone number: {inp[event.sender.id].phone_number}!'
                                  f'\nSending sms code!', buttons=Button.clear())

        except PhoneNumberError as e:
            await event.reply(e.reason)
        except UnauthorizedError:
            await event.reply('You are not authorized')
        except NotFoundError:
            await event.reply('Provided phone number does not exist')
        except UnidentifiedAPIError:
            await event.reply('Unexpected error occurred, call admin')

    @client.on(NewMessage(pattern='/confirm'))
    async def confirm_sms(event):
        if event.sender.id in inp and len(event.text.split()) == 2:
            try:
                if await inp[event.sender.id].confirm_sms_code(event.text.split()[1].strip()):
                    await event.reply(f'Succesfully verifed!', buttons=[Button.inline('Pending Parcels'),
                                                                        Button.inline('Delivered Parcels')])
                else:
                    await event.reply('You fucked up')

            except PhoneNumberError as e:
                await event.reply(e.reason)
            except SmsCodeError as e:
                await event.reply(e.reason)
            except UnauthorizedError:
                await event.reply('You are not authorized')
            except NotFoundError:
                await event.reply('Provided phone number does not exist')
            except UnidentifiedAPIError:
                await event.reply('Unexpected error occurred, call admin')
        else:
            await event.reply('No sms code provided or not initialized')

    @client.on(NewMessage(pattern='/parcel'))
    async def get_parcel(event):
        if event.sender.id in inp and len(event.text.split(' ')) == 2:
            try:
                package: Parcel = await inp[event.sender.id].get_parcel(
                    shipment_number=event.text.split(' ')[1].strip(),
                    parse=True)

                await event.reply(f'Sender: {package.sender.sender_name}\n'
                                  f'Shipment number: {package.shipment_number}\n'
                                  f'Status: {package.status.value}\n'
                                  f'Pickup point: {package.pickup_point}',
                                  buttons=[
                                      [Button.inline('Open Code'),
                                       Button.inline('QR Code')],
                                      [Button.inline('Open Compartment')]] if package.status != ParcelStatus.DELIVERED else
                                  [Button.inline('Open Code'),
                                   Button.inline('QR Code')]
                                  )

            except NotAuthenticatedError as e:
                await event.reply(e.reason)
            except UnauthorizedError:
                await event.reply('You are not authorized')
            except NotFoundError:
                await event.reply('Parcel does not exist!')
            except UnidentifiedAPIError:
                await event.reply('Unexpected error occurred, call admin')
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
                              ParcelStatus.TAKEN_BY_COURIER, ParcelStatus.TAKEN_BY_COURIER_FROM_POK]
                elif event.data == b'Delivered Parcels':
                    status = ParcelStatus.DELIVERED
            elif isinstance(event, NewMessage.Event):
                if event.text == '/pending':
                    status = [ParcelStatus.READY_TO_PICKUP, ParcelStatus.CONFIRMED,
                              ParcelStatus.ADOPTED_AT_SORTING_CENTER, ParcelStatus.ADOPTED_AT_SOURCE_BRANCH,
                              ParcelStatus.COLLECTED_FROM_SENDER, ParcelStatus.DISPATCHED_BY_SENDER,
                              ParcelStatus.DISPATCHED_BY_SENDER_TO_POK, ParcelStatus.OUT_FOR_DELIVERY,
                              ParcelStatus.OUT_FOR_DELIVERY_TO_ADDRESS, ParcelStatus.SENT_FROM_SOURCE_BRANCH,
                              ParcelStatus.TAKEN_BY_COURIER, ParcelStatus.TAKEN_BY_COURIER_FROM_POK]
                elif event.text == '/delivered':
                    status = ParcelStatus.DELIVERED
                else:
                    pass
            try:
                p: List[Parcel] = await inp[event.sender.id].get_parcels(status=status, parse=True)
                if len(p) > 0:
                    for package in p:
                        await event.reply(f'Sender: {package.sender.sender_name}\n'
                                          f'Shipment number: {package.shipment_number}\n'
                                          f'Status: {package.status.value}\n'
                                          f'Pickup point: {package.pickup_point}',
                                          buttons=[
                                              [Button.inline('Open Code'),
                                               Button.inline('QR Code')],
                                              [Button.inline('Open')]] if package.status != ParcelStatus.DELIVERED else
                                          [Button.inline('Open Code'),
                                           Button.inline('QR Code')]
                                          )
                else:
                    if isinstance(event, CallbackQuery.Event):
                        await event.answer('No parcels with specified status!', alert=True)
                    elif isinstance(event, NewMessage.Event):
                        await event.reply('No parcels with specified status!')

            except NotAuthenticatedError as e:
                await event.reply(e.reason)
            except ParcelTypeError as e:
                await event.reply(e.reason)
            except UnauthorizedError:
                await event.reply('You are not authorized to fetch parcels!')
            except NotFoundError:
                await event.reply('No parcels found!')
            except UnidentifiedAPIError:
                await event.reply('Unexpected error occurred, call admin')
            except Exception:
                await event.reply('Bad things happened, call admin now!')

        else:
            await event.reply('You are not initialized')

    @client.on(CallbackQuery(pattern=b'QR Code'))
    async def send_qr_code(event):
        if event.sender.id in inp:
            msg = await event.get_message()
            shipment_number = msg.raw_text.split('\n')[1].split(':')[1].strip()
            try:
                p: Parcel = await inp[event.sender.id].get_parcel(shipment_number=shipment_number, parse=True)

                await event.reply(file=p.generate_qr_image)

            except NotAuthenticatedError as e:
                await event.reply(e.reason)
            except ParcelTypeError as e:
                await event.reply(e.reason)
            except UnauthorizedError:
                await event.reply('You are not authorized to fetch parcels!')
            except NotFoundError:
                await event.reply('No parcels found!')
            except UnidentifiedAPIError:
                await event.reply('Unexpected error occurred, call admin')
            except Exception:
                await event.reply('Bad things happened, call admin now!')
        else:
            await event.reply('You are not initialized')

    @client.on(CallbackQuery(pattern=b'Open Code'))
    async def show_open_code(event):
        if event.sender.id in inp:
            msg = await event.get_message()
            shipment_number = msg.raw_text.split('\n')[1].split(':')[1].strip()
            try:
                p: Parcel = await inp[event.sender.id].get_parcel(shipment_number=shipment_number, parse=True)

                await event.answer(f'This parcel open code is: {p.open_code}', alert=True)

            except NotAuthenticatedError as e:
                await event.reply(e.reason)
            except ParcelTypeError as e:
                await event.reply(e.reason)
            except UnauthorizedError:
                await event.reply('You are not authorized to fetch parcels!')
            except NotFoundError:
                await event.reply('No parcels found!')
            except UnidentifiedAPIError:
                await event.reply('Unexpected error occurred, call admin')
            except Exception:
                await event.reply('Bad things happened, call admin now!')

        else:
            await event.reply('You are not initialized')

    @client.on(CallbackQuery(pattern=b'Open Compartment'))
    async def open_compartment(event):
        if event.sender.id in inp:
            msg = await event.get_message()
            shipment_number = msg.raw_text.split('\n')[1].split(':')[1].strip()
            try:
                p: Parcel = await inp[event.sender.id].get_parcel(shipment_number=shipment_number, parse=True)

                match p.status:
                    case ParcelStatus.DELIVERED:
                        await event.answer('Parcel already delivered!', alert=True)
                    case ParcelStatus.READY_TO_PICKUP:
                        await inp[event.sender.id].collect(parcel_obj=p)
                    case _:
                        await event.answer(f'Parcel not ready for pickup! Status: {p.status.value}', alert=True)

            except NotAuthenticatedError as e:
                await event.reply(e.reason)
            except ParcelTypeError as e:
                await event.reply(e.reason)
            except UnauthorizedError:
                await event.reply('You are not authorized to fetch parcels!')
            except NotFoundError:
                await event.reply('No parcels found!')
            except UnidentifiedAPIError:
                await event.reply('Unexpected error occurred, call admin')
            except Exception:
                await event.reply('Bad things happened, call admin now!')
        else:
            await event.reply('You are not initialized')

    async with client:
        print("Good morning!")
        await client.run_until_disconnected()


if __name__ == '__main__':
    with open("config.yml", 'r') as f:
        config = yaml.safe_load(f)
        asyncio.run(main(config=config, inp=dict()))
