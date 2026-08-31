# TRM 科技资源管理系统 V5.1（数字化投入管理版）

> 本试点版支持 SQLite 本地运行与 Neon PostgreSQL 云端持久化。Render + Neon 的部署步骤见 [NEON_RENDER_DEPLOY.md](NEON_RENDER_DEPLOY.md)。

本版本保留 V4.9 的登录权限、需求、项目、预算、合同、结算、TAPD 双向同步、AI 助手与 MCP 能力，新增数字化投入全生命周期管理。项目同时支持 SQLite 本地试用与 Render + Neon PostgreSQL 持久化部署。

## V5.1 数字化投入管理

- **投入计划编制**：线上创建年度计划，维护投入明细、自定义分类/子类别标签、新增投入标识、计划外候补标识、数量、金额、支付方、业务用途和计划周期；编制时展示上年预算与当年执行率参考。
- **投入审批与财务确认**：按部门负责人→财务→分管领导流转，待办支持批量通过/驳回；审批完成后导出 Excel 财务复核资料，财务可批量确认生效。
- **投入调整**：已生效投入可发起金额/范围调整；金额变动超过 5 万元时增加分管领导节点，审批通过后自动更新投入基线及版本号。
- **执行跟踪与核销**：投入项可关联项目成本和已有合同，登记付款单据后即时更新已支付、已核销、剩余金额和执行进度。
- **跨年校验**：普通费用必须匹配投入年度；合同付款可跨年，但必须关联合同且付款年份不早于投入年度；单据号唯一防止重复核销。
- **投入预警**：预算接近上限、超预算、长期未执行、临近到期、付款进度偏离五类规则，支持阈值、等级和启停配置，并自动去重/恢复。
- **统计分析驾驶舱**：汇总计划数、明细数、申请金额、审核金额、已核销、未核销和执行率，支持按年度、部门、分类、状态聚合。
- **真实 RBAC**：新增查看、编制、审批、财务、调整、执行、配置 7 类权限，与现有多角色权限合并模型和后端 API 校验一致。

投入管理主表为 `investment_plans`，明细、审批、调整、付款核销、预警规则与预警事件均为独立持久化表，不是前端缓存或写死数据。

## 本次关键调整

- “项目360视图–机器人”接入真实智能体，并将当前项目的任务、预算、需求、合同、结算、里程碑和价值指标作为只读事实上下文。
- “AI智能问答”页面改为多轮智能体对话，服务端自动保存并复用 G.AIOS `session_id`。
- 新增右下角全局 AI 助手悬浮球，登录后可在任意业务页面展开对话。
- AI回答统一渲染标题、列表、粗体和标准表格，MCP工具结果会先转换为面向业务用户的简洁答案。
- 新增 `/api/ai/chat` 后端代理和 NDJSON 解析器，浏览器不直接调用外部平台，也不保存后台账号、密码或管理员 Token。
- 新增标准 Streamable HTTP MCP 服务 `/mcp/`，供 G.AIOS 智能体查询TRM预算/项目/需求，并在用户确认后创建项目、需求草稿及维护需求工时。
- MCP写操作实施“预览→用户确认→幂等创建”，服务端独立Bearer鉴权、写操作总开关、登录用户短时委托令牌和完整审计。
- AI查询和创建能力与“系统管理 → 角色管理”中的现有业务权限完全同步；不设独立AI操作权限，业务权限变更会立即作用于已存在的AI会话。
- 外部智能体不可用时，项目360与AI问答自动降级到原有本地事实问答，不影响POC演示。
- 系统管理 → 集成配置支持维护 G.AIOS 地址、公开 `agent_id` 和只读连通性检查。
- 新增独立登录页面，未登录时不能直接进入系统业务 API。
- 用户管理支持一个用户同时担任多个角色，有效权限按所有启用角色取并集。
- 角色权限不再只是菜单显隐；所有业务 API 都由后端逐请求强制校验，越权调用直接返回 `403 AUTH-4030`。
- 管理员修改角色权限后，已登录会话、页面菜单、后端 API 和 AI/MCP 均在下一次请求按最新权限判定，无需重新登录。
- 右上角用户区域不再允许“一键切换角色”，只显示当前账号信息、修改密码和退出登录。
- 左侧父级菜单改为手风琴逻辑：展开一个分组时，其他已展开分组自动收起。
- “需求申请 + 需求查询”合并：菜单只保留“需求列表”，列表右上角“新建需求”进入空白需求申请单。
- “立项申请 + 立项查询”合并：菜单只保留“立项列表”，列表右上角“新建立项”进入空白立项单。
- 修复新建需求错误带入上一次需求数据的问题：只有点击“编辑”时才加载指定需求，点击“新建需求”始终为空白单。
- 保留 V4.7 之前的 POC 全链路、TAPD Mock/Live、预算、项目、合同、结算、指标等功能。
- 指标库内置15项多维指标和近期趋势，支持目标方向、达成度、健康状态、分类筛选、数据录入与看板。
- 项目编辑内置合同/结算关联工作台，可关联已有单据或直接新建并同步对应台账；常规项目在立项最终审批通过后自动生成并进入项目台账，无需手动转换；直接创建入口明确为“录入存量项目”。
- 全局表单填写助手：日期/时间字段提供显式选择器；说明、背景、目标、收益、审批意见等长文本支持AI草拟与润色，预览确认后才回填，能力实时继承当前用户AI权限。

