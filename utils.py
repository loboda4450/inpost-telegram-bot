import json
import os
from typing import List

import arrow
from inpost.static import Parcel, ParcelShipmentType, ParcelStatus
from telethon import Button
from telethon.events import NewMessage, CallbackQuery

from constants import multicompartment_message_builder, compartment_message_builder, delivered_message_builder, \
    details_message_builder, open_comp_message_builder, ready_to_pickup_message_builder, courier_message_builder, \
    out_of_range_message_builder


class BotUserConfig:
    def __init__(self, **kwargs):
        self.notifications: bool = kwargs['notifications']
        self.default_parcel_machine: str = kwargs['default_parcel_machine']
        self.geocheck: bool = kwargs['geocheck']
        self.airquality: bool = kwargs['airquality']
        self.location: tuple | None = kwargs['location'] if 'location' in kwargs else None  # lat, long
        self.location_time: arrow.arrow | None = kwargs['location_time'] if 'location_time' in kwargs else None
        self.location_time_lock: bool = False


async def init_phone_number(event: NewMessage) -> str | None:
    if event.message.contact:  # first check if NewMessage contains contact field
        return event.message.contact.phone_number[-9:]  # cut the region part, 9 last digits
    elif not event.text.startswith('/init'):  # then check if starts with /init, if so proceed
        return None
    elif len(event.text.split(' ')) == 2 and event.text.split()[1].strip().isdigit():
        return event.text.split()[1].strip()
    else:
        await event.reply('Something is wrong with provided phone number')
        return None


async def get_phone_number(inp: dict, event: NewMessage):
    if len(inp[event.sender.id]) == 1:
        return list(inp[event.sender.id])[0]

    elif inp[event.sender.id].default_phone_number and len(inp[event.sender.id]) != 1 and len(
            event.text.split(' ')) == 2:
        return inp[event.sender.id].default_phone_number

    elif not inp[event.sender.id].default_phone_number and len(event.text.split(' ')) == 2:
        if not len(event.text.split()[1].strip()) == 9:
            await event.reply('Phone number is not 9 digit long')
            return None

        if not event.text.split()[1].strip().isdigit():
            await event.reply('Phone number must contain only digits')
            return None

        return event.text.split()[1].strip()

    else:
        return await validate_number(event=event, phone_number=True)


async def confirm_location(event: NewMessage, inp: dict, phone_number, shipment_number):
    p: Parcel = await inp[event.sender.id][phone_number]['inpost'].get_parcel(shipment_number=shipment_number,
                                                                              parse=True)

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
                await event.reply(out_of_range_message_builder(parcel=p),
                                  buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])
        case _:
            await event.answer(f'Parcel not ready for pick up!\nStatus: {p.status.value}', alert=True)


async def get_shipment_number(event: NewMessage):
    if len(event.text.split(' ')) == 2:
        return event.raw_text.split(' ')[1].strip()
    elif len(event.text.split(' ')) == 3:
        return event.raw_text.split(' ')[2].strip()
    else:
        return None


async def validate_number(event: NewMessage, phone_number: bool) -> str | None:
    if phone_number:
        if len(event.text.split()) != 3:
            await event.reply('Wrong message format')  # TODO: Format message
            return None

        if not len(event.text.split()[1].strip()) == 9:
            await event.reply('Phone number is not 9 digit long')
            return None

        if not event.text.split()[1].strip().isdigit():
            await event.reply('Phone number must contain only digits')
            return None

    else:
        if not len(event.text.split()) == 2:
            await event.reply('No SMS Code provided to make this operation')
            return None

        if not len(event.text.split()[1].strip()) == 6:
            await event.reply('SMS code is not 6 digit long')
            return None

        if not event.text.split()[1].strip().isdigit():
            await event.reply('SMS code must contain only digits')
            return None

    return event.text.split()[1].strip()


async def send_pcg(event: NewMessage, inp: dict, phone_number: int):
    package: Parcel = await inp[event.sender.id][phone_number]['inpost'].get_parcel(
        shipment_number=(await get_shipment_number(event)), parse=True)

    if package.is_multicompartment:
        packages: List[Parcel] = await inp[event.sender.id][phone_number]['inpost'].get_multi_compartment(
            multi_uuid=package.multi_compartment.uuid, parse=True)
        package = next((parcel for parcel in packages if parcel.is_main_multicompartment), None)
        other = '\n'.join(f'📤 **Sender:** `{p.sender.sender_name}`\n'
                          f'📦 **Shipment number:** `{p.shipment_number}`' for p in packages if
                          not p.is_main_multicompartment)

        message = multicompartment_message_builder(amount=len(packages), package=package, other=other)

    elif package.status == ParcelStatus.DELIVERED:
        message = delivered_message_builder(package=package)
    else:
        message = compartment_message_builder(package=package)

    to_log = await inp[event.sender.id][phone_number]['inpost'].get_parcel(
        shipment_number=package.shipment_number, parse=False)
    filename = f"parcel_logs/{event.sender.id}/{phone_number}/{package.shipment_number} {arrow.now('Europe/Warsaw').format('DD.MM.YYYY HH:mm:ss')}.json"
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    with open(filename, "w") as f:
        json.dump(to_log, f)

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


