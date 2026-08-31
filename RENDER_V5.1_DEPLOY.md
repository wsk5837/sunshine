# V5.1 GitHub + Render 部署

本工程面向新建 GitHub 仓库和 Render Web Service，不依赖 `1.116.124.213` 服务器。

## 1. 上传 GitHub

将本文件夹中的内容作为新仓库根目录提交。不要上传 `.env`、`.venv/` 或真实密钥。

## 2. Render Blueprint

1. Render 选择 **New + → Blueprint**。
2. 连接新 GitHub 仓库。
3. Render 自动读取根目录 `render.yaml`。
4. 使用免费 Web Service，区域为 Singapore。

## 3. 必填环境变量

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | Neon 提供的 PostgreSQL pooled connection string，必须包含 `sslmode=require` |
| `TRM_MCP_API_TOKEN` | 至少 32 位随机值 |
| `TRM_TAPD_WORKSPACE_ID` | TAPD 项目 Workspace ID |
| `TRM_TAPD_API_USER` | TAPD API 帐号 |
| `TRM_TAPD_API_PASSWORD` | TAPD API 密钥/口令 |
| `TRM_TAPD_WEBHOOK_SECRET` | TAPD 回调签名密钥 |

Gazellio AI/MCP 写操作需要时，把 `TRM_MCP_WRITE_ENABLED` 从 `false` 改为 `true`。

## 4. 部署后检查

1. 访问 `https://<Render域名>/api/health`，应返回成功状态且数据库类型为 PostgreSQL。
2. 使用 `admin / Admin@123` 首次登录并立即修改密码。
3. 进入“投入管理 → 投入分析驾驶舱”确认演示台账正常。
4. 新建一条投入计划，重启 Render 服务后再查询，数据应仍存在 Neon 中。
5. 检查“系统管理 → 角色管理”的 7 项投入管理权限。

Render 免费 Web Service 可以休眠，但休眠不会删除 Neon 中的数据。附件目录仍使用 `/tmp/trm_uploads`，如需附件持久化，后续应接入对象存储。
