from pony.orm import *
from telethon.events import NewMessage

db = Database('sqlite', 'inpost.sqlite', create_db=True)


class User(db.Entity):
    userid = PrimaryKey(int, size=64)
    phone_number = Required(int)
    sms_code = Optional(int)
    refr_token = Optional(str)
    auth_token = Optional(str)


db.generate_mapping(create_tables=True)


@db_session
def add_user(event: NewMessage, phone_number: int, sms_code: int | None = None,
             refr_token: str | None = None, auth_token: str | None = None):
    if not User.exists(userid=event.sender.id):
        User(userid=event.sender.id, phone_number=phone_number)


@db_session
def edit_user(event: NewMessage, phone_number: int | None = None, sms_code: int | None = None,
              refr_token: str | None = None, auth_token: str | None = None):
    if User.exists(userid=event.sender.id):
        user = User.get_for_update(userid=event.sender.id)

        if phone_number:
            user.phone_numer = phone_number
        if sms_code:
            user.sms_code = sms_code
        if refr_token:
            user.refr_token = refr_token
        if auth_token:
            user.auth_token = auth_token

        commit()


@db_session
def delete_user(event: NewMessage):
    User.get(userid=event.sender.id).delete()


@db_session
def get_dict():
    return {user.userid: {'phone_number': user.phone_number,
                          'sms_code': user.sms_code,
                          'refr_token': user.refr_token,
                          'auth_token': user.auth_token} for user in User.select()}
