# 服装订单管理系统 — 技术文档

## 项目概述

一个面向服装面料行业的订单管理 Web 应用，基于 Django 5.2 开发。涵盖订单录入/跟踪、库存管理、供应商协同、Excel 导入导出、数据看板等功能。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | Django 5.2 |
| 数据库 | MySQL 8.0（生产）/ SQLite（开发兜底） |
| 缓存 | 本地内存缓存（LocMemCache，开发用） |
| Web 服务器 | Nginx（反向代理 + 静态文件） |
| WSGI 服务器 | Gunicorn |
| 容器化 | Docker + Docker Compose |
| Python 依赖 | pandas, openpyxl, mysqlclient, gunicorn, python-dotenv |
| 前端 | Bootstrap 5 + Chart.js（仪表盘图表） |

## 项目结构

```
cloth-system/
├── cloth_system/              # Django 项目配置
│   ├── settings.py            # 全局配置（数据库、日志、安全等）
│   ├── urls.py                # 根路由
│   └── wsgi.py                # WSGI 入口
├── order/                     # 核心业务应用
│   ├── models.py              # 数据模型（5 个 Model）
│   ├── views.py               # 视图函数 + 类视图（~2400 行）
│   ├── urls.py                # 业务路由（40+ 条）
│   ├── forms.py               # 表单定义（5 个 Form）
│   ├── admin.py               # Django Admin 配置
│   ├── decorators.py          # 自定义装饰器
│   ├── mixins.py              # 模型混合类（代码复用）
│   ├── constants.py           # 常量定义
│   ├── dashboard_stats.py     # 看板统计逻辑
│   ├── import_progress.py     # 异步导入任务管理（缓存 + 后台线程）
│   ├── order_excel.py         # 订单 Excel 导入导出
│   ├── inventory_excel.py     # 库存 Excel 导入导出
│   ├── order_filters.py       # 订单列表筛选
│   ├── statement.py           # 对账单生成
│   ├── tests.py               # 测试（当前为空）
│   └── templates/order/       # HTML 模板
├── docker-compose.yml         # Docker 编排
├── Dockerfile                 # 应用镜像构建
├── nginx.conf                 # Nginx 配置
├── requirements.txt           # Python 依赖
└── .env                       # 环境变量（密钥、数据库配置）
```

## 数据模型（5 个）

### Supplier（供应商账号）
- `user` → 关联 `auth.User`（一对一）
- `company_name` — 公司名称（唯一、索引）
- `contact_person`, `phone`, `is_active`
- 登录后可查看分配给自己的订单并填写价格

### ClothOrder（服装订单）— **核心模型**
- 继承 `ClothCatalogMixin` + `ProgressStageMixin`
- 40+ 字段，涵盖：
  - **基本信息**：订单编号、日期、客户、订单类型（大货/样品/印花/其他）
  - **产品信息**：布种编号、颜色、规格、成分、门幅、克重
  - **价格与金额**：单价、数量、总金额、成品售价
  - **供应商信息**：供应商、仓库
  - **进度**：进度阶段 JSON、当前进度
  - **付款**：付款状态、逾期状态、付款日期
  - **出货**：5 个批次的出货日期和数量
  - **品检**：品检状态、色牢度、缩水率、证书

### InventoryItem（布料库存）
- 字段类似 ClothOrder 的产品部分，加上库存数量、库位
- `quantity=0` 时标记为"零库存"

### InventoryLog（库存变动记录）
- `item` → InventoryItem（外键）
- `log_type` — 'in'（入库） / 'out'（出库）
- 变动前后的数量、操作人、时间

### ClothCatalog（布种编号表）
- 从外部 Excel 导入的布种字典
- `cloth_code`, `cloth_name`, `color`, `specification`, `composition_cn` 等

## 视图一览

### 认证
| URL | 视图 | 说明 |
|---|---|---|
| `/` | `home_view` | 登录页，POST 登录，已登录跳转看板 |
| `/logout/` | `logout_view` | 登出 |

### 仪表盘
| URL | 视图 | 说明 |
|---|---|---|
| `/dashboard/` | `dashboard_view` | 统计卡片 + 月度趋势图 + Top5 客户/供应商 |

### 订单管理
| URL | 视图 | 说明 |
|---|---|---|
| `/orders/` | `OrderListView` | 订单列表（分页、筛选、搜索、批量操作） |
| `/orders/add/` | `OrderCreateView` | 新增订单（分步表单） |
| `/orders/<pk>/` | `OrderDetailView` | 订单详情 + 内联编辑 |
| `/orders/<pk>/edit/` | `order_edit_redirect` | 编辑重定向 |
| `/orders/import/` | `orders_import_page` + `_start` + `_progress` | Excel 异步导入 |
| `/orders/export/` | `orders_export` | Excel 导出 |
| `/orders/delete-all/` | `orders_delete_all` | 清空所有订单 |

### 库存管理
| URL | 视图 | 说明 |
|---|---|---|
| `/inventory/` | `InventoryListView` | 库存列表（分页、筛选） |
| `/inventory/import/` | `inventory_import_page` + `_start` + `_progress` | Excel 异步导入 |
| `/inventory/export/` | `inventory_export` | Excel 导出 |
| `/inventory/delete-all/` | `inventory_delete_all` | 清空库存 |

