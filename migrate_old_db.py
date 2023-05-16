import database

from pony.orm import *

old_db = Database('sqlite', 'path/to/your/database')


class User(old_db.Entity):
    userid = PrimaryKey(int, size=64)
    phone_number = Required(int)
    sms_code = Optional(int)
    refr_token = Optional(str)
    auth_token = Optional(str)


old_db.generate_mapping()

for user in User.select():
    new_user = database.User(userid=user.userid)
    new_user.phone_numbers.create(phone_number=user.phone_number, sms_code=user.sms_code, refr_token=user.refr_token,
                                  auth_token=user.auth_token, notifications=True, geocheck=True, airquality=True)

commit()
