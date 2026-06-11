"""
抖音 (Douyin) & TikTok 无水印高清媒体解析与下载 API
================================================
技术栈: FastAPI + httpx (异步流式传输)

核心接口:
  GET /api/hybrid/video_data?url=<分享链接或分享文本>   解析元数据
  GET /api/download?url=<分享链接>&media=video|audio|images&index=0
        后端代理流式下载(携带 UA/Referer/Cookie, 跟随重定向, 附件形式回传)

环境变量(可选):
  PROXY_URL      全局代理, 例如 http://127.0.0.1:7890
  DOUYIN_COOKIE  抖音 Cookie(部分资源需要)
  TIKTOK_COOKIE  TikTok Cookie(部分地区/资源需要)

启动: uvicorn main:app --host 0.0.0.0 --port 8000
"""

import asyncio
import io
import json
import os
import re
import shutil
import tempfile
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

# ----------------------------------------------------------------------------
# 全局配置
# ----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent

PROXY: Optional[str] = os.getenv("PROXY_URL") or None
DOUYIN_COOKIE: str = os.getenv("DOUYIN_COOKIE", "")
TIKTOK_COOKIE: str = os.getenv("TIKTOK_COOKIE", "")

REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=15.0)
CHUNK_SIZE = 64 * 1024  # 流式回传分块大小

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 解析页面时使用的请求头
DOUYIN_PAGE_HEADERS = {
    "User-Agent": MOBILE_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.douyin.com/",
}
TIKTOK_PAGE_HEADERS = {
    "User-Agent": DESKTOP_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.tiktok.com/",
}

# 下载 CDN 资源时使用的请求头(防盗链关键: Referer + UA)
DOUYIN_CDN_HEADERS = {
    "User-Agent": MOBILE_UA,
    "Referer": "https://www.douyin.com/",
    "Accept": "*/*",
}
TIKTOK_CDN_HEADERS = {
    "User-Agent": DESKTOP_UA,
    "Referer": "https://www.tiktok.com/",
    "Accept": "*/*",
}

URL_REGEX = re.compile(r"https?://[\w\-._~:/?#\[\]@!$&'()*+,;=%]+", re.I)


def new_client(headers: Optional[dict] = None) -> httpx.AsyncClient:
    """统一构造 httpx 异步客户端: 跟随重定向 + 全局代理 + 超时"""
    return httpx.AsyncClient(
        headers=headers,
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,  # 必须开启: 短链/无水印接口均依赖 302 跳转
        proxy=PROXY,
    )


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------
def extract_url(text: str) -> str:
    """从分享文本中提取第一个 URL(支持文本中夹杂链接)"""
    match = URL_REGEX.search(text or "")
    if not match:
        raise HTTPException(status_code=400, detail="未在输入中找到有效链接")
    return match.group(0)


