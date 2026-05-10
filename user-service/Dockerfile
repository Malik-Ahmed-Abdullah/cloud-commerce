FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl
RUN pip install --upgrade pip

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py .

EXPOSE 5001   

CMD ["python", "app.py"]