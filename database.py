from datetime import datetime
from pony.orm import *
from telethon.events import NewMessage

db = Database('sqlite', 'inpost.sqlite', create_db=True)


class ParcelData(db.Entity):
    # TODO: QRCode getter
    # TODO: opencode getter
    # TODO: Date validation in getting details so they are not outdated
    phone_number = Required('PhoneNumberConfig')
    timestamp = Required(datetime)
    shipment_number = Required(str)
    parcel = Required(Json)

    def qrcode(self) -> str | None:
        return self.parcel.get('qrCode')

    def opencode(self) -> str | None:
        return self.parcel.get('openCode')
    #
    # def latest(self):
    #     # had to do it this way, pony seems not to be fully functionable on py3.11...
    #     return max([p for p in ParcelData.select() if p.parcel.get('shipmentNumber') == self.parcel.get('shipmentNumber')], key=lambda p: p.timestamp)


class PhoneNumberConfig(db.Entity):
    user = Required('User')
    default_to = Optional('User')
    parcels = Set('ParcelData')
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
    default_phone_number = Optional(PhoneNumberConfig, reverse='default_to')
    data_collecting_consent = Optional(bool)
    phone_numbers = Set(PhoneNumberConfig)


db.generate_mapping(create_tables=True)


@db_session
def get_user_consent(userid):
    if not User.exists(userid=userid):
        return False

    return User[userid].data_collecting_consent


@db_session
def set_user_consent(event: NewMessage, consent: bool):
    if not User.exists(userid=event.sender.id):
        return False

    u = User.get_for_update(userid=event.sender.id)
    u.data_collecting_consent = consent
    commit()
    return True


@db_session
def add_parcel(event: NewMessage, phone_number: int, parcel: dict):
    if not User.exists(userid=event.sender.id):
        return

    user = User.get_for_update(userid=event.sender.id)
    if PhoneNumberConfig.exists(phone_number=phone_number) and PhoneNumberConfig[phone_number].user == user:
        pn = PhoneNumberConfig.get_for_update(phone_number=phone_number)
        pn.parcels.create(timestamp=datetime.now(), parcel=parcel, shipment_number=parcel.get('shipmentNumber'))

        commit()
    return


@db_session
def add_user(event: NewMessage):
    if not User.exists(userid=event.sender.id):
        return User(userid=event.sender.id)


@db_session
def phone_number_exists(phone_number):
    return PhoneNumberConfig.exists(phone_number=phone_number)


@db_session
def add_phone_number_config(event: NewMessage, phone_number: int | str, notifications: bool = True,
                            geocheck: bool = True,
                            airquality: bool = True):
    if not User.exists(userid=event.sender.id):
        return

    if isinstance(phone_number, str):
        phone_number = int(phone_number)

    user = User.get_for_update(userid=event.sender.id)

    user.phone_numbers.create(phone_number=phone_number,
                              notifications=notifications,
                              geocheck=geocheck,
                              airquality=airquality)

    commit()


@db_session
def get_default_phone_number(userid: str | int):
    return User.get(userid=userid).default_phone_number


@db_session
def edit_default_phone_number(event: NewMessage, default_phone_number: int | str):
    if not User.exists(userid=event.sender.id):
        return

    if isinstance(default_phone_number, str):
        default_phone_number = int(default_phone_number)

    user = User.get_for_update(userid=event.sender.id)
    if PhoneNumberConfig.exists(phone_number=default_phone_number) and PhoneNumberConfig[
        default_phone_number].user == user:
        user.default_phone_number = default_phone_number
        commit()

    return


@db_session
def edit_default_parcel_machine(event: NewMessage, phone_number: int | str, default_parcel_machine: int | str):
    if not User.exists(userid=event.sender.id):
        return

    if isinstance(phone_number, str):
        phone_number = int(phone_number)

    user = User.get(userid=event.sender.id)
    if PhoneNumberConfig.exists(phone_number=phone_number) and PhoneNumberConfig[phone_number].user == user:
        phone = PhoneNumberConfig.get_for_update(phone_number=phone_number)
        phone.default_parcel_machine = default_parcel_machine
        commit()

    return


@db_session
def user_is_phone_number_owner(event: NewMessage):
    if not User.exists(userid=event.sender.id):
        return

    return int(event.text.split()[1].strip()) in (pn.phone_number for pn in User[event.sender.id].phone_numbers)


@db_session
def edit_phone_number_config(event: NewMessage, phone_number: int | str, sms_code: int | None = None,
                             refr_token: str | None = None, auth_token: str | None = None,
                             notifications: bool | None = None, default_parcel_machine: str | None = None,
                             geocheck: bool | None = None, airquality: bool | None = None):
    if not User.exists(userid=event.sender.id):
        return

    if isinstance(phone_number, str):
        phone_number = int(phone_number)

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
    if not User.exists(userid=event.sender.id):
        return

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
