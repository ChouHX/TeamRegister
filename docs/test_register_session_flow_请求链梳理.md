# test_register_session_flow.py 请求链梳理

## 1. 文档范围

这份文档只梳理 [`test_register_session_flow.py`](D:\Work\Demo\gpt-team\test_register_session_flow.py) 里**脚本本身能直接看到的链条**。

不展开两类外部细节：

- `mail_provider.create_mailbox(...)` 内部到底发了哪些请求
- `mail_provider.wait_for_otp(...)` 轮询邮箱时到底发了哪些请求

也就是说，下面的“主请求链”以脚本里的 `_perform_request(...)` 为准；邮箱 provider 只当成黑盒步骤看。


## 2. 总览

脚本主流程入口在 [`test_register_session_flow.py:324`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L324) 的 `main()`。

真实链条可以概括成下面这条线：

```text
check_proxy
  -> 创建临时邮箱（黑盒）
  -> chatgpt_csrf
  -> chatgpt_signin_openai
  -> auth_oauth_init
  -> sentinel
  -> signup
  -> send_otp
  -> 等待 OTP（黑盒）
  -> verify_otp
  -> create_account
  -> [可选] workspace_select
  -> redirect_1 ~ redirect_n
  -> [可选] home
  -> auth_session
  -> [可选] oauth_token_exchange
  -> 整理最终凭证
```

主目标有两个：

- 完成 `auth.openai.com` 的注册链路
- 回跳到 `chatgpt.com`，拿到 `__Secure-next-auth.session-token`，再尽量补齐 `access_token / id_token / refresh_token`


## 3. 请求记录机制

脚本里所有“被视为主链”的 HTTP 请求都经过 [`test_register_session_flow.py:126`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L126) 的 `_perform_request(...)`。

这个函数会做几件事：

- 在请求前记录 `step / method / url / headers / data / params`
- 在请求前后记录 cookie jar
- 调用 `_call_with_http_fallback(...)` 实际发请求
- 把响应的 `status_code / url / headers / text` 记进 `debug_result["requests"]`

所以最终 `test_outputs/register_session_flow_*.debug.json` 里的 `requests`，本质上就是这份链条的落盘记录。


## 4. 主请求链明细

## 4.1 网络检查

步骤名：`check_proxy`

- 代码位置：[`test_register_session_flow.py:187`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L187)
- 请求：

```http
GET https://cloudflare.com/cdn-cgi/trace
```

作用：

- 做连通性检查
- 从响应文本里提取 `loc` 和 `ip`

说明：

- 这里虽然叫 `check_proxy`，但当前脚本把 `proxy` 固定成空串、`proxies=None`，见 [`test_register_session_flow.py:327`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L327)


## 4.2 创建临时邮箱

步骤名：无 `_perform_request` 记录

- 代码位置：[`test_register_session_flow.py:367`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L367)
- 调用：

```python
mail_provider = _create_mail_provider(config.mail.provider, config.mail.config)
email, mailbox_auth = mail_provider.create_mailbox(proxy=proxy)
```

作用：

- 生成一个可接收 OTP 的邮箱
- 返回 `email` 和后续轮询邮箱要用的 `mailbox_auth`

说明：

- 这是主流程的前置条件
- 但它内部请求链不在本脚本可见范围内


## 4.3 获取 ChatGPT 登录 CSRF

步骤名：`chatgpt_csrf`

- 代码位置：[`test_register_session_flow.py:377`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L377)
- 请求：

```http
GET https://chatgpt.com/api/auth/csrf
Referer: https://chatgpt.com/auth/login
Accept: application/json
```

输出：

- 从响应 JSON 中提取 `csrfToken`

依赖关系：

- 后面的 `chatgpt_signin_openai` 必须依赖这里拿到的 `csrfToken`


## 4.4 让 ChatGPT 生成 OpenAI 授权地址

步骤名：`chatgpt_signin_openai`

