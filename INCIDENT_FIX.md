# 签到状态与鉴权排查

[KNOWN | HIGH] 基线：`40518149ea8f40ad589ef2a60d6720850c001891`。原始运行：[39036428078](https://github.com/Attyeek/Glados-Railgun-checkin/actions/runs/39036428078)，作业 `117407124362`，2026-10-17 13:24 UTC。作业结论 success；脚本汇总成功 0、失败 2、重复 0。

[KNOWN | HIGH] `glados.cloud` 的状态接口返回 -2，签到接口返回 -2 / 没有权限，积分查询没有有效数据；`railgun.info` 对应返回 -2 / No permission。原脚本对两个域名还继续请求兑换接口，均被拒绝。日志加载了 1 个 Cookie，代码将它发送给两个域名。

[INFERRED | HIGH] 请求已到达能返回业务 JSON 的服务，故不能把本次故障主要归因于 DNS、连接超时或 JSON 解码。连只读状态查询也失败，故单独更换签到 token 并不能解释全部故障。

[INFERRED | MED] 当前会话无效、Cookie 获取不完整、域名会话不匹配，均是优先验证方向。账号权限或服务端鉴权变化也未排除。现有证据不能确认是哪一个；没有读取 Secret、浏览器会话或凭证明文，也没有执行带真实 Cookie 的请求。

## 修复行为

[KNOWN | HIGH] 任一已配置签到任务失败、任务缺失、无 Cookie 或脚本异常，主程序返回 1；全部任务 code=0（新签到）或 code=1（沿用仓库原有“重复签到”语义），返回 0。入口使用 `sys.exit(main(...))`。推送失败不覆盖签到退出码；配置构造失败时不会使用空字符串充当配置对象。

[KNOWN | HIGH] 各任务独立处理异常；失败仍继续其他账号/域名。签到失败跳过积分及兑换；积分达到门槛才沿用原兑换策略。积分、状态查询或兑换的辅助错误不单独推翻已明确成功的签到结果。

[KNOWN | HIGH] HTTP 非 2xx、重定向、非 JSON、非对象 JSON、缺失或类型错误的 code 均不会被判为签到成功。请求改用 `json=` 发送 JSON，补充 Referer / Accept；这些是请求规范化，不宣称已修复实际鉴权。禁用自动重定向，以免凭证随跳转发送。日志仅保留固定诊断、HTTP 状态、整数业务码与任务定位，不输出 Cookie、响应正文、任意服务端消息或异常文本。

[KNOWN | HIGH] 工作流修正 push 分支为 master，先执行无凭证回归测试，再执行签到。没有设置 continue-on-error 或吞掉退出码的 shell 命令。保活允许签到失败后运行，仍保留失败的作业结论。

## 配置方式

[KNOWN | HIGH] 旧 `GLADOS_COOKIES` 保持兼容：以 `&` 分隔账号，每个 Cookie 仍发送到两个域名；不会自动认定两个域名共享会话。

[KNOWN | HIGH] 推荐在仓库 Settings → Secrets and variables → Actions 添加新的 Repository secret `GLADOS_COOKIES_BY_DOMAIN`。格式如下（仅占位符，不是真实凭证）：

```json
{
  "glados.cloud": ["<该域名实际成功请求中的 Cookie 值>"],
  "railgun.info": ["<该域名实际成功请求中的 Cookie 值>"]
}
```

[KNOWN | HIGH] 新映射非空时完全替代旧变量；只运行映射明确列出的域名。支持每个域名多个账号，Cookie 字符串内的 `&` 不再被拆分。空映射、未知域名、空账号列表、非字符串或换行均使配置失败。只配置一个域名意味着另一域名不再是受监测任务；不能把这种绿色结果解释为两个域名都恢复。

## 安全更新与定位

1. [COMMON | HIGH] 在自己的浏览器分别登录两个域名。先确认能打开个人控制台并正常查看状态。若浏览器本身也无法登录或操作，应先处理账号权限或服务端问题。
2. [COMMON | HIGH] 在开发者工具 Network 中查看发往**同一域名**的成功 `/api/user/status` 请求；在自己的设备上复制 Request Headers 中的 Cookie **值**。使用实际成功请求携带的完整会话 Cookie，避免混用不同登录时间的签名 Cookie。不使用响应里的 Set-Cookie 作为请求头值，不复制整段 cURL 命令。
3. [COMMON | HIGH] 自己把各域名的值填入上述新 Secret；不要发送到聊天、Issue、PR、源代码、截图或日志。JSON 的特殊字符需正确转义；可使用本地可信 JSON 编辑器，勿用在线格式化网站。保存后清除剪贴板。不删除旧 Secret 也可以，新映射会优先使用。
4. [KNOWN | HIGH] 应用此修复后，在 Actions → auto check → Run workflow 选择合并后分支，勾选 `diagnose`。该模式仅对配置域名执行 GET `/api/user/status`，不签到、不兑换、不发送推送。预期看到 HTTP 200、业务 code=0 且状态数据具备 leftDays；否则退出 1。
5. [INFERRED | HIGH] 浏览器正常、新 Cookie 的诊断也通过：支持“旧 Cookie/会话或域名配对问题”。再取消 diagnose 运行真实签到，确认每个配置任务 code=0 或 code=1，作业退出 0。重复签到单独计数。
6. [INFERRED | MED] 浏览器正常，而新 Cookie 仍返回 -2/没有权限：核对实际成功请求是否还有额外鉴权头、路径/方法变更或不同请求体，勿盲改 token。只比较字段名和结构，不分享字段值；若 browser 与 runner 差异持续，再检查服务端对 runner 出口/会话上下文的限制。HTTP 403/429/5xx 与业务 -2 分开处理。
7. [COMMON | HIGH] 若凭证曾公开，先在站点退出/撤销旧会话并重新登录，再替换 Secret；删除日志不能代替撤销会话。

## 测试与应用

[KNOWN | HIGH] 本地依赖与离线测试（只使用虚构凭证，网络由 mock 替代）：

```sh
python -m pip install requests pypushdeer
python -m unittest discover -s tests -v
```

[KNOWN | HIGH] 测试覆盖成功、重复、部分失败、全失败、缺任务、空/错误配置、JSON 异常、code 类型、401/302/500、超时、敏感文本不入日志、失败隔离、推送异常及只读诊断。另使用真实子进程确认没有配置时 OS 退出码为 1。

[KNOWN | HIGH] 本地若已安全设置环境变量，可使用 `python checkin.py --diagnose` 只读检查；不要通过命令行参数传递 Cookie，不启用 shell xtrace，不打印环境变量。真实签到使用 `python checkin.py`，会保留原有积分兑换功能。

[COMMON | HIGH] 合并修复后再从新分支/默认分支手动运行；不要只重跑旧 run，旧 run 可能继续使用原来的提交。离线测试通过不等于真实会话恢复；最终验收必须看到两个域名各自的有效业务响应。
