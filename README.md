# auto_cpa_register

一个用于 ChatGPT 账号自动注册与补量的 Python 项目，支持：

- 邮箱服务：`tempmail_lol` / `duckmail` / `lamail` / `cfmail`
- 注册后 OAuth 获取 Codex Token
- Token 保存到本地并上传 CPA
- 调度器定时检测账号数量，不足时自动触发注册

---

## 目录结构

- [`app/`](app)
  - [`app/register_web.py`](app/register_web.py)：FastAPI Web 主入口
  - [`app/ncs_register.py`](app/ncs_register.py)：主注册脚本核心实现
  - [`app/payment_bind_app.py`](app/payment_bind_app.py)：独立绑卡 CLI 核心实现
  - [`app/account_store.py`](app/account_store.py)：SQLite 账号存储层
  - [`app/address_generator.py`](app/address_generator.py)：本地账单地址生成器（当前支持 `US` / `UK`）
- [`register_web.py`](register_web.py)：兼容入口，转发到 [`app.register_web`](app/register_web.py)
- [`ncs_register.py`](ncs_register.py)：兼容入口，转发到 [`app.ncs_register`](app/ncs_register.py)
- [`payment_bind_app.py`](payment_bind_app.py)：兼容入口，转发到 [`app.payment_bind_app`](app/payment_bind_app.py)
- [`account_store.py`](account_store.py)：兼容入口，转发到 [`app.account_store`](app/account_store.py)
- [`templates/register.html`](templates/register.html)：注册页面模板（TailwindCSS + Font Awesome CDN）
- [`auto_scheduler.py`](auto_scheduler.py)：自动调度器（定时检测 + 自动触发注册）
- [`config.json`](config.json)：项目运行配置
- [`zhuce5_cfmail_accounts.json`](zhuce5_cfmail_accounts.json)：CF 自建邮箱配置（仅 `mail_provider=cfmail` 时使用）

---

## 环境要求

- Python 3.10+
- 依赖：`curl_cffi`

安装示例：

```bash
pip install curl_cffi
```

---

## 配置说明（config.json）

关键字段：

- `mail_provider`：`tempmail_lol` / `duckmail` / `lamail` / `cfmail`
- `proxy`：默认 `http://127.0.0.1:7890`
- `enable_oauth`：是否执行 OAuth 获取 Token
- `oauth_required`：OAuth 失败是否判定注册失败
- `upload_api_url` / `upload_api_token`：CPA 上传接口
- `upload_api_proxy`：CPA 上传单独代理。留空时优先沿用默认代理，失败后自动直连重试；可填 `direct` 强制直连
- `cpa_upload_every_n`：每成功 N 个账号触发一次 CPA 上传（默认 3）
- `lamail_api_base`：LaMail API 根地址，默认 `https://maliapi.215.im/v1`
- `lamail_api_key`：LaMail API Key，可留空；留空时走匿名临时邮箱能力
- `lamail_domain`：可选，指定 LaMail 创建邮箱时使用的域名；支持多域名，多个用逗号分隔，创建时随机选择一个

示例：

```json
{
  "mail_provider": "tempmail_lol",
  "lamail_api_base": "https://maliapi.215.im/v1",
  "lamail_api_key": "",
  "lamail_domain": "a.example.com,b.example.com",
  "proxy": "http://127.0.0.1:7890",
  "enable_oauth": true,
  "oauth_required": true,
  "upload_api_url": "http://localhost:8317/v0/management/auth-files",
  "upload_api_token": "YOUR_TOKEN",
  "upload_api_proxy": "direct",
  "cpa_upload_every_n": 3
}
```

### LaMail 接入说明

LaMail 对应文档：`https://maliapi.215.im/v1/llms.txt`

- 临时邮箱创建：`POST /accounts`
- 拉取邮件列表：`GET /messages?address=...`
- 拉取邮件详情：`GET /messages/{id}`
- 鉴权方式：使用创建邮箱返回的临时 `token` 作为 `Bearer Token`
- 若配置 `lamail_api_key`，创建邮箱时会额外带上 `X-API-Key`

如果要切到 LaMail，最小配置如下：

```json
{
  "mail_provider": "lamail",
  "lamail_api_base": "https://maliapi.215.im/v1",
  "lamail_api_key": "",
  "lamail_domain": "a.example.com,b.example.com"
}
```

---

## 手动运行注册

```bash
python3 ncs_register.py
```

或直接使用新结构入口：

```bash
python3 -m app.ncs_register
```

运行时交互项包括：

1. 代理确认
2. 是否执行预检（连通性检查）
3. 是否清理 CPA 无效号（若配置了 CPA）
4. 注册数量
5. 并发数
6. 每成功多少个账号触发 CPA 上传

注册成功后，账号的 OAuth 与会话信息会导出到 [`codex_tokens/*.json`](codex_tokens/jl0ho19n@xcmt.online.json)，供独立绑卡工具使用。

---

## FastAPI 注册管理界面

启动命令：

```bash
uvicorn app.register_web:app --reload
```

默认访问地址：

```text
http://127.0.0.1:8000
```

界面特性：

1. 使用 [`templates/register.html`](templates/register.html) 单独存放页面模板
2. 使用你指定的 TailwindCSS CDN 与 Font Awesome CDN
3. 可填写代理、注册数量、并发数、CPA 上传阈值
4. 可选择是否执行预检、是否注册前清理 CPA 无效号
5. 后台线程执行现有 [`run_batch()`](ncs_register.py:2873)
6. 页面显示最近一次执行日志与错误信息

---

## Docker 部署

### 1. 构建并启动

使用 [`docker-compose.yml`](docker-compose.yml) 启动：

```bash
docker compose up -d --build
```

