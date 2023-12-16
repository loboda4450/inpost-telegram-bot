FROM python:alpine

WORKDIR /app

RUN chown nobody:nogroup /app
RUN apk add --no-cache --virtual .build-deps gcc build-base libffi-dev libretls-dev zlib-dev libjpeg-turbo-dev

RUN pip install --upgrade pip
ADD requirements.txt .
RUN pip install -r requirements.txt
RUN apk del .build-deps

COPY --chown=nobody:nogroup . .
USER nobody

ENTRYPOINT [ "python", "main.py" ]