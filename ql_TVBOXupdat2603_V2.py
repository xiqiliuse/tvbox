import requests
import re
import json
import os
import time
import warnings
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple, Optional, Any
import logging

warnings.filterwarnings("ignore")

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
    'timeout': 60,
    'headers': {"User-Agent": "okhttp/4.1.0"},
    'verify': False
}

# 备用数据源列表
FALLBACK_URLS = [
    'https://gh-proxy.com/https://raw.githubusercontent.com/hd9211/Tvbox1/main/优质.json',
    'https://gh-proxy.com/https://raw.githubusercontent.com/hd9211/Tvbox1/main/gaotianliuyun.json',
    'https://gh-proxy.com/https://raw.githubusercontent.com/hd9211/Tvbox1/main/cr.json',
    'https://gitee.com/jiangnandao/tvboxshare/raw/master/TVLineTest2.json',
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
        self.session.verify = False
        self.session.headers.update(REQUEST_CONFIG['headers'])
        
    def classify_response(self, response_text: str) -> int:
        """
        分类响应内容
        返回：0-线路，1-单仓，2-多仓，-1-其他
        """
        mcang = PATTERNS['storeHouse'].search(response_text)
        scang = PATTERNS['urls'].search(response_text)
        xlu = PATTERNS['sites'].search(response_text)
        
        if xlu is not None:
            return 0  # 线路
        elif xlu is None and scang is not None and mcang is None:
            return 1  # 单仓
        elif xlu is None and scang is None and mcang is not None:
            return 2  # 多仓
        else:
            return -1  # 其他
    
    def validate_single_url(self, url_data: Dict) -> Tuple[Dict, bool, float, float, int, int]:
        """
        验证单个 URL
        返回：(url_data, is_valid, response_time, content_length_kb, name_count, api_count)
        """
        url = url_data['url']
        name = url_data['name']
        start_time = time.time()
        
        # 初始化变量
        name_count = 0
        api_count = 0
        content_length_kb = 0
        response_time = 0.0
    
        try:
            response = self.session.get(url, timeout=REQUEST_CONFIG['timeout'])
            response_time = time.time() - start_time
            content_length_kb = len(response.content) / 1024
            
            if response.status_code == 200:
                # 解析 response.text 并统计键值对数量
                try:
                    json_data = json.loads(response.text)
                    key_count, name_count, api_count = self.count_keys(json_data)
                    logger.info(f"📊 键值统计 - key:{key_count}, name:{name_count}, api:{api_count}")
                except json.JSONDecodeError:
                    logger.warning(f"无法解析 JSON 格式：{url}")
                
                classification = self.classify_response(response.text)
                
                # 第一次验证
                if classification == 0 and content_length_kb > 2:
                    logger.info(f"✓ {name} - {url} 线路成功")
                    return (url_data, True, response_time, content_length_kb, name_count, api_count)
                
                # 第二次验证（使用特定 headers）
                response2 = self.session.get(url, timeout=REQUEST_CONFIG['timeout'])
                if response2.status_code == 200:
                    classification2 = self.classify_response(response2.text)
                    content_length_kb2 = len(response2.content) / 1024
                    
                    if classification2 == 0 and content_length_kb2 > 2:
                        logger.info(f"✓ {name} - {url} 线路成功（二次验证）")
                        return (url_data, True, response_time, content_length_kb2, name_count, api_count)
            
            logger.warning(f"✗ {name} - {url} 线路失败")
            return (url_data, False, response_time, content_length_kb, name_count, api_count)
            
        except Exception as e:
            response_time = time.time() - start_time
            logger.error(f"✗ {name} - {url} 请求异常：{e}")
            return (url_data, False, response_time, content_length_kb, name_count, api_count)
        
    def count_keys(self, data: Any, target_keys: List[str] = None) -> Tuple[int, int, int]:
        """
        递归统计 JSON 数据中指定键的出现次数
        返回：(key_count, name_count, api_count)
        """
        if target_keys is None:
            target_keys = ['key', 'name', 'api']
        
        key_count = 0
        name_count = 0
        api_count = 0
        
        if isinstance(data, dict):
            for k, v in data.items():
                if k == 'key':
                    key_count += 1
                elif k == 'name':
                    name_count += 1
                elif k == 'api':
                    api_count += 1
                # 递归统计嵌套数据
                sub_key, sub_name, sub_api = self.count_keys(v, target_keys)
                key_count += sub_key
                name_count += sub_name
                api_count += sub_api
        
        elif isinstance(data, list):
            for item in data:
                sub_key, sub_name, sub_api = self.count_keys(item, target_keys)
                key_count += sub_key
                name_count += sub_name
                api_count += sub_api
        
        return (key_count, name_count, api_count)
    
    def fetch_urls_from_source(self, url: str) -> List[Dict]:
        """
        从单个数据源获取 URLs 列表
        返回：urls 列表
        """
        try:
            response = self.session.get(url, timeout=REQUEST_CONFIG['timeout'])
            response.raise_for_status()
            
            logger.info(f"响应状态码：{response.status_code}")
            logger.info(f"响应内容类型：{response.headers.get('Content-Type', 'unknown')}")

            try:
                json_data = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"JSON 解析失败：{e}")
                logger.error(f"错误位置：line {e.lineno}, column {e.colno}, char {e.pos}")
                with open('debug_response.txt', 'w', encoding='utf-8') as f:
                    f.write(response.text)
                logger.error("原始响应已保存到 debug_response.txt")
                return []
            
            datas = json_data.get('urls', [])
            logger.info(f'从 {url} 获取到 {len(datas)} 条线路')
            return datas
            
        except Exception as e:
            logger.error(f"获取数据源失败 {url}：{e}")
            return []
    
    def validate_urls(self, datas: List[Dict]) -> List[Dict]:
        """
        验证 URLs 列表
        返回：有效的 urls 列表
        """
        valid_urls = []
        total_count = len(datas)
        logger.info(f'待验证线路条数：{total_count}')
        
        # 使用多线程并发验证
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_url = {
                executor.submit(self.validate_single_url, data): data 
                for data in datas
            }
            
            for future in as_completed(future_to_url):
                url_data, is_valid, response_time, content_length_kb, name_count, api_count = future.result()
                
                # 性能过滤：响应时间>10 秒且内容<8KB 的线路删除
                if is_valid and response_time > 10 and content_length_kb < 8:
                    logger.warning(f"删除慢速线路：{url_data['name']} - {url_data['url']}")
                    is_valid = False
                
                # 键值统计过滤 - name_count<20 且 api_count<6 的线路删除
                if is_valid and name_count < 20 and api_count < 6:
                    logger.warning(f"删除线路（键值统计不足）：{url_data['name']} - name_count:{name_count}, api_count:{api_count}")
                    is_valid = False
                
                if is_valid:
                    valid_urls.append(url_data)
                
                logger.info(f"响应时间：{response_time:.4f}秒; 内容长度：{content_length_kb:.2f}KB")
                logger.info("-" * 100)
        
        return valid_urls
    
    def remove_duplicates(self, data: List[Dict], key: str) -> Tuple[List[Dict], int]:
        """
        去除重复数据
        返回：(去重后的列表，重复数量)
        """
        seen = set()
        unique_data = []
        
        for item in data:
            if item[key] not in seen:
                seen.add(item[key])
                unique_data.append(item)
        
        duplicate_count = len(data) - len(unique_data)
        return (unique_data, duplicate_count)
    
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


