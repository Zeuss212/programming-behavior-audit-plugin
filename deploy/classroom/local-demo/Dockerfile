FROM python:3.12-slim

WORKDIR /app
COPY deploy/classroom/local-demo/fincolab_demo.py /app/fincolab_demo.py
USER 65532:65532
EXPOSE 8080
CMD ["python", "/app/fincolab_demo.py"]