def detect_platform(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "douyin" in host or "iesdouyin" in host:
        return "douyin"
    if "tiktok" in host:
        return "tiktok"
    raise HTTPException(status_code=400, detail="仅支持抖音 (Douyin) 与 TikTok 链接")


async def resolve_url(client: httpx.AsyncClient, url: str) -> str:
    """解析短链(v.douyin.com / vt.tiktok.com / vm.tiktok.com)的最终重定向地址"""
    try:
        resp = await client.get(url)
        return str(resp.url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"短链重定向失败: {exc}") from exc


def first_url(obj: Any) -> Optional[str]:
    """从 {'url_list': [...]} 结构中安全取第一个地址"""
    if isinstance(obj, dict):
        urls = obj.get("url_list") or obj.get("urlList") or []
        if urls:
            return urls[0]
    return None


def sanitize_filename(name: str, fallback: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n#%&{}$@\s]+", "_", (name or "").strip())
    name = name.strip("._")
    return name[:40] or fallback


def content_disposition(filename: str) -> str:
    """同时提供 ASCII 回退名与 RFC 5987 UTF-8 文件名, 兼容所有浏览器。
    HTTP 头只能是 latin-1, 回退名必须先剥离所有非 ASCII 字符(中文等),
    否则 Starlette 编码响应头时会抛 UnicodeEncodeError 导致 500。"""
    quoted = urllib.parse.quote(filename)
    stem, dot, ext = filename.rpartition(".")
    ascii_stem = (stem or filename).encode("ascii", "ignore").decode()
    ascii_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_stem).strip("._") or "download"
    ascii_name = f"{ascii_stem}.{ext}" if dot else ascii_stem
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"


# ----------------------------------------------------------------------------
# 抖音解析
# ----------------------------------------------------------------------------
DOUYIN_ID_PATTERNS = [
    re.compile(r"/(?:video|note|slides)/(\d{8,25})"),
    re.compile(r"modal_id=(\d{8,25})"),
    re.compile(r"aweme_id=(\d{8,25})"),
]


async def parse_douyin(share_url: str) -> dict:
    headers = dict(DOUYIN_PAGE_HEADERS)
    if DOUYIN_COOKIE:
        headers["Cookie"] = DOUYIN_COOKIE

    async with new_client(headers) as client:
        final_url = share_url
        if "v.douyin.com" in share_url:
            final_url = await resolve_url(client, share_url)

        aweme_id = None
        for pattern in DOUYIN_ID_PATTERNS:
            m = pattern.search(final_url)
            if m:
                aweme_id = m.group(1)
                break
        if not aweme_id:
            raise HTTPException(status_code=400, detail="无法从链接中提取抖音作品 ID")

        page = await client.get(f"https://www.iesdouyin.com/share/video/{aweme_id}/")
        m = re.search(
            r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", page.text, re.S
        )
        if not m:
            raise HTTPException(
                status_code=502,
                detail="抖音页面结构变化或触发风控, 解析失败(可尝试配置 DOUYIN_COOKIE)",
            )

        router_data = json.loads(m.group(1))
        item = None
        for value in (router_data.get("loaderData") or {}).values():
            if isinstance(value, dict) and "videoInfoRes" in value:
                items = value["videoInfoRes"].get("item_list") or []
                if items:
                    item = items[0]
                    break
        if not item:
            raise HTTPException(status_code=404, detail="未找到该作品, 可能已被删除或设为私密")

    author = item.get("author") or {}
    stats = item.get("statistics") or {}
    video = item.get("video") or {}
    music = item.get("music") or {}
    images = item.get("images") or []

    # --- 无水印地址构造: 分享页给出的是带水印 playwm 接口, 替换为 play 即为无水印,
    #     再升级 ratio 参数得到高清版; 这两个接口都会 302 跳转到真实 CDN ---
    wm_url = first_url(video.get("play_addr"))
    nwm_url = wm_url.replace("playwm", "play") if wm_url else None
    nwm_url_hq = None
    if nwm_url:
        if "ratio=" in nwm_url:
            nwm_url_hq = re.sub(r"ratio=\w+", "ratio=1080p", nwm_url)
        else:
            sep = "&" if "?" in nwm_url else "?"
            nwm_url_hq = f"{nwm_url}{sep}ratio=1080p"

    image_urls = [u for u in (first_url(img) for img in images) if u]

    return {
        "code": 200,
        "platform": "douyin",
        "aweme_id": aweme_id,
        "type": "image" if image_urls else "video",
        "desc": item.get("desc") or "",
        "author": {
            "nickname": author.get("nickname") or "",
            "unique_id": author.get("unique_id")
            or author.get("short_id")
            or author.get("sec_uid", "")[:16],
            "avatar": first_url(author.get("avatar_medium"))
            or first_url(author.get("avatar_thumb"))
            or "",
        },
        "statistics": {
            "digg_count": stats.get("digg_count", 0),
            "comment_count": stats.get("comment_count", 0),
            "share_count": stats.get("share_count", 0),
        },
        "cover": first_url(video.get("cover")) or "",
        "video": {
            "nwm_video_url_HQ": nwm_url_hq,
            "nwm_video_url": nwm_url,
            "wm_video_url": wm_url,
            "duration": video.get("duration", 0),
        },
        "images": image_urls,
        "music": {
            "title": music.get("title") or "",
            "author": music.get("author") or "",
            "url": first_url(music.get("play_url")),
        },
    }


# ----------------------------------------------------------------------------
# TikTok 解析
# ----------------------------------------------------------------------------
async def parse_tiktok(share_url: str) -> dict:
    headers = dict(TIKTOK_PAGE_HEADERS)
    if TIKTOK_COOKIE:
        headers["Cookie"] = TIKTOK_COOKIE

    async with new_client(headers) as client:
        final_url = share_url
        if re.search(r"https?://(vm|vt)\.tiktok\.com", share_url):
            final_url = await resolve_url(client, share_url)

        m = re.search(r"/(?:video|photo)/(\d{8,25})", final_url)
        if not m:
            raise HTTPException(status_code=400, detail="无法从链接中提取 TikTok 作品 ID")
        aweme_id = m.group(1)

        page = await client.get(f"https://www.tiktok.com/@i/video/{aweme_id}")
        m = re.search(
            r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
            page.text,
            re.S,
        )
        if not m:
            raise HTTPException(
                status_code=502,
                detail="TikTok 页面结构变化或触发风控, 解析失败(可尝试配置 TIKTOK_COOKIE / 代理)",
            )

        universal = json.loads(m.group(1))
        try:
            item = universal["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"][
                "itemStruct"
            ]
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="未找到该作品, 可能已被删除、私密或地区受限"
            ) from exc

    author = item.get("author") or {}
    stats = item.get("stats") or {}
    video = item.get("video") or {}
    music = item.get("music") or {}
    image_post = item.get("imagePost") or {}

    # --- 高清无水印: bitrateInfo 中码率最高的一档; 普通无水印: playAddr;
    #     带水印: downloadAddr ---
    nwm_url_hq = None
    bitrates = video.get("bitrateInfo") or []
    if bitrates:
        best = max(bitrates, key=lambda b: b.get("Bitrate", 0))
        url_list = ((best.get("PlayAddr") or {}).get("UrlList")) or []
        if url_list:
            nwm_url_hq = url_list[-1]

    image_urls = []
    for img in image_post.get("images") or []:
        u = first_url(img.get("imageURL"))
        if u:
            image_urls.append(u)

    return {
        "code": 200,
        "platform": "tiktok",
        "aweme_id": aweme_id,
        "type": "image" if image_urls else "video",
        "desc": item.get("desc") or "",
        "author": {
            "nickname": author.get("nickname") or "",
            "unique_id": author.get("uniqueId") or "",
            "avatar": author.get("avatarMedium") or author.get("avatarThumb") or "",
        },
        "statistics": {
            "digg_count": stats.get("diggCount", 0),
            "comment_count": stats.get("commentCount", 0),
            "share_count": stats.get("shareCount", 0),
        },
        "cover": video.get("cover") or "",
        "video": {
            "nwm_video_url_HQ": nwm_url_hq,
            "nwm_video_url": video.get("playAddr"),
            "wm_video_url": video.get("downloadAddr"),
            "duration": video.get("duration", 0),
        },
        "images": image_urls,
        "music": {
            "title": music.get("title") or "",
            "author": music.get("authorName") or "",
            "url": music.get("playUrl"),
        },
    }


