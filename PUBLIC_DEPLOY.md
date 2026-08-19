# 科技资源管理系统 V4.4 公网部署说明

本版本在 V4.3 功能基础上增加了公网部署配置。部署完成后，访问者无需安装 Python、PyCharm 或数据库，只需在浏览器输入公网地址即可打开系统。

## 推荐方案：Render 公网 Web Service

项目根目录已经提供：

- `render.yaml`：免费演示配置
- `render-persistent.yaml`：带持久化磁盘的长期运行配置
- `.python-version`：固定使用 Python 3.12 系列

### A. 免费演示版

适合讲标、POC 演示和短期分享。

1. 将整个项目上传到 GitHub / GitLab / Bitbucket 仓库。
2. 登录 Render。
3. 点击 **New → Blueprint**。
4. 连接刚才的代码仓库。
5. Render 会自动读取根目录的 `render.yaml`。
6. 点击 **Apply** 开始构建。
7. 构建成功后会得到类似：

   `https://trm-tech-resource-poc.onrender.com`

以后任何联网电脑只要打开这个地址即可访问。

> 免费 Web Service 在长时间无人访问后会休眠，下一次打开时可能需要等待服务唤醒。免费服务的文件系统是临时的，因此在线修改的数据、上传附件不适合作为长期正式数据保存。

### B. 长期运行 / 数据持久化版

如果需要在线录入的数据、审批记录、附件在重新部署或服务重启后仍然保留，建议使用 Render 付费 Web Service + Persistent Disk。

操作方式：

1. 将 `render-persistent.yaml` 重命名为 `render.yaml`（覆盖免费版配置）。
2. 提交到代码仓库。
3. 在 Render 新建 Blueprint 或同步现有 Blueprint。
4. 配置会创建 Starter Web Service，并将持久化目录挂载到：

   `/opt/render/project/src/runtime`

5. SQLite 数据库保存到：

   `/opt/render/project/src/runtime/trm_system.db`

6. 上传附件保存到：

   `/opt/render/project/src/runtime/uploads`

这样服务重启或重新部署后，业务数据和上传文件仍可保留。

## 自定义网址

Render 部署成功后会自动提供 `*.onrender.com` 地址。如果后续有自己的域名，也可以在 Render 的 **Settings → Custom Domains** 中绑定，例如：

`https://trm.yourcompany.com`

## 临时外网分享（无需部署）

如果只是临时给同事看，而且电脑可以一直保持开机，可以使用 Cloudflare Quick Tunnel：

1. 先在本机启动系统：

   `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`

2. 安装 `cloudflared` 后运行：

   `cloudflared tunnel --url http://localhost:8000`

3. 终端会生成一个临时的 `https://xxxx.trycloudflare.com` 地址，发给其他人即可直接访问。

该方式只适合临时演示：电脑关机、Uvicorn 停止或 Tunnel 进程退出后，地址就不能再访问。

## 公网部署前的安全提醒

当前系统是 POC/演示系统，页面内可以切换角色进行流程演示。如果把地址公开到互联网，任何拿到链接的人都可能操作演示数据。正式生产上线前应增加统一登录认证、会话管理、密码/SSO、CSRF 防护、生产级日志和正式外部系统凭证管理。
