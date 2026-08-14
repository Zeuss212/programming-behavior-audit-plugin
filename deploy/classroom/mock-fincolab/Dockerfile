FROM python:3.12-slim

WORKDIR /app
COPY deploy/classroom/mock-fincolab/app.py /app/app.py

EXPOSE 8080
CMD ["python", "/app/app.py"]