async def hybrid_parse(text: str) -> dict:
    url = extract_url(text)
    platform = detect_platform(url)
    if platform == "douyin":
        return await parse_douyin(url)
    return await parse_tiktok(url)


def pick_video_url(data: dict, watermark: bool = False) -> str:
    """智能回退: HQ 无水印 -> 普通无水印 -> 带水印, 保证下载接口不因 HQ 缺失而崩溃"""
    video = data.get("video") or {}
    if watermark:
        candidates = [
            video.get("wm_video_url"),
            video.get("nwm_video_url"),
            video.get("nwm_video_url_HQ"),
        ]
    else:
        candidates = [
            video.get("nwm_video_url_HQ"),
            video.get("nwm_video_url"),
            video.get("wm_video_url"),
        ]
    for url in candidates:
        if url:
            return url
    raise HTTPException(status_code=404, detail="该作品没有可用的视频地址")


def cdn_headers_for(platform: str) -> dict:
    headers = dict(DOUYIN_CDN_HEADERS if platform == "douyin" else TIKTOK_CDN_HEADERS)
    cookie = DOUYIN_COOKIE if platform == "douyin" else TIKTOK_COOKIE
    if cookie:
        headers["Cookie"] = cookie
    return headers


async def extract_audio_from_video(
    video_url: str, headers: dict, filename: str
) -> FileResponse:
    """兜底方案: 分享页未提供音乐直链时, 下载无水印视频并用 ffmpeg 抽取 MP3。
    临时目录通过 BackgroundTask 在响应发送完毕后清理。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(
            status_code=501,
            detail="该作品未提供音乐直链, 且服务器未安装 ffmpeg, 无法从视频中提取音频 "
            "(macOS: brew install ffmpeg)",
        )

    tmp_dir = tempfile.mkdtemp(prefix="aerofetch_")
    src_path = os.path.join(tmp_dir, "source.mp4")
    dst_path = os.path.join(tmp_dir, "audio.mp3")
    try:
        async with new_client(headers) as client:
            async with client.stream("GET", video_url) as resp:
                if resp.status_code >= 400:
                    raise HTTPException(
                        status_code=502,
                        detail=f"下载视频失败 (CDN 返回 {resp.status_code}), 无法提取音频",
                    )
                with open(src_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(CHUNK_SIZE):
                        f.write(chunk)

        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y", "-i", src_path,
            "-vn", "-acodec", "libmp3lame", "-q:a", "2",
            dst_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not os.path.exists(dst_path):
            detail = (stderr or b"").decode(errors="ignore").strip().splitlines()
            raise HTTPException(
                status_code=500,
                detail=f"ffmpeg 提取音频失败: {detail[-1] if detail else '未知错误'}",
            )
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return FileResponse(
        dst_path,
        media_type="audio/mpeg",
        headers={"Content-Disposition": content_disposition(filename)},
        background=BackgroundTask(shutil.rmtree, tmp_dir, ignore_errors=True),
    )


# ----------------------------------------------------------------------------
# FastAPI 应用
# ----------------------------------------------------------------------------
app = FastAPI(
    title="Douyin & TikTok 无水印解析下载 API",
    version="1.0.0",
    description="抖音 / TikTok 无水印高清媒体解析与流式代理下载",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "proxy": bool(PROXY)}


@app.get("/api/hybrid/video_data")
async def video_data(
    url: str = Query(..., description="抖音/TikTok 分享链接或包含链接的分享文本"),
) -> dict:
    return await hybrid_parse(url)


@app.get("/api/download")
async def download(
    url: str = Query(..., description="抖音/TikTok 分享链接或分享文本"),
    media: str = Query("video", pattern="^(video|audio|image|images)$"),
    index: int = Query(0, ge=0, description="单图下载时的图片序号"),
    watermark: bool = Query(False, description="是否下载带水印版本"),
):
    """
    后端代理流式下载:
    由服务端携带正确的 UA / Referer / Cookie 向 CDN 发起请求(浏览器直连会被
    CORS 与防盗链拦截), 跟随重定向取得文件流, 以 attachment 附件形式回传,
    强制触发浏览器本地下载。
    """
    data = await hybrid_parse(url)
    platform = data["platform"]
    headers = cdn_headers_for(platform)
    base_name = sanitize_filename(data["desc"], data["aweme_id"])

    # ---- 图集打包 ZIP ----
    if media == "images":
        images = data.get("images") or []
        if not images:
            raise HTTPException(status_code=404, detail="该作品不是图集或没有图片")
        async with new_client(headers) as client:
            results = await asyncio.gather(
                *(client.get(img) for img in images), return_exceptions=True
            )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            count = 0
            for i, resp in enumerate(results):
                if isinstance(resp, Exception) or resp.status_code >= 400:
                    continue
                ext = "jpg"
                ctype = resp.headers.get("content-type", "")
                if "png" in ctype:
                    ext = "png"
                elif "webp" in ctype:
                    ext = "webp"
                zf.writestr(f"{base_name}_{i + 1:02d}.{ext}", resp.content)
                count += 1
        if count == 0:
            raise HTTPException(status_code=502, detail="所有图片下载失败, 请稍后重试")
        buffer.seek(0)
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": content_disposition(f"{base_name}_图集.zip")},
        )

    # ---- 选取目标直链(含智能回退) ----
    if media == "audio":
        target = (data.get("music") or {}).get("url")
        filename = f"{base_name}_音乐.mp3"
        if not target:
            # 分享页未给音乐直链(抖音现状), 回退为 ffmpeg 从无水印视频流抽取
            video_url = pick_video_url(data)
            return await extract_audio_from_video(video_url, headers, filename)
        default_type = "audio/mpeg"
    elif media == "image":
        images = data.get("images") or []
        if index >= len(images):
            raise HTTPException(status_code=404, detail="图片序号超出范围")
        target = images[index]
        filename = f"{base_name}_{index + 1:02d}.jpg"
        default_type = "image/jpeg"
    else:
        target = pick_video_url(data, watermark=watermark)
        suffix = "带水印" if watermark else "高清无水印"
        filename = f"{base_name}_{suffix}.mp4"
        default_type = "video/mp4"

    # ---- 流式代理: 打开上游流后边收边发, 不在内存中缓存完整文件 ----
    client = new_client(headers)
    try:
        request = client.build_request("GET", target)
        upstream = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"上游请求失败: {exc}") from exc

    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(
            status_code=502,
            detail=f"CDN 返回 {upstream.status_code}, 链接可能已过期, 请重新解析",
        )

    async def stream_body():
        try:
            async for chunk in upstream.aiter_bytes(CHUNK_SIZE):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    response_headers = {"Content-Disposition": content_disposition(filename)}
    if upstream.headers.get("content-length"):
        response_headers["Content-Length"] = upstream.headers["content-length"]

    return StreamingResponse(
        stream_body(),
        media_type=upstream.headers.get("content-type", default_type),
        headers=response_headers,
    )


# 托管前端静态文件(index.html / style.css / app.js), 必须在 API 路由之后挂载
app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