## 初始登录账号

系统首次初始化会创建以下账号：

- 管理员：`admin` / `Admin@123`
- 业务演示账号（如 `lili11-ghq`、`wangzg`、`zhaomin` 等）：密码统一为 `Demo@123`

公网部署后建议管理员首次登录立即修改密码。

## 真实多角色权限模型

- 用户与角色为多对多关系，`system_user_roles` 保存用户的所有角色；`system_users.role_code` 仅作为兼容旧数据和审计显示的主角色。
- 用户的有效权限是所有“启用”角色权限的并集；任一角色具有 `*` 时视为全部权限。
- 每次 API 请求都会通过登录会话重新读取用户状态、启用角色和权限，不信任浏览器自行传入的角色请求头。
- 页面隐藏只是交互优化，真正的授权边界在 FastAPI 后端中间件，直接调用 URL/API 也无法绕过权限。
- 审批操作在功能权限之上仍会校验当前流程节点角色；多角色用户可处理任一所属角色的待办。
- AI/MCP 不保存独立的业务权限。旧的 AI 委托令牌在每次工具调用时也会重新读取该用户的实时多角色权限并集。

## 标准目录

```text
科技资源管理系统_V5.1_数字化投入管理_Render版/
├── app/
│   ├── __init__.py
│   ├── ai_gateway.py
│   ├── investment.py
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
│       ├── app.css
│       ├── investment.js
│       └── investment.css
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
python -m uvicorn app.runtime:app --reload
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
- `trm_prepare_update_work_plan` -> `trm_update_work_plan`：预览并维护需求工时计划。
- `trm_prepare_log_work_hours` -> `trm_log_work_hours`：预览并提交关联功能点的实际工时；审批通过后才汇总实际工时并重算预警。

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
TRM_MCP_ROLE=mcp_service
TRM_AI_DELEGATION_SECRET=<可选，独立的24字符以上随机密钥>
TRM_AI_DELEGATION_TTL_SECONDS=900
TRM_MCP_ALLOWED_HOSTS=trm.example.com
TRM_MCP_ALLOWED_ORIGINS=https://adk.gazellio.com
```

`TRM_MCP_WRITE_ENABLED` 是写操作二次开关。首次部署可保持 `false`，先验证工具发现；验收确认与幂等逻辑后再开启。`TRM_MCP_ROLE` 只是集成服务元数据，不参与用户授权，不要设成 `admin`。

### AI与业务权限同步

