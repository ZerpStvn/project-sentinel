FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir websockets==12.0
COPY stream.py .

EXPOSE 8765
CMD ["python", "stream.py"]
