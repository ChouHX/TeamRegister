这个接口：

```
POST https://chatgpt.com/backend-api/payments/checkout
```

本质是 **创建 Stripe Checkout Session（团队订阅）**。
参数分三类：**认证参数 / 风控参数 / 业务参数**。

---

# 一、请求头必需参数

## 1 认证

必须：

```
authorization: Bearer <access_token> #从登录流程中获取
```

来源：

```
/api/auth/session
```

JWT 内包含：

```
chatgpt_account_id
chatgpt_user_id
chatgpt_plan_type
email
```

---

## 2 会话 Cookie

必须：

```
__Secure-next-auth.session-token #从重定向中获取 不走交换token继续重定向到chat.gpt
```

否则接口返回：

```
401 unauthorized
```

---

## 3 CSRF

```
__Host-next-auth.csrf-token 登录流程中应该也能拿到 __Host-next-auth.csrf-token
```

NextAuth 防护。

---

# 二、设备识别参数

## 1 设备ID

```
oai-device-id: 49c3663c-8d1f-4370-9972-9654d7903cd4 #重定向到gpt都有 字段 device_id
```

同时 cookie 必须一致：

```
oai-did=49c3663c-8d1f-4370-9972-9654d7903cd4
```

---

## 2 Sentinel 风控 token

```
openai-sentinel-token #参考
 # ------- 步骤4：获取 Sentinel Token -------
        emitter.info("正在获取 Sentinel Token...", step="sentinel")
        signup_body = f'{{"username":{{"value":"{email}","kind":"email"}},"screen_hint":"signup"}}'
        sen_req_body = f'{{"p":"","id":"{did}","flow":"authorize_continue"}}'

        sen_resp = _raw_post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=sen_req_body,
        )

        if sen_resp.status_code != 200:
            emitter.error(f"Sentinel 异常拦截，状态码: {sen_resp.status_code}", step="sentinel")
            return None

        sen_token = sen_resp.json()["token"]
        sentinel = f'{{"p": "", "t": "", "c": "{sen_token}", "id": "{did}", "flow": "authorize_continue"}}'
        emitter.success("Sentinel Token 获取成功", step="sentinel")
```

来自：

```
/sentinel/.../sdk.js
```

作用：

```
反自动化 / bot detection
```

缺失会：

```
403
```

---

# 三、Cloudflare 校验

必须 cookie：

```
cf_clearance 不需要传参
__cf_bm 有
_cf_uvid 有 _cfuvid
__cflb 有
```

否则：

```
403 blocked by cloudflare
```

---

# 四、客户端标识

浏览器环境指纹：

```
oai-client-version  #固定值
oai-client-build-number
sec-ch-ua*
origin
referer
```

关键：

```
oai-client-version: prod-d7360e59f...
```

---

# 五、请求 Body 参数

```json
{
 "plan_name": "chatgptteamplan",
 "team_plan_data": {
  "workspace_name": "Artizancloud",
  "price_interval": "month",
  "seat_quantity": 5
 },
 "billing_details": {
  "country": "JP",
  "currency": "JPY"
 },
 "cancel_url": "https://chatgpt.com/?promo_campaign=team1dollar#team-pricing",
 "promo_campaign": {
  "promo_campaign_id": "team1dollar",
  "is_coupon_from_query_param": true
 },
 "checkout_ui_mode": "custom"
}
```

---

# 六、Body 字段说明

## 1 plan_name

```
chatgptteamplan
```

可选：

```
chatgptplus
chatgptteamplan
chatgptenterprise
```

---

## 2 team_plan_data

团队订阅参数：

```
workspace_name   团队名称
price_interval   month / year
seat_quantity    成员数量
```

---

## 3 billing_details

```
country  国家
currency 货币
```

示例：

```
JP JPY
US USD
EU EUR
```

---

## 4 promo_campaign

优惠活动：

```
team1dollar
```

来源：

```
?promo_campaign=team1dollar
```

---

## 5 cancel_url

取消支付返回地址

---

## 6 checkout_ui_mode

当前：

```
custom
```

用于：

```
嵌入式 Stripe checkout
```

---

# 七、接口返回

成功返回：

```json
{
 "checkout_url": "https://checkout.stripe.com/c/pay/cs_test_xxxxx"
}
```

或

```
client_secret
session_id
```

前端随后跳转 Stripe。

---

# 八、最少参数（核心）

实际上只需要：

### Header

```
authorization
__Secure-next-auth.session-token
oai-device-id
openai-sentinel-token
cf_clearance
```

---

### Body

```
plan_name
team_plan_data
billing_details
```

---

# 九、完整调用流程

```
登录
 ↓
获得 session-token
 ↓
GET /api/auth/session
 ↓
获得 Bearer access_token
 ↓
生成 sentinel token
 ↓
POST /backend-api/payments/checkout
 ↓
返回 stripe checkout session
```

---
