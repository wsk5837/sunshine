# TRM 科技资源管理系统 V4.7（标准目录版）

本版本将 V4.6 的功能增强、百分比重复修复以及 TAPD Mock/Live 同步能力，重新整理回稳定的标准项目结构。以后版本都建议在此结构上继续修改，不再扁平化目录。

## 标准目录

```text
科技资源管理系统_V4.7_标准目录版/
├── app/
│   ├── __init__.py
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

GitHub 仓库根目录必须直接看到 `app/`、`render.yaml`、`requirements.txt`。

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

真实 TAPD 凭据只放环境变量：

- `TRM_TAPD_API_USER`
- `TRM_TAPD_API_PASSWORD`

不要提交到 GitHub。
