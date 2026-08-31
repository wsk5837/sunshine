# Neon + Render 试点部署

这版在本地没有 `DATABASE_URL` 时继续使用 SQLite；Render 设置 `DATABASE_URL` 后使用 Neon PostgreSQL。Render 服务休眠或重启不会丢失 PostgreSQL 数据。

## 一、新建试点仓库

1. 将本文件夹作为一个全新的 Git 仓库上传。
2. 不要提交 `.env`，也不要把 Neon、TAPD 或 MCP 密钥写入任何源码文件。
3. `data/trm_system.db` 是当前来源仓库的数据快照，供首次部署自动迁移。

## 二、新建 Render 服务

推荐从本目录的 `render.yaml` 创建 Blueprint。区域保持 `Singapore`，然后一次性填写所有 `sync: false` 的环境变量：

| 变量 | 填写内容 |
| --- | --- |
| `DATABASE_URL` | Neon 的 pooled connection string（开启 Connection pooling 时复制的完整字符串） |
| `TRM_TAPD_WORKSPACE_ID` | TAPD 项目空间 ID |
| `TRM_TAPD_API_USER` | TAPD API 帐号 |
| `TRM_TAPD_API_PASSWORD` | TAPD API 密钥 |
| `TRM_TAPD_WEBHOOK_SECRET` | 自行生成的高强度随机字符串 |
| `TRM_MCP_API_TOKEN` | 自行生成的高强度随机字符串 |

`render.yaml` 已设置 `TRM_DATABASE_BACKEND=postgresql`、`TRM_AUTO_MIGRATE_SQLITE=true` 和 `TRM_TAPD_MODE=live`。

Render 的 `Save Changes` 会立即部署是正常行为。新建服务时先把上述变量填完，再触发第一次部署即可。

## 三、首次部署发生什么

1. 应用在 Neon 创建完整表结构。
2. 将 `data/trm_system.db` 的当前数据放在一个事务中迁移到 Neon。
3. 写入持久化标记 `sqlite-bootstrap:v1`。
4. 以后休眠、重启和重新部署只读取 Neon，不会再次用 SQLite 覆盖云端数据。

迁移任何一步失败时整个事务会回滚，不会留下半套数据。

## 四、验证

打开 `https://你的Render域名/api/health`，成功时响应中应包含：

```json
{"code":0,"message":"ok","database":"postgresql"}
```

随后登录系统，新建一条试点数据，等待 Render 重启或手动重新部署，再确认该数据仍存在。

## 五、重要边界

数据库记录已经持久化；上传附件的实体文件仍位于 `TRM_UPLOAD_DIR=/tmp/trm_uploads`，Render 重启后文件本体可能丢失。附件要完全持久化还需接入 S3、Cloudflare R2 或其他对象存储；数据库里的附件元数据不能替代文件本体。

`TRM_AUTO_MIGRATE_SQLITE=true` 可以保留，因为数据库标记会阻止重复迁移。如需对另一个全新的 Neon 数据库重新初始化，使用新的数据库连接串即可；不要删除生产库中的迁移标记。
