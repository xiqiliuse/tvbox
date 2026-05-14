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
    'timeout': 20,
    'headers': {"User-Agent": "okhttp/4.1.0"},
    'verify': False
}

MAX_WORKERS = 10  # 最大并发线程数，用于数据源解析和线路验证
MIN_CONTENT_KB = 2  # 最小内容大小（KB），小于此值视为无效线路
SLOW_RESPONSE_SECONDS = 30  # 响应时间阈值，超过则视为慢速线路
SLOW_RESPONSE_MIN_KB = 8  # 慢速线路的最小内容大小，响应慢且内容小则淘汰
MIN_SITES_KEYS = 10  # sites 键总数阈值，低于此值认为线路数据不完整
MAX_SOURCE_DEPTH = 2  # 多仓展开最大层级，防止递归过深

# 备用数据源入口。每个入口可以是 {"urls": [...]} 单仓列表，也可以是 {"storeHouse": [...]} 多仓列表。
FALLBACK_URLS = [
    'https://raw.githubusercontent.com/hd9211/Tvbox1/main/优质.json',
    ''
]

# 固定线路列表（将添加到结果数组开头）
FIXED_LINES = [
    {"url": "http://肥猫.com/", "name": "肥猫线路"},
    {"url": "http://www.饭太硬.com/tv", "name": "饭太硬"},
]


