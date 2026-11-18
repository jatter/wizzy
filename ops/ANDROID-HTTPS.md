# Android 与 HTTPS 运维

网站继续解析到腾讯云 `175.24.197.8`，由它终止 HTTPS，再代理到 `106.13.191.107:9060`。不需要更换 DNS。Passkey 的 RP ID 为 `wizzy.refineview.com`，浏览器 origin 为 `https://wizzy.refineview.com`。

## 腾讯云网关

将本目录的 `enable-tencent-https.sh`、`tencent-wizzy-https.conf` 一起复制到网关的 `/root/wizzy-https/`。**不要把模板留在 nginx 的 `conf.d` 里**，因为 `*.conf` 会在证书生成前被自动加载。确认安全组和防火墙放行 TCP 443，保留 TCP 80 供 ACME 续期。需已有 `nginx`、`python3`、`curl`、`certbot`；OpenCloudOS 可以先检查其软件源是否提供 `certbot` 软件包，不要替换其他站点的 nginx 配置。

在网关以 root 执行，使用实际的配置绝对路径和证书通知邮箱：

```bash
bash enable-tencent-https.sh /etc/nginx/conf.d/wizzy.conf your-email@example.com
```

脚本备份原配置、添加 ACME webroot、申请免费证书、仅替换 Wizzy 配置、检查 nginx 后 reload，并安装续期 hook；没有现成 certbot 定时器时添加该证书的 cron 续期。失败会恢复原配置。首次申请需接受 Let's Encrypt 服务条款，邮箱交由该 CA 用于证书联系。

应用服务器的发布脚本只信任来源 IP `175.24.197.8` 传来的 `X-Forwarded-Proto: https`。网关发送 `Host $proxy_host` 和 `X-Forwarded-Proto $scheme`。此回源 Host 为 `106.13.191.107:9060`；传公网域名会触发百度云的域名拦截。浏览器仍使用 HTTPS 公网域名，API 通过明确配置的 `WIZZY_FAMILY_PUBLIC_ORIGIN` 校验 origin、确定 Passkey RP，不依赖回源 Host。Uvicorn 只监听本机，信任本机 nginx。发布保留已有公共 origin；网关 HTTPS 验证通过后，将 `/www/wizzy/api/.env` 的 `WIZZY_FAMILY_PUBLIC_ORIGIN` 设置为 `https://wizzy.refineview.com` 并重启 API，之后发布会保留这个值。

检查：

```bash
curl -I http://wizzy.refineview.com/
curl https://wizzy.refineview.com/health/ready
curl https://wizzy.refineview.com/downloads/android/latest.json
curl https://wizzy.refineview.com/.well-known/assetlinks.json
certbot renew --cert-name wizzy.refineview.com --dry-run
```

HTTP 应跳转到 HTTPS；证书必须受系统信任；assetlinks 应直接返回 JSON 200，不能返回 H5 首页或跳转到另一个域名。

网关至百度云当前仍为跨公网 HTTP。这满足浏览器 WebAuthn 对前端 HTTPS origin 的要求；回源链路也会承载会话和设备密钥，进一步保护可使用经验证的 HTTPS 回源或私网隧道。当前改动保持原来的回源地址。

## APP 发布与签名

```bash
frontend/android/build-release.sh
./build-deploy.sh 0
```

需要 Java 17、Android SDK platform 35、build-tools 35.0.0。构建执行 Android lint 和 APK 验签，输出真实版本、大小、SHA-256 和 Digital Asset Links。网站登录页的“下载 APP”读取同源 `/downloads/android/latest.json`，提供 APK、二维码及安装说明。Android 8.0 起可安装，需联网；iOS 暂无安装包。厂商的来源安装限制仍由家长在系统中确认。

首次构建会在 `.local/android-signing/` 生成签名密钥和属性文件，必须安全备份这整个目录。**不能删除或重新生成后用于已安装用户升级**。发布压缩包只包含 APK、发布 JSON 和公开的证书指纹，不包含私钥或密码。以后发新版本同时递增 `app/build.gradle` 的 `versionCode` 与 `versionName`。

