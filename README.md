# 服装订单管理系统

基于 Django 5.2 的服装面料行业订单管理系统，覆盖订单管理、供应商协同、库存管理、布种编号、面料报价和系统监控。

## 功能特性

- 订单全流程管理：新增、编辑、详情、进度跟踪、出货批次、对账、批量操作、Excel 导入导出
- 供应商协同：供应商账号、供应商面板、分配订单、填写报价、提交出货
- 库存管理：库存列表、库存变动日志、Excel 导入导出
- 基础资料：布种编号目录、面料报价
- 数据看板：月度趋势、Top 客户/供应商、财务汇总
- 通知中心：站内通知、未读提醒
- 系统监控：CPU、内存、磁盘、项目体积、日志，以及 MySQL / Redis / Nginx 依赖服务状态
- 安全管理：登录限流、管理员权限校验、CSRF 防护、Nginx 安全响应头

## 技术栈

| 分类 | 技术 |
| --- | --- |
| 后端 | Django 5.2、Python 3.12 |
| 数据库 | MySQL 8（生产）、SQLite（本地兜底） |
| 缓存 | Redis（生产）、本地内存缓存（本地兜底） |
| Web 服务 | Gunicorn、Nginx |
| 前端 | Bootstrap 5、Chart.js、ECharts |
| 数据处理 | pandas、openpyxl |
| 部署 | Docker Compose |

## 项目结构

```text
cloth-system/
├── cloth_system/            # Django 项目配置
├── order/                   # 核心业务应用
│   ├── management/commands/ # 自定义管理命令
│   ├── templates/order/     # 页面模板
│   ├── middleware.py        # 会话超时中间件（默认注释停用）
│   ├── service_monitor.py   # 依赖服务健康探测
│   ├── monitor.py           # 系统指标采集
│   └── views.py             # 业务视图
├── static/                  # 原始静态资源
├── staticfiles/             # collectstatic 输出目录
├── logs/                    # 运行日志
├── docker-compose.yml
├── Dockerfile
├── nginx.conf
└── .env.example
```

## 快速开始

### 本地开发

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # 按需修改配置
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

访问 `http://127.0.0.1:8000/`。未配置 `DB_NAME` 时自动使用 SQLite，未配置 `REDIS_URL` 时使用本地内存缓存。

### Docker Compose

```bash
cp .env.example .env
docker compose up -d --build
```

容器启动时会自动执行 `collectstatic`、`migrate`，并在未创建超管时尝试使用 `DJANGO_SUPERUSER_*` 环境变量创建账号。Nginx 对外端口为 `8233`，访问 `http://服务器IP:8233/`。

常用容器命令：

```bash
docker compose ps
docker compose logs -f web
docker compose logs -f nginx
docker compose exec web python manage.py migrate
```

## 环境变量

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Django 密钥，生产环境必须替换 | 开发默认值 |
| `DJANGO_DEBUG` | 生产环境必须为 `False` | `True` |
| `DJANGO_ALLOWED_HOSTS` | 允许访问的域名，逗号分隔 | `localhost,127.0.0.1` |
| `DB_NAME` | MySQL 数据库名，不配置则使用 SQLite | 空 |
| `DB_USER` / `DB_PASSWORD` | MySQL 账号密码 | 空 |
| `DB_HOST` / `DB_PORT` | MySQL 地址和端口 | `127.0.0.1` / `3306` |
| `REDIS_URL` | Redis 连接地址，不配置则使用本地内存缓存 | 空 |
| `NGINX_CHECK_URL` | Nginx 健康探测地址 | `https://your-nginx.example.com:40614` |
| `DJANGO_WORKERS` | Gunicorn worker 数 | 空 |
| `DJANGO_SUPERUSER_USERNAME` | 首次启动自动创建超管用户名 | 空 |
| `DJANGO_SUPERUSER_PASSWORD` | 首次启动自动创建超管密码 | 空 |
| `DJANGO_SUPERUSER_EMAIL` | 超管邮箱 | 空 |

会话超时相关变量已保留但默认注释停用：

| 变量 | 说明 |
| --- | --- |
| `SUPPLIER_SESSION_IDLE_TIMEOUT_MINUTES` | 供应商账号空闲超时分钟数 |
| `SUPERUSER_SESSION_IDLE_TIMEOUT_MINUTES` | 超管账号空闲超时分钟数 |
| `SESSION_TIMEOUT_WARNING_SECONDS` | 前端退出提醒倒计时秒数 |

## 常用命令

```bash
python manage.py check
python manage.py migrate
python manage.py makemigrations
python manage.py collectstatic --noinput
python manage.py test order.tests
```

自定义管理命令：

```bash
python manage.py create_supplier
python manage.py import_quotations
python manage.py fix_progress_stages
```

## 主要页面

| 地址 | 说明 |
| --- | --- |
| `/` | 登录页 |
| `/dashboard/` | 管理端仪表盘 |
| `/orders/` | 订单列表 |
| `/inventory/` | 库存管理 |
| `/cloth-catalog/` | 布种编号 |
| `/quotation/` | 面料报价 |
| `/supplier/` | 供应商面板 |
| `/notifications/` | 通知中心 |
| `/monitor/` | 系统监控 |
| `/admin/` | Django Admin |

## 系统监控

`/monitor/` 仅管理员可访问，包含：

- 系统指标：CPU、内存、磁盘、项目目录占用、应用进程
- 日志查看：日志文件列表、日志筛选、自动刷新
- 依赖服务：数据库、Redis、Nginx 的状态、延迟和检查时间

依赖服务探测规则：

- 数据库使用 Django 当前配置探测，MySQL 或 SQLite 均可
- Redis 仅在配置 `REDIS_URL` 时探测
- Nginx 使用 `NGINX_CHECK_URL`，生产默认通过 FRP 公网地址探测
- 未配置的服务显示“未配置”，不会影响监控页

## 部署上线

```bash
cd /path/to/cloth-system
git pull

docker compose exec web python manage.py migrate --noinput
docker compose exec web python manage.py collectstatic --noinput
docker compose up -d --build

docker compose ps
curl -I http://127.0.0.1:8233/
```

上线后确认：

- `.env` 中 `DJANGO_DEBUG=False`
- `/monitor/` 中 MySQL、Redis、Nginx 均显示正常
- `docker compose logs -f web` 无报错

## 安全说明

- 生产环境必须设置 `DJANGO_SECRET_KEY` 并保持 `DJANGO_DEBUG=False`
- `.env` 和 `db.sqlite3` 已加入 `.gitignore`，不要提交
- 登录接口有每 IP 每分钟 5 次尝试限制
- 供应商越权访问、订单 IDOR 等已做权限校验
- Nginx 已配置 X-Frame-Options、X-Content-Type-Options、Referrer-Policy 等安全头
- 依赖升级前建议先在测试环境验证，并定期检查 Django 安全公告
