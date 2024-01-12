import requests
import json
import os,time
# import notify
import warnings 
from git.repo import Repo
from datetime import datetime

warnings.filterwarnings("ignore")

# 判断url路线有效性，并在本地生成tvbox_json文件
def process_json(data):
        # 发送 HTTP 请求并获取 JSON 响应
    response = requests.get(url,verify=False)

    # 检查响应是否成功
    if response.status_code == 200:
    # 解析 JSON 响应
        jsondata = json.loads(response.text)
        datas=jsondata['urls']
        print('总线路条数：',len(datas))
        for data in datas:
            testurl=data['url']
            try:
                rnse = requests.get(testurl,verify=False,timeout =60)
                if rnse.status_code==200:
                    print(data['name'],data['url'],"成功",)
                else:
                    # print(data['name'],data['url'],"失败")
                    datas.remove(data)
                    print('已删除当前失败',data['name'])
            except Exception:
                # print(data['name'],data['url'],"error")
                datas.remove(data)
                print('已删除当前error',data['name'])
            else:
                pass
            finally:
                pass
    print('成功路线条数：',len(datas))
    dict2={}
    dict2['urls']=datas
    savejson2=json.dumps(dict2, indent=4, ensure_ascii=False)
    
    print(type(savejson2))
    with open("tvbox_json", "w", encoding="utf-8") as file:
        file.write(savejson2)
        file.close()
    return savejson2

# 获取当前时间 
def get_time():
    time=time.ctime()

    # print(timeformat)
    return time # 获取当前时间并返回到函数调用处

# 将本地tvbox_json文件上传gitee
def giteeup(path):
    time=time.ctime()
    '''
    repo = Repo("/ql/data/scripts/tvboxshare/")
    index = repo.index
    index.add([path])
    index.commit(str(str(datetime.now())))
    remote = repo.remote()
    remote.push()
    '''
    order_arr = ["git add *","git commit -m " + '"' + time + '"',"git push origin master"] # 创建指令集合
    for order in order_arr:
        system(order) # 执行每一项指令
    return 

def check_file_exist():
    folder_path = os.getcwd()
    file_list = os.listdir(folder_path)
    if 'tvbox_json' in file_list:
        # print(file_list)
        file_path = os.path.join(folder_path,'tvbox_json')
        # 获取tvboxjson文件的修改时间
        filelast_modified=os.path.getmtime(file_path)
        last_modified_time = datetime.fromtimestamp(filelast_modified)
        formatted_time = last_modified_time.strftime('%Y-%m-%d %H:%M:%S')
        print('文件存在，文件修改时间是：',formatted_time,"文件路径是：",file_path)
        return file_path
    else:
        print('文件不存在')
        # print(file_list)

if __name__ == '__main__':
    url = "https://gitee.com/jiangnandao/tvboxshare/raw/master/TvBoxLink"
    # tvjson=process_json(url)
    # notify.send("tvbox路线失效验证", folder_path)