## 登录行为

- 首次由家长生成配对码；APP 配对后记住孩子设备。设备密钥默认 180 天，到期重新配对，在 Android Keystore 下加密保存。后端仅保存哈希。
- 自动登录每次创建短期 HttpOnly 子会话。设备密钥不进入 JavaScript、localStorage 或网页 cookie。关闭自动登录/退出会清除该设备密钥。
- 孩子可在“快捷登录”设置 Passkey。系统可能用指纹、人脸或屏幕锁；网站不采集人脸照片，也不能指定一定刷脸。Android WebView 能力不支持时保留配对码入口。
- Android WebView 使用 `FOR_APP` 模式，Passkey 的 clientData origin 是 `android:apk-key-hash:<签名证书 SHA-256 的 base64url>`。发布先构建并验签 APK，从同一产物的 assetlinks 提取正式签名，配置 `WIZZY_PASSKEY_ANDROID_ORIGIN`；后端仅接受网站 origin 和这一个明确的 APP origin，未配置时不接受 APP origin。不要加入通配符或调试签名。
- 普通退出保留用户主动设置的 Passkey，之后必须重新执行系统验证才能进入。家长撤销设备或撤回学生授权会使该设备的自动登录、Passkey 以及派生会话全部失效；重新授权不会恢复旧设备。
- Passkey 只进入孩子学习空间。家长中心仍需密码。云同步的 Passkey 可能出现在同一凭据账户的其他设备上，家长的“撤销设备”实际撤销该配对授权及其所有凭证。
- APK 仅在 HTTPS 下运行，不忽略证书错误。没有可信证书时不能把 APK 下载成功当作 APP 功能验收通过。

## 已执行的本地验证

真实 Chrome + WebAuthn 虚拟认证器 + 独立 PostgreSQL 已验证注册、退出后登录、HttpOnly 会话、刷新恢复、家长密码门禁和远程撤销。另有真实签名测试覆盖错误 origin、challenge、signature、user verification、重放、过期和授权撤回。前端通过类型检查、47 项单元测试、24 项浏览器测试，以及 H5/微信小程序构建。

腾讯网关 HTTPS、线上 Secure Cookie、APK 下载哈希与签名，以及 Android 模拟器上的正式 APK 已验证。真实设备上的凭据提供程序及人脸/指纹认证仍需验收；虚拟认证器不等于人脸硬件验收。

2026-11-15：完整后端回归 1482 通过、6 个需显式开启的浏览器测试跳过；家庭真实 API 浏览器验收及独立 Passkey 浏览器验收另行开启并通过；平台单元测试 92 通过。Android 15 模拟器执行 3 项原生测试通过（加密密钥持久化、损坏密钥拒绝、明文连接/备份策略）。补充后端正式签名 origin 回归 36 通过，接受已配置 APK 签名，拒绝其他 APK 签名及未配置的 Android origin。

`./build-deploy.sh 0` 已执行成功，生产数据库迁移为 `0040_learning_device_login`，正式 APP 为 0.1.1（versionCode 2）。网关已修正为 `Host $proxy_host`，公共 HTTPS 的 GET `/health/ready` 返回 200；HTTP 跳转 HTTPS，assetlinks 返回 JSON 200。正常域名的 Passkey options 使用正确 RP ID，ceremony cookie 含 Secure、HttpOnly、SameSite=Strict。用户重新执行 certbot dry-run，但最新提供的日志仍只到 `Simulating renewal of an existing certificate`，没有最终成功或失败结果，续期尚未验证通过。

公网下载 APK 为 3,588,548 字节，SHA-256 为 `7fcc4ee6f0957be2e48900479b3fd4a2106fee7023e2527ecb3b6a3156626aa2`，MIME 与发布清单匹配。下载产物通过 apksigner 验证，签名证书 SHA-256 为 `5F:09:65:D8:30:38:98:BA:FE:29:45:ED:FB:8F:F5:AA:59:76:30:DB:2B:3F:D4:B5:32:27:08:FC:C9:30:5B:EF`。正式域名 Chrome + 虚拟认证器完成注册、退出后 Passkey 登录、刷新、家长密码门禁、远程撤销，未出现页面脚本错误。