def main():
    """主函数 - 合并多个数据源"""
    validator = TVBoxValidator(filename='tvbox.json')
    
    all_urls = []
    
    # 1. 遍历所有数据源，收集 URLs
    for url in FALLBACK_URLS:
        logger.info(f"=" * 50)
        logger.info(f"正在获取数据源：{url}")
        logger.info(f"=" * 50)
        
        urls = validator.fetch_urls_from_source(url)
        if urls:
            all_urls.extend(urls)
            logger.info(f"✓ 成功从 {url} 获取 {len(urls)} 条线路")
        else:
            logger.warning(f"✗ 数据源 {url} 获取失败")
    
    if not all_urls:
        logger.error("所有数据源均失败，未获取到任何线路")
        return
    
    logger.info(f"\n{'=' * 50}")
    logger.info(f"所有数据源合并后总线路数：{len(all_urls)}")
    logger.info(f"{'=' * 50}\n")
    
    # 2. 验证所有 URLs
    valid_urls = validator.validate_urls(all_urls)
    logger.info(f"验证后有效线路数：{len(valid_urls)}")
    
    # 3. 去重处理
    unique_urls, duplicate_count = validator.remove_duplicates(valid_urls, 'url')
    logger.info(f'去除重复线路：{duplicate_count}')
    logger.info(f'去重后线路数：{len(unique_urls)}')

    # 4. 将固定线路添加到 urls 数组开头（逆序插入保持原顺序）
    for line in reversed(FIXED_LINES):
        unique_urls.insert(0, line)
    logger.info(f'添加固定线路数：{len(FIXED_LINES)}')

    final_count = len(unique_urls)
    logger.info(f'最终线路条数：{final_count}')
    
    # 5. 构建结果 JSON
    result_dict = {'urls': unique_urls}
    json_string = json.dumps(result_dict, indent=4, ensure_ascii=False)
    
    # 6. 保存文件
    validator.save_json_file(json_string)
    
    # 7. 发送通知
    if final_count > 0:
        send_notification(final_count)
        validator.check_file_exist()
    else:
        logger.error("未生成有效线路文件")


if __name__ == '__main__':
    main()