from inpost.static import ParcelStatus, Parcel

pending_statuses = [ParcelStatus.READY_TO_PICKUP, ParcelStatus.CONFIRMED,
                    ParcelStatus.ADOPTED_AT_SORTING_CENTER, ParcelStatus.ADOPTED_AT_SOURCE_BRANCH,
                    ParcelStatus.COLLECTED_FROM_SENDER, ParcelStatus.DISPATCHED_BY_SENDER,
                    ParcelStatus.DISPATCHED_BY_SENDER_TO_POK, ParcelStatus.OUT_FOR_DELIVERY,
                    ParcelStatus.OUT_FOR_DELIVERY_TO_ADDRESS, ParcelStatus.SENT_FROM_SOURCE_BRANCH,
                    ParcelStatus.TAKEN_BY_COURIER, ParcelStatus.TAKEN_BY_COURIER_FROM_POK,
                    ParcelStatus.STACK_IN_BOX_MACHINE, ParcelStatus.STACK_IN_CUSTOMER_SERVICE_POINT,
                    ParcelStatus.PICKUP_REMINDER_SENT, ParcelStatus.PICKUP_REMINDER_SENT_ADDRESS]

welcome_message = 'Hello!\nThis is a bot helping you to manage your InPost parcels!\n' \
                  'If you want to contribute to Inpost development you can find us there: ' \
                  '[Inpost](https://github.com/IFOSSA/inpost-python)\n\n' \
                  'Log in using button that just shown up below the text box ' \
                  'or by typing `/init <phone_number>`!\n\n' \
                  '**List of commands:**\n' \
                  '/start - display start message and allow user to login with Telegram\n' \
                  '/init - login using phone number `/init <phone_number>`\n' \
                  '/me - show information about all initialized accounts\n' \
                  '/pending - return pending parcels\n' \
                  '/delivered - return delivered parcels\n' \
                  '/parcel - return parcel `/parcel <shipment_number>`\n' \
                  '/friends - list all known inpost friends \n' \
                  '/share <reply to parcel message> - share parcel to listed friend\n' \
                  '/all - return all available parcels\n' \
                  '/clear - if you accidentally invoked `/start` and annoying box sprang up\n\n' \
                  'Deprecated commands:\n' \
                  '/refresh - refresh authorization token\n' \
                  '/confirm <sms code> - confirm login with sms code' \
                  '\n\n\nBeware, before you start using this bot you must **set your consent** to data collecting. ' \
                  'It will affect all your connected accounts. Dev team assure you, that this data **will not** be ' \
                  'used or sold and all sensitive ones like your name and surname **will be** removed. ' \
                  'Collected data are used for three reasons:\n' \
                  '    - cache - if we save your data bot has to less frequently gather data from inpost,\n' \
                  '    - development,\n' \
                  '    - debugging - in case some error occurs.\n' \
                  '\n\nYou can set your consent by typing `/consent yes` or `/consent no`' \

not_enough_parameters_provided = 'No phone number provided or no option selected!\n' \
           'You can set your default phone number by sending `/set_default_phone_number <phone_number>` ' \
           'or invoke this command by sending message following this template' \
           '`/command <phone_number> <following part of command (e.g shipment number, On/Off)>`'


def courier_message_builder(package: Parcel) -> str:
    return f'📤 **Sender:** `{package.sender.sender_name}`\n' \
           f'📦 **Shipment number:** `{package.shipment_number}`\n' \
           f'📮 **Status:** `{package.status.value}`\n'


def delivered_message_builder(package: Parcel) -> str:  # Duplicate just to be clear
    return f'📤 **Sender:** `{package.sender.sender_name if package.sender is not None else None}`\n' \
           f'📦 **Shipment number:** `{package.shipment_number}`\n' \
           f'📮 **Status:** `{package.status.value}`\n'


def multicompartment_message_builder(amount: int, package: Parcel, other: str) -> str:
    return f'⚠️ **THIS IS MULTICOMPARTMENT CONTAINING {amount} PARCELS!** ⚠\n️\n' \
           f'📤 **Sender:** `{package.sender.sender_name}`\n' \
           f'📦 **Shipment number:** `{package.shipment_number}`\n' \
           f'📮 **Status:** `{package.status.value}`\n' \
           f'📥 **Pick up point:** `{package.pickup_point}, {package.pickup_point.city} ' \
           f'{package.pickup_point.street} {package.pickup_point.building_number}`\n\n' \
           f'Other parcels inside:\n{other}'


def compartment_message_builder(package: Parcel) -> str:
    return f'📤 **Sender:** `{package.sender.sender_name}`\n' \
           f'📦 **Shipment number:** `{package.shipment_number}`\n' \
           f'📮 **Status:** `{package.status.value}`\n' \
           f'📥 **Pick up point:** `{package.pickup_point}, {package.pickup_point.city} ' \
           f'{package.pickup_point.street} {package.pickup_point.building_number}`'


def details_message_builder(parcel: Parcel, events: str) -> str:
    return f'**Shipment number**: {parcel.shipment_number}\n' \
           f'**Stored**: {parcel.stored_date.to("local").format("DD.MM.YYYY HH:mm")}\n' \
           f'**Open code**: {parcel.open_code}\n' \
           f'**Events**:\n{events}\n\n'


def open_comp_message_builder(parcel: Parcel) -> str:
    return f'Compartment opened!\nLocation:\n   ' \
           f'Side: {parcel.compartment_location.side}\n   ' \
           f'Row: {parcel.compartment_location.row}\n   ' \
           f'Column: {parcel.compartment_location.column}'


def ready_to_pickup_message_builder(parcel: Parcel, events: str, air_quality: str | None) -> str:
    msg = f'**Stored**: {parcel.stored_date.to("local").format("DD.MM.YYYY HH:mm")}\n' \
          f'**Open code**: {parcel.open_code}\n' \
          f'**Events**:\n{events}'

    if air_quality:
        msg = msg + air_quality

    return msg


def out_of_range_message_builder(parcel: Parcel) -> str:
    return f'Your location is outside the range that is allowed to open this parcel machine. ' \
           f'Confirm that you are standing nearby, there is description:' \
           f'\n\n**Name: {parcel.pickup_point.name}**' \
           f'\n**Address: {parcel.pickup_point.post_code} {parcel.pickup_point.city}, ' \
           f'{parcel.pickup_point.street} {parcel.pickup_point.building_number}**\n' \
           f'**Description: {parcel.pickup_point.description}**\n\n' \
           f'Do you still want me to open it for you?'


def friend_invitations_message_builder(friend) -> str:
    return f'**Name**: {friend["friend"]["name"]}\n' \
           f'**Phone number**: {friend["friend"]["phoneNumber"]}\n' \
           f'**Invitation code**: `{friend["invitationCode"]}`\n' \
           f'**Expiry date**: {friend["expiryDate"]}'


def use_command_as_reply_message_builder(command: str) -> str:
    return f'Buttons works only with default phone number. ' \
           f'Please set up one before using them or type following command and send it as a reply to desired parcel: ' \
           f'\n`{command} <phone_number>'
