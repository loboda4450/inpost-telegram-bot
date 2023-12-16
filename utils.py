from typing import List, Dict

import arrow
from inpost import Inpost
from inpost.static import Parcel, ParcelShipmentType, ParcelStatus, ParcelType, ParcelOwnership
from telethon import Button
from telethon.events import NewMessage, CallbackQuery
from telethon.tl.patched import Message

import database
from constants import multicompartment_message_builder, compartment_message_builder, delivered_message_builder, \
    details_message_builder, ready_to_pickup_message_builder


class BotUserPhoneNumberConfig:
    def __init__(self, **kwargs):
        self.inpost: Inpost = Inpost.from_dict(kwargs['inpost']) if 'inpost' in kwargs else Inpost(
            phone_number=kwargs['phone_number'])
        self.notifications: bool = kwargs['notifications']
        self.default_parcel_machine: str = kwargs['default_parcel_machine']
        self.geocheck: bool = kwargs['geocheck']
        self.airquality: bool = kwargs['airquality']
        self.location: tuple | None = kwargs['location'] if 'location' in kwargs else (0, 0)  # lat, long
        self.location_time: arrow.arrow | None = kwargs['location_time'] if 'location_time' in kwargs else arrow.get(
            str(arrow.now(tz="Europe/Warsaw").year))
        self.location_time_lock: bool = False

    @property
    def phone_number(self):
        return self.inpost.phone_number


class BotUserConfig:
    def __init__(self, default_phone_number: int | str | None = None, consent: bool | None = None,
                 phone_numbers: Dict = dict()):
        self._default_phone_number = default_phone_number
        self.consent: bool = consent
        self.phone_numbers: Dict = {pn: BotUserPhoneNumberConfig(
            **{'inpost': {'phone_number': pn,
                          'sms_code': phone_numbers[pn]['sms_code'],
                          'auth_token': phone_numbers[pn]['auth_token'],
                          'refr_token': phone_numbers[pn]['refr_token']
                          },
               'notifications': phone_numbers[pn]['notifications'],
               'default_parcel_machine': phone_numbers[pn]['default_parcel_machine'],
               'geocheck': phone_numbers[pn]['geocheck'],
               'airquality': phone_numbers[pn]['airquality'],
               'consent': consent,
               'location': (0, 0),
               'location_time': arrow.get(2023, 1, 1)}) for pn in phone_numbers} if phone_numbers is not None else None

    def __getitem__(self, item):
        return self.phone_numbers[int(item)]

    def __contains__(self, item):
        return item in self.phone_numbers

    @property
    def default_phone_number(self):
        return self.phone_numbers[self._default_phone_number]

    @default_phone_number.setter
    def default_phone_number(self, value):
        self._default_phone_number = int(value)


async def init_phone_number(event: NewMessage) -> int | None:
    if event.message.contact:  # first check if NewMessage contains contact field
        return int(event.message.contact.phone_number[-9:])  # cut the region part, 9 last digits
    elif not event.text.startswith('/init'):  # then check if starts with /init, if so proceed
        return None
    elif len(event.text.split(' ')) == 2 and event.text.split()[1].strip().isdigit():
        return int(event.text.split()[1].strip())
    else:
        return None


async def confirm_location(event: NewMessage | Message,
                           inp: dict,
                           phone_number: str | None = None,
                           shipment_number: str | None = None,
                           parcel_obj: Parcel | None = None) -> str | None:
    if shipment_number and parcel_obj:
        return

    if shipment_number and parcel_obj is None:
        p: Parcel = await inp[event.sender.id][phone_number].inpost.get_parcel(shipment_number=shipment_number,
                                                                               parse=True)

    else:
        p = parcel_obj

    if isinstance(event, NewMessage):
        loc = event.message.geo
    elif isinstance(event, Message):
        loc = event.geo
    else:
        return

    match p.status:
        case ParcelStatus.DELIVERED:
            return 'DELIVERED'
        case ParcelStatus.READY_TO_PICKUP | ParcelStatus.STACK_IN_BOX_MACHINE:
            if (p.pickup_point.latitude - 0.0005 <= loc.lat <= p.pickup_point.latitude + 0.0005) and (
                    p.pickup_point.longitude - 0.0005 <= loc.long <= p.pickup_point.longitude + 0.0005):
                return 'IN RANGE'
            else:
                return 'OUT OF RANGE'
        case _:
            return 'NOT READY'


def verify_location(p: Parcel, latlong: tuple) -> bool:
    if latlong is None:
        return False

    return (p.pickup_point.latitude - 0.0005 <= latlong[0] <= p.pickup_point.latitude + 0.0005) and (
            p.pickup_point.longitude - 0.0005 <= latlong[1] <= p.pickup_point.longitude + 0.0005)


