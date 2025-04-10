FROM python:3.11-slim

WORKDIR /code
COPY . .

# Installer wheel explicitement avant les autres dépendances
RUN pip install --upgrade pip wheel setuptools --timeout 1000 && \
    pip install --timeout 1000 --retries 5 -e .

WORKDIR /code/app
ENV PYTHONPATH=/code

CMD ["python", "-m", "app"]