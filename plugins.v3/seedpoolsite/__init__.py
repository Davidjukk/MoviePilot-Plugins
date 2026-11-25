# -*- coding: utf-8 -*-
"""
SeedPool（UNIT3D）站点搜索支持插件 —— MoviePilot v3。

解决的问题：
1. MoviePilot v3 内置站点索引里没有 seedpool.org，搜索时该站点根本不会被请求，
   表现为「永远 0 条结果且不报错」。本插件通过 get_module() 暴露插件级搜索源补上。
2. seedpool 的资源以英文发布名为主，直接用中文片名搜索必然 0 条。插件会把中文
   关键词经 TMDB 解析成 IMDb ID 与英文片名，优先用 ?imdbId= 做精确匹配，
   其次用英文片名搜索，从而解决中文搜不到的问题。

Cookie 默认自动从「站点管理」里 seedpool 的站点记录读取，站点侧更新即可。
"""

import os
import re
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from app.plugins import _PluginBase
from app.schemas.types import MediaType
from app.sdk.logging import logger
from app.sdk.media import TorrentInfo
from app.sdk.network import AsyncRequestUtils, RequestUtils

DEFAULT_DOMAIN = "https://seedpool.org/"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# seedpool 实际分类 ID（实测）：1=电影 2=电视剧 3=游戏 5=音乐 6=动漫 9=有声书
CATEGORY_MAP = {
    "1": MediaType.MOVIE,
    "2": MediaType.TV,
    "6": MediaType.TV,
    "5": MediaType.MUSIC,
}

_SIZE_UNITS = {
    "B": 1, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
    "PB": 1000 ** 5, "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3,
    "TIB": 1024 ** 4, "PIB": 1024 ** 5,
}

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def _clean(value: Optional[str]) -> str:
    """压缩空白字符。"""
    return " ".join(str(value or "").split())


def _has_cjk(text: Optional[str]) -> bool:
    """是否包含中日韩汉字。"""
    return bool(_CJK_RE.search(str(text or "")))


_SITE_PICKER_PATCHED = False


def _install_site_picker_patch() -> bool:
    """让「选择站点」弹窗能看到内置索引之外的自定义站点（如 seedpool.org）。

    MoviePilot v3 的 ``GET /api/v1/site/media/{media_type}`` 先用内置站点索引
    （``SitesHelper`` 的加密资源）算出「支持该媒体类型的站点 id / 域名」，再用它过滤
    已配置站点。seedpool.org 不在索引里，于是被整体过滤掉，前端「选择站点」弹窗里
    永远看不到它。

    这里在运行时给该接口的返回值做「追加」：先照常走 MP 自己的包装（统一响应外壳），
    再把「已启用但域名不在内置索引中」的站点塞进 data 里。实现方式是在 FastAPI 的
    ``run_endpoint_function`` 这一层做分流（uvicorn 用的是工厂里另建的 app 实例，
    改路由对象碰不到它），因此不需要修改 MP 的任何源文件，重启/更新都不会被还原。
    """
    global _SITE_PICKER_PATCHED
    if _SITE_PICKER_PATCHED:
        return True
    try:
        import fastapi.routing as _fastapi_routing

        from app.api.endpoints import site as _site_ep
        from app.api.response import COLLECTION_TOTAL_HEADER
        from app.application.site.sites import SitesHelper
        from app.domain import site as _site_rules

        source_module = _site_ep.__name__

        async def _extra_sites(query) -> list:
            """返回「已启用、但域名不在内置索引里」的站点。"""
            indexers = await SitesHelper().async_get_indexers() or []
            known = {
                _site_rules.extract_domain(item.get("domain"))
                for item in indexers if isinstance(item, dict)
            }
            extras = []
            for item in await query.list_ordered(is_active=True):
                domain = _site_rules.extract_domain(getattr(item, "domain", None))
                if domain and domain not in known:
                    extras.append(item)
            return extras

        _orig_run = getattr(_fastapi_routing, "run_endpoint_function", None)
        if not callable(_orig_run):
            logger.warning("SeedPool：当前 FastAPI 版本无 run_endpoint_function，补丁未挂载")
            return False

        async def _run_endpoint_function(*, dependant, values, is_coroutine):
            call = getattr(dependant, "call", None)
            is_target = (
                getattr(call, "__name__", "") == "read_sites_by_media_type"
                and getattr(call, "__module__", "") == source_module
            )
            if not is_target:
                return await _orig_run(dependant=dependant, values=values,
                                       is_coroutine=is_coroutine)
            # 先让 MP 自己的包装跑完，拿到统一响应外壳（Response(success, data)）
            result = await call(**values)
            try:
                payload = getattr(result, "data", None)
                if payload is None and isinstance(result, dict):
                    payload = result.get("data")
                if isinstance(payload, list):
                    have = {
                        _site_rules.extract_domain(getattr(item, "domain", None))
                        for item in payload
                    }
                    added = 0
                    for item in await _extra_sites(values.get("query")):
                        domain = _site_rules.extract_domain(
                            getattr(item, "domain", None)
                        )
                        if not domain or domain in have:
                            continue
                        payload.append(item)
                        have.add(domain)
                        added += 1
                    if added:
                        response = values.get("response")
                        headers = getattr(response, "headers", None)
                        if headers is not None:
                            headers[COLLECTION_TOTAL_HEADER] = str(len(payload))
            except Exception as err:  # pylint: disable=broad-except
                logger.warning(f"SeedPool：站点选择器补全失败：{err}")
            return result

        _fastapi_routing.run_endpoint_function = _run_endpoint_function
        _SITE_PICKER_PATCHED = True
        logger.info("SeedPool：站点选择器补丁已挂载，自定义站点可见")
        return True
    except Exception as err:  # pylint: disable=broad-except
        logger.warning(f"SeedPool：安装站点选择器补丁失败：{err}")
        return False