系统不再配置独立的 `ai.query.*` / `ai.create.*` 权限。用管理员账号进入“系统管理 → 角色管理”配置现有业务权限，AI按下列关系自动、同步继承：

- `ai`：是否允许使用AI助手（总开关）。
- `budget`：页面可查预算，AI同步可查预算。
- `demand.list`：页面可查需求，AI同步可查需求。
- `project360` 或 `project`：页面可查项目，AI同步可查项目。
- `demand.create`：页面可新建需求，AI同步可创建需求草稿。
- `initiative.create` 或 `project`：页面可新建立项/管理项目，AI同步可创建项目。

因此，默认“需求申请人”拥有 `initiative.create` 和 `demand.create`，本人可新建立项和需求，AI也同步可创建项目和需求草稿。如果管理员撤销其中任一业务权限，当前AI会话的对应能力也会立即失效。静态 MCP Bearer Token 只验证 G.AIOS 集成服务，不代表业务用户。用户在TRM发起每次AI对话时，后端会签发最长30分钟的短时委托令牌；MCP每次调用都重新读取账号、所有启用角色以及当前业务权限并集。

AI创建需求时，申请人账号、姓名和部门由TRM服务端按当前登录用户强制写入，智能体不能伪造他人身份。预览确认令牌和幂等键也与该用户绑定。

3. 使用管理员登录 TRM，访问 `GET /api/mcp/status` 可查看非敏感就绪状态。

### 在 G.AIOS 后台注册

在公司AI后台的 MCP 管理页新增：

```text
名称：TRM科技资源管理
类型：http_streamable
端点：https://<TRM公网域名>/mcp/
Header：Authorization = Bearer <TRM_MCP_API_TOKEN>
```

先执行“连通性/发现工具”验证，然后把上述13个 `trm_*` 工具加到需要对话的智能体并重新发布。推荐先添加5个只读工具，验收后再添加8个带预览/确认的写入工具。

从旧版MCP升级到本版后，必须重启TRM服务，在 G.AIOS MCP管理页点击“刷新/重连”以重新发现工具参数（所有工具新增必填 `delegation_token`），然后重新发布智能体。TRM用户应退出并重新登录一次，再开始新的AI对话。

也可参考 `config/gaios-mcp-config.example.json` 录入配置。部署完成后，在本地执行下列只读检查：

```bash
TRM_MCP_URL=https://<TRM公网域名>/mcp/ \
TRM_MCP_API_TOKEN='<已配置的Token>' \
python scripts/check_mcp.py
```

推荐智能体提示词加入：

```text
涉及TRM操作时必须先查询有效预算/项目/需求。
调用任何 trm_* 工具时，必须原样传入TRM上下文中的 delegation_token，不得在回答中显示该令牌。
创建前先调用 trm_prepare_create_* 并向用户展示预览。
用户未明确确认时禁止调用 trm_create_*。
用户确认后使用预览返回的确认令牌，并为这次业务操作生成唯一幂等键。
```

MCP工具注解仅供客户端/模型理解风险，不代替安全控制。真正的登录用户鉴权、角色权限、写开关、参数校验、确认令牌、幂等键和审计都在TRM服务端执行。

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
python -m uvicorn app.runtime:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"
```

Python：3.13.5。

## TAPD Live 模式

真实 TAPD 凭据仅配置在 Render / 本地环境变量：

- `TRM_TAPD_API_USER`
- `TRM_TAPD_API_PASSWORD`
- `TRM_TAPD_MODE=live`
- `TRM_TAPD_WORKSPACE_ID`
- `TRM_TAPD_BASE_URL=https://api.tapd.cn`
- `TRM_TAPD_WEBHOOK_SECRET`（独立随机密钥，用于校验TAPD回调）

不要提交到 GitHub。

