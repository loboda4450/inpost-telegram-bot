import asyncio
import logging
from typing import List, Dict

import yaml

from telethon import TelegramClient, Button
from telethon.events import NewMessage, CallbackQuery

from inpost.static import Parcel, ParcelStatus
from inpost.api import Inpost


async def main(config, inp: Dict[Inpost]):
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
                          'or by typing `/init <phone_number>`!',
                          buttons=[Button.request_phone('Log in via Telegram')])

    @client.on(NewMessage())
    async def init(event):
        if event.text.startswith('/init'):
            phone_number = event.text.split()[1].strip()
        elif event.message.contact:
            phone_number = event.message.contact.phone_number[-9:]  # cut the region part, 9 last digits
        else:
            return

        if event.sender.id not in inp:
            inp[event.sender.id] = Inpost()
            await inp[event.sender.id].set_phone_number(phone_number=phone_number)
            if await inp[event.sender.id].send_sms_code():
                await event.reply(f'Initialized with phone number: {inp[event.sender.id].phone_number}!'
                                  f'\nSending sms code!')

        else:
            await event.reply('Bwoy, you are already initialized')

    @client.on(NewMessage(pattern='/confirm'))
    async def confirm_sms(event):
        if event.sender.id in inp and event.text.split()[1]:
            if await inp[event.sender.id].confirm_sms_code(event.text.split()[1].strip()):
                await event.reply(f'Succesfully verifed!')
            else:
                await event.reply('You fucked up')

    @client.on(NewMessage(pattern='/packs'))
    async def get_packages(event):
        if event.sender.id in inp:
            p: List[Parcel] = await inp[event.sender.id].get_parcels(parse=True)
            for package in p:
                await event.reply(f'Shipment number: {package.shipment_number}\n'
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
            await event.reply('Bwoy, you are not initialized')

    @client.on(CallbackQuery(pattern=b'QR Code'))
    async def send_qr_code(event):
        if event.sender.id in inp:
            msg = await event.get_message()
            shipment_number = msg.raw_text.split('\n')[0].split(':')[1].strip()
            p: Parcel = await inp[event.sender.id].get_parcel(shipment_number=shipment_number, parse=True)

            await event.reply(file=p.generate_qr_image)

    @client.on(CallbackQuery(pattern=b'Open Code'))
    async def show_open_code(event):
        if event.sender.id in inp:
            msg = await event.get_message()
            shipment_number = msg.raw_text.split('\n')[0].split(':')[1].strip()
            p: Parcel = await inp[event.sender.id].get_parcel(shipment_number=shipment_number, parse=True)

            await event.answer(f'This parcel open code is: {p.open_code}', alert=True)

    @client.on(CallbackQuery(pattern=b'Open'))
    async def open_compartment(event):
        if event.sender.id in inp:
            msg = await event.get_message()
            shipment_number = msg.raw_text.split('\n')[0].split(':')[1].strip()
            p: Parcel = await inp[event.sender.id].get_parcel(shipment_number=shipment_number, parse=True)

            match p.status:
                case ParcelStatus.DELIVERED:
                    await event.answer('Parcel already delivered!', alert=True)
                case ParcelStatus.READY_TO_PICKUP:
                    await inp[event.sender.id].collect(parcel_obj=p)
                case _:
                    await event.answer(f'Parcel not ready for pickup! Status: {p.status.value}', alert=True)

    async with client:
        print("Good morning!")
        await client.run_until_disconnected()


if __name__ == '__main__':
    with open("config.yml", 'r') as f:
        config = yaml.safe_load(f)
        asyncio.run(main(config=config, inp=dict()))
