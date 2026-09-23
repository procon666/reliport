"""检索层：搜索（Searcher）+ 抓取（Fetcher）。
- Searcher：获取候选 URL（内置 DuckDuckGo 作为默认实现，可切 Tavily）
- Fetcher：用 trafilatura 提取正文；失败时降级返回搜索摘要
"""
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = ""


@dataclass
class SourceDoc:
    """一次抓取/检索得到的文档。"""
    url: str
    title: str
    content: str          # 正文（抓取）或摘要（降级）
    snippet: str = ""     # 搜索摘要
    from_fetch: bool = True
    fetched_at: str = ""
    credibility: float = 0.5  # 0~1，由 Analyzer 更新
    extra: dict = field(default_factory=dict)


class Fetcher:
    """用 trafilatura 抓取正文。"""

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }

    def fetch(self, url: str, snippet: str = "", timeout: int = 20) -> Optional[SourceDoc]:
        try:
            resp = requests.get(url, headers=self.HEADERS, timeout=timeout)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logger.warning("抓取失败 %s: %s", url, e)
            # 降级：仅用搜索摘要
            return self._degraded(url, snippet)

        try:
            import trafilatura
            content = trafilatura.extract(html, include_comments=False,
                                          include_tables=True, favor_precision=True)
        except Exception as e:
            logger.warning("正文提取失败 %s: %s", url, e)
            content = None

        if not content:
            # 提取不到正文，降级用摘要
            return self._degraded(url, snippet)

        title = ""
        try:
            title = trafilatura.extract_metadata(html).title or ""
        except Exception:
            pass

        return SourceDoc(
            url=url,
            title=title or self._guess_title(url),
            content=content.strip(),
            snippet=snippet,
            from_fetch=True,
            fetched_at=time.strftime("%Y-%m-%d %H:%M"),
        )

    def _degraded(self, url: str, snippet: str) -> Optional[SourceDoc]:
        if not snippet:
            return None
        return SourceDoc(
            url=url,
            title=self._guess_title(url),
            content=snippet,
            snippet=snippet,
            from_fetch=False,
            fetched_at=time.strftime("%Y-%m-%d %H:%M"),
            credibility=0.3,
        )

    @staticmethod
    def _guess_title(url: str) -> str:
        path = urlparse(url).path.strip("/")
        last = path.split("/")[-1].replace("-", " ").replace("_", " ")
        return last[:60] or url


class Searcher:
    """获取候选 URL。默认使用 DuckDuckGo（无需 Key），可切换 Tavily。"""

    def __init__(self, provider: str = "builtin"):
        self.provider = provider
        self.fetcher = Fetcher()

    def search(self, query: str, max_results: int = 8,
               include_domains=None) -> List[SearchResult]:
        if self.provider == "tavily":
            return self._search_tavily(query, max_results, include_domains)
        # 默认走 Bing（更稳），失败时降级 DDG
        res = self._search_bing(query, max_results)
        if not res:
            res = self._search_ddg(query, max_results)
        return res

    def _search_bing(self, query: str, max_results: int = 8) -> List[SearchResult]:
        """Bing 网页搜索（无需 Key，作为默认实现）。"""
        try:
            resp = requests.get(
                "https://www.bing.com/search",
                params={"q": query, "setlang": "zh-CN", "count": max_results},
                headers=Fetcher.HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Bing 搜索失败: %s", e)
            return []

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for li in soup.select("li.b_algo")[:max_results]:
            h2 = li.select_one("h2 a")
            if not h2:
                continue
            snip = li.select_one("p")
            results.append(SearchResult(
                title=h2.get_text(strip=True),
                url=h2.get("href", ""),
                snippet=snip.get_text(strip=True) if snip else "",
                source="bing",
            ))
        return results

    def _search_ddg(self, query: str, max_results: int = 8) -> List[SearchResult]:
        """DuckDuckGo HTML 版搜索（无需 Key，作为内置默认）。"""
        url = "https://html.duckduckgo.com/html/"
        try:
            resp = requests.post(
                url,
                data={"q": query},
                headers=Fetcher.HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning("DDG 搜索失败: %s", e)
            return []

        results = []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.select(".result__body")[:max_results]:
            a = link.select_one(".result__a")
            snip = link.select_one(".result__snippet")
            if not a:
                continue
            href = a.get("href", "")
            results.append(SearchResult(
                title=a.get_text(strip=True),
                url=href,
                snippet=snip.get_text(strip=True) if snip else "",
                source="duckduckgo",
            ))
        return results

    def _search_tavily(self, query: str, max_results: int = 8,
                       include_domains=None) -> List[SearchResult]:
        from .config import settings
        key = settings.tavily_api_key
        if not key:
            logger.warning("未配置 TAVILY_API_KEY，退回内置搜索")
            return self._search_ddg(query, max_results)
        payload = {"api_key": key, "query": query,
                   "max_results": max_results, "include_answer": False}
        # 权威源定向：把检索限定在指定域名内，抓"一手稿"而非转载稿
        if include_domains:
            payload["include_domains"] = list(include_domains)[:20]
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json=payload,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("Tavily 搜索失败: %s", e)
            return []
        out = []
        for r in data.get("results", [])[:max_results]:
            out.append(SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
                source="tavily",
            ))
        return out


class SearchPipeline:
    """组合：改写查询 → 搜索 → 抓取正文。"""

    def __init__(self, searcher: Optional[Searcher] = None):
        self.searcher = searcher or Searcher()
        self.fetcher = self.searcher.fetcher

    # 权威信号词：Bing 检索质量低时用于提升信息密度（Tavily 质量高，无需）
    SIGNAL_WORDS = ["报告", "分析", "数据", "研究", "市场", "统计", "预测", "调查", "白皮书"]

    def collect(self, query: str, max_results: int = 6, min_content: int = 200) -> List[SourceDoc]:
        # 根据 provider 决定是否做信号词增强
        provider = self.searcher.provider
        if provider == "tavily":
            results = self.searcher.search(query, max_results)
        else:
            results = self.searcher.search(query, max_results)
            base = query
            for word in self.SIGNAL_WORDS:
                results += self.searcher.search(f"{base} {word}", max_results // 2)
        # 去重
        seen, uniq = set(), []
        for r in results:
            key = r.url.split("#")[0]
            if key not in seen:
                seen.add(key)
                uniq.append(r)

        docs = []
        for r in uniq[:max_results * 3]:
            doc = self.fetcher.fetch(r.url, snippet=r.snippet)
            if doc:
                docs.append(doc)
            time.sleep(0.4)  # 礼貌抓取
        return docs