- 代码位置：[`test_register_session_flow.py:393`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L393)
- 请求：

```http
POST https://chatgpt.com/api/auth/signin/openai
Content-Type: application/x-www-form-urlencoded
Origin: https://chatgpt.com
Referer: https://chatgpt.com/auth/login
Accept: application/json
```

请求体：

```x-www-form-urlencoded
csrfToken=<上一步拿到的 csrfToken>
callbackUrl=https://chatgpt.com/
json=true
```

输出：

- 从响应 JSON 中取出 `url`
- 脚本把这个值命名为 `auth_url`

依赖关系：

- 这是从 `chatgpt.com` 切到 `auth.openai.com` 注册流的入口地址


## 4.5 初始化 OAuth / 获取设备标识

步骤名：`auth_oauth_init`

- 代码位置：[`test_register_session_flow.py:422`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L422)
- 请求：

```http
GET <auth_url>
Referer: https://chatgpt.com/auth/login
```

特点：

- 这是主链里少数设置了 `allow_redirects=True` 的请求

输出：

- 脚本优先从 cookie 中拿 `oai-did`
- 如果 cookie 没拿到，再从响应 HTML 文本里用正则提取 `oai-did`

依赖关系：

- 下一步 `sentinel` 请求必须依赖这里拿到的 `device_id`


## 4.6 获取 Sentinel Token

步骤名：`sentinel`

- 代码位置：[`test_register_session_flow.py:453`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L453)
- 请求：

```http
POST https://sentinel.openai.com/backend-api/sentinel/req
Origin: https://sentinel.openai.com
Referer: https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6
Content-Type: text/plain;charset=UTF-8
```

请求体：

```json
{"p":"","id":"<device_id>","flow":"authorize_continue"}
```

输出：

- 从响应 JSON 里取 `token`
- 然后拼出一个新的请求头值 `openai-sentinel-token`

脚本拼接结果：

```json
{"p":"","t":"","c":"<sentinel_token>","id":"<device_id>","flow":"authorize_continue"}
```

依赖关系：

- 下一步 `signup` 直接依赖 `openai-sentinel-token`


## 4.7 注册入口推进

步骤名：`signup`

- 代码位置：[`test_register_session_flow.py:483`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L483)
- 请求：

```http
POST https://auth.openai.com/api/accounts/authorize/continue
Referer: https://auth.openai.com/create-account
Accept: application/json
Content-Type: application/json
openai-sentinel-token: <上一步拼出的 JSON 字符串>
```

请求体：

```json
{
  "username": {
    "value": "<email>",
    "kind": "email"
  },
  "screen_hint": "signup"
}
```

作用：

- 把注册邮箱提交给 OpenAI 注册流程
- 明确告诉服务端当前走的是 `signup` 分支


## 4.8 发送 OTP

步骤名：`send_otp`

- 代码位置：[`test_register_session_flow.py:511`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L511)
- 请求：

```http
POST https://auth.openai.com/api/accounts/passwordless/send-otp
Referer: https://auth.openai.com/create-account/password
Accept: application/json
Content-Type: application/json
```

请求体：

```json
{}
```

作用：

- 让 OpenAI 往上面那个临时邮箱发送验证码

分支条件：

- 非 `200` 直接抛错，流程中断


## 4.9 等待 OTP

步骤名：无 `_perform_request` 记录

- 代码位置：[`test_register_session_flow.py:534`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L534)
- 调用：

```python
otp_code = mail_provider.wait_for_otp(mailbox_auth, email, proxy=proxy)
```

作用：

- 阻塞等待邮箱收到 OTP

说明：

- 这里是脚本里的第二个黑盒步骤
- 脚本本身只消费结果 `otp_code`


## 4.10 验证 OTP

步骤名：`verify_otp`

- 代码位置：[`test_register_session_flow.py:540`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L540)
- 请求：

```http
POST https://auth.openai.com/api/accounts/email-otp/validate
Referer: https://auth.openai.com/email-verification
Accept: application/json
Content-Type: application/json
```

