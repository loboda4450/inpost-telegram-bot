from pony.orm import *
from telethon.events import NewMessage

db = Database('sqlite', 'inpost.sqlite', create_db=True)


class PhoneNumberConfig(db.Entity):
    user = Required('User')
    phone_number = PrimaryKey(int)
    sms_code = Optional(int)
    refr_token = Optional(str)
    auth_token = Optional(str)
    notifications = Required(bool)
    default_parcel_machine = Optional(str)
    geocheck = Required(bool)
    airquality = Required(bool)
    composite_key(user, phone_number)


class User(db.Entity):
    userid = PrimaryKey(int, size=64)
    phone_numbers = Set(PhoneNumberConfig)


db.generate_mapping(create_tables=True)


@db_session
def add_user(event: NewMessage):
    if not User.exists(userid=event.sender.id):
        User(userid=event.sender.id)


@db_session
def add_phone_number_config(event: NewMessage, phone_number: int, notifications: bool = True, geocheck: bool = True,
                            airquality: bool = True):
    if User.exists(userid=event.sender.id):
        user = User.get_for_update(userid=event.sender.id)

        user.phone_numbers.create(phone_number=phone_number,
                                  notifications=notifications,
                                  geocheck=geocheck,
                                  airquality=airquality)

        commit()


@db_session
def edit_phone_number_config(event: NewMessage, phone_number: int, sms_code: int | None = None,
                             refr_token: str | None = None, auth_token: str | None = None,
                             notifications: bool | None = None, default_parcel_machine: str | None = None,
                             geocheck: bool | None = None, airquality: bool | None = None):
    if not User.exists(userid=event.sender.id):
        return

    if not PhoneNumberConfig[phone_number]:
        return

    if PhoneNumberConfig[phone_number] not in User[event.sender.id].phone_numbers:
        return

    phone_number_config = PhoneNumberConfig.get_for_update(phone_number=phone_number)

    if sms_code:
        phone_number_config.sms_code = sms_code
    if refr_token:
        phone_number_config.refr_token = refr_token
    if auth_token:
        phone_number_config.auth_token = auth_token
    if notifications is not None:
        phone_number_config.notifications = notifications
    if default_parcel_machine:
        phone_number_config.default_parcel_machine = default_parcel_machine
    if geocheck is not None:
        phone_number_config.geocheck = geocheck
    if airquality is not None:
        phone_number_config.airquality = airquality

    commit()


@db_session
def delete_user(event: NewMessage):
    User.get(userid=event.sender.id).delete()


@db_session
def get_dict():
    return {user.userid: {
        phone_number.phone_number: {
            'phone_number': phone_number.phone_number,
            'sms_code': phone_number.sms_code,
            'refr_token': phone_number.refr_token,
            'auth_token': phone_number.auth_token,
            'notifications': phone_number.notifications,
            'default_parcel_machine': phone_number.default_parcel_machine,
            'geocheck': phone_number.geocheck,
            'airquality': phone_number.airquality,
        } for phone_number in user.phone_numbers.select()
    } for user in User.select()
    }
