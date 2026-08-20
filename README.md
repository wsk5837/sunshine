# TRM 科技资源管理系统 V4.9（AI智能体对接版）

本版本保持 V4.8 标准项目目录和部署结构，在原有登录权限、需求全生命周期、项目、预算与TAPD能力上，接入 Gazellio G.AIOS 企业智能体。

## 本次关键调整

- “项目360视图–机器人”接入真实智能体，并将当前项目的任务、预算、需求、合同、结算、里程碑和价值指标作为只读事实上下文。
- “AI智能问答”页面改为多轮智能体对话，服务端自动保存并复用 G.AIOS `session_id`。
- 新增右下角全局 AI 助手悬浮球，登录后可在任意业务页面展开对话。
- 新增 `/api/ai/chat` 后端代理和 NDJSON 解析器，浏览器不直接调用外部平台，也不保存后台账号、密码或管理员 Token。
- 外部智能体不可用时，项目360与AI问答自动降级到原有本地事实问答，不影响POC演示。
- 系统管理 → 集成配置支持维护 G.AIOS 地址、公开 `agent_id` 和只读连通性检查。
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
科技资源管理系统_V4.9_AI智能体对接版/
├── app/
│   ├── __init__.py
│   ├── ai_gateway.py
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

### macOS 双击启动（推荐）

在项目根目录双击：

```text
启动系统.command
```

首次运行会自动创建 `.venv` 并安装依赖，启动成功后自动打开 `http://127.0.0.1:8000`。运行期间请保留打开的终端窗口。

不要直接双击 `app/static/index.html`。该文件只是前端页面，登录、业务数据、360机器人和AI助手都必须通过 FastAPI 后端运行。

### 命令行启动

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

浏览器访问：`http://127.0.0.1:8000`

## Gazellio G.AIOS 智能体配置

本版本默认使用已发布的 `default` 智能体，可以直接进行接口联调。推荐通过环境变量配置：

```bash
TRM_AI_MODE=live
TRM_AI_BASE_URL=https://adk.gazellio.com
TRM_AI_AGENT_ID=default
TRM_AI_CONNECT_TIMEOUT=8
TRM_AI_READ_TIMEOUT=120
```

也可以用管理员账号登录后，在“系统管理 → 集成配置 → AI问答服务”中修改服务地址和智能体公开标识。

后台现有“需求拆解智能体”的公开标识为 `lQy17aaNarxvYrFh`，更适合需求分析与功能点拆解，但当前仍为草稿。需要先在 G.AIOS 后台发布并保持“允许直接对话”，再把 `TRM_AI_AGENT_ID` 改成该标识。

运行时接口由服务端调用：

```text
POST https://adk.gazellio.com/adk/run_stream
Content-Type: application/json
Accept: application/x-ndjson
```

生产部署时不要把 `sys_admin` 账号、密码或后台管理 Token 写入环境变量或前端代码。本系统的用户登录、智能体白名单和请求审计均在 TRM 后端完成。

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
