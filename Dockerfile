FROM python:3.12-slim

WORKDIR /app

RUN mkdir -p /app/logs

# MySQL编译依赖 + 工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    pkg-config \
    libmariadb-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn python-dotenv

COPY . .

EXPOSE 8000

# 启动流程：等MySQL → 收集静态文件写入共享卷 → 迁移 → 创建超级用户 → 启动gunicorn
CMD ["sh", "-c", "\
until timeout 1 bash -c 'echo > /dev/tcp/db/3306'; do sleep 1; echo '等待MySQL...'; done; \
python manage.py collectstatic --noinput; \
python manage.py migrate --noinput && \
python manage.py createsuperuser --noinput 2>/dev/null || true; \
exec gunicorn cloth_system.wsgi:application --bind 0.0.0.0:8000 --workers ${DJANGO_WORKERS}\
"]