FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-proctor.txt ./
RUN pip install --no-cache-dir -r requirements-proctor.txt

COPY proctor.py behavioral_analysis.py frame_buffer.py .
COPY weights/ weights/
COPY scripts/constrained-hardware-test/smoke_test.py .
COPY scripts/constrained-hardware-test/endurance_test.py .

ENV PROCTOR_ORT_THREADS=2
ENV OMP_NUM_THREADS=2

CMD ["python3", "smoke_test.py"]