class TVBoxValidator:
    """TVBox 线路验证器"""
    
    def __init__(self, filename: str = 'tvbox.json'):
        self.filename = filename
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=MAX_WORKERS * 2, pool_maxsize=MAX_WORKERS * 2)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
    
    def get_response(self, url: str, use_tvbox_headers: bool = False) -> requests.Response:
        """统一请求入口，确保超时、UA 和证书校验配置一致。"""
        headers = REQUEST_CONFIG['headers'] if use_tvbox_headers else None
        return self.session.get(
            url,
            headers=headers,
            timeout=REQUEST_CONFIG['timeout'],
            verify=REQUEST_CONFIG['verify']
        )
    
    def sanitize_json_text(self, text: str) -> str:
        """删除 JSON 文本中的 JavaScript 注释，兼容 UTF-8 BOM。"""
        text = text.lstrip('\ufeff')
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
        try:
            return json.loads(self.sanitize_json_text(response_text))
        except json.JSONDecodeError as e:
            location = f"：{url}" if url else ""
            logger.warning(f"JSON 解析失败{location}，line {e.lineno}, column {e.colno}, char {e.pos}")
            return None
    
    def has_key_recursive(self, data: Any, target_key: str) -> bool:
        """递归判断 JSON 中是否存在指定 key。"""
        if isinstance(data, dict):
            return target_key in data or any(self.has_key_recursive(v, target_key) for v in data.values())
        if isinstance(data, list):
            return any(self.has_key_recursive(item, target_key) for item in data)
        return False
    
    def classify_response(self, response_text: str, json_data: Optional[Any] = None) -> int:
        """
        分类响应内容
        返回：0-线路，1-单仓，2-多仓，-1-其他
        """
        if json_data is None:
            try:
                json_data = json.loads(response_text.lstrip('\ufeff'))
            except json.JSONDecodeError:
                json_data = None
        
        if json_data is not None:
            has_sites = self.has_key_recursive(json_data, 'sites')
            has_urls = self.has_key_recursive(json_data, 'urls')
            has_store_house = self.has_key_recursive(json_data, 'storeHouse')
        else:
            has_store_house = PATTERNS['storeHouse'].search(response_text) is not None
            has_urls = PATTERNS['urls'].search(response_text) is not None
            has_sites = PATTERNS['sites'].search(response_text) is not None
        
        if has_sites:
            return 0  # 线路
        elif has_urls and not has_store_house:
            return 1  # 单仓
        elif has_store_house and not has_urls:
            return 2  # 多仓
        else:
            return -1  # 其他
    
    def count_sites_keys_and_names(self, data: Any) -> Tuple[int, int]:
        """
        统计 JSON 数据中 sites 数组内每个对象的 Keys 数量和 name 数量
        返回：(sites_keys 总数，name 数量)
        """
        total_keys = 0
        name_count = 0
        
        if isinstance(data, dict):
            for k, v in data.items():
                if k == 'sites' and isinstance(v, list):
                    # 统计 sites 数组中每个对象的 keys 数量和 name 数量
                    for item in v:
                        if isinstance(item, dict):
                            total_keys += len(item.keys())
                            if 'name' in item:
                                name_count += 1
                else:
                    # 递归统计嵌套数据
                    sub_keys, sub_names = self.count_sites_keys_and_names(v)
                    total_keys += sub_keys
                    name_count += sub_names
        
        elif isinstance(data, list):
            for item in data:
                sub_keys, sub_names = self.count_sites_keys_and_names(item)
                total_keys += sub_keys
                name_count += sub_names
        
        return (total_keys, name_count)
    
    def analyze_response(self, response: requests.Response, url: str) -> Tuple[int, float, int, int]:
        """
        分析响应内容。
        返回：(分类, 内容大小 KB, sites_keys 总数, name 数量)
        """
        content_length_kb = len(response.content) / 1024
        json_data = self.parse_json_text(response.text, url)
        sites_keys_count, name_count = (0, 0)
        if json_data is not None:
            sites_keys_count, name_count = self.count_sites_keys_and_names(json_data)
            logger.info(f"sites Keys 统计：{sites_keys_count}, name 数量：{name_count}")
        
        classification = self.classify_response(response.text, json_data)
        return classification, content_length_kb, sites_keys_count, name_count
    
    def validate_single_url(self, url_data: Dict) -> Tuple[Dict, bool, float, float, int, int]:
        """
        验证单个 URL
        返回：(url_data, is_valid, response_time, content_length_kb, sites_keys_count, name_count)
        """
        url = url_data.get('url', '')
        name = url_data.get('name', url)
        start_time = time.time()
        content_length_kb = 0.0
        sites_keys_count = 0
        name_count = 0
    
        if not url:
            logger.warning(f"跳过缺少 url 的数据：{url_data}")
            return (url_data, False, 0.0, content_length_kb, sites_keys_count, name_count)
        
        for attempt, use_tvbox_headers in enumerate((False, True), start=1):
            try:
                response = self.get_response(url, use_tvbox_headers=use_tvbox_headers)
                response_time = time.time() - start_time
                content_length_kb = len(response.content) / 1024
                
                if response.status_code != 200:
                    logger.warning(f"{name} - {url} 第 {attempt} 次请求状态码：{response.status_code}")
                    continue
                
                classification, content_length_kb, sites_keys_count, name_count = self.analyze_response(response, url)
                if classification == 0 and content_length_kb > MIN_CONTENT_KB:
                    suffix = "（二次验证）" if attempt == 2 else ""
                    logger.info(f"✓ {name} - {url} 线路成功{suffix}")
                    return (url_data, True, response_time, content_length_kb, sites_keys_count, name_count)
                
                logger.warning(f"{name} - {url} 第 {attempt} 次验证失败，分类：{classification}")
            except Exception as e:
                response_time = time.time() - start_time
                logger.warning(f"{name} - {url} 第 {attempt} 次请求异常：{e}")
        
        response_time = time.time() - start_time
        logger.warning(f"✗ {name} - {url} 线路失败")
        return (url_data, False, response_time, content_length_kb, sites_keys_count, name_count)
    
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

                json_data = self.parse_json_text(response.text, url)
                if json_data is None:
                    with open('debug_response.txt', 'w', encoding='utf-8') as f:
                        f.write(response.text)
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
        valid_sources = [source_url for source_url in source_urls if source_url]
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
        返回：有效的 urls 列表 [(url_data, sites_keys_count, name_count), ...]
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
                    url_data, is_valid, response_time, content_length_kb, sites_keys_count, name_count = future.result()
                except Exception as e:
                    source_data = future_to_url[future]
                    logger.error(f"验证任务异常：{source_data}，错误：{e}")
                    continue
                
                # 性能过滤：响应时间>10 秒且内容<8KB 的线路删除
                if is_valid and response_time > SLOW_RESPONSE_SECONDS and content_length_kb < SLOW_RESPONSE_MIN_KB:
                    logger.warning(f"删除慢速线路：{url_data['name']} - {url_data['url']}")
                    is_valid = False
                
                # sites Keys 数量过滤 - 小于 10 个则删除
                if is_valid and sites_keys_count < MIN_SITES_KEYS:
                    logger.warning(f"删除线路（sites Keys 不足）：{url_data['name']} - sites_keys:{sites_keys_count}")
                    is_valid = False
                
                if is_valid:
                    # 保存 url_data 和统计信息用于后续排序
                    valid_urls.append((url_data, sites_keys_count, name_count))
                
                logger.info(f"响应时间：{response_time:.4f}秒; 内容长度：{content_length_kb:.2f}KB")
                logger.info("-" * 100)
        
        return valid_urls
    
    def sort_by_keys_and_names(self, data: List[Tuple[Dict, int, int]]) -> List[Dict]:
        """
        按 sites_keys 数量和 name 数量降序排序
        返回：排序后的 url_data 列表
        """
        # 先按 sites_keys 降序，再按 name_count 降序
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
    
    # 3. 按 sites_keys 和 name 数量降序排序
    sorted_urls = validator.sort_by_keys_and_names(valid_urls_with_stats)
    logger.info(f'排序完成（按 sites_keys 和 name 数量降序）')
    
    # 4. 去重处理
    unique_urls, duplicate_count = validator.remove_duplicates(sorted_urls, 'url')
    logger.info(f'去除重复线路：{duplicate_count}')
    logger.info(f'去重后线路数：{len(unique_urls)}')

    # 5. 更新固定线路状态并添加到 urls 数组开头（逆序插入保持原顺序）
    validated_fixed_lines = [validator.update_fixed_line_status(line.copy()) for line in FIXED_LINES]
    for line in reversed(validated_fixed_lines):
        unique_urls.insert(0, line)
    logger.info(f'添加固定线路数：{len(validated_fixed_lines)}')

    final_count = len(unique_urls)
    logger.info(f'最终线路条数：{final_count}')
    
    # 6. 构建结果 JSON
    result_dict = {'urls': unique_urls}
    json_string = json.dumps(result_dict, indent=4, ensure_ascii=False)
    
    # 7. 保存文件
    validator.save_json_file(json_string)
    
    # 8. 发送通知
    if final_count > 0:
        send_notification(final_count)
        validator.check_file_exist()
    else:
        logger.error("未生成有效线路文件")


if __name__ == '__main__':
    main()
