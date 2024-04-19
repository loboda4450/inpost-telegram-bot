import datetime
from typing import List, Dict, Tuple

import arrow
from inpost import Inpost
from inpost.static import Parcel, ParcelShipmentType, ParcelStatus, ParcelType, ParcelOwnership
from telethon import Button
from telethon.events import NewMessage, CallbackQuery
from telethon.tl.patched import Message

import database
from database import User
from constants import multicompartment_message_builder, compartment_message_builder, delivered_message_builder, \
    details_message_builder, ready_to_pickup_message_builder, out_of_range_message_builder, open_comp_message_builder, \
    pending_statuses


async def init_phone_number(event: NewMessage) -> Tuple[int | str, str] | None:
    if event.message.contact:  # first check if NewMessage contains contact field
        # WTF telegram/telethon,
        # that shit is because in Contact sometimes they send +48, sometimes 48 even for the same number in a series.
        return (event.message.contact.phone_number[:-9]
                if event.message.contact.phone_number[:-9].startswith('+') else
                '+' + event.message.contact.phone_number[:-9], event.message.contact.phone_number[-9:])
    elif not event.text.startswith('/init'):  # then check if starts with /init, if so proceed
        return None
    elif (len(event.text.split(' ')) == 2
          and event.text.split()[1].strip()[-9:].isdigit()
          and 3 <= len(event.text.split()[1][:-9] <= 4)
          and event.text.split()[1].strip().startswith("+")):
        return event.text.split(' ', 1)[1][:-9], event.text.split(' ', 1)[1][-9:]
    else:
        return None


async def confirm_location(event: NewMessage | Message,
                           parcel_obj: Parcel | None = None) -> str | None:
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


async def get_shipment_number_from_button(event):
    msg = await event.get_message()
    shipment_number = \
        (next((data for data in msg.raw_text.split('\n') if 'Shipment number' in data))).split(':')[1].strip()

    return shipment_number


async def send_pcg(event: NewMessage, inp: Inpost, phone_number: int, parcel_type: ParcelType):
    package: Parcel = await inp.get_parcel(shipment_number=event.text.strip(),
                                           parcel_type=parcel_type,
                                           parse=True)
    with database.db_session:
        if package.is_multicompartment:
            packages: List[Parcel] = await inp.get_multi_compartment(multi_uuid=package.multi_compartment.uuid,
                                                                     parse=True)
            package = next((parcel for parcel in packages if parcel.is_main_multicompartment), None)
            other = '\n'.join(f'📤 **Sender:** `{p.sender.sender_name}`\n'
                              f'📦 **Shipment number:** `{p.shipment_number}`' for p in packages if
                              not p.is_main_multicompartment)

            message = multicompartment_message_builder(amount=len(packages), package=package, other=other)

        elif package.status == ParcelStatus.DELIVERED:
            message = delivered_message_builder(package=package)
        else:
            message = compartment_message_builder(package=package)

        if User.get(userid=event.sender.id).data_collecting_consent:
            to_log = await inp.get_parcel(
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
    packages: List[Parcel] = await inp.get_parcels(status=status, parcel_type=parcel_type, parse=True)
    exclude = []
    if len(packages) > 0:
        for package in packages:
            if package.shipment_number in exclude:
                continue

            if package.is_multicompartment and not package.is_main_multicompartment:
                exclude.append(package.shipment_number)
                continue

            elif package.is_main_multicompartment:
                packages: List[Parcel] = await inp.get_multi_compartment(multi_uuid=package.multi_compartment.uuid,
                                                                         parse=True)
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

            if database.get_user_consent(userid=event.sender.id):
                to_log = await inp.get_parcel(
                    shipment_number=package.shipment_number, parcel_type=parcel_type, parse=False)

                database.add_parcel(event=event, phone_number=phone_number, ptype=parcel_type, parcel=to_log)

            match package.status:
                case ParcelStatus.READY_TO_PICKUP | ParcelStatus.STACK_IN_BOX_MACHINE | ParcelStatus.STACK_IN_CUSTOMER_SERVICE_POINT | ParcelStatus.PICKUP_REMINDER_SENT:
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
                                                   'Share')] if package.operations.can_share_parcel and package.ownership_status == 'OWN' else [
                                          Button.inline('Details')])

    else:
        if isinstance(event, CallbackQuery.Event):
            await event.answer('No parcels with specified status!', alert=True)
        elif isinstance(event, NewMessage.Event):
            await event.reply('No parcels with specified status!')

    return status


async def send_qrc(event, parcel, inp):
    if parcel.status not in pending_statuses:
        parcel: Parcel = await inp.get_parcel(shipment_number=parcel.shipment_number, parse=True)

    if parcel.status not in pending_statuses:
        await event.answer(f'Parcel not ready for pick up!\nStatus: {parcel.status.value}', alert=True)
        return

    await event.reply(file=parcel.generate_qr_image)



async def show_oc(event, parcel, inp):
    if parcel.status not in pending_statuses:
        parcel: Parcel = await inp.get_parcel(shipment_number=parcel.shipment_number, parse=True)

    if parcel.status not in pending_statuses:
        await event.answer(f'Parcel not ready for pick up!\nStatus: {parcel.status.value}', alert=True)

    await event.answer(f'This parcel open code is: {parcel.open_code}', alert=True)


