import requests
import re
import json
import os
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from typing import Dict, List, Tuple, Optional, Any
import logging

requests.packages.urllib3.disable_warnings()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 预编译正则表达式，提高性能
PATTERNS = {
    'storeHouse': re.compile(r"storeHouse", re.M | re.I),
    'urls': re.compile(r"urls", re.M | re.I),
    'sites': re.compile(r"sites", re.M | re.I)
}

# 请求配置
REQUEST_CONFIG = {
    # (连接超时, 读取超时)。连接超时设短一些，可以更快淘汰解析不出结果的主机，避免长时间空等
    'timeout': (6, 15),
    'headers': {"User-Agent": "okhttp/4.1.0"},
    'verify': False
}

MAX_WORKERS = 16  # 最大并发线程数，用于数据源解析和线路验证
MIN_CONTENT_KB =20  # 最小内容大小（KB），小于此值视为无效线路
SLOW_RESPONSE_SECONDS = 15  # 慢速线路的响应时间阈值（秒），超过即淘汰
SLOW_RESPONSE_MIN_KB = 20  # 慢速线路的内容大小阈值（KB），低于即淘汰
# sites 质量阈值：sites 中「同时包含 key 与 name」的站点条目数（有效站点数），低于此值认为线路数据不完整
MIN_SITES_KEYS = 20
MAX_SOURCE_DEPTH = 2  # 多仓展开最大层级，防止递归过深

# ==================== Gitee 违规信息审查 ====================
# 在生成 JSON 前，对每条线路的名称、URL 及其他字段进行违规信息审查，
# 命中以下任一关键词的线路会被删除，避免推送到 Gitee 时触发平台内容审查导致仓库受限。
# 匹配规则：
#   1. 中文关键词按子串匹配（如「色情」）；
#   2. 英文/数字关键词按独立单词匹配，避免 av 命中 avatar、sex 命中 essex 之类的误判。
# 可按需自行增删关键词。
GITEE_VIOLATION_KEYWORDS = [
    # # 色情、低俗
    # '色情', '成人', '里番', '福利', '18禁', '十八禁', '三级', '情色', '裸', '主播',
    # '黄片', '艳照', '啪啪', '约炮', '一夜情', '免脱',
    # 'porn', 'sex', 'xxx', 'hentai', 'r18', 'adult',
    # # 赌博、博彩
    # '赌博', '博彩', '赌场', '彩票', '六合彩', '棋牌', '押注',
    # 'bet', 'casino', 'gambling',
    # # 毒品、违禁品
    # '毒品', '大麻', '冰毒', '海洛因', '枪支', '军火', '弹药',
    # # 政治敏感、暴力、恐怖
    # '政治', '反动', '暴力', '恐怖', '血腥',
    # # 其他违规
    # '翻墙', '代刷', '洗钱', '私服',
]

# 备用数据源入口。每个入口可以是 {"urls": [...]} 单仓列表，也可以是 {"storeHouse": [...]} 多仓列表。
# FALLBACK_URLS = [
#     'https://gh-proxy.com/https://raw.githubusercontent.com/hd9211/Tvbox1/main/优质.json',
#     'https://gh-proxy.com/https://raw.githubusercontent.com/hd9211/Tvbox1/main/cr.json',
# ]
FALLBACK_URLS = [
    'https://play.iptv365.org/tvbox.txt',
    'https://gh-proxy.com/https://raw.githubusercontent.com/hd9211/Tvbox1/main/gaotianliuyun.json',
    'https://ghfast.top/https://raw.githubusercontent.com/chendi0207/my-TVBOX/refs/heads/main/本地仓.txt',
    'https://gh-proxy.com/https://raw.githubusercontent.com/lemonguo121/BoxRes/main/Myuse/lemon.json',
    'https://gitee.com/jiangnandao/tvboxshare/raw/master/TVLineTest2.json',
    'https://gh-proxy.com/https://raw.githubusercontent.com/hd9211/Tvbox1/refs/heads/main/%E4%BC%98%E8%B4%A8.json',
    'https://gh-proxy.com/https://github.com/hd9211/Tvbox1/blob/main/%E5%B8%B8%E7%94%A8.json',
    'https://gh-proxy.com/',
    'https://gh-proxy.com/',
    'https://gh-proxy.com/',
    'http://qxyc.cc/自用测试',
    'http://xhztv.top/DC.txt',
    'http://ztha.top/TVBox/GYCK.json',
    'http://xhztv.top/dc',
    'http://xmbjm.fh4u.org/dc.txt',
    '',
    '',
    'https://gh-proxy.com/https://raw.githubusercontent.com/xmbjm/svip/main/dc.json'
]

