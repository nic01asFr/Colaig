FROM python:3.11-slim

WORKDIR /code
COPY . .
RUN pip install --upgrade pip && pip install -e .

WORKDIR /code/app
ENV PYTHONPATH=/code/app

CMD ["python3", "-m", "app"]