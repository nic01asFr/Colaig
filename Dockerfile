FROM python:3.11-slim

WORKDIR /code
COPY . .

# Installation avec timeout augmenté et retries
RUN pip install --upgrade pip --timeout 1000 && \
    pip install --timeout 1000 --retries 5 -e .

WORKDIR /code/app
ENV PYTHONPATH=/code

CMD ["python", "-m", "app"]