# 固定线路列表（将添加到结果数组开头）
FIXED_LINES = [
    {"url": "https://gh-proxy.com/https://raw.githubusercontent.com/chendi0207/my-TVBOX/main/tvboxqq/饭太硬/api.json", 
    "name": "饭太硬"},
    {"url": "https://gh-proxy.comp/https://raw.githubusercontent.com/chendi0207/my-TVBOX/main/tvboxqq/OK/api.json",
    "name": "OK"},
    {"url": "https://gh-proxy.com/https://raw.githubusercontent.com/chendi0207/my-TVBOX/main/tvboxqq/摸鱼儿/api.json",
    "name": "摸鱼儿"}
]


class TVBoxValidator:
    """TVBox 线路验证器"""
    
    def __init__(self, filename: str = 'tvbox.json'):
        self.filename = filename
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=MAX_WORKERS * 2, pool_maxsize=MAX_WORKERS * 2)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        # 预编译违规关键词正则，避免逐条审查时重复编译
        self.violation_patterns = self.build_violation_patterns(GITEE_VIOLATION_KEYWORDS)
    
    def get_response(self, url: str, use_tvbox_headers: bool = False) -> requests.Response:
        """统一请求入口，确保超时、UA 和证书校验配置一致。"""
        headers = REQUEST_CONFIG['headers'] if use_tvbox_headers else None
        return self.session.get(
            url,
            headers=headers,
            timeout=REQUEST_CONFIG['timeout'],
            verify=REQUEST_CONFIG['verify']
        )

    @staticmethod
    def decode_response_text(response: requests.Response) -> str:
        """
        解码响应文本。
        优先按 UTF-8 解码：response.text 在响应头缺少 charset 时会对整段内容做字符集探测，
        大响应下非常慢；而 TVBox 配置基本都是 UTF-8。仅当 UTF-8 解码失败时才回退。
        """
        try:
            return response.content.decode('utf-8')
        except UnicodeDecodeError:
            return response.text

    def sanitize_json_text(self, text: str) -> str:
        """删除 JSON 文本中的 JavaScript 注释，兼容 UTF-8 BOM。"""
        text = text.lstrip('\ufeff')
        # 快速路径：不含注释标记时无需逐字符扫描
        if '/*' not in text and '//' not in text:
            return text

        result = []
        in_string = False
        escaped = False
        comment_type = None
        i = 0

        while i < len(text):
            c = text[i]
            if comment_type == 'line':
                if c == '\n':
                    comment_type = None
                    result.append(c)
            elif comment_type == 'block':
                if c == '*' and i + 1 < len(text) and text[i + 1] == '/':
                    comment_type = None
                    i += 1
            else:
                if in_string:
                    if escaped:
                        result.append(c)
                        escaped = False
                    elif c == '\\':
                        result.append(c)
                        escaped = True
                    elif c == '"':
                        in_string = False
                        result.append(c)
                    else:
                        result.append(c)
                else:
                    if c == '"':
                        in_string = True
                        result.append(c)
                    elif c == '/' and i + 1 < len(text) and text[i + 1] == '/':
                        comment_type = 'line'
                        i += 1
                    elif c == '/' and i + 1 < len(text) and text[i + 1] == '*':
                        comment_type = 'block'
                        i += 1
                    else:
                        result.append(c)
            i += 1

        return ''.join(result)

    def parse_json_text(self, response_text: str, url: str = '') -> Optional[Any]:
        """解析 JSON，兼容 UTF-8 BOM 和 JS 注释。"""
        text = response_text.lstrip('\ufeff')
        # 快速路径：标准 JSON 直接解析，避免「逐字符清洗注释」带来的性能损耗
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        try:
            return json.loads(self.sanitize_json_text(text))
        except json.JSONDecodeError as e:
            location = f"：{url}" if url else ""
            logger.warning(f"JSON 解析失败{location}，line {e.lineno}, column {e.colno}, char {e.pos}")
            return None
    
    TARGET_KEYS = ('sites', 'urls', 'storeHouse')

    def scan_json_data(self, data: Any) -> Tuple[set, int, int]:
        """
        单次遍历 JSON，同时完成原先需要多次全量遍历的工作：
          1. 收集命中的关键 key（sites / urls / storeHouse）；
          2. 统计 sites 中「同时包含 key 与 name 的站点条目数」（线路质量主指标，即有效站点数），
             以及 sites 内所有键值对总数 Σlen(站点对象)（排序次指标）。
        使用显式栈而非递归，避免深层嵌套时的函数调用开销。
        返回：(命中的关键 key 集合, 含 key+name 的站点条目数, sites 键值对总数)
        """
        found_keys: set = set()
        site_pair_count = 0
        sites_keys_total = 0

        # 栈元素：(节点, 是否参与 sites 统计)。sites 数组内部仍需检测关键 key，但不再重复统计
        stack: List[Tuple[Any, bool]] = [(data, True)]
        while stack:
            node, count_enabled = stack.pop()

            if isinstance(node, dict):
                for key in self.TARGET_KEYS:
                    if key in node:
                        found_keys.add(key)

                for key, value in node.items():
                    if key == 'sites' and count_enabled and isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict):
                                sites_keys_total += len(item)
                                # 只有同时带 key 与 name 的站点才算一个有效「key-name 键值对」
                                if 'key' in item and 'name' in item:
                                    site_pair_count += 1
                        stack.extend((item, False) for item in value)
                    elif isinstance(value, (dict, list)):
                        stack.append((value, count_enabled))

            elif isinstance(node, list):
                stack.extend((item, count_enabled) for item in node)

        return found_keys, site_pair_count, sites_keys_total

    @staticmethod
    def classify_keys(found_keys: set) -> int:
        """
        根据命中的关键 key 分类。
        返回：0-线路，1-单仓，2-多仓，-1-其他
        """
        has_sites = 'sites' in found_keys
        has_urls = 'urls' in found_keys
        has_store_house = 'storeHouse' in found_keys

        if has_sites:
            return 0  # 线路
        elif has_urls and not has_store_house:
            return 1  # 单仓
        elif has_store_house and not has_urls:
            return 2  # 多仓
        else:
            return -1  # 其他

    def classify_response(self, response_text: str, json_data: Optional[Any] = None) -> int:
        """
        分类响应内容
        返回：0-线路，1-单仓，2-多仓，-1-其他
        """
        if json_data is None:
            json_data = self.parse_json_text(response_text)

        if json_data is not None:
            found_keys, _, _ = self.scan_json_data(json_data)
            return self.classify_keys(found_keys)

        # 非 JSON 内容退化为正则匹配判断
        has_store_house = PATTERNS['storeHouse'].search(response_text) is not None
        has_urls = PATTERNS['urls'].search(response_text) is not None
        has_sites = PATTERNS['sites'].search(response_text) is not None
        return self.classify_keys(
            {key for key, hit in (('sites', has_sites), ('urls', has_urls), ('storeHouse', has_store_house)) if hit}
        )

    def analyze_response(self, response: requests.Response, url: str) -> Tuple[int, float, int, int]:
        """
        分析响应内容。
        返回：(分类, 内容大小 KB, 含 key+name 的站点条目数, sites 键值对总数)
        """
        content_length_kb = len(response.content) / 1024
        response_text = self.decode_response_text(response)
        json_data = self.parse_json_text(response_text, url)
        site_pair_count, sites_keys_total = (0, 0)

        if json_data is not None:
            # 一次遍历同时得到分类和 sites 统计，避免重复全量扫描
            found_keys, site_pair_count, sites_keys_total = self.scan_json_data(json_data)
            logger.info(f"sites 有效站点数（含 key+name）：{site_pair_count}, sites 键值对总数：{sites_keys_total}")
            classification = self.classify_keys(found_keys)
        else:
            classification = self.classify_response(response_text, None)

        return classification, content_length_kb, site_pair_count, sites_keys_total
    
    def validate_single_url(self, url_data: Dict) -> Tuple[Dict, bool, float, float, int, int]:
        """
        验证单个 URL
        返回：(url_data, is_valid, response_time, content_length_kb, site_pair_count, sites_keys_total)
        """
        url = url_data.get('url', '')
        name = url_data.get('name', url)
        start_time = time.time()
        content_length_kb = 0.0
        site_pair_count = 0
        sites_keys_total = 0
    
        if not url:
            logger.warning(f"跳过缺少 url 的数据：{url_data}")
            return (url_data, False, 0.0, content_length_kb, site_pair_count, sites_keys_total)
        
        for attempt, use_tvbox_headers in enumerate((False, True), start=1):
            try:
                response = self.get_response(url, use_tvbox_headers=use_tvbox_headers)
            except Exception as e:
                # 连接超时、DNS 失败、连接被拒等传输层异常换 UA 也无法解决，
                # 直接判定失败，避免再等一个完整的超时周期
                logger.warning(f"{name} - {url} 第 {attempt} 次请求异常：{e}")
                break

            try:
                response_time = time.time() - start_time
                content_length_kb = len(response.content) / 1024
                
                if response.status_code != 200:
                    logger.warning(f"{name} - {url} 第 {attempt} 次请求状态码：{response.status_code}")
                    continue
                
                classification, content_length_kb, site_pair_count, sites_keys_total = self.analyze_response(response, url)
                if classification == 0 and content_length_kb > MIN_CONTENT_KB:
                    suffix = "（二次验证）" if attempt == 2 else ""
                    logger.info(f"✓ {name} - {url} 线路成功{suffix}")
                    return (url_data, True, response_time, content_length_kb, site_pair_count, sites_keys_total)
                
                logger.warning(f"{name} - {url} 第 {attempt} 次验证失败，分类：{classification}")
            except Exception as e:
                response_time = time.time() - start_time
                logger.warning(f"{name} - {url} 第 {attempt} 次处理异常：{e}")
        
        response_time = time.time() - start_time
        logger.warning(f"✗ {name} - {url} 线路失败")
        return (url_data, False, response_time, content_length_kb, site_pair_count, sites_keys_total)
    
    def normalize_url_items(self, items: Any, source_url: str, url_keys: Tuple[str, ...] = ('url',)) -> List[Dict]:
        """清洗数据源中的 urls 列表，只保留带 url 的字典项。"""
        if not isinstance(items, list):
            logger.warning(f"数据源 urls 不是列表：{source_url}")
            return []
        
        normalized = []
        for item in items:
            if not isinstance(item, dict):
                logger.warning(f"跳过无效线路项：{item}")
                continue
            
            raw_url = next((item.get(key) for key in url_keys if item.get(key)), None)
            if not raw_url:
                logger.warning(f"跳过无效线路项：{item}")
                continue
            
            url = str(raw_url).strip()
            name = str(item.get('name') or url).strip()
            if url:
                cleaned = item.copy()
                cleaned['url'] = url
                cleaned['name'] = name
                normalized.append(cleaned)
        
        return normalized
    
    def find_nested_key(self, data: Any, key: str) -> Optional[Any]:
        """递归查找 JSON 中第一个指定 key 的值。"""
        if isinstance(data, dict):
            if key in data:
                return data[key]
            for value in data.values():
                found = self.find_nested_key(value, key)
                if found is not None:
                    return found
        elif isinstance(data, list):
            for item in data:
                found = self.find_nested_key(item, key)
                if found is not None:
                    return found
        return None

    def extract_urls_from_source_data(self, json_data: Any, source_url: str, depth: int) -> List[Dict]:
        """
        从数据源 JSON 中提取线路。
        支持 {"urls": [...]} 单仓列表；遇到 {"storeHouse": [...]} 多仓列表时继续展开仓库链接。
        如果解析后的内容为 {"urls":[{"url":"xxx","name":"xxx"}, ...]}，会提取 url 链接并返回。
        """
        urls_list = self.find_nested_key(json_data, 'urls')
        if isinstance(urls_list, list):
            urls = self.normalize_url_items(urls_list, source_url)
            logger.info(f"识别为单仓 urls 数据源：{source_url}，提取 {len(urls)} 条线路")
            return urls

        store_house = self.find_nested_key(json_data, 'storeHouse')
        if isinstance(store_house, list):
            if depth >= MAX_SOURCE_DEPTH:
                logger.warning(f"多仓展开层级超过限制，跳过：{source_url}")
                return []

            warehouses = self.normalize_url_items(
                store_house,
                source_url,
                url_keys=('url', 'sourceUrl')
            )
            logger.info(f"识别为多仓 storeHouse 数据源：{source_url}，包含 {len(warehouses)} 个仓库")

            all_urls = []
            if warehouses:
                with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(warehouses))) as executor:
                    future_to_url = {
                        executor.submit(self.fetch_urls_from_source, warehouse['url'], depth + 1): warehouse['url']
                        for warehouse in warehouses
                    }
                    for future in as_completed(future_to_url):
                        try:
                            all_urls.extend(future.result())
                        except Exception as e:
                            logger.warning(f"获取仓库 {future_to_url[future]} 失败：{e}")
            return all_urls

        if isinstance(json_data, list):
            urls = self.normalize_url_items(json_data, source_url)
            logger.info(f"识别为数组数据源：{source_url}，提取 {len(urls)} 条线路")
            return urls

        logger.warning(f"未识别的数据源结构：{source_url}")
        return []
        
    def fetch_urls_from_source(self, url: str, depth: int = 0) -> List[Dict]:
        """
        从单个数据源入口获取 URLs 列表。
        数据源可以直接包含 urls，也可以包含 storeHouse 仓库列表。
        返回：urls 列表
        """
        for attempt, use_tvbox_headers in enumerate((False, True), start=1):
            try:
                response = self.get_response(url, use_tvbox_headers=use_tvbox_headers)
                response.raise_for_status()
                
                logger.info(f"第 {attempt} 次响应状态码：{response.status_code}")
                logger.info(f"第 {attempt} 次响应内容类型：{response.headers.get('Content-Type', 'unknown')}")

                response_text = self.decode_response_text(response)
                json_data = self.parse_json_text(response_text, url)
                if json_data is None:
                    with open('debug_response.txt', 'w', encoding='utf-8') as f:
                        f.write(response_text)
                    logger.error("原始响应已保存到 debug_response.txt")
                    continue
                
                datas = self.extract_urls_from_source_data(json_data, url, depth)
                logger.info(f'从 {url} 获取到 {len(datas)} 条线路')
                return datas
            except Exception as e:
                logger.warning(f"第 {attempt} 次获取数据源失败 {url}：{e}")
        
        logger.error(f"获取数据源失败：{url}")
        return []

    def load_urls_from_sources(self, source_urls: List[str]) -> List[Dict]:
        """
        解析 FALLBACK_URLS 中每个数据源，提取其中的 url 条目
        返回：合并后的 urls 列表
        """
        all_urls = []
        # 去重并保持原有顺序，避免对同一个数据源重复发起请求
        valid_sources = list(dict.fromkeys(source_url for source_url in source_urls if source_url))
        if not valid_sources:
            return all_urls

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(valid_sources))) as executor:
            future_to_source = {
                executor.submit(self.fetch_urls_from_source, source_url): source_url
                for source_url in valid_sources
            }
            for future in as_completed(future_to_source):
                source_url = future_to_source[future]
                try:
                    urls = future.result()
                    url_count = len(urls)
                    logger.info(f"=" * 50)
                    logger.info(f"正在解析数据源：{source_url}")
                    logger.info(f"=" * 50)
                    logger.info(f"数据源 {source_url} 包含 {url_count} 个 URL")

                    if urls:
                        all_urls.extend(urls)
                        logger.info(f"✓ 成功解析数据源 {source_url}，获取 {url_count} 条线路")
                    else:
                        logger.warning(f"✗ 解析数据源失败：{source_url}")
                except Exception as e:
                    logger.warning(f"获取数据源 {source_url} 失败：{e}")

        return all_urls

    def validate_urls(self, datas: List[Dict]) -> List[Tuple[Dict, int, int]]:
        """
        验证 URLs 列表
        返回：有效的 urls 列表 [(url_data, site_pair_count, sites_keys_total), ...]
        """
        valid_urls = []
        total_count = len(datas)
        logger.info(f'待验证线路条数：{total_count}')
        
        # 使用多线程并发验证
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_url = {
                executor.submit(self.validate_single_url, data): data 
                for data in datas
            }
            
            for future in as_completed(future_to_url):
                try:
                    url_data, is_valid, response_time, content_length_kb, site_pair_count, sites_keys_total = future.result()
                except Exception as e:
                    source_data = future_to_url[future]
                    logger.error(f"验证任务异常：{source_data}，错误：{e}")
                    continue
                
                # 性能过滤：响应超过 15 秒「或」内容小于 20KB，任一命中即视为慢速线路并删除
                if is_valid and (response_time > SLOW_RESPONSE_SECONDS or content_length_kb < SLOW_RESPONSE_MIN_KB):
                    logger.warning(f"删除慢速线路：{url_data['name']} - {url_data['url']}")
                    is_valid = False
                
                # sites 中「含 key 与 name」的站点条目数少于 20 个则删除
                if is_valid and site_pair_count < MIN_SITES_KEYS:
                    logger.warning(f"删除线路（含 key+name 的站点不足）：{url_data['name']} - site_count:{site_pair_count}")
                    is_valid = False
                
                if is_valid:
                    # 保存 url_data 和统计信息用于后续排序
                    valid_urls.append((url_data, site_pair_count, sites_keys_total))
                
                logger.info(f"响应时间：{response_time:.4f}秒; 内容长度：{content_length_kb:.2f}KB; 有效站点数：{site_pair_count}")
                logger.info("-" * 100)
        
        return valid_urls
    
    def sort_by_keys_and_names(self, data: List[Tuple[Dict, int, int]]) -> List[Dict]:
        """
        排序：先按「sites 中同时含 key 与 name 的站点条目数」降序，
        再按 sites 键值对总数 Σlen 降序，使数据最完整的线路排在输出 JSON 最上面。
        返回：排序后的 url_data 列表
        """
        # 先按站点条目数降序，再按键值对总数降序
        sorted_data = sorted(data, key=lambda x: (x[1], x[2]), reverse=True)
        
        # 只返回 url_data，丢弃统计信息
        return [item[0] for item in sorted_data]
    
    def remove_duplicates(self, data: List[Dict], key: str) -> Tuple[List[Dict], int]:
        """
        去除重复数据
        返回：(去重后的列表，重复数量)
        """
        seen = set()
        unique_data = []
        
        for item in data:
            value = item.get(key)
            if not value:
                logger.warning(f"跳过去重字段缺失的数据：{item}")
                continue
            if value not in seen:
                seen.add(value)
                unique_data.append(item)
        
        duplicate_count = len(data) - len(unique_data)
        return (unique_data, duplicate_count)
    
    def update_fixed_line_status(self, line: Dict) -> Dict:
        """
        对固定线路 URL 进行 JSON 解析，并在 name 中追加状态说明
        """
        original_name = line.get('name', '')
        base_name = re.sub(r'(可用|解析失败)$', '', original_name).strip()
        status = '解析失败'

        try:
            response = self.get_response(line['url'], use_tvbox_headers=True)
            response.raise_for_status()
            classification, content_length_kb, _, _ = self.analyze_response(response, line['url'])
            if classification == 0 and content_length_kb > MIN_CONTENT_KB:
                status = '可用'
        except Exception as e:
            logger.warning(f"固定线路 {line['url']} 验证异常：{e}")
            status = '解析失败'

        line['name'] = f"{base_name}{status}"
        logger.info(f"固定线路 {line['url']} 状态：{line['name']}")
        return line

    @staticmethod
    def build_violation_patterns(keywords: List[str]) -> List[re.Pattern]:
        """把关键词拆分成中文子串正则和英文单词正则两类。"""
        chinese_words = [kw for kw in keywords if kw and not kw.isascii()]
        ascii_words = [kw for kw in keywords if kw and kw.isascii()]

        patterns: List[re.Pattern] = []
        if chinese_words:
            patterns.append(re.compile('|'.join(re.escape(kw) for kw in chinese_words), re.I))
        if ascii_words:
            joined = '|'.join(re.escape(kw) for kw in ascii_words)
            patterns.append(re.compile(rf'(?<![a-z0-9])(?:{joined})(?![a-z0-9])', re.I))
        return patterns

    def find_violation(self, text: str) -> Optional[str]:
        """返回文本中命中的第一个违规关键词，未命中返回 None。"""
        for pattern in self.violation_patterns:
            match = pattern.search(text)
            if match:
                return match.group(0)
        return None

    def review_and_remove_violations(self, lines: List[Dict]) -> Tuple[List[Dict], List[Tuple[str, str]]]:
        """
        Gitee 违规信息审查：逐条检查线路的所有字段（名称、URL 及其他字段），
        命中违规关键词的线路整条删除。
        返回：(保留的线路列表，被删除的 [(线路名称, 命中关键词), ...])
        """
        kept_lines: List[Dict] = []
        removed_lines: List[Tuple[str, str]] = []

        for line in lines:
            # 序列化整条线路进行审查，避免遗漏自定义字段中的违规内容
            line_text = json.dumps(line, ensure_ascii=False)
            hit_keyword = self.find_violation(line_text)
            if hit_keyword:
                name = str(line.get('name') or line.get('url') or line)
                removed_lines.append((name, hit_keyword))
                logger.warning(f"删除违规线路（命中关键词「{hit_keyword}」）：{name}")
                continue
            kept_lines.append(line)

        logger.info(f"Gitee 违规信息审查完成：删除 {len(removed_lines)} 条，保留 {len(kept_lines)} 条")
        return kept_lines, removed_lines

    def save_json_file(self, json_string: str) -> None:
        """保存 JSON 文件"""
        try:
            with open(self.filename, "w", encoding="utf-8") as file:
                file.write(json_string)
            logger.info(f"文件已保存：{self.filename}")
        except Exception as e:
            logger.error(f"保存文件失败：{e}")
    
    def check_file_exist(self) -> Optional[str]:
        """检查文件是否存在"""
        folder_path = os.getcwd()
        file_path = os.path.join(folder_path, self.filename)
        
        if os.path.exists(file_path):
            filelast_modified = os.path.getmtime(file_path)
            last_modified_time = datetime.fromtimestamp(filelast_modified)
            formatted_time = last_modified_time.strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f'文件存在，修改时间：{formatted_time}, 路径：{file_path}')
            return file_path
        else:
            logger.info('文件不存在')
            return None


