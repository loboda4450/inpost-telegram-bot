from telethon.events import NewMessage


async def get_phone_number(inp: dict, event: NewMessage):
    if len(inp[event.sender.id]) == 1:
        return list(inp[event.sender.id])[0]
    elif inp[event.sender.id].default_phone_number and len(inp[event.sender.id]) != 1 and len(
            event.text.split(' ')) == 2:
        return inp[event.sender.id].default_phone_number
    else:
        return await validate_number(event=event, phone_number=True)


async def get_shipment_number(event: NewMessage):
    if event.text.split(' ') == 2:
        return event.raw_text.split(' ')[1].strip()
    elif event.text.split(' ') == 3:
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