### 布种目录
| URL | 视图 | 说明 |
|---|---|---|
| `/cloth-catalog/` | `ClothCatalogListView` | 布种列表 |
| `/cloth-catalog/import/` | `cloth_catalog_import` + `_start` + `_progress` | Excel 异步导入 |
| `/cloth-catalog/api/autocomplete/` | `cloth_catalog_autocomplete` | 自动补全 API |

### 供应商端
| URL | 视图 | 说明 |
|---|---|---|
| `/supplier/` | `SupplierDashboardView` | 供应商面板 |
| `/supplier/manage/` | 增删改查 | 管理员管理供应商账号 |
| `/supplier/orders/<pk>/` | `SupplierOrderDetailView` | 供应商填写价格 |

## 关键技术设计

### 1. 异步导入机制

三种导入（订单、库存、布种）共用同一套异步模式：

```
前端上传文件 → 后端创建缓存任务(task_id) → 后台线程执行 → 前端轮询进度
```

- 任务状态存储在 Django 缓存（`LocMemCache`）中
- `import_progress.py` 封装了三个命名空间：`order_import:*`、`inventory_import:*`、`catalog_import:*`
- 前端使用 `setInterval` 轮询 `/progress/<task_id>/` 接口

### 2. 权限体系

```
游客 → 登录页
├── 供应商账号（有 supplier_profile） → 供应商面板，只能查看/填写分配到的订单
└── 管理员（is_staff=True） → 管理后台，全部功能可用
```

- `@admin_required` 装饰器：组合了 `@login_required` + 供应商拦截
- `SupplierRequiredMixin`：供应商专属视图的权限控制
- `AdminRequiredMixin`：CBV 版本的管理员权限控制

### 3. 代码复用

- `mixins.py` — `ClothCatalogMixin`（布种查询）、`ProgressStageMixin`（进度阶段）、`AdminRequiredMixin`（权限检查）
- `decorators.py` — `@admin_required`、`@validate_file_upload`、`@validate_ids`
- `_import_progress_response()` — 三个导入进度查询共用的通用函数

### 4. Excel 导入导出

- 订单：`order_excel.py` — `export_orders_dataframe()` / `import_orders_from_file()`
- 库存：`inventory_excel.py` — `export_inventory_dataframe()`
- 格式：所有导出使用 `pandas.ExcelWriter` + `openpyxl`
- 导入：读取为 DataFrame 后逐行写入数据库，BATCH=200

### 5. 日志

| 输出目标 | 级别 | 文件 |
|---|---|---|
| 文件 | ERROR | `logs/django-error.log` |
| 文件 | INFO | `logs/django-info.log` |
| 控制台 stdout | INFO | 终端输出 |

## 部署

### Docker Compose（推荐）

```bash
# 1. 准备 .env 文件
DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=False
DB_NAME=cloth_order_db
DB_USER=root
DB_PASSWORD=your-password
DB_HOST=db
DB_PORT=3306

# 2. 启动
docker-compose up -d

# 3. 执行数据库迁移
docker-compose exec web python manage.py migrate

# 4. 收集静态文件
docker-compose exec web python manage.py collectstatic --noinput
```

服务监听 `8233` 端口（Nginx），包含 MySQL + Redis + Nginx 三个依赖容器。

### 手动部署

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic
gunicorn cloth_system.wsgi:application -b 0.0.0.0:8000
```

## 开发说明

### 运行开发服务器

```bash
python manage.py runserver
```

### 注意事项

- 环境变量 `DJANGO_SECRET_KEY` **必须设置**（生产环境），开发环境有默认值兜底
- 环境变量 `DB_NAME` 不设时会自动回落 SQLite，方便本地开发
- 日志目录 `logs/` 自动创建
- 静态文件收集到 `staticfiles/`

## 安全

### 会话超时

- 当前默认已注释停用；后续需要时恢复 `cloth_system/settings.py`、`order/views.py`、两个 base 模板中的注释即可
- 供应商账号默认空闲 30 分钟、超管账号默认空闲 15 分钟后强制退出并重新登录
- 超时分钟数可通过 `SUPPLIER_SESSION_IDLE_TIMEOUT_MINUTES`、`SUPERUSER_SESSION_IDLE_TIMEOUT_MINUTES` 环境变量调整，设为 `0` 可关闭
- 服务端由 `order.middleware.SessionTimeoutMiddleware` 强制执行，前端倒计时仅作提醒
- 通知、监控、导入进度等后台轮询接口不会延长会话

## 系统监控

### 依赖服务健康检查

- 监控页新增数据库、Redis、Nginx 状态卡片，展示正常 / 异常 / 未配置及响应延迟
- 数据库按 Django 当前配置探测（MySQL 或 SQLite 均可），Redis 仅在配置 `REDIS_URL` 时探测
- Nginx 通过 `NGINX_CHECK_URL` 探测，生产使用 FRP 公网地址（如 `https://your-nginx.example.com:40614`），使用 GET 请求并兼容自签名证书
- 服务未配置时显示“未配置”，不会导致监控页报错