访问地址：

```text
http://127.0.0.1:8000
```

### 2. 关键文件说明

- [`Dockerfile`](Dockerfile)：构建运行镜像，默认启动 [`uvicorn register_web:app`](register_web.py:343)
- [`docker-compose.yml`](docker-compose.yml)：映射端口、挂载配置和数据文件
- [`.dockerignore`](.dockerignore)：避免把本地缓存、数据库、token 数据直接打进镜像

### 3. 默认挂载内容

当前 [`docker-compose.yml`](docker-compose.yml) 已挂载：

- [`config.json`](config.json)
- [`templates/`](templates/register.html)
- [`codex_tokens/`](codex_tokens)
- [`accounts.db`](account_store.py)
- [`registered_accounts.txt`](registered_accounts.txt)
- [`ak.txt`](ak.txt)
- [`rk.txt`](rk.txt)
- [`zhuce5_cfmail_accounts.json`](zhuce5_cfmail_accounts.json)

这样容器重建后，配置、账号库、token 文件仍会保留在宿主机。

### 4. 常用命令

启动：

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f register-web
```

停止：

```bash
docker compose down
```

进入容器：

```bash
docker compose exec register-web bash
```

### 5. Docker 使用建议

1. 容器内仍然依赖外部代理、邮箱 API、OpenAI / Stripe 网络可达性
2. 若宿主机已有代理，请在 [`config.json`](config.json) 中继续配置可用代理地址
3. 如果只跑 Web 面板，当前镜像方案已经足够
4. 若后续要把调度器也容器化，可在 [`docker-compose.yml`](docker-compose.yml) 里再增加 scheduler 服务

---

## 独立绑卡 CLI 工具

```bash
python3 -m app.payment_bind_app
```

当前优先从 SQLite 账号库 [`accounts.db`](account_store.py) 读取账号；如果数据库中没有记录，再回退读取本地旧的 [`codex_tokens/*.json`](codex_tokens/jl0ho19n@xcmt.online.json)。

启动后访问：

```text
http://127.0.0.1:8787
```

功能：

1. 从 [`codex_tokens/`](codex_tokens/jl0ho19n@xcmt.online.json) 中选择一个已注册账号 JSON
2. 在页面中填写或修改绑卡参数
3. 将表单内容回写到 [`config.json`](config.json)
4. 独立执行 `checkout -> m.stripe.com/6 -> confirm`
5. 支持失败后按配置自动重试
6. 支持单独测试 Stripe 连通性，不必每次都跑完整绑卡

可配置重试参数：

- `payment_retry_enabled`：是否启用自动重试
- `payment_retry_max_attempts`：最大尝试次数（含第一次）
- `payment_retry_interval_ms`：每次失败后的等待毫秒数

页面中的“测试 Stripe 连通性”按钮会单独调用 [`m.stripe.com/6`](docs/支付链接获取.md)，输出：

- `GET` 探测结果
- 若已从 checkout 自动提取到 publishable key，则再追加一次 `POST /6` 探测结果
- `request_url`
- `final_url`
- `status`
- `content_type`
- `body_preview`
- `proxy`

当前实现会优先尝试 `GET /6`，若从 checkout 响应中自动提取到了 Stripe publishable key，则会继续按 `POST /6` 思路回退获取 `guid/muid/sid`。

适合先检查代理、TLS、重定向以及 `POST /6` 回退链是否正常，再决定是否继续正式绑卡。

这意味着：

- 注册与绑卡已经完全拆分
- 绑卡不再依赖注册主流程现场执行
- 绑卡基于注册导出的账号配置文件单独进行

---

## 自动调度运行

```bash
python3 auto_scheduler.py
```

调度器逻辑：

- 每隔 `CHECK_INTERVAL_SECONDS` 检测有效账号数
- 小于 `ACCOUNT_THRESHOLD` 时自动调用 `ncs_register.py`
- 自动传入：
  - 默认代理 `http://127.0.0.1:7890`
  - 预检默认 `n`（避免调度阻塞）
  - `cpa_upload_every_n`（默认 3，可在 `AUTO_PARAMS` 改）

---

## 分批上传 CPA 说明

在 `ncs_register.py` 中：

- 每成功 `N` 个账号（`cpa_upload_every_n`）触发一次 `_upload_all_tokens_to_cpa()`
- 任务结束后会再进行一次“收尾上传”，上传剩余不足 N 的 token

这样可以减少本地 token 堆积，并让上传更及时。

---

## 常见问题

### 1) `403` / `csrf 非 JSON`

通常是代理出口被风控拦截。建议：

- 更换高质量代理
- 降低并发到 1 先验证链路
- 使用脚本内预检功能排查

### 2) `OAuth 获取失败（oauth_required=true）`

请查看 OAuth 详细日志（已增强），重点关注：

- `authorize/continue` 状态码与返回 body
- `password/verify` 状态码与返回 body
- sentinel token 生成是否失败

---

## CPA 导出

新增导出脚本：

- [`export_cpa.py`](export_cpa.py)

示例：

```bash
python export_cpa.py --email user@example.com
python export_cpa.py --email user1@example.com --email user2@example.com
python export_cpa.py --all
```

导出规则：

- 单个账号：导出一个 JSON 文件
- 多个账号：导出一个 ZIP
- `--all`：导出数据库中的全部账号为 ZIP
- ZIP 内每个账号一个 JSON 文件

账号平时写入 SQLite [`accounts.db`](account_store.py)，只有在导出时才生成 CPA 格式文件。

---

## 安全提醒

- 不要在公开仓库提交真实 `upload_api_token`
- 不要在公开仓库提交真实卡信息、OAuth token、SQLite 账号库数据
