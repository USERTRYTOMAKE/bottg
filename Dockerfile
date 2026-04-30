FROM python:3.11-slim

WORKDIR /app

# Окружение: не пишем байт-код и не буферизуем stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY . .

# Создаем папку для БД (на всякий случай)
RUN mkdir -p /app/data

# Запуск бота
CMD ["python", "main.py"]
