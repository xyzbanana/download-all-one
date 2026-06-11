# AeroFetch · 抖音 / TikTok 无水印高清解析下载

一个开箱即用的全栈 Web 应用：粘贴抖音或 TikTok 分享链接，解析出无水印高清视频、图集与背景音乐，并通过后端代理流式下载到本地。

## 功能特性

- **多平台解析**：支持抖音 / TikTok 短链（`v.douyin.com`、`vt.tiktok.com`、`vm.tiktok.com`）重定向与分享文本中夹杂链接的提取
- **元数据提取**：创作者头像、昵称、ID、作品描述、点赞 / 评论 / 分享数
- **智能回退**：高清无水印 → 普通无水印 → 带水印，逐级回退保证下载可用
- **防盗链代理下载**：后端携带 UA / Referer / Cookie 流式转发 CDN 文件，`Content-Disposition` 强制触发浏览器下载
- **备用直链**：`rel="noreferrer"` 跳转直链，绕开 Referer 防盗链 403
- **图集轮播**：左右箭头 + 分页指示器 + 触摸滑动，一键打包 ZIP
- **音频提取**：单独下载背景音乐 MP3
- **解析历史**：LocalStorage 保存最近 6 条记录，点击一键重新解析
- **现代 UI**：暗黑极光渐变 + 毛玻璃 + 霓虹渐变按钮 + 骨架屏加载

## 项目结构

```
douyin/
├── main.py            # FastAPI 后端（解析 + 流式代理下载 + 托管前端）
├── index.html         # 前端页面结构
├── style.css          # Aero 设计系统样式
├── app.js             # 前端交互逻辑（原生 JS）
├── requirements.txt   # Python 依赖
└── README.md
```

## 快速开始

### 在线体验

**开箱即用，无需任何配置：**

- 🌐 完整站点（前后端一体）：**https://aerofetch-api.onrender.com**
- 🌐 GitHub Pages 入口：**https://jfgatlas.github.io/AeroFetch/**（自动调用上面的公共后端）

> 公共后端部署在 Render 免费档：闲置 15 分钟后休眠，首次访问冷启动约 30~60 秒，之后恢复秒级响应。

前端兼容两种自建后端（在右上角 ⚙ 设置中填入即可）：

1. **本项目后端**（推荐，功能最全：代理下载 / 图集 ZIP / ffmpeg 音频提取）
2. **自建 [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) 节点**（`minimal` 模式，需开启 CORS 与下载端点；若填作者公共节点 `api.douyin.wtf`，将自动经公共 CORS 中继访问且仅支持直链下载）

### 一键部署后端（免费）

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/JFGAtlas/AeroFetch)

点击上方按钮，用 GitHub 账号登录 Render 即可免费部署后端（已内置 Dockerfile 与 ffmpeg）。部署完成后会得到一个 `https://xxx.onrender.com` 地址：

- 直接访问该地址 = 前后端一体的完整网站，开箱即用
- 或在 GitHub Pages 演示页的 ⚙ 设置中填入该地址作为 API 后端

> 免费档约 15 分钟无访问会休眠，下次访问冷启动需 30~60 秒。海外服务器解析抖音偶尔触发风控，配置 `DOUYIN_COOKIE` 环境变量可显著提高成功率。

### 本地运行

```bash
# 1. 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 启动服务（同时托管前端与 API）
uvicorn main:app --host 0.0.0.0 --port 8000

# 3. 浏览器打开
open http://127.0.0.1:8000
```

### Docker 运行

```bash
docker build -t aerofetch .
docker run -d -p 8000:8000 aerofetch
```

## API

### `GET /api/hybrid/video_data`

解析作品元数据。

| 参数 | 说明 |
| ---- | ---- |
| `url` | 分享链接或包含链接的分享文本（URL 编码） |

返回统一结构：`platform`、`aweme_id`、`type`（video / image）、`desc`、`author`、`statistics`、`video`（`nwm_video_url_HQ` / `nwm_video_url` / `wm_video_url`）、`images`、`music`。

### `GET /api/download`

后端代理流式下载（附件形式回传）。

| 参数 | 说明 |
| ---- | ---- |
| `url` | 分享链接或分享文本 |
| `media` | `video`（默认）/ `audio` / `image` / `images`（打包 ZIP） |
| `index` | `media=image` 时的图片序号，默认 0 |
| `watermark` | `true` 时优先下载带水印版本，默认 `false` |

### `GET /api/health`

健康检查，返回 `{"status": "ok"}`。

## 环境变量（可选）

| 变量 | 说明 |
| ---- | ---- |
| `PROXY_URL` | 全局代理，例如 `http://127.0.0.1:7890`（访问 TikTok 通常需要） |
| `DOUYIN_COOKIE` | 抖音 Cookie，触发风控时配置 |
| `TIKTOK_COOKIE` | TikTok Cookie，部分地区 / 资源需要 |

```bash
PROXY_URL=http://127.0.0.1:7890 uvicorn main:app --port 8000
```

## 注意事项

- 抖音 / TikTok 页面结构与接口可能随时调整，解析失败时优先尝试配置 Cookie 或代理
- 本项目仅供个人学习与技术研究使用，请尊重内容创作者版权，勿用于商业用途

---

## 声明

> **慈善开源项目 · 无广告 · 永久更新 · 免费使用**

本项目完全免费开源，不含任何广告与商业推广，将持续维护更新。

## 联系作者

| 渠道 | 地址 |
| ---- | ---- |
| 𝕏 (Twitter) | [@JFGAi](https://x.com/JFGAi) |
| Telegram | [t.me/jfgae](https://t.me/jfgae) |
| GitHub | [JFGAtlas](https://github.com/JFGAtlas) |

## 捐赠支持

如果这个项目对你有帮助，欢迎捐赠支持项目的持续维护：

| 网络 | 地址 |
| ---- | ---- |
| EVM (ETH / BSC / Polygon 等) | `0x3EE918603d5a1c0f983BEC5B5d8C301F8ed58A2C` |
| Solana | `2LEDYj19kormPezoiFgZAguyCVsfaM3HExsYe2NWpNqk` |
| Bitcoin | `bc1qs2nwumk24fjtk574f0awaxnh7jl9v7shrd5yw7` |