请求体：

```json
{"code":"<otp_code>"}
```

作用：

- 把邮箱验证码提交回注册流程

分支条件：

- 非 `200` 直接抛错


## 4.11 创建账户

步骤名：`create_account`

- 代码位置：[`test_register_session_flow.py:562`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L562)
- 请求：

```http
POST https://auth.openai.com/api/accounts/create_account
Referer: https://auth.openai.com/about-you
Accept: application/json
Content-Type: application/json
```

请求体：

```json
{"name":"Neo","birthdate":"2000-02-20"}
```

输出：

- 脚本从响应 JSON 读取 `continue_url`

额外动作：

- 同时从 cookie `oai-client-auth-session` 里解析 `workspace_id`
- 解析逻辑在 [`test_register_session_flow.py:214`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L214)

分支条件：

- 非 `200` 直接抛错


## 4.12 可选的 workspace 选择

步骤名：`workspace_select`

- 代码位置：[`test_register_session_flow.py:592`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L592)
- 触发条件：

```text
create_account 返回的 continue_url 为空
并且
cookie 中成功解析出了 workspace_id
```

- 请求：

```http
POST https://auth.openai.com/api/accounts/workspace/select
Referer: https://auth.openai.com/sign-in-with-chatgpt/codex/consent
Accept: application/json
Content-Type: application/json
```

请求体：

```json
{"workspace_id":"<workspace_id>"}
```

输出：

- 再次尝试从响应 JSON 中拿 `continue_url`

分支条件：

- 如果这里之后 `continue_url` 仍然为空，脚本直接报错退出


## 4.13 手动跟踪重定向链

步骤名：`redirect_1` 到 `redirect_n`

- 代码位置：
  - 调用入口：[`test_register_session_flow.py:618`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L618)
  - 循环实现：[`test_register_session_flow.py:233`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L233)

输入：

- `start_url = continue_url`

实现方式：

- 最多跟 12 跳
- 每一跳都自己发一个 `GET`
- `allow_redirects=False`
- 如果响应状态码是 `301/302/303/307/308` 且存在 `Location`，就继续拼出下一个 URL

每跳请求形态：

```http
GET <current_url>
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
Referer: <上一跳 URL 或 https://chatgpt.com/>
```

输出：

- `visited_urls`
- `final_url`
- `final_status_code`

这一步很关键，因为后面的两个值都从这里衍生：

- `callback_url`：从 `visited_urls` 或 `final_url` 里找 `/api/auth/callback/openai`
- `auth_code`：从 `callback_url` 的 query 里取 `code`


## 4.14 可选补一跳首页

步骤名：`home`

- 代码位置：[`test_register_session_flow.py:633`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L633)
- 触发条件：

```text
redirect_chain.final_url != https://chatgpt.com/
```

- 请求：

```http
GET https://chatgpt.com/
Referer: <final_url 或 continue_url>
```

作用：

- 如果手动跟完重定向后最终还没真正落到 ChatGPT 首页，就再主动补一次首页访问


## 4.15 拉取当前登录 Session

步骤名：`auth_session`

- 代码位置：[`test_register_session_flow.py:649`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L649)
- 请求：

```http
GET https://chatgpt.com/api/auth/session
Accept: application/json
Referer: https://chatgpt.com/
```

输出：

- 从 cookie 中取 `__Secure-next-auth.session-token`
- 从响应 JSON 中取 `accessToken`

作用：

- 这是脚本判断 ChatGPT Web 登录态是否建立的关键一步


## 4.16 可选的 OAuth Token 交换

步骤名：`oauth_token_exchange`

- 代码位置：[`test_register_session_flow.py:694`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L694)
- 触发条件：

```text
callback_url 中成功解析出 auth_code
并且
continue_url 中成功解析出 login_verifier
```

参数来源：

- `auth_code`：从 `callback_url` query 里提取
- `login_verifier`：从 `continue_url` query 里提取

