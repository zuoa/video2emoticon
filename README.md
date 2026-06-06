# Video2Emoticon

通过视频文件或 Bilibili BV 号生成自定义 GIF 表情的 Web 工具，并提供独立的 BV 音频片段提取页面。前端提供框选、时间段、帧率、文字层和循环设置，后端用 FFmpeg 与 yt-dlp 完成下载和导出。

## 功能

- 上传本地视频，或输入 Bilibili BV 号 / `bilibili.com/video/BV...` 地址下载视频；URL 会自动提取 BV 与 `p` 参数，多分 P 视频可选择具体分 P。
- 在视频上拖拽框选裁剪区域，预览阶段使用遮罩显示选区。
- 可通过播放位置滑块定位片段，使用开始时间和持续秒数控制导出范围。
- 支持输出宽度、帧率、变速、循环播放设置，导出后自动下载 GIF。
- 支持单个基础文字层：内容、位置、字号、颜色、描边、背景框。
- 支持循环或非循环 GIF。
- 独立音频工具页 `/#/audio` 支持输入 BV 号 / Bilibili URL、选择分 P、先下载视频、设置开始和结束时间、试听片段，并导出 `mp3`、`m4a` 或 `wav` 音频片段。
- 上传和下载的原视频会保留用于连续制作多个 GIF；同一个 BV 的同一分 P 会复用本地源文件，服务会自动清理 24 小时未使用的原视频文件。
- Docker 单容器部署，运行时文件通过 `/data` 持久化；Compose 默认映射到宿主机目录。

## Docker 运行

```bash
docker build -t video2emoticon .
docker run --rm -p 8000:8000 -v video2emoticon-data:/data video2emoticon
```

打开 `http://localhost:8000`。

也可以使用 Compose：

```bash
mkdir -p data/uploads data/downloads data/outputs data/fonts cookies
docker compose pull
docker compose up
```

Compose 默认使用目录映射：

```text
./data/uploads   -> /data/uploads
./data/downloads -> /data/downloads
./data/outputs   -> /data/outputs
./data/fonts     -> /data/fonts
./cookies        -> /data/cookies
```

字体文件可以通过页面上传，也可以直接放入 `data/fonts/` 后在页面点击刷新。支持 `.ttf`、`.otf`、`.ttc`、`.otc`。

## Bilibili Cookie

Bilibili 可能返回 `HTTP Error 412: Precondition Failed`，这通常需要带登录态 cookie 下载。

推荐方式是使用 Netscape 格式的 cookies 文件：

```bash
mkdir -p cookies
# 将导出的 Bilibili cookies 保存为 cookies/bilibili.cookies.txt
docker compose up
```

也可以直接传浏览器请求里的原始 `Cookie` header：

```bash
docker run --rm \
  -p 8000:8000 \
  -v video2emoticon-data:/data \
  -e BILIBILI_COOKIE_HEADER='SESSDATA=...; bili_jct=...; DedeUserID=...' \
  video2emoticon
```

还支持把 Netscape cookies 文件内容放到 `BILIBILI_COOKIES` 环境变量中，服务会写入 `/data/cookies/bilibili.cookies.txt` 后交给 `yt-dlp`。

`yt-dlp` 会在读取 cookies 文件后写回更新后的 cookie jar，因此 Compose 里的 `cookies/` 挂载需要保持可写。不要把 cookie 提交到 GitHub；`cookies/` 已加入 `.gitignore`。

## 本地开发

后端：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

前端开发服务默认代理 `/api` 到 `http://127.0.0.1:8000`。

## GitHub Actions 镜像

`.github/workflows/docker.yml` 会在以下场景构建镜像：

- push 到 `main` 或 `master`
- push `v*` tag
- pull request 构建校验
- 手动触发 `workflow_dispatch`

非 pull request 事件会推送到：

```text
ghcr.io/<owner>/<repo>:latest
ghcr.io/<owner>/<repo>:<branch-or-tag>
ghcr.io/<owner>/<repo>:sha-<commit>
```

部署时挂载 `/data`：

```bash
docker run -p 8000:8000 -v video2emoticon-data:/data ghcr.io/<owner>/<repo>:latest
```

如果用 Compose 部署，按本仓库的 `docker-compose.yml` 会映射到当前目录下的 `data/` 和 `cookies/`。

## 环境变量

- `DATA_DIR`：运行时数据目录，默认 `/data`。
- `FRONTEND_DIST`：前端静态文件目录，镜像内默认 `/app/frontend/dist`。
- `FONT_FILE`：FFmpeg `drawtext` 使用的字体文件路径。
- `/data/fonts`：页面可扫描的字体目录，用于文字层字体选择和预览。
- `CORS_ORIGINS`：开发时允许的跨域来源，默认 `*`。
- `BILIBILI_COOKIES_FILE`：Netscape 格式 Bilibili cookies 文件路径，推荐挂载到 `/data/cookies/bilibili.cookies.txt`。
- `BILIBILI_COOKIE_HEADER`：浏览器请求里的原始 `Cookie` header，例如 `SESSDATA=...; bili_jct=...`。
- `BILIBILI_COOKIES`：Netscape cookies 文件内容，适合通过部署平台 Secret 注入。

## 运行时依赖

镜像内置：

- Python 3.12
- FFmpeg
- yt-dlp
- DejaVu 字体

Bilibili 下载取决于部署环境网络可达性和 cookie 有效性。部分视频如果需要会员、地区权限或 cookie 过期，yt-dlp 可能无法下载，后端会把失败原因返回给前端。
