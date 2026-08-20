# TRM 科技资源管理系统 V4.9（AI智能体对接版）

本版本保持 V4.8 标准项目目录和部署结构，在原有登录权限、需求全生命周期、项目、预算与TAPD能力上，接入 Gazellio G.AIOS 企业智能体。

## 本次关键调整

- “项目360视图–机器人”接入真实智能体，并将当前项目的任务、预算、需求、合同、结算、里程碑和价值指标作为只读事实上下文。
- “AI智能问答”页面改为多轮智能体对话，服务端自动保存并复用 G.AIOS `session_id`。
- 新增右下角全局 AI 助手悬浮球，登录后可在任意业务页面展开对话。
- 新增 `/api/ai/chat` 后端代理和 NDJSON 解析器，浏览器不直接调用外部平台，也不保存后台账号、密码或管理员 Token。
- 新增标准 Streamable HTTP MCP 服务 `/mcp/`，供 G.AIOS 智能体通过工具查询TRM预算/项目/需求，并在用户确认后创建项目或需求草稿。
- MCP写操作实施“预览→用户确认→幂等创建”，服务端独立Bearer鉴权、写操作总开关、固定服务身份和完整审计。
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
│   ├── trm_mcp.py
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
├── config/
│   └── gaios-mcp-config.example.json
├── scripts/
│   └── check_mcp.py
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

## TRM MCP 工具对接

### 调用链路

```text
TRM网页（AI问答/360机器人/悬浮助手）
  -> POST /api/ai/chat
  -> Gazellio G.AIOS 智能体
  -> Streamable HTTP MCP
  -> https://<TRM公网域名>/mcp/
  -> TRM原有业务数据库与审计日志
```

G.AIOS 运行在远程服务器，无法访问你电脑上的 `127.0.0.1:8000`。正式联调前必须先把 TRM 部署到 G.AIOS 可访问的 HTTPS 域名；本项目不会自动创建公网隧道或上传代码。

### 已封装工具

- `trm_list_budgets`：查询预算及执行率。
- `trm_search_demands` / `trm_get_demand`：搜索需求、读取全生命周期详情。
- `trm_list_projects` / `trm_get_project`：搜索项目、读取360详情。
- `trm_prepare_create_demand` -> `trm_create_demand`：预览并创建需求草稿。
- `trm_prepare_create_project` -> `trm_create_project`：预览并创建项目。

创建需求时保留原系统业务规则：标题1~100字、描述不超过5000字、需求类型/优先级使用有效字典，预算出处必须存在。预算超过5万元仍可创建草稿，但提交审批前必须补传预算依据。REQ编号按原流程在“提交需求”时生成，MCP不会越过审批前置条件。

### 服务端配置

1. 生成独立服务Token（不要使用 G.AIOS 后台密码）：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

2. 在 TRM 部署环境中设置：

```bash
TRM_MCP_API_TOKEN=<上一步生成的随机值>
TRM_MCP_WRITE_ENABLED=true
TRM_MCP_ACTOR=gaios-mcp-agent
TRM_MCP_ROLE=admin
TRM_MCP_ALLOWED_HOSTS=trm.example.com
TRM_MCP_ALLOWED_ORIGINS=https://adk.gazellio.com
```

`TRM_MCP_WRITE_ENABLED` 是写操作二次开关。首次部署可保持 `false`，先验证查询工具；验收确认与幂等逻辑后再开启。

3. 使用管理员登录 TRM，访问 `GET /api/mcp/status` 可查看非敏感就绪状态。

### 在 G.AIOS 后台注册

在公司AI后台的 MCP 管理页新增：

```text
名称：TRM科技资源管理
类型：http_streamable
端点：https://<TRM公网域名>/mcp/
Header：Authorization = Bearer <TRM_MCP_API_TOKEN>
```

先执行“连通性/发现工具”验证，然后把上述9个 `trm_*` 工具加到需要对话的智能体并重新发布。推荐先添加5个只读工具，验收后再添加4个创建预览/执行工具。

也可参考 `config/gaios-mcp-config.example.json` 录入配置。部署完成后，在本地执行下列只读检查：

```bash
TRM_MCP_URL=https://<TRM公网域名>/mcp/ \
TRM_MCP_API_TOKEN='<已配置的Token>' \
python scripts/check_mcp.py
```

推荐智能体提示词加入：

```text
涉及TRM操作时必须先查询有效预算/项目/需求。
创建前先调用 trm_prepare_create_* 并向用户展示预览。
用户未明确确认时禁止调用 trm_create_*。
用户确认后使用预览返回的确认令牌，并为这次业务操作生成唯一幂等键。
```

MCP工具注解仅供客户端/模型理解风险，不代替安全控制。真正的鉴权、写开关、参数校验、确认令牌、幂等键和审计都在TRM服务端执行。

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