- 请求：

```http
POST https://auth.openai.com/oauth/token
Content-Type: application/x-www-form-urlencoded
Accept: application/json
Origin: https://chatgpt.com
Referer: <callback_url>
```

请求体：

```x-www-form-urlencoded
grant_type=authorization_code
client_id=app_X8zY6vW2pQ9tR3dE7nK1jL5gH
code=<auth_code>
redirect_uri=https://chatgpt.com/api/auth/callback/openai
code_verifier=<login_verifier>
```

输出：

- `id_token`
- `access_token`
- `refresh_token`
- `expires_in`

说明：

- 如果缺 `auth_code` 或缺 `login_verifier`，脚本会记录日志并跳过这一步，不会报错


## 5. 关键中间值流转

脚本里最重要的值，基本按这个顺序流动：

1. `email`
   - 来自 `create_mailbox(...)`

2. `csrfToken`
   - 来自 `GET /api/auth/csrf`

3. `auth_url`
   - 来自 `POST /api/auth/signin/openai`

4. `device_id`
   - 来自 `auth_oauth_init` 之后的 `oai-did` cookie 或 HTML

5. `sentinel_token`
   - 来自 `POST /backend-api/sentinel/req`

6. `openai-sentinel-token`
   - 由 `device_id + sentinel_token` 拼出来，供 `signup` 使用

7. `otp_code`
   - 来自 `wait_for_otp(...)`

8. `continue_url`
   - 优先来自 `create_account`
   - 不足时来自 `workspace_select`

9. `workspace_id`
   - 来自 cookie `oai-client-auth-session`

10. `callback_url`
   - 来自手动重定向链中的 `/api/auth/callback/openai`

11. `auth_code`
   - 从 `callback_url` query 提取

12. `login_verifier`
   - 从 `continue_url` query 提取

13. `session_token`
   - 来自 cookie `__Secure-next-auth.session-token`

14. `access_token`
   - 优先取 `oauth_token_exchange`
   - 兜底取 `auth_session` 返回的 `accessToken`

15. `id_token / refresh_token`
   - 主要来自 `oauth_token_exchange`


## 6. 链条里的条件分支

这个测试脚本不是一条完全固定的直线，有 3 个关键分支：

### 分支 A：是否需要 `workspace_select`

条件：

```text
continue_url 为空
且 workspace_id 存在
```

结果：

- 命中则多一次 `POST /api/accounts/workspace/select`

### 分支 B：是否需要额外访问首页

条件：

```text
redirect_chain.final_url 不是 https://chatgpt.com/
```

结果：

- 命中则多一次 `GET https://chatgpt.com/`

### 分支 C：是否执行 OAuth Token 交换

条件：

```text
auth_code 和 login_verifier 同时存在
```

结果：

- 命中则执行 `POST https://auth.openai.com/oauth/token`
- 未命中则只保留已有 session / access token 信息


## 7. 脚本最终产物

脚本结束时，不管成功失败，都会在 `test_outputs/` 下写两个文件，见 [`test_register_session_flow.py:330`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L330) 和 [`test_register_session_flow.py:797`](D:\Work\Demo\gpt-team\test_register_session_flow.py#L797) 附近逻辑：

- `register_session_flow_<timestamp>.json`
  - 最终凭证摘要

- `register_session_flow_<timestamp>.debug.json`
  - 调试信息
  - 包括完整 `requests` 列表、日志、cookie 快照、各步骤响应摘要


## 8. 一句话总结

`test_register_session_flow.py` 的核心思路是：

先从 `chatgpt.com` 拿登录入口和授权上下文，再到 `auth.openai.com` 完成邮箱注册、OTP 验证、建号，随后沿 `continue_url` 手动走回跳链，最后在 `chatgpt.com` 读取 session，并在条件满足时补做 `oauth/token` 交换，把 Web 登录态整理成可落盘的凭证结果。