正式签名 APK 在 Android 15 模拟器连接生产域名，通过一次性配对、显示已开启自动登录、重启恢复学习空间、家长密码门禁、检查最新版本 0.1.1、图片来源选择器及相机启动。将仅本次验收孩子会话设为过期后，服务端确认原生设备恢复接口签发了新子会话；撤销该设备后再次启动回到配对页。Passkey 注册可调起 Android Credential Manager，模拟器仅提供其他设备创建入口，已取消，未认定本机人脸/指纹验收通过。验收使用隔离的临时家庭，结束后撤回授权、禁用测试账号和家庭，并撤销其设备及会话。

另行执行 Android WebView + 可信测试 HTTPS + 真实 API + 独立 PostgreSQL 联调通过：配对后自动保存、24 小时会话过期后重启恢复、家长密码门禁、退出清除密钥、再次启动保持退出、家长远程撤销阻止恢复，以及网关异常跳转时显示原生错误并可点击重新连接恢复。测试仅在复制出的临时 debug 项目配置测试 CA，生产 APK 不信任该 CA。

补充原生文件回传验收：将生成的测试 JPG 放入模拟器，实际打开 APP 的 Android 文件选择器并选择该文件，确认网页显示文件名和单张预览，移除后预览清空。扩展后的上述独立联调用例全部通过（23.29 秒），未绕过原生选择器注入网页文件。测试结束清除模拟器中的样例图片。

正式升级验收：在模拟器安装已发布的 0.1.0，再以保留数据方式安装已发布的 0.1.1，系统接受升级，应用 UID 保持一致。两个下载产物均通过 APK 验签，签名证书一致，升级后恢复安装的正式 0.1.1。

## 待执行的真机验收

用户已确认有 Android 真机，以下操作由用户手动执行。记录手机品牌型号、Android 版本、实际提供的认证方式及失败时的完整提示。

1. 手机浏览器打开网站，在登录页下载并安装 APP 0.1.1。手机先设置屏幕锁；如要验证人脸或指纹，先在手机系统中完成录入。
2. 家长在网站登录，选择测试孩子，在“家长中心 → 授权与设备”生成孩子配对码；APP 输入配对码并进入学习空间。
3. 彻底关闭并重新打开 APP，确认自动恢复孩子学习空间。这一步验证设备自动登录，不代表已验证 Passkey 或生物识别。
4. APP 点击“快捷登录 → 设置免密进入”，按系统提示创建凭据。返回后应显示“免密进入已开启”并列出凭据。
5. 点击“退出登录”，在登录页点击“免密进入学习”，完成系统认证后应回到孩子学习空间。系统可提供人脸、指纹或屏幕锁，分别记录实际出现的方式；不能把锁屏密码验证记为刷脸通过。
6. 点击“家长中心”，确认仍需家长密码；返回学习后拍摄或选择一张测试作业图片，确认预览可见并能移除。
7. 如测试远程撤销，在家长网站撤销本次测试设备；APP 重启后应不能自动恢复，该配对授权创建的 Passkey 也不能重新登录。需要继续使用时重新生成配对码。

如果系统只提供在其他设备创建凭据、没有本机凭据提供程序、显示暂不支持，或认证后返回错误，请记录发生的步骤和提示，不能据此判为 Passkey 真机验收通过。

证书演练若没有继续输出，保留现有进程，另开网关终端读取 `tail -n 60 /var/log/letsencrypt/letsencrypt.log`。根据日志诊断，不重复启动续期任务或改动已正常工作的代理配置。

复现命令（会清除所选模拟器中的小知数据；拒绝实体设备）：

```bash
cd backend
WIZZY_ANDROID_TESTS=1 WIZZY_ANDROID_SERIAL=emulator-5554 \
JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home \
ANDROID_HOME=/opt/homebrew/share/android-commandlinetools \
uv run pytest tests/test_learning_android.py -q
```