def send_notification(valid_count: int) -> None:
    """发送通知"""
    try:
        import notify
        notify.send("tvbox 路线失效验证", f"最后成功的线路条数有：{valid_count}")
    except ImportError:
        logger.warning("notify 模块未安装，跳过通知")
    except Exception as e:
        logger.warning(f"通知发送失败，已跳过：{e}")


def main():
    """主函数 - 合并多个数据源"""
    validator = TVBoxValidator(filename='tvbox.json')
    
    # 1. 解析 FALLBACK_URLS 中每个源，提取 urls 链接
    all_urls = validator.load_urls_from_sources(FALLBACK_URLS)

    if not all_urls:
        logger.error("所有数据源均失败，未获取到任何线路")
        return
    
    logger.info(f"\n{'=' * 50}")
    logger.info(f"所有数据源合并后总线路数：{len(all_urls)}")
    logger.info(f"{'=' * 50}\n")
    
    # 2. 验证所有 URLs（返回带统计信息的元组列表）
    valid_urls_with_stats = validator.validate_urls(all_urls)
    logger.info(f"验证后有效线路数：{len(valid_urls_with_stats)}")
    
    # 3. 按站点条目数和 sites 键值对总数降序（数据最完整的线路排最上面）
    sorted_urls = validator.sort_by_keys_and_names(valid_urls_with_stats)
    logger.info(f'排序完成（按含 key+name 的站点条目数、sites 键值对总数降序）')
    
    # 4. 去重处理
    unique_urls, duplicate_count = validator.remove_duplicates(sorted_urls, 'url')
    logger.info(f'去除重复线路：{duplicate_count}')
    logger.info(f'去重后线路数：{len(unique_urls)}')

    # 5. 并发更新固定线路状态，再添加到 urls 数组开头（逆序插入保持原顺序）
    with ThreadPoolExecutor(max_workers=len(FIXED_LINES)) as executor:
        validated_fixed_lines = list(
            executor.map(validator.update_fixed_line_status, (line.copy() for line in FIXED_LINES))
        )
    for line in reversed(validated_fixed_lines):
        unique_urls.insert(0, line)
    logger.info(f'添加固定线路数：{len(validated_fixed_lines)}')

    final_count = len(unique_urls)
    logger.info(f'最终线路条数：{final_count}')

    # 6. Gitee 违规信息审查：删除命中违规关键词的线路（必须在构建 JSON 之前执行）
    unique_urls, removed_lines = validator.review_and_remove_violations(unique_urls)
    final_count = len(unique_urls)
    logger.info(f'违规审查后线路条数：{final_count}')
    
    # 7. 构建结果 JSON
    result_dict = {'urls': unique_urls}
    json_string = json.dumps(result_dict, indent=4, ensure_ascii=False)
    
    # 8. 保存文件
    validator.save_json_file(json_string)
    
    # 9. 发送通知
    if final_count > 0:
        send_notification(final_count)
        validator.check_file_exist()
    else:
        logger.error("未生成有效线路文件")


if __name__ == '__main__':
    main()
