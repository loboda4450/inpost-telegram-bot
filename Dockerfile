FROM python:3.11-alpine

WORKDIR /app

RUN chown nobody:nogroup /app \
	&& apk add --no-cache --virtual .build-deps gcc build-base libffi-dev libretls-dev zlib-dev jpeg-dev

ADD requirements.txt .
RUN pip install -r requirements.txt \
	&& apk del .build-deps

COPY --chown=nobody:nogroup . .
USER nobody

ENTRYPOINT [ "python", "main.py" ]