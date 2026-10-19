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
    fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

# 设置阿里云pip源
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ENV PIP_TRUSTED_HOST=mirrors.aliyun.com

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 8000
# 启动流程：等MySQL → 校验并补齐依赖 → 收集静态文件 → 迁移 → 创建超级用户 → 启动gunicorn
# 说明：构建时已经装过一次依赖；启动时再校验一遍，
# 这样即使只改了 requirements.txt 而没有 --build，容器启动也会自动补齐新增依赖。
CMD ["sh", "-c", "\
until timeout 1 bash -c 'echo > /dev/tcp/db/3306'; do sleep 1; echo '等待MySQL...'; done; \
echo '[启动] 校验 Python 依赖(requirements.txt)...'; \
python -m pip install --no-cache-dir --quiet -r requirements.txt; \
if [ ! -f /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc ]; then echo '[启动] 安装中文字体 fonts-wqy-zenhei...'; apt-get update -qq && apt-get install -y --no-install-recommends fonts-wqy-zenhei >/dev/null 2>&1; fi; \
python manage.py collectstatic --noinput; \
python manage.py migrate --noinput && \
python manage.py createsuperuser --noinput 2>/dev/null || true; \
exec gunicorn cloth_system.wsgi:application --bind 0.0.0.0:8000 --workers ${DJANGO_WORKERS}\
"]