Live模式支持双向通信：终审后创建TAPD需求，TRM可把最新标题、描述、优先级、计划完成日期和预估工时回写TAPD；TAPD状态、任务和工时由手动/定时同步或Webhook回读TRM。Webhook地址为 `https://<你的Render域名>/api/tapd/webhook`，并须通过 `X-TAPD-Webhook-Secret` 请求头或 `token` 查询参数携带与 `TRM_TAPD_WEBHOOK_SECRET` 相同的值。

Webhook不使用TRM登录会话，避免TAPD服务器回调被登录鉴权拦截；它只接受独立Webhook密钥校验。该密钥不要与TAPD API密钥、TRM MCP Token或管理员密码复用。

## 预算分摊与预算管理

- 预算总览、费用分摊台账、项目概览（人力）和项目概览（财务）统一收拢在“预算管理”菜单下。
- 产品经理审批前费用分摊必须合计100%；系统按最新功能点评估金额重新计算每行分摊金额，并自动处理分币舍入差额。
- 财务审批通过时逐行校验可用预算并真实记账占用；预算不足会阻止审批，执行率达到95%及以上要求填写财务意见并产生预警。
- 后续审批驳回会自动释放已占用金额；预算交易保留需求、分摊行、部门、操作人及幂等事件键，避免重复审批造成重复记账。
- 预算已使用金额由业务交易汇总，普通预算编辑不再允许直接覆盖该字段。

## 工时偏差预警与功能点计算

- 产品经理或项目经理可在需求详情维护预估工时和计划完成日期。每笔实际工时必须关联需求功能点，提交后进入待审批，仅已通过记录计入实际工时与偏差预警。
- TAPD Live/Mock 回读时优先按工时填报汇总实际投入，无填报才回退到任务已完成工时；人工接管外部工时必须明确确认。
- 实际工时超出预估工时 30% 时，后端会真实写入两条定向消息：产品经理、项目经理各一条；低于预估不再误报。
- 未关闭需求超过计划完成日期时会生成工时计划逾期预警；调整计划或关闭需求后自动撤销未读旧预警。
- 同一工时事件会分别投递到产品经理和项目经理，但多角色用户的消息中心会合并为一张卡片；已读状态按具体用户隔离，预警解除后显示“已恢复”。
- AI通过MCP维护工时与页面使用同一实时权限，所有写入均需预览、用户确认、幂等键和审计记录。
- 历史工时数据会在服务启动、需求详情读取或消息中心读取时自动补齐，相同偏差不会重复发送。
- 定向消息只对目标角色可见；系统管理员可查看全部消息，申请人不会在自己的消息中心看到发给产品/项目经理的预警。
- 需求基本信息中的“预估功能点”可点击进入功能点评估详情；“预估金额”支持鼠标悬停或键盘聚焦查看每条功能点的 `FP × 单价 = 金额` 计算过程。

## 首页权限隔离

首页采用模块级权限加载。没有 `demand.approve` 的需求申请人不会请求审批待办接口，也不显示“我的需求审批待办”模块，其他首页内容仍正常加载。TAPD、项目、预算、立项、合同及快捷入口同样按当前账号实时权限独立显示或隐藏。

## 全站权限显示规则

- 左侧菜单、子菜单、页面内操作和跨模块关联字段均按当前实时权限直接隐藏。
- 手工输入无权的 Hash 路由、使用旧书签，或管理员在用户正在操作时撤回权限，系统会刷新权限并静默跳转到该用户第一个可访问页面。
- 前端不显示“当前账号未授权访问该功能”类红色失败页或提示；后端仍保留 `403 AUTH-4030` 作为不可绕过的真实安全边界。
- TAPD 同步权限与集成配置权限已拆分：普通 TAPD 用户可查看同步概况，只有 `system.integrations` 用户才会看到连接配置、连通性测试和后台任务操作。

## 受保护文件下载

首页汇总导出、需求附件下载、功能点模板和功能点导出不再直接打开受保护的 API 地址。前端会携带当前登录会话读取文件并触发浏览器下载，不跳离系统页面；会话失效时直接返回登录页。
