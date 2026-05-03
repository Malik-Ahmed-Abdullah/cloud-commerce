FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt
RUN apt-get update && apt-get install -y curl   # add this line

COPY app.py .

EXPOSE 5003  # (5001 and 5002 for the other two)

CMD ["python", "app.py"]