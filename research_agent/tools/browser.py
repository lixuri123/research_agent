"""Read public webpages and PDFs; optionally render JavaScript with Chromium."""
from __future__ import annotations

import hashlib
import io
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx

from research_agent.core.budget import remaining_timeout
from research_agent.core.config import get_settings
from research_agent.core.schemas import Document, RetrievalResult
from research_agent.tools import register_tool

MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024


def public_url(value: str) -> str:
    """Reject local/private URLs, including redirect and browser subresource targets."""
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('只支持不含登录凭证的 HTTP/HTTPS 公网链接')
    if parsed.port not in {None, 80, 443}:
        raise ValueError('浏览器只支持公网标准 HTTP/HTTPS 端口')
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('不允许读取本机、内网或保留地址')
    return value


def download_url(url: str) -> tuple[str, str, bytes]:
    timeout = remaining_timeout(get_settings().request_timeout)
    with httpx.Client(timeout=timeout, headers={'User-Agent': 'ResearchAgent/0.1 (read-only research browser)'}) as client:
        for _ in range(6):
            url = public_url(url)
            with client.stream('GET', url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers['location'])
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    remaining_timeout(get_settings().request_timeout)
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise ValueError('页面或 PDF 超过 15 MB 下载上限')
                    chunks.append(chunk)
                return str(response.url), response.headers.get('content-type', ''), b''.join(chunks)
    raise ValueError('网页重定向次数超过上限')


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.title, self.links = [], [], []
        self.hidden, self.in_title, self.anchor = 0, False, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.hidden += 1
        if tag == 'title':
            self.in_title = True
        if tag == 'a':
            self.anchor = [attrs.get('href', ''), []]
        if tag in {'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'article', 'section'}:
            self.text.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.hidden = max(0, self.hidden - 1)
        if tag == 'title':
            self.in_title = False
        if tag == 'a' and self.anchor:
            self.links.append((self.anchor[0], ''.join(self.anchor[1]).strip()))
            self.anchor = None

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if not self.hidden:
            self.text.append(data)
            if self.anchor:
                self.anchor[1].append(data)

    def content(self):
        return '\n'.join(line.strip() for line in ''.join(self.text).splitlines() if line.strip())


def _chromium_read(url: str) -> tuple[str, str, str, list[dict[str, str]]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError('Chromium 模式需 uv sync --extra browser，并执行 uv run playwright install chromium') from exc
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        try:
            context = browser.new_context(accept_downloads=False, service_workers='block')
            context.set_default_timeout(int(remaining_timeout(get_settings().request_timeout) * 1000))

            def intercept(route):
                try:
                    if route.request.method != 'GET':
                        route.abort()
                        return
                    public_url(route.request.url)
                    route.continue_()
                except Exception:
                    route.abort()

            context.route('**/*', intercept)
            page = context.new_page()
            response = page.goto(public_url(url), wait_until='domcontentloaded',
                timeout=int(remaining_timeout(get_settings().request_timeout) * 1000))
            if response and response.status >= 400:
                raise RuntimeError(f'浏览器访问失败：HTTP {response.status}')
            page.locator('body').wait_for(timeout=int(remaining_timeout(get_settings().request_timeout) * 1000))
            # Let JavaScript populate content without an unbounded network-idle wait.
            try:
                page.wait_for_load_state('networkidle', timeout=int(min(5, remaining_timeout(get_settings().request_timeout)) * 1000))
            except Exception:
                remaining_timeout(get_settings().request_timeout)
            links = page.locator('a[href]').evaluate_all("els => els.slice(0, 100).map(a => ({url: a.href, title: a.innerText.trim().slice(0, 200)}))")
            return public_url(page.url), page.title(), page.locator('body').inner_text(), links
        finally:
            browser.close()


@register_tool('browser_read', description='打开公网 URL，读取网页正文；PDF 链接自动提取文本。输入必须是 URL')
def browser_read(url: str) -> RetrievalResult:
    url = url.strip()
    final_url, media_type, body = download_url(url)
    links = []
    if 'pdf' in media_type or body.startswith(b'%PDF'):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(body))
        pages = []
        for page in reader.pages:
            remaining_timeout(get_settings().request_timeout)
            pages.append(page.extract_text() or '')
        text = '\n'.join(pages)
        title = str((reader.metadata or {}).get('/Title') or urlsplit(final_url).path.rsplit('/', 1)[-1])
        source = 'pdf'
    elif get_settings().browser_backend == 'chromium':
        final_url, title, text, links = _chromium_read(final_url)
        source = 'web'
    elif 'html' in media_type or not media_type:
        parser = PageText()
        parser.feed(body.decode('utf-8', errors='replace'))
        text, title, source = parser.content(), ''.join(parser.title).strip(), 'web'
        links = [{'url': urljoin(final_url, href), 'title': name[:200]} for href, name in parser.links[:100]]
    elif media_type.startswith('text/'):
        text, title, source = body.decode('utf-8', errors='replace'), final_url, 'web'
    else:
        return RetrievalResult(query=url, tool_used='browser_read', error=f'不支持的页面类型：{media_type}')
    if not text.strip():
        return RetrievalResult(query=url, tool_used='browser_read', error='页面无可读正文，可能需要登录、JavaScript 或 OCR')
    document = Document(id='url-' + hashlib.sha256(final_url.encode()).hexdigest()[:24],
        title=title, url=final_url, source=source, content=text,
        links=[link for link in links if urlsplit(link['url']).scheme in {'https', 'http'}])
    return RetrievalResult(query=url, tool_used='browser_read', documents=[document])


def public_web_search(query: str) -> list[Document]:
    """Keyless DuckDuckGo HTML search; results are discovery snippets, not full text."""
    url = 'https://html.duckduckgo.com/html/?' + urlencode({'q': query})
    _, _, body = download_url(url)
    parser = PageText()
    parser.feed(body.decode('utf-8', errors='replace'))
    documents, seen = [], set()
    for href, title in parser.links:
        link = urljoin('https://html.duckduckgo.com', href)
        parsed = urlsplit(link)
        link = parse_qs(parsed.query).get('uddg', [link])[0]
        destination = urlsplit(link)
        if not title or destination.scheme not in {'http', 'https'} or not destination.hostname:
            continue
        if destination.hostname.endswith('duckduckgo.com') or link in seen:
            continue
        seen.add(link)
        documents.append(Document(id='search-' + hashlib.sha256(link.encode()).hexdigest()[:24],
            title=title, url=link, abstract=title, source='web'))
        if len(documents) >= 8:
            break
    return documents