async def get_shipment_number(event: NewMessage):
    if len(event.text.split(' ')) == 2:
        return event.raw_text.split(' ')[1].strip()
    elif len(event.text.split(' ')) == 3:
        return event.raw_text.split(' ')[2].strip()
    else:
        return None


async def get_shipment_and_phone_number_from_reply(event, inp):
    match len(event.text.strip().split(' ')):
        case 1:
            phone_number = inp[event.sender.id].default_phone_number.inpost.phone_number
        case 2:
            phone_number = inp[event.sender.id][event.text.strip().split(' ')[1]].inpost.phone_number
        case _:
            phone_number = None

    msg = await event.get_reply_message()
    shipment_number = \
        (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()

    return shipment_number, phone_number


async def get_shipment_and_phone_number_from_button(event, inp):
    msg = await event.get_message()
    shipment_number = \
        (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()

    phone_number = inp[event.sender.id].default_phone_number.inpost.phone_number

    return shipment_number, phone_number


async def send_pcg(event: NewMessage, inp: dict, phone_number: int, parcel_type: ParcelType):
    package: Parcel = await inp[event.sender.id][phone_number].inpost.get_parcel(
        shipment_number=(await get_shipment_number(event)), parcel_type=parcel_type, parse=True)

    if package.is_multicompartment:
        packages: List[Parcel] = await inp[event.sender.id][phone_number].inpost.get_multi_compartment(
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

    if inp[event.sender.id].consent:
        to_log = await inp[event.sender.id][phone_number].inpost.get_parcel(
            shipment_number=package.shipment_number, parcel_type=parcel_type, parse=False)

        database.add_parcel(event=event, phone_number=phone_number, ptype=parcel_type, parcel=to_log)

    match package.status:
        case ParcelStatus.READY_TO_PICKUP | ParcelStatus.STACK_IN_BOX_MACHINE:
            await event.reply(message,
                              buttons=[
                                  [Button.inline('Open Code'), Button.inline('QR Code')],
                                  [Button.inline('Details'), Button.inline('Open Compartment')],
                                  [Button.inline(
                                      'Share')]] if package.operations.can_share_parcel and package.ownership_status == 'OWN' else [
                                  [Button.inline('Open Code'), Button.inline('QR Code')],
                                  [Button.inline('Details'), Button.inline('Open Compartment')]])
        case _:
            await event.reply(message,
                              buttons=[Button.inline('Details'),
                                       Button.inline(
                                           'Share')] if package.operations.can_share_parcel and package.ownership_status == 'OWN' else [
                                  Button.inline('Details')])


async def send_pcgs(event, inp, status, phone_number, parcel_type):
    packages: List[Parcel] = await inp[event.sender.id][int(phone_number)].inpost.get_parcels(status=status,
                                                                                              parcel_type=parcel_type,
                                                                                              parse=True)
    exclude = []
    if len(packages) > 0:
        for package in packages:
            if package.shipment_number in exclude:
                continue

            if package.is_multicompartment and not package.is_main_multicompartment:
                exclude.append(package.shipment_number)
                continue

            elif package.is_main_multicompartment:
                packages: List[Parcel] = await inp[event.sender.id][int(phone_number)].inpost.get_multi_compartment(
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

            if inp[event.sender.id].consent:
                to_log = await inp[event.sender.id][int(phone_number)].inpost.get_parcel(
                    shipment_number=package.shipment_number, parcel_type=parcel_type, parse=False)

                database.add_parcel(event=event, phone_number=phone_number, ptype=parcel_type, parcel=to_log)

            match package.status:
                case ParcelStatus.READY_TO_PICKUP | ParcelStatus.STACK_IN_BOX_MACHINE | ParcelStatus.STACK_IN_CUSTOMER_SERVICE_POINT:
                    await event.reply(message + f'\n🫳 **Pick up until:** '
                                                f'`{package.expiry_date.to("local").format("DD.MM.YYYY HH:mm")}`',
                                      buttons=[
                                          [Button.inline('Open Code'), Button.inline('QR Code')],
                                          [Button.inline('Details'), Button.inline('Open Compartment')],
                                          [Button.inline(
                                              'Share')]] if package.operations.can_share_parcel and package.ownership_status == ParcelOwnership.OWN else
                                      [[Button.inline('Open Code'), Button.inline('QR Code')],
                                       [Button.inline('Details'), Button.inline('Open Compartment')]]
                                      )
                case _:
                    await event.reply(message,
                                      buttons=[Button.inline('Details'),
                                               Button.inline(
                                                   'Share')] if package.operations.can_share_parcel and package.ownership_status == ParcelOwnership.OWN else [
                                          Button.inline('Details')])

    else:
        if isinstance(event, CallbackQuery.Event):
            await event.answer('No parcels with specified status!', alert=True)
        elif isinstance(event, NewMessage.Event):
            await event.reply('No parcels with specified status!')

    return status


async def send_qrc(event, inp, phone_number, shipment_number):
    p: Parcel = await inp[event.sender.id][phone_number].inpost.get_parcel(shipment_number=shipment_number,
                                                                           parse=True)
    if p.status == ParcelStatus.READY_TO_PICKUP or p.status == ParcelStatus.STACK_IN_BOX_MACHINE:
        await event.reply(file=p.generate_qr_image)
    else:
        await event.answer(f'Parcel not ready for pick up!\nStatus: {p.status.value}', alert=True)


async def show_oc(event, inp, phone_number, shipment_number):
    p: Parcel = await inp[event.sender.id][phone_number].inpost.get_parcel(shipment_number=shipment_number,
                                                                           parse=True)
    if p.status == ParcelStatus.READY_TO_PICKUP or p.status == ParcelStatus.STACK_IN_BOX_MACHINE:
        await event.answer(f'This parcel open code is: {p.open_code}', alert=True)
    else:
        await event.answer(f'Parcel not ready for pick up!\nStatus: {p.status.value}', alert=True)


async def open_comp(event, inp, phone_number, p: Parcel):
    p_ = await inp[event.sender.id][phone_number].inpost.collect(parcel_obj=p)
    if p_ is not None:
        if inp[event.sender.id].consent:
            to_log = await inp[event.sender.id][int(phone_number)].inpost.get_parcel(
                shipment_number=p.shipment_number, parcel_type=ParcelType.TRACKED, parse=False)

            database.add_parcel(event=event, phone_number=phone_number, ptype=ParcelType.TRACKED, parcel=to_log)

        return p_

    return None


async def send_details(event, inp, shipment_number, parcel_type, phone_number=None):
    if phone_number is None:
        if inp[event.sender.id].default_phone_number is None:
            await event.reply(f'Buttons works only with default phone number. '
                              f'Please set up one before using them or type following command: '
                              f'\n`/details <phone_number> {shipment_number}')
            return

        phone_number = inp[event.sender.id].default_phone_number.inpost.phone_number

    parcel: Parcel = await inp[event.sender.id][int(phone_number)].inpost.get_parcel(shipment_number=shipment_number,
                                                                                     parcel_type=parcel_type,
                                                                                     parse=True)

    if parcel.is_multicompartment:  # TODO: Add airsensor data
        parcels = await inp[event.sender.id][phone_number].inpost.get_multi_compartment(
            multi_uuid=parcel.multi_compartment.uuid, parse=True)
        message = ''

        for p in parcels:
            message = message + f'**Sender:** {p.sender}\n'
            events = "\n".join(
                f'{status.date.to("local").format("DD.MM.YYYY HH:mm"):>22}: {status.name.value}' for status in
                p.event_log)
            if p.status == ParcelStatus.READY_TO_PICKUP or p.status == ParcelStatus.STACK_IN_BOX_MACHINE:
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
        air_quality = None
        if inp[event.sender.id][int(phone_number)].airquality and parcel.pickup_point.air_sensor:
            air_quality = f'Air quality: {parcel.pickup_point.air_sensor_data.air_quality}\n' \
                          f'Temperature: {parcel.pickup_point.air_sensor_data.temperature}\n' \
                          f'Humidity: {parcel.pickup_point.air_sensor_data.humidity}\n' \
                          f'Pressure: {parcel.pickup_point.air_sensor_data.pressure}\n' \
                          f'PM25: {parcel.pickup_point.air_sensor_data.pm25_value}, {parcel.pickup_point.air_sensor_data.pm25_percent}%\n' \
                          f'Temperature: {parcel.pickup_point.air_sensor_data.pm10_value}, {parcel.pickup_point.air_sensor_data.pm10_percent}%\n'

        if parcel.status == ParcelStatus.READY_TO_PICKUP or parcel.status == ParcelStatus.STACK_IN_BOX_MACHINE:
            await event.reply(ready_to_pickup_message_builder(parcel=parcel, events=events, air_quality=air_quality))
        elif parcel.status == ParcelStatus.DELIVERED:
            await event.reply(f'**Picked up**: {parcel.pickup_date.to("local").format("DD.MM.YYYY HH:mm")}\n'
                              f'**Events**:\n{events}')
        else:
            await event.reply(f'**Events**:\n{events}')


async def is_parcel_owner(inp, shipment_number, phone_number, event, parcel_type) -> bool:
    parcel = await inp[event.sender.id][phone_number].inpost.get_parcel(
        shipment_number=shipment_number, parcel_type=parcel_type, parse=True)

    return parcel.ownership_status == ParcelOwnership.OWN
