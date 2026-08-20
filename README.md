# TRM 科技资源管理系统 V4.8（登录与权限重构版）

本版本继续使用标准项目目录，不再改变部署结构；重点完成登录认证、用户管理、角色管理、菜单手风琴逻辑，以及需求/立项入口合并。

## 本次关键调整

- 新增独立登录页面，未登录时不能直接进入系统业务 API。
- 新增用户管理：账号、姓名、部门、邮箱、手机号、角色、启停状态、最近登录、密码重置。
- 新增角色管理：角色名称、说明、启停状态、菜单权限配置、自定义角色。
- 右上角用户区域不再允许“一键切换角色”，只显示当前账号信息、修改密码和退出登录。
- 左侧父级菜单改为手风琴逻辑：展开一个分组时，其他已展开分组自动收起。
- “需求申请 + 需求查询”合并：菜单只保留“需求列表”，列表右上角“新建需求”进入空白需求申请单。
- “立项申请 + 立项查询”合并：菜单只保留“立项列表”，列表右上角“新建立项”进入空白立项单。
- 修复新建需求错误带入上一次需求数据的问题：只有点击“编辑”时才加载指定需求，点击“新建需求”始终为空白单。
- 保留 V4.7 之前的 POC 全链路、TAPD Mock/Live、预算、项目、合同、结算、指标等功能。

## 初始登录账号

系统首次初始化会创建以下账号：

- 管理员：`admin` / `Admin@123`
- 业务演示账号（如 `lili11-ghq`、`wangzg`、`zhaomin` 等）：密码统一为 `Demo@123`

公网部署后建议管理员首次登录立即修改密码。

## 标准目录

```text
科技资源管理系统_V4.8_登录与权限重构版/
├── app/
│   ├── __init__.py
│   ├── auth.py
│   ├── main.py
│   ├── db.py
│   ├── extended.py
│   ├── poc.py
│   ├── rules.py
│   ├── v4.py
│   └── static/
│       ├── index.html
│       ├── app.js
│       └── app.css
├── data/
├── uploads/
├── runtime/
├── tests/
├── .gitignore
├── .python-version
├── .env.example
├── requirements.txt
├── render.yaml
├── render-persistent.yaml
├── Dockerfile
├── docker-compose.yml
├── start.sh
└── README.md
```

## 本地启动

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

浏览器访问：`http://127.0.0.1:8000`

## Render 部署

GitHub 仓库根目录必须直接看到：

```text
app/
render.yaml
requirements.txt
```

Render Root Directory：**留空**。

Build Command：

```bash
pip install -r requirements.txt
```

Start Command：

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"
```

Python：3.13.5。

## TAPD Live 模式

真实 TAPD 凭据仅配置在 Render / 本地环境变量：

- `TRM_TAPD_API_USER`
- `TRM_TAPD_API_PASSWORD`

不要提交到 GitHub。