def _to_int(value: Optional[str]) -> int:
    """提取整数。"""
    match = re.search(r"\d[\d,]*", str(value or ""))
    if not match:
        return 0
    try:
        return int(match.group().replace(",", ""))
    except ValueError:
        return 0


def _to_size(value: Optional[str]) -> float:
    """把 "1.11 GiB" 换算成字节数。"""
    match = re.search(r"([\d.,]+)\s*([A-Za-z]{1,3})", _clean(value))
    if not match:
        return 0.0
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return 0.0
    return number * _SIZE_UNITS.get(match.group(2).upper(), 0)


class SeedPoolSite(_PluginBase):
    """为 SeedPool 补充搜索能力（含中文转英文）。"""

    plugin_name = "SeedPool 站点支持"
    plugin_desc = "为 MoviePilot 未内建的 SeedPool（UNIT3D）站点补充搜索能力；中文片名自动转 IMDb / 英文名检索。"
    plugin_icon = ""
    plugin_version = "1.3.2"
    plugin_author = "WorkBuddy"
    author_url = "https://github.com/jxxghp/MoviePilot"
    plugin_config_prefix = "seedpoolsite_"
    plugin_order = 21
    auth_level = 1

    _enabled: bool = False
    _cookie: str = ""
    _domain: str = DEFAULT_DOMAIN
    _site_name: str = "SeedPool"
    _ua: str = ""
    _proxy: bool = False
    _timeout: int = 30
    _max_results: int = 100
    _search_param: str = "auto"
    _auto_translate: bool = True

    def __init__(self) -> None:
        super().__init__()
        self._resolve_cache: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        self._plan_cache: Dict[str, List[Tuple[str, str]]] = {}
        self._cache_lock = threading.Lock()

    def init_plugin(self, config: Optional[dict] = None) -> None:
        """加载配置。"""
        self._enabled = False
        self._cookie = ""
        self._domain = DEFAULT_DOMAIN
        self._site_name = "SeedPool"
        self._ua = ""
        self._proxy = False
        self._timeout = 30
        self._max_results = 100
        self._search_param = "auto"
        self._auto_translate = True
        if config:
            self._enabled = bool(config.get("enabled"))
            self._cookie = str(config.get("cookie") or "").strip()
            self._domain = (str(config.get("domain") or DEFAULT_DOMAIN).strip()
                            or DEFAULT_DOMAIN)
            self._site_name = str(config.get("site_name") or "SeedPool").strip() or "SeedPool"
            self._ua = str(config.get("ua") or "").strip()
            self._proxy = bool(config.get("proxy"))
            self._auto_translate = bool(config.get("auto_translate", True))
            try:
                self._timeout = int(config.get("timeout") or 30)
            except (TypeError, ValueError):
                self._timeout = 30
            try:
                self._max_results = int(config.get("max_results") or 100)
            except (TypeError, ValueError):
                self._max_results = 100
            self._search_param = str(config.get("search_param") or "auto")
        if not self._domain.endswith("/"):
            self._domain += "/"
        # 让前端「选择站点」弹窗能看到索引外的站点（seedpool 本身）
        _install_site_picker_patch()

    # ------------------------------------------------------------------ 站点凭据

    def __host(self) -> str:
        """返回不带协议与结尾斜杠的主机名。"""
        return re.sub(r"^https?://", "", self._domain, flags=re.IGNORECASE).rstrip("/")

    def __site_row(self) -> Dict[str, Any]:
        """从 MoviePilot 数据库读取本站「站点管理」中的记录。"""
        try:
            from app.core.config import settings

            db_path = os.path.join(str(settings.CONFIG_PATH), "user.db")
            if not os.path.exists(db_path):
                return {}
            conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                rows = [dict(r) for r in conn.execute("select * from site")]
            finally:
                conn.close()
            host = self.__host().lower()
            for row in rows:
                domain = str(row.get("domain") or "").lower()
                if domain and (domain == host or host in domain or domain in host):
                    return row
            return {}
        except Exception as err:  # noqa: BLE001
            logger.debug(f"【SeedPool】读取站点记录失败：{err}")
            return {}

    def __credentials(self) -> Tuple[str, str, bool, Optional[int], Optional[str]]:
        """解析请求凭据，返回 (cookie, ua, proxy, site_id, downloader)。"""
        row = self.__site_row() if not self._cookie else {}
        cookie = self._cookie or str(row.get("cookie") or "").strip()
        ua = self._ua or str(row.get("ua") or "").strip() or DEFAULT_UA
        proxy = self._proxy or bool(row.get("proxy"))
        site_id = row.get("id") if row else None
        downloader = str(row.get("downloader") or "").strip() or None
        return cookie, ua, proxy, site_id, downloader

    # ------------------------------------------------------------------ 关键词规划

    def __tmdb_resolve_sync(self, keyword: str) -> Tuple[Optional[str], Optional[str]]:
        """同步查询 TMDB，返回 (imdb_id, 英文标题)。"""
        try:
            from app.modules.themoviedb.tmdbapi import TmdbApi  # type: ignore

            api = TmdbApi(language="en-US")
            records = api.search_multiis(keyword) or []
            for record in records[:3]:
                media_type = record.get("media_type")
                tmdb_id = record.get("id")
                if not tmdb_id:
                    continue
                info = api.get_info(media_type, tmdb_id) or {}
                imdb_id = str(info.get("imdb_id") or "").strip() or None
                english = str(info.get("title") or info.get("name")
                              or info.get("original_title") or info.get("original_name")
                              or "").strip() or None
                if imdb_id or english:
                    return imdb_id, english
        except Exception as err:  # noqa: BLE001
            logger.debug(f"【SeedPool】TMDB 解析失败：{err}")
        return None, None

    async def __tmdb_resolve_async(self, keyword: str) -> Tuple[Optional[str], Optional[str]]:
        """异步查询 TMDB，返回 (imdb_id, 英文标题)。"""
        try:
            from app.modules.themoviedb.tmdbapi import TmdbApi  # type: ignore

            api = TmdbApi(language="en-US")
            records = await api.async_search_multiis(keyword) or []
            for record in records[:3]:
                media_type = record.get("media_type")
                tmdb_id = record.get("id")
                if not tmdb_id:
                    continue
                info = await api.async_get_info(media_type, tmdb_id) or {}
                imdb_id = str(info.get("imdb_id") or "").strip() or None
                english = str(info.get("title") or info.get("name")
                              or info.get("original_title") or info.get("original_name")
                              or "").strip() or None
                if imdb_id or english:
                    return imdb_id, english
        except Exception as err:  # noqa: BLE001
            logger.debug(f"【SeedPool】TMDB 解析失败：{err}")
        return None, None

    def __cached_resolve(self, keyword: str) -> Tuple[Optional[str], Optional[str]]:
        """带缓存的同步解析。"""
        with self._cache_lock:
            if keyword in self._resolve_cache:
                return self._resolve_cache[keyword]
        resolved = self.__tmdb_resolve_sync(keyword)
        with self._cache_lock:
            if len(self._resolve_cache) > 500:
                self._resolve_cache.clear()
            self._resolve_cache[keyword] = resolved
        return resolved

    async def __cached_resolve_async(self, keyword: str) -> Tuple[Optional[str], Optional[str]]:
        """带缓存的异步解析。"""
        with self._cache_lock:
            if keyword in self._resolve_cache:
                return self._resolve_cache[keyword]
        resolved = await self.__tmdb_resolve_async(keyword)
        with self._cache_lock:
            if len(self._resolve_cache) > 500:
                self._resolve_cache.clear()
            self._resolve_cache[keyword] = resolved
        return resolved

    @staticmethod
    def __title_matches(keyword: str, english: str) -> bool:
        """判断 TMDB 解析结果是否与关键词足够接近，避免把别的片子当成目标。"""
        def tokens(text: str) -> set:
            norm = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", str(text or "").lower())
            return {token for token in norm.split() if len(token) > 1}

        left, right = tokens(keyword), tokens(english)
        if not left or not right:
            return False
        return len(left & right) >= max(1, min(len(left), len(right)) * 0.6)

    def __plan(self, keyword: str, imdb_id: Optional[str], english: Optional[str]) -> List[Tuple[str, str]]:
        """生成按优先级排序的 (参数名, 值) 尝试列表。

        seedpool 的 name 参数是站内模糊匹配，英文关键词可能命中无关资源，
        因此只要能解析出 IMDb ID 就优先用 imdbId 精确匹配。
        """
        fixed = (self._search_param or "auto").strip()
        if fixed in ("name", "search", "imdbId"):
            if fixed == "imdbId":
                return [("imdbId", imdb_id)] if imdb_id else []
            return [(fixed, keyword)]

        attempts: List[Tuple[str, str]] = []
        is_cjk = _has_cjk(keyword)
        # 非中文关键词：TMDB 结果必须与关键词高度相似才敢用 ID 精确检索
        trust_imdb = bool(imdb_id) and (is_cjk or not english
                                        or self.__title_matches(keyword, english))
        if trust_imdb:
            attempts.append(("imdbId", imdb_id))
        if is_cjk:
            if english:
                attempts.append(("name", english))
            attempts.append(("name", keyword))
        else:
            attempts.append(("name", keyword))
            if english and english.lower() != keyword.lower():
                attempts.append(("name", english))

        seen = set()
        ordered: List[Tuple[str, str]] = []
        for item in attempts:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    # ------------------------------------------------------------------ 搜索源

    def get_module(self) -> Optional[Dict[str, Any]]:
        """向外暴露插件搜索源。"""
        if not self._enabled:
            return {}
        return {
            "search_torrents": self.search_torrents,
            "async_search_torrents": self.async_search_torrents,
        }

    def search_torrents(
        self,
        site: Optional[dict] = None,
        keyword: Optional[str] = None,
        mtype: Optional[MediaType] = None,
        page: Optional[int] = 0,
        **_: Any,
    ) -> List[TorrentInfo]:
        """同步搜索入口。"""
        del site, mtype
        keyword = str(keyword or "").strip()
        if not self._enabled or not keyword:
            return []
        cookie, ua, proxy, site_id, downloader = self.__credentials()
        if not cookie:
            logger.warning("【SeedPool】站点与插件都没有 Cookie，跳过搜索")
            return []

        attempts = self.__plan(keyword, None, None)
        if self._auto_translate:
            imdb_id, english = self.__cached_resolve(keyword)
            attempts = self.__plan(keyword, imdb_id, english)
            if imdb_id or english:
                logger.info(f"【SeedPool】关键词「{keyword}」解析为 imdb={imdb_id} en={english}")

        for param, value in attempts:
            html_text = self.__fetch(self.__build_url(value, page, param), cookie, ua, proxy)
            if not html_text:
                continue
            if self.__looks_like_login(html_text):
                logger.warning("【SeedPool】返回登录页，站点 Cookie 已失效，请到「站点管理」更新")
                return []
            rows = self.__parse(html_text, site_id, downloader, cookie, ua, proxy)
            if rows:
                logger.info(f"【SeedPool】{keyword} 命中 {len(rows)} 条（{param}={value}）")
                return rows
        logger.info(f"【SeedPool】{keyword} 无结果（已尝试 {len(attempts)} 种方式）")
        return []

    async def async_search_torrents(
        self,
        site: Optional[dict] = None,
        keyword: Optional[str] = None,
        mtype: Optional[MediaType] = None,
        page: Optional[int] = 0,
        **_: Any,
    ) -> List[TorrentInfo]:
        """异步搜索入口。"""
        del site, mtype
        keyword = str(keyword or "").strip()
        if not self._enabled or not keyword:
            return []
        cookie, ua, proxy, site_id, downloader = self.__credentials()
        if not cookie:
            logger.warning("【SeedPool】站点与插件都没有 Cookie，跳过搜索")
            return []

        attempts = self.__plan(keyword, None, None)
        if self._auto_translate:
            imdb_id, english = await self.__cached_resolve_async(keyword)
            attempts = self.__plan(keyword, imdb_id, english)
            if imdb_id or english:
                logger.info(f"【SeedPool】关键词「{keyword}」解析为 imdb={imdb_id} en={english}")

        for param, value in attempts:
            html_text = await self.__fetch_async(self.__build_url(value, page, param), cookie, ua, proxy)
            if not html_text:
                continue
            if self.__looks_like_login(html_text):
                logger.warning("【SeedPool】返回登录页，站点 Cookie 已失效，请到「站点管理」更新")
                return []
            rows = self.__parse(html_text, site_id, downloader, cookie, ua, proxy)
            if rows:
                logger.info(f"【SeedPool】{keyword} 命中 {len(rows)} 条（{param}={value}）")
                return rows
        logger.info(f"【SeedPool】{keyword} 无结果（已尝试 {len(attempts)} 种方式）")
        return []

    def __build_url(self, value: str, page: int, param: str) -> str:
        """拼装搜索地址，UNIT3D 页码从 1 开始。"""
        return f"{self._domain}torrents?{param}={quote(str(value), safe='')}&page={int(page) + 1}"

    def __proxies(self, enabled: bool) -> Optional[dict]:
        """按需返回系统代理。"""
        if not enabled:
            return None
        try:
            from app.runtime.settings import get_runtime_setting  # type: ignore

            return get_runtime_setting("PROXY") or None
        except Exception:  # noqa: BLE001
            return None

    def __headers(self) -> Dict[str, str]:
        """请求头。"""
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": f"{self._domain}torrents",
        }

    def __fetch(self, url: str, cookie: str, ua: str, proxy: bool) -> str:
        """同步抓取。"""
        try:
            response = RequestUtils(
                ua=ua, cookies=cookie, timeout=self._timeout,
                proxies=self.__proxies(proxy), verify=False, headers=self.__headers(),
            ).get_res(url, allow_redirects=True)
        except Exception as err:  # noqa: BLE001
            logger.error(f"【SeedPool】请求失败：{err}")
            return ""
        return self.__response_text(response)

    async def __fetch_async(self, url: str, cookie: str, ua: str, proxy: bool) -> str:
        """异步抓取。"""
        try:
            response = await AsyncRequestUtils(
                ua=ua, cookies=cookie, timeout=self._timeout,
                proxies=self.__proxies(proxy), verify=False, headers=self.__headers(),
            ).get_res(url, allow_redirects=True)
        except Exception as err:  # noqa: BLE001
            logger.error(f"【SeedPool】请求失败：{err}")
            return ""
        return self.__response_text(response)

    @staticmethod
    def __response_text(response: Any) -> str:
        """从响应对象取正文。"""
        if response is None:
            return ""
        text = getattr(response, "text", None)
        if isinstance(text, str) and text:
            return text
        content = getattr(response, "content", None)
        if isinstance(content, (bytes, bytearray)):
            return bytes(content).decode("utf-8", "ignore")
        return ""

    @staticmethod
    def __looks_like_login(html_text: str) -> bool:
        """判断是否被踢回登录页。"""
        if "torrent-search--list__row" in html_text:
            return False
        head = html_text[:6000].lower()
        return "/login" in head or 'name="password"' in head or 'type="password"' in head

    # ------------------------------------------------------------------ 解析

    def __parse(
        self,
        html_text: str,
        site_id: Optional[int],
        downloader: Optional[str],
        cookie: str,
        ua: str,
        proxy: bool,
    ) -> List[TorrentInfo]:
        """解析搜索结果。"""
        try:
            from lxml import etree
        except ImportError:  # pragma: no cover
            logger.error("【SeedPool】缺少 lxml，无法解析页面")
            return []
        try:
            doc = etree.HTML(html_text, parser=etree.HTMLParser(encoding="utf-8"))
        except Exception as err:  # noqa: BLE001
            logger.error(f"【SeedPool】页面解析失败：{err}")
            return []
        if doc is None:
            return []

        results: List[TorrentInfo] = []
        seen = set()
        for row in doc.xpath('//tr[contains(@class, "torrent-search--list__row")]'):
            if len(results) >= self._max_results:
                break
            try:
                item = self.__parse_row(row, site_id, downloader, cookie, ua, proxy)
            except Exception as err:  # noqa: BLE001
                logger.debug(f"【SeedPool】单行解析异常：{err}")
                continue
            if item is None or item.enclosure in seen:
                continue
            seen.add(item.enclosure)
            results.append(item)
        return results

    def __parse_row(
        self,
        row: Any,
        site_id: Optional[int],
        downloader: Optional[str],
        cookie: str,
        ua: str,
        proxy: bool,
    ) -> Optional[TorrentInfo]:
        """把单行 HTML 转成 TorrentInfo。"""
        title = _clean("".join(row.xpath(
            './/a[contains(@class, "torrent-search--list__name")]//text()')))
        if not title:
            return None

        detail = (row.xpath('.//a[contains(@class, "torrent-search--list__name")]/@href') or [""])[0]
        torrent_id = (row.xpath("@data-torrent-id") or [""])[0]
        download = (row.xpath('.//a[contains(@class, "torrent-search--list__file")]/@href') or [""])[0]
        if not download and torrent_id:
            download = f"{self._domain}torrents/download/{torrent_id}"
        if not download:
            return None

        size_text = _clean("".join(row.xpath('.//td[contains(@class, "torrent-search--list__size")]//text()')))
        seeders = _to_int("".join(row.xpath('.//td[contains(@class, "torrent-search--list__seeders")]//text()')))
        leechers = _to_int("".join(row.xpath('.//td[contains(@class, "torrent-search--list__leechers")]//text()')))
        grabs = _to_int("".join(row.xpath('.//td[contains(@class, "torrent-search--list__completed")]//text()')))

        pubdate = ((row.xpath('.//td[contains(@class, "torrent-search--list__age")]//time/@datetime') or [""])[0] or None)
        elapsed = (_clean("".join(row.xpath('.//td[contains(@class, "torrent-search--list__age")]//time//text()'))) or None)

        freeleech = bool(row.xpath('.//i[contains(@class, "torrent-icons__freeleech")]'))
        doubleup = bool(row.xpath('.//i[contains(@class, "torrent-icons__double-upload")]'))

        resolution = _clean("".join(row.xpath('.//span[contains(@class, "torrent-search--list__resolution")]//text()')))
        rtype = _clean("".join(row.xpath('.//span[contains(@class, "torrent-search--list__type")]//text()')))

        labels: List[str] = []
        if resolution:
            labels.append(resolution)
        if rtype:
            labels.append(rtype)
        if freeleech:
            labels.append("Free")
        if doubleup:
            labels.append("2x")

        category_id = str((row.xpath("@data-category-id") or [""])[0])
        media_type = CATEGORY_MAP.get(category_id)
        description = " ".join([p for p in (resolution, rtype) if p] + (["免费"] if freeleech else []))

        return TorrentInfo(
            site=site_id,
            site_name=self._site_name,
            site_cookie=cookie,
            site_ua=ua,
            site_proxy=proxy,
            site_order=0,
            site_downloader=downloader,
            title=title,
            description=description or None,
            enclosure=download,
            page_url=detail or None,
            size=_to_size(size_text),
            seeders=seeders,
            peers=leechers,
            grabs=grabs,
            pubdate=pubdate,
            date_elapsed=elapsed,
            downloadvolumefactor=0.0 if freeleech else 1.0,
            uploadvolumefactor=2.0 if doubleup else 1.0,
            category=media_type.value if media_type else None,
            labels=labels,
        )

    # ------------------------------------------------------------------ 插件页面

    def get_state(self) -> bool:
        """插件启用状态。"""
        return self._enabled

    @staticmethod
    def get_render_mode() -> Tuple[str, Optional[str]]:
        """使用 Vuetify 渲染配置页。"""
        return "vuetify", None

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """拼装配置页。"""
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{"component": "VSwitch",
                                             "props": {"model": "enabled", "label": "启用插件"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{"component": "VSwitch",
                                             "props": {"model": "auto_translate",
                                                       "label": "中文自动转 IMDb/英文"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{"component": "VSwitch",
                                             "props": {"model": "proxy", "label": "使用系统代理"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{"component": "VTextField",
                                             "props": {"model": "site_name", "label": "站点显示名称",
                                                       "placeholder": "SeedPool"}}],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [{
                            "component": "VCol",
                            "props": {"cols": 12},
                            "content": [{
                                "component": "VTextarea",
                                "props": {
                                    "model": "cookie",
                                    "label": "站点 Cookie（可选，留空自动读取站点管理）",
                                    "rows": 3,
                                    "persistent-hint": True,
                                    "hint": "留空即自动复用「站点管理 → SeedPool」里的 Cookie，通常无需填写",
                                },
                            }],
                        }],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{"component": "VTextField",
                                             "props": {"model": "domain", "label": "站点地址",
                                                       "placeholder": DEFAULT_DOMAIN}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{"component": "VSelect",
                                             "props": {"model": "search_param", "label": "搜索参数",
                                                       "items": [
                                                           {"title": "自动（推荐）", "value": "auto"},
                                                           {"title": "name", "value": "name"},
                                                           {"title": "imdbId", "value": "imdbId"},
                                                           {"title": "search", "value": "search"},
                                                       ]}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{"component": "VTextField",
                                             "props": {"model": "timeout", "label": "请求超时（秒）",
                                                       "type": "number"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{"component": "VTextField",
                                             "props": {"model": "max_results", "label": "单次返回上限",
                                                       "type": "number"}}],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [{
                            "component": "VCol",
                            "props": {"cols": 12},
                            "content": [{"component": "VTextField",
                                         "props": {"model": "ua", "label": "User-Agent（可选）",
                                                   "placeholder": "留空自动复用站点管理里的 UA"}}],
                        }],
                    },
                ],
            }
        ], {
            "enabled": False,
            "auto_translate": True,
            "cookie": "",
            "domain": DEFAULT_DOMAIN,
            "site_name": "SeedPool",
            "ua": "",
            "proxy": False,
            "timeout": 30,
            "max_results": 100,
            "search_param": "auto",
        }

    def get_page(self) -> Optional[List[dict]]:
        """无需详情页。"""
        return None

    def get_api(self) -> List[Dict[str, Any]]:
        """无需注册 API。"""
        return []

    def stop_service(self) -> None:
        """无后台服务。"""
        pass