async def send_pcgs(event, inp, status, phone_number):
    packages: List[Parcel] = await inp[event.sender.id][phone_number]['inpost'].get_parcels(status=status, parse=True)
    exclude = []
    if len(packages) > 0:
        for package in packages:
            if package.shipment_number in exclude:
                continue

            if package.is_multicompartment and not package.is_main_multicompartment:
                exclude.append(package.shipment_number)
                continue

            elif package.is_main_multicompartment:
                packages: List[Parcel] = await inp[event.sender.id][phone_number]['inpost'].get_multi_compartment(
                    multi_uuid=package.multi_compartment.uuid, parse=True)
                package = next((parcel for parcel in packages if parcel.is_main_multicompartment), None)
                other = '\n'.join(f'📤 **Sender:** `{p.sender.sender_name}`\n'
                                  f'📦 **Shipment number:** `{p.shipment_number}\n`' for p in packages if
                                  not p.is_main_multicompartment)

                message = multicompartment_message_builder(amount=len(packages), package=package, other=other)

            elif package.shipment_type == ParcelShipmentType.courier:
                message = delivered_message_builder(package=package)

            else:
                message = compartment_message_builder(package=package)

            if package.status in (ParcelStatus.STACK_IN_BOX_MACHINE, ParcelStatus.STACK_IN_CUSTOMER_SERVICE_POINT):
                message = f'⚠️ **PARCEL IS IN SUBSTITUTIONARY PICK UP POINT!** ⚠\n️\n' + message

            to_log = await inp[event.sender.id][phone_number]['inpost'].get_parcel(
                    shipment_number=package.shipment_number, parse=False)
            filename = f"parcel_logs/{event.sender.id}/{phone_number}/{package.shipment_number}/{arrow.now('Europe/Warsaw').format('DD.MM.YYYY HH:mm:ss')}.json"
            os.makedirs(os.path.dirname(filename), exist_ok=True)

            with open(filename, "w") as f:
                json.dump(to_log, f)

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
    phone_number = await get_phone_number(inp, event)
    if phone_number is None:
        await event.reply('No phone number provided!')
        return

    p: Parcel = await inp[event.sender.id][phone_number]['inpost'].get_parcel(shipment_number=shipment_number,
                                                                              parse=True)
    if p.status == ParcelStatus.READY_TO_PICKUP:
        await event.reply(file=p.generate_qr_image)
    else:
        await event.answer(f'Parcel not ready for pick up!\nStatus: {p.status.value}', alert=True)


async def show_oc(event, inp, shipment_number):
    phone_number = await get_phone_number(inp, event)
    if phone_number is None:
        await event.reply('No phone number provided!')
        return

    p: Parcel = await inp[event.sender.id][phone_number]['inpost'].get_parcel(shipment_number=shipment_number,
                                                                              parse=True)
    if p.status == ParcelStatus.READY_TO_PICKUP:
        await event.answer(f'This parcel open code is: {p.open_code}', alert=True)
    else:
        await event.answer(f'Parcel not ready for pick up!\nStatus: {p.status.value}', alert=True)


async def open_comp(event, inp, p: Parcel):
    phone_number = await get_phone_number(inp, event)
    if phone_number is None:
        await event.reply('No phone number provided!')
        return

    await inp[event.sender.id][phone_number]['inpost'].collect(parcel_obj=p)
    await event.reply(open_comp_message_builder(parcel=p))


async def send_details(event, inp, shipment_number):
    phone_number = await get_phone_number(inp, event)
    if phone_number is None:
        await event.reply('No phone number provided!')
        return

    parcel: Parcel = await inp[event.sender.id][phone_number]['inpost'].get_parcel(shipment_number=shipment_number,
                                                                                   parse=True)

    if parcel.is_multicompartment:
        parcels = await inp[event.sender.id][phone_number]['inpost'].get_multi_compartment(
            multi_uuid=parcel.multi_compartment.uuid, parse=True)
        message = ''

        for p in parcels:
            message = message + f'**Sender:** {p.sender}\n'
            events = "\n".join(
                f'{status.date.to("local").format("DD.MM.YYYY HH:mm"):>22}: {status.name.value}' for status in
                p.event_log)
            if p.status == ParcelStatus.READY_TO_PICKUP:
                message = message + details_message_builder(parcel=p, events=events)

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
            await event.reply(ready_to_pickup_message_builder(parcel=parcel, events=events))
        elif parcel.status == ParcelStatus.DELIVERED:
            await event.reply(f'**Picked up**: {parcel.pickup_date.to("local").format("DD.MM.YYYY HH:mm")}\n'
                              f'**Events**:\n{events}'
                              )
        else:
            await event.reply(f'**Events**:\n{events}')
