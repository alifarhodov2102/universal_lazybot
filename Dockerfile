# Python-ning yengil versiyasi
FROM python:3.11-slim

# Muhit o'zgaruvchilari - Alice'ga kofesini sovuq bermaslik uchun ☕
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

# Tizim paketlarini o'rnatish
RUN apt-get update && apt-get install -y \
    poppler-utils \
    tesseract-ocr \
    libtesseract-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Ishchi katalog
WORKDIR /app

# Kutubxonalarni o'rnatish (Cache optimizatsiyasi bilan)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Loyihani nusxalash
COPY . .

# Botni ishga tushirish
CMD ["python", "main.py"]