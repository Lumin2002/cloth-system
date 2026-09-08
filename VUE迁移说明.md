# Vue 化迁移说明

## 当前结论

项目采用 **Django 服务端渲染 + Vue 3 局部组件** 的渐进式迁移方式，不改成完整 SPA。

本次先做“新增订单页”的 Vue 试点，控制改动范围，保证现有功能不回归。

---

## 本次已 Vue 化的位置

### 页面

- 新增订单页：`order/templates/order/order_create.html`
- 编辑订单页：`order/templates/order/order_form.html`
- 订单详情编辑态：`order/templates/order/order_detail.html`
- 客户管理列表：`order/templates/order/customer_list.html`
- 仪表盘汇总区：`order/templates/order/dashboard.html`
- 布种编号列表：`order/templates/order/cloth_catalog_list.html`
- 面料报价列表：`order/templates/order/quotation_list.html`
- 供应商账号列表：`order/templates/order/supplier_manage_list.html`
- 库存日志列表：`order/templates/order/inventory_log_list.html`

### Vue 组件文件

- `order/templates/order/_order_interactions_vue.html`
- `order/templates/order/_customer_list_vue.html`
- `order/templates/order/_dashboard_summary_vue.html`
- `order/templates/order/_cloth_catalog_list_vue.html`
- `order/templates/order/_quotation_list_vue.html`
- `order/templates/order/_supplier_manage_list_vue.html`
- `order/templates/order/_vue_pagination_component.html`
- `order/templates/order/_inventory_log_list_vue.html`

### Vue 负责的内容

1. 客户已有客户建议
   - 数据接口：`/customers/api/options/`
   - 用 Vue 渲染 `<datalist id="customer-options">`
   - 订单表单里的客户输入框仍由 Django 渲染，通过 `list="customer-options"` 关联

2. 历史价格参考
   - 数据接口：`/orders/price-history/`
   - Vue 根据订单类型、布种、客户、颜色自动加载最近 5 次价格
   - 点击历史价格后同步：
     - 价格
     - 价格单位
     - 数量单位

3. Vue 采用自定义插值符
   - Django 模板默认使用 `{{ }}`
   - Vue 组件设置为 `[[ ]]`
   - 避免 Django 和 Vue 模板语法冲突

4. 客户管理卡片列表
   - Django 只负责输出初始客户 JSON
   - Vue 负责搜索、状态筛选和卡片渲染
   - 编辑、删除仍走原有 Django 接口

5. 仪表盘汇总区
   - Vue 负责核心指标卡片、状态快捷入口、本月对比卡片
   - 图表仍保留 ECharts，后续再单独评估是否 Vue 化

6. 布种编号、面料报价、供应商账号列表
   - Django 输出初始 JSON
   - Vue 负责客户端搜索和表格渲染
   - 新增、编辑、删除仍走 Django 原有页面和接口
   - 布种编号、面料报价带客户端分页
   - 供应商账号已改为卡片展示
   - 分页组件已抽成通用 `pagination-bar`，供各列表页复用

7. 库存日志列表
   - Vue 负责搜索、类型筛选、表格渲染和分页

---

## 还未 Vue 化的位置

以下页面仍使用原有原生 JS + Django 模板：

- 订单列表、供应商端、库存、报价等页面

原因：

- 先验证新增订单页的 Vue 交互和接口联调
- 稳定后再逐步复制到编辑订单页
- 避免一次性改多个页面导致回归

---

## 后续建议顺序

1. 将订单列表的筛选、批量选择、表头排序逐步 Vue 化。
2. 如需更大规模改造，再引入 Vite + Vue 3 独立前端，Django 改造成 API。

---

## 依赖

当前 Vue 通过 CDN 引入：

```text
https://unpkg.com/vue@3/dist/vue.global.prod.js
```

如果后续需要内网离线运行，可以把 Vue 下载到 `order/static/js/vue.global.prod.js`，再改为本地静态文件引用。

---

## 全局输入规则

Vue 化过程中，同时加入了全局交互规则：

- 所有 `input[type="number"]` 编辑框禁止通过鼠标滚轮修改数值。
- 该规则写在公共模板 `order/templates/order/base.html` 中，所有页面自动生效。
- 所有页面增加“回到顶部”浮动按钮，滚动超过 300px 后显示。