async def open_comp(event, inp, p: Parcel):
    if p.status not in pending_statuses:
        p: Parcel = await inp.get_parcel(shipment_number=p.shipment_number, parse=True)

    p_ = await inp.collect(parcel_obj=p)
    if p_ is not None:
        to_log = await inp.get_parcel(shipment_number=p.shipment_number,
                                      parcel_type=ParcelType.TRACKED,
                                      parse=False)

        database.add_parcel(event=event, phone_number=inp.phone_number, ptype=ParcelType.TRACKED, parcel=to_log)

        return p_

    return None


async def send_details(event, inp, parcel):
    if parcel.is_multicompartment:  # TODO: Add airsensor data
        parcels = await inp.get_multi_compartment(multi_uuid=parcel.multi_compartment.uuid, parse=True)
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
        if database.get_user_air_quality(userid=event.sender.id) and parcel.pickup_point.air_sensor:
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

    return


async def share_parcel(event, convo, inp, shipment_number):
    friends = await inp.get_parcel_friends(shipment_number=shipment_number, parse=True)

    if not await is_parcel_owner(inp=inp,
                                 shipment_number=shipment_number,
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
            await friend.reply(
                'You must reply to message with desired friend, start sharing again!')
            return

        friend_event = friend
        friend = await friend.get_reply_message()

    friend = friend.raw_text.split('\n')
    friend = [friend[0].split(':')[1].strip(), friend[1].split(':')[1].strip()]

    uuid = (next((f for f in friends['friends'] if
                  (f.name == friend[0] and f.phone_number == friend[1])))).uuid
    if await inp.share_parcel(uuid=uuid, shipment_number=shipment_number):
        await friend_event.reply('Parcel shared!')
    else:
        await friend_event.reply('Not shared, try again!')


async def open_compartment(event, convo, inp, parcel, parcel_type):
    # TODO: Add database check if user consent if parcel
    #  is ParcelType.TRACKED using /open instead of button
    # TODO: Add database parcel get if user consent
    p: Parcel = await inp.get_parcel(shipment_number=parcel.shipment_number, parcel_type=parcel_type,
                                     parse=True)
    if (database.get_user_geocheck(userid=event.sender.id) or
            database.get_user_default_parcel_machine(userid=event.sender.id) != p.pickup_point.name):
        user_location = database.get_user_location(userid=event.sender.id)
        if any(loc_val is None for loc_val in user_location.values()):
            check_location = True
        else:
            check_location = (datetime.datetime.now() - user_location['location_time']) > datetime.timedelta(minutes=2)

        if check_location:
            await convo.send_message(
                'Please share your location so I can check '
                'whether you are near parcel machine or not.',
                buttons=[Button.request_location('Confirm localization')])

            geo = await convo.get_response(timeout=30)
            if not geo.geo:
                await convo.send_message(
                    'Your message does not contain geolocation, start opening again!',
                    buttons=Button.clear())
                convo.cancel()
                return

            database.update_user_location(userid=event.sender.id,
                                          lat=geo.geo.lat,
                                          long=geo.geo.long,
                                          loc_time=datetime.datetime.now()
                                          )

            status = await confirm_location(event=geo, parcel_obj=p)

            match status:
                case 'IN RANGE':
                    await convo.send_message('You are in range. Are you sure to open?',
                                             buttons=[Button.inline('Yes!'),
                                                      Button.inline('Hell no!')])
                case 'OUT OF RANGE':
                    await convo.send_message(out_of_range_message_builder(parcel=p),
                                             buttons=[Button.inline('Yes!'),
                                                      Button.inline('Hell no!')])
                case 'NOT READY':
                    await convo.send_message(f'Parcel is not ready for pick up! Status: {p.status}')
                case 'DELIVERED':
                    await convo.send_message('Parcel has been already delivered!')
                    return

        else:
            await convo.send_message(
                f'Less than 2 minutes have passed since the last compartment opening, '
                f'you were in range of **{p.pickup_point.name}** parcel machine, '
                f'assuming you still are and skipping location verification.'
                f'\nAre you sure to open?',
                buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])
    else:
        await convo.send_message(
            f'You have location checking off or this parcel is in default parcel '
            f'machine, skipping! You can turn location checking on by sending:\n '
            f'`/set_geocheck {inp.phone_number} On`!\n\nAre you sure to open?',
            buttons=[Button.inline('Yes!'), Button.inline('Hell no!')])

    decision = await convo.wait_event(event=CallbackQuery(), timeout=30)

    match decision.data:
        case b'Yes!':
            if p_ := await open_comp(event, inp, p):
                await decision.reply(open_comp_message_builder(parcel=p_), buttons=Button.clear())
        case b'Hell no!':
            await decision.reply('Fine, compartment remains closed!', buttons=Button.clear())
        case _:
            await decision.reply('Unrecognizable decision made, please start opening compartment '
                                 'again!')


async def is_parcel_owner(inp, shipment_number, parcel_type) -> bool:
    parcel = await inp.get_parcel(shipment_number=shipment_number, parcel_type=parcel_type, parse=True)

    return parcel.ownership_status == ParcelOwnership.OWN
