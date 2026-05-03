FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt
RUN apt-get update && apt-get install -y curl   # add this line

COPY app.py .

EXPOSE 5001   # (5002 and 5003 for the other two)

CMD ["python", "app.py"]