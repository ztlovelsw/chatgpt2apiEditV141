<h1 align="center">ChatGPT2API</h1>


<p align="center">ChatGPT2API 主要是对 ChatGPT 官网相关能力进行逆向整理与封装，提供面向 ChatGPT 图片生成、图片编辑、参考图生成 GIF 与多图组图编辑场景的 OpenAI 兼容 API / 代理，并集成在线画图、号池管理、多种账号导入方式与 Docker 自托管部署能力。</p>

> [!WARNING]
> 免责声明：
>
> 本项目涉及对 ChatGPT 官网文本生成、图片生成与图片编辑等相关接口的逆向研究，仅供个人学习、技术研究与非商业性技术交流使用。
>
> - 严禁将本项目用于任何商业用途、盈利性使用、批量操作、自动化滥用或规模化调用。
> - 严禁将本项目用于破坏市场秩序、恶意竞争、套利倒卖、二次售卖相关服务，以及任何违反 OpenAI 服务条款或当地法律法规的行为。
> - 严禁将本项目用于生成、传播或协助生成违法、暴力、色情、未成年人相关内容，或用于诈骗、欺诈、骚扰等非法或不当用途。
> - 使用者应自行承担全部风险，包括但不限于账号被限制、临时封禁或永久封禁以及因违规使用等所导致的法律责任。
> - 使用本项目即视为你已充分理解并同意本免责声明全部内容；如因滥用、违规或违法使用造成任何后果，均由使用者自行承担。
> - 本项目基于对 ChatGPT 官网相关能力的逆向研究实现，存在账号受限、临时封禁或永久封禁的风险。请勿使用你自己的重要账号、常用账号或高价值账号进行测试。

## 快速开始

### Docker 运行

```bash
git clone https://github.com/basketikun/chatgpt2api.git
cd chatgpt2api
docker compose up -d
```

启动前请先在 `config.json` 中设置 `auth-key`，也可以在 `docker-compose.yml` 中通过 `CHATGPT2API_AUTH_KEY` 覆盖。

- Web 面板：`http://localhost:3000`
- API 地址：`http://localhost:3000/v1`
- 数据目录：`./data`

### WARP / FlareSolverr 稳定代理部署

如果注册或图片链路经常遇到 Cloudflare 拦截，可以启用附带的 WARP + Privoxy + FlareSolverr 方案：

```bash
cp .env.example .env
docker compose -f docker-compose.warp.yml up -d --build
```

该 compose 会启动：

- `warp-proxy`：提供 WARP SOCKS5 出口。
- `privoxy`：把 WARP SOCKS5 转成 HTTP 代理。
- `flaresolverr`：刷新 Cloudflare clearance。
- `init-config`：幂等写入 `proxy_runtime` 默认配置。
- `app`：启动 ChatGPT2API 主服务。

默认只让上游 OpenAI / ChatGPT 请求走稳定代理，账号邮箱、CPA 等辅助链路不会被强制接管。账号自身配置的代理优先级最高，其次是稳定代理运行时，再其次是显式代理和旧版全局代理。

可在 `.env` 中调整端口和代理运行时参数，也可在后台设置页的「稳定代理运行时」面板手动保存、测试代理和测试 clearance。

### 本地开发

启动后端：

```bash
git clone https://github.com/basketikun/chatgpt2api.git
cd chatgpt2api
uv sync
uv run main.py
```

启动前端：

```bash
cd chatgpt2api/web
bun install
bun run dev
```

后续更新新版本：

```bash
docker compose pull
docker compose up -d
```

### 存储后端配置

支持通过环境变量 `STORAGE_BACKEND` 切换存储方式：

- `json` - 本地 JSON 文件（默认）
- `sqlite` - 本地 SQLite 数据库
- `postgres` - 外部 PostgreSQL（需配置 `DATABASE_URL`）
- `git` - Git 私有仓库（需配置 `GIT_REPO_URL` 和 `GIT_TOKEN`）

示例：使用 PostgreSQL

```yaml
environment:
  - STORAGE_BACKEND=postgres
  - DATABASE_URL=postgresql://user:password@host:5432/dbname
```

## 功能

### 提示词链接导入与示例图

- 「提示词」页默认展示完整宽度的浏览列表，支持搜索、分类筛选和分页。点击「新建提示词」后选择「提示词订阅」或「自定义添加提示词」，在弹窗内完成添加；列表中的编辑按钮也会打开编辑弹窗。
- 一次性导入时，进入「新建提示词 → 自定义添加提示词」，展开「从链接导入内容」，粘贴公开网页、Markdown、纯文本或 JSON 链接，点击「拉取」，核对内容后保存。GitHub `blob` 文件链接会自动读取原始文件。
- 支持识别代码块、引用块、提示词字段以及 JSON 的 `prompt` / `content` 内容；一页包含多条提示词时可以逐条选择保存。JSON 示例：`{"name":"橘猫","prompt":"一只橘猫坐在窗台上","images":["https://example.com/cat.png"]}`。
- 已适配[水仙的 AI 提示词库](https://github.com/BaYue-SYJ/shuixian-prompts)：直接粘贴仓库主页、`https://prompt.qqsrc.com/` 或单条详情链接即可。读取完整数据分片，保留原始提示词（含模板参数）、分类、来源及多张效果图；全库每页 100 条，可按标题、分类或正文搜索，数据缓存 5 分钟。也支持该仓库的 `prompts.part*.json` / `list.part*.json` 文件链接。
- 每条提示词可保留来源 URL 和最多 12 张示例效果图，支持预览、移除和手动补充图片链接。生图页选择提示词时也会显示示例缩略图。
- 示例图以原站 URL 保存，显示受原站可访问性影响。需要登录、反爬验证或完全依赖 JavaScript 加载的页面可能无法解析；未识别到内容时会提示更换公开页面或原始文件链接。
- 导入接口：`POST /api/prompt-library/import-url`，使用现有 Bearer 密钥认证，请求体为 `{"url":"https://example.com/prompt.md"}`，返回 `{items, warnings}` 供预览，不会自动写入提示词库。
- 水仙库导入可额外传入 `page`（从 1 开始）与 `query`，返回 `total`、`page`、`page_size`、`has_more` 和 `source_name`。只有固定的水仙数据分片使用每片 8MB 上限，普通网页仍限制为 2MB。

### 多来源提示词订阅

- 在「提示词 → 新建提示词 → 提示词订阅」弹窗内填写名称、链接和自定义更新间隔，可添加多个来源，也可管理已有订阅。默认每 12 小时同步，支持选择分钟、小时或天（例如 30 分钟、1.5 小时、2 天），范围为 5 分钟至 7 天、精确到整分钟；添加后自动排队进行首次同步，也可立即同步、暂停、恢复或删除订阅。编辑时保留已有订阅的更新间隔。
- 同步任务在服务器后台运行，每 30 秒检查到期订阅，关闭浏览器也会继续。订阅配置、上次结果和下次时间保存到 `data/libraries/prompt_subscriptions.json`，服务重启后恢复计划。服务需要保持运行；当前部署方式为单个应用进程。
- 水仙库订阅每次读取全部数据分片；普通网页、Markdown 和 JSON 也可订阅，不受手动预览的 100 条分页限制。JSON 建议给每条提示词提供稳定的 `id`；缺少 ID 时按标题和同名条目的顺序匹配，没有标题的条目按文档位置匹配。单个通用来源最多 20000 条，下载大小限制与手动导入一致。
- 同一订阅再次同步时更新已有条目的文字和示例图，保持本地条目 ID。手动编辑的条目保留本地内容，手动删除的条目不会被重新加入；上游删除条目、删除订阅时均保留已同步内容。下载或解析失败时保留本地库，并展示错误和下次重试时间。
- 提示词列表和生图页的提示词选择器支持本地搜索、每页 30 条，避免全量订阅后一次渲染大量图片。
- 管理接口均使用现有 Bearer 密钥：`GET/POST /api/prompt-subscriptions`、`POST/DELETE /api/prompt-subscriptions/{id}`、`POST /api/prompt-subscriptions/{id}/sync`。新增请求示例：`{"name":"水仙提示词","url":"https://github.com/BaYue-SYJ/shuixian-prompts","interval_minutes":720}`，省略间隔时默认 720 分钟。编辑支持 `name`、`interval_minutes`、`enabled`；修改来源链接请删除后重新订阅。

### 生图号池分档

- 按订阅档位划分 GPT Free、Plus、Pro 三档。账号页面分别展示真实剩余额度和账号数；登录来源不再决定普通账号的订阅分组。
- 自动生图优先使用可用 Free 账号，其次 Plus，最后 Pro；额度为 0、限流、禁用、异常或并发已满的账号不参与当次调度。手动指定档位时只使用该档位。
- 生图及编辑接口的 `account_pool` 支持 `default`（自动）、`free`、`plus`、`pro`。旧 `gptfree` 来源参数保留兼容；画图页旧的 gptFree 选择会迁移到按订阅划分的 Free 档。
- Free 的剩余额度使用账号上游返回值，正常显示为 20 的账号不会被来源筛选误显示为 0；实际用尽的额度保留为 0。

### 自动发现模型

- `GET /v1/models` 合并当前启用账号的上游模型列表，保留模型名称、隐藏标记和能力信息，不再插入固定的“最新聊天模型”。新增同协议模型无需更新代码。
- 聊天模型缓存 5 分钟，账号增删、类型或状态变化后自动失效；`GET /v1/models?refresh=true` 可立即重新获取。
- 图片模型由 [OpenAI 官方图片模型目录](https://developers.openai.com/api/reference/resources/images/methods/generate) 自动补齐，每 6 小时更新，并持久化至 `data/image_model_catalog.json`。目录故障时保留上次成功结果；首次获取失败时使用兼容列表。
- 返回字段 `availability=account` 表示账号接口收录，`availability=catalog` 表示官方图片目录候选，后者不保证单个账号具备调用权限。模型名称识别支持后续主版本、子版本和快照；上游协议改变仍需适配。
- 聊天、画图和接口说明页每 5 分钟更新模型，切回窗口时也会检查更新。原有的自定义模型输入仍可使用。

### API 兼容能力

- GIF 文生图可指定 `output_format: "gif"`、`frame_count`（2–16，默认 9）和 `frame_duration_ms`（20–2000，默认 200）。尺寸使用 `auto` 或长宽为 16 倍数的 `WIDTHxHEIGHT`。
- 普通图片模型通过 ChatGPT 官网图片对话链路生成 GIF，支持 Web/OAuth 账号；Plus 账号已实测成功。帧数、帧间隔通过提示词传递，接口返回实际动画元数据，并过滤同时返回的静态预览图。
- `codex-` 前缀模型仍使用 `/backend-api/codex/images/generations` 独立接口；该接口可能拒绝 GIF，但不能据此判断账号的官网 GIF 功能不可用。
- GIF 支持输入参考图：使用普通图片模型调用 `POST /v1/images/edits`，通过 multipart 上传 `image` 或通过 JSON 提供 `images: [{"image_url":"..."}]`，同时设置 `output_format: "gif"`，可选 `frame_count` 和 `frame_duration_ms`。网页上传参考图后选择 GIF 即可使用，Chat Completions / Responses 的图片输入也支持 GIF 输出。Codex 前缀模型的独立 GIF 接口暂不支持参考图。
- GIF 不支持 Images API 的 `stream=true`。网页图像设置可选择 GIF、设置帧数和帧间隔；生成记录保留参数，原始动画文件以 `.gif` 保存、预览和下载。

图生 GIF 调用示例（将 `<auth-key>` 替换为自己的密钥）：

```bash
curl http://localhost:3000/v1/images/edits \
  -H "Authorization: Bearer <auth-key>" \
  -F "model=gpt-image-2" \
  -F "image=@./input.png" \
  -F "prompt=让图片中的角色挥手，保持原图风格" \
  -F "output_format=gif" \
  -F "frame_count=9" \
  -F "frame_duration_ms=200"
```

- 兼容 `POST /v1/images/generations` 图片生成接口
- 兼容 `POST /v1/images/edits` 图片编辑接口
- 兼容面向图片场景的 `POST /v1/chat/completions`
- 兼容面向图片场景的 `POST /v1/responses`
- `GET /v1/models` 动态返回当前账号可用模型及图片模型目录候选；实际结果以接口响应为准
- 支持通过 `n` 返回多张生成结果
- 支持 `gpt-image-2.5`、`gpt-image-2.6`，简称默认使用对应的 `sunburst`；也可指定 `gpt-image-2.5-flare`、`gpt-image-2.6-flare` 等完整名称和快照名称。
- 新版图片模型可用于文生图、图片编辑、Chat Completions 和 Responses。Codex 账号可使用对应的 `codex-` 模型名及 `plus-` / `team-` / `pro-` 前缀；实际可用性取决于上游账号权限。
- 支持生成可编辑 PPT 文件
- 支持生成可编辑 PSD 文件
- 支持 Codex 中的画图接口逆向，仅 `Plus` / `Team` / `Pro` 订阅可用，模型别名为 `codex-gpt-image-2`，如有需要可自行在其他场景映射回
  `gpt-image-2`，用于和官网画图区分；也就意味着同一账号会同时有官网和 Codex 两份生图额度

### 在线画图功能

- 内置在线画图工作台，支持生成、图片编辑与多图组图编辑
- 模型列表自动跟随 `GET /v1/models` 更新，也支持自定义输入模型名
- 编辑模式支持参考图上传
- 支持 PNG / GIF 输出，可设置 GIF 帧数与帧间隔
- 前端支持多图生成交互
- 本地保存图片会话历史，支持回看、删除和清空
- 支持服务端缓存图片 URL
- 图片生成进度追踪，超时后可继续等待
- 图片懒加载与滚动位置记忆，优化大量图片场景性能

### 号池管理功能

- 自动刷新账号邮箱、类型、额度和恢复时间（异步进度追踪）
- 轮询可用账号执行图片生成与图片编辑
- 遇到 Token 失效类错误时自动剔除无效 Token
- 定时检查限流账号并自动刷新
- 支持密码重新登录恢复异常账号，刷新后可自动重登
- 支持网页端配置全局 HTTP / HTTPS / SOCKS5 / SOCKS5H 代理
- 支持 WARP / FlareSolverr 稳定代理运行时
- 支持搜索、筛选、批量刷新、导出、手动编辑和清理账号
- 支持四种导入方式：本地 CPA JSON 文件导入、远程 CPA 服务器导入、`sub2api` 服务器导入、`access_token` 导入
- 支持在设置页配置 `sub2api` 服务器，筛选并批量导入其中的 OpenAI OAuth 账号

### 实验性 / 规划中

- 详细状态说明见：[功能清单](./docs/feature-status.en.md)

## 效果展示

<table width="100%">
  <tr>
    <td width="50%"><img src="https://i.ibb.co/Jj8nfwwP/image.png" alt="image" border="0"></td>
    <td width="50%"><img src="https://i.ibb.co/pqf235v/image-edit.png" alt="image edit" border="0"></td>
  </tr>
  <tr>
    <td width="50%"><img src="https://i.ibb.co/tPcqtVfd/chery-studio.png" alt="chery studio" border="0"></td>
    <td width="50%"><img src="https://i.ibb.co/PsT9YHBV/account-pool.png" alt="account pool" border="0"></td>
  </tr>
  <tr>
    <td width="50%"><img src="https://i.ibb.co/rRWLG08q/new-api.png" alt="new api" border="0"></td>
  </tr>
</table>

## API

所有 AI 接口都需要请求头：

```http
Authorization: Bearer <auth-key>
```

以下示例使用 Docker 默认地址 `http://localhost:3000`；本地直接运行后端时，Uvicorn 默认监听 `http://localhost:8000`。

<details>
<summary><code>GET /v1/models</code></summary>
<br>

返回当前账号上游模型和图片模型目录候选的合并列表。添加 `?refresh=true` 可跳过缓存并立即刷新。

```bash
curl 'http://localhost:3000/v1/models?refresh=true' \
  -H "Authorization: Bearer <auth-key>"
```

<details>
<summary>说明</summary>
<br>

| 字段 / 参数 | 说明 |
|:------------|:-----|
| `refresh` | 查询参数；设为 `true` 时立即重新拉取模型 |
| `availability` | `account` 表示账号上游已收录，`catalog` 表示图片目录候选 |
| 接入场景 | 可接入 Cherry Studio、New API 等上游或客户端 |

<br>
</details>
</details>

<details>
<summary><code>POST /v1/images/generations</code></summary>
<br>

OpenAI 兼容图片生成接口，用于文生图。

```bash
curl http://localhost:3000/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "一只漂浮在太空里的猫",
    "n": 1,
    "response_format": "b64_json"
  }'
```

<details>
<summary>字段说明</summary>
<br>

| 字段 | 说明 |
|:-----|:-----|
| `model` | 图片模型，当前可用值以 `/v1/models` 返回结果为准，默认 `gpt-image-2` |
| `prompt` | 图片生成提示词，必填 |
| `n` | 生成数量，范围 `1-4`，默认 `1` |
| `size` | 图片尺寸；GIF 使用 `auto` 或宽高均为 16 倍数的 `WIDTHxHEIGHT` |
| `quality` | 生成质量，默认 `auto` |
| `response_format` | 返回格式，默认 `b64_json`；也可使用 `url` |
| `account_pool` | `default`、`free`、`plus` 或 `pro`；`default` 按 Free → Plus → Pro 自动选择 |
| `output_format` | `png`（默认）或 `gif` |
| `frame_count` | GIF 帧数，范围 `2-16`，默认 `9` |
| `frame_duration_ms` | GIF 单帧时长，范围 `20-2000` 毫秒，默认 `200` |
| `stream` | 是否流式返回；GIF 不支持 `true` |

<br>
</details>
</details>

<details>
<summary><code>POST /v1/images/edits</code></summary>
<br>

OpenAI 兼容图片编辑接口，可上传图片文件，也可通过 JSON 传入远程 URL、Data URL 或 Base64 图片并生成编辑结果。单张图片最大 50 MB。

```bash
curl http://localhost:3000/v1/images/edits \
  -H "Authorization: Bearer <auth-key>" \
  -F "model=gpt-image-2" \
  -F "prompt=把这张图改成赛博朋克夜景风格" \
  -F "n=1" \
  -F "image=@./input.png"
```

也可以直接传图片 URL：

```bash
curl http://localhost:3000/v1/images/edits \
  -H "Authorization: Bearer <auth-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "把这张图改成赛博朋克夜景风格",
    "images": [
      {"image_url": "https://example.com/input.png"}
    ]
  }'
```

<details>
<summary>字段说明</summary>
<br>

| 字段 | 说明 |
|:-----|:-----|
| `model` | 图片模型，默认 `gpt-image-2` |
| `prompt` | 图片编辑提示词，必填 |
| `n` | 生成数量，范围 `1-4`，默认 `1` |
| `image` / `image[]` | multipart 图片文件；字段可重复以传入多张图 |
| `images` | JSON 图片引用数组，支持 `image_url`、`b64_json` 或 `base64` |
| `image_url` | 表单或 JSON 图片链接；支持远程 HTTP(S) URL 和 Data URL |
| `mask` / `mask[]` | 可选遮罩图片 |
| `account_pool` | `default`、`free`、`plus` 或 `pro` |
| `output_format` | `png`（默认）或 `gif`；参考图生成 GIF 请使用普通图片模型 |
| `frame_count` | GIF 帧数，范围 `2-16`，默认 `9` |
| `frame_duration_ms` | GIF 单帧时长，范围 `20-2000` 毫秒，默认 `200` |
| `stream` | 是否流式返回；GIF 不支持 `true` |

<br>
</details>
</details>

<details>
<summary><code>POST /v1/chat/completions</code></summary>
<br>

面向文本、网页搜索与图片场景的 Chat Completions 兼容接口，不是完整通用聊天代理。

```bash
curl http://localhost:3000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -d '{
    "model": "gpt-image-2",
    "messages": [
      {
        "role": "user",
        "content": "生成一张雨夜东京街头的赛博朋克猫"
      }
    ],
    "n": 1
  }'
```

<details>
<summary>字段说明</summary>
<br>

| 字段                   | 说明                                                                           |
|:---------------------|:-----------------------------------------------------------------------------|
| `model`              | 文本、搜索或图片模型；搜索模型会触发网页搜索兼容逻辑                                                   |
| `messages`           | 消息数组，支持文本、搜索和图片请求内容                                                          |
| `n`                  | 图片生成数量，按当前实现解析为图片数量                                                          |
| `stream`             | 文本、搜索和图片场景均支持，仍在测试                                                           |
| `tools`              | 文本场景支持 `web_search` / `web_search_preview` / `web_search_preview_2025_03_11` |
| `web_search_options` | 传入时会触发网页搜索兼容逻辑                                                               |
| `account_pool`       | 图片或文本请求使用的账号池：`default`、`free`、`plus` 或 `pro`                             |
| `output_format`      | 图片请求输出 `png` 或 `gif`；GIF 还可传 `frame_count`、`frame_duration_ms`                 |

<br>
</details>
</details>

<details>
<summary><code>POST /v1/responses</code></summary>
<br>

面向文本、网页搜索和图片生成工具调用的 Responses API 兼容接口，不是完整通用 Responses API 代理。

```bash
curl http://localhost:3000/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <auth-key>" \
  -d '{
    "model": "gpt-5",
    "input": "生成一张未来感城市天际线图片",
    "tools": [
      {
        "type": "image_generation"
      }
    ]
  }'
```

<details>
<summary>字段说明</summary>
<br>

| 字段       | 说明                                                                                      |
|:---------|:----------------------------------------------------------------------------------------|
| `model`  | 响应中会回显该模型字段，搜索和图片生成会走对应兼容逻辑                                                             |
| `input`  | 输入内容；搜索使用最后一条用户文本，图片生成需能解析出提示词                                                          |
| `tools`  | 支持 `image_generation`、`web_search`、`web_search_preview`、`web_search_preview_2025_03_11` |
| `stream` | 已实现，但仍在测试                                                                               |
| `account_pool` | 图片或文本请求使用的账号池：`default`、`free`、`plus` 或 `pro`                                      |
| GIF 参数 | 可在请求体或 `image_generation` 工具中传 `output_format`、`frame_count`、`frame_duration_ms`          |

<br>
</details>
</details>

## 社区支持

学 AI , 上 L 站：[LinuxDO](https://linux.do)

## Contributors

感谢所有为本项目做出贡献的开发者：

<a href="https://github.com/basketikun/chatgpt2api/graphs/contributors">
  <img alt="Contributors" src="https://contrib.rocks/image?repo=basketikun/chatgpt2api" />
</a>

## Star History

[![Star History Chart](https://api.star-history.com/chart?repos=basketikun/chatgpt2api&type=date&legend=top-left)](https://www.star-history.com/?repos=basketikun%2Fchatgpt2api&type=date&legend=top-left)
