import requests
import json
import os,time
import notify
import warnings 
from datetime import datetime

warnings.filterwarnings("ignore")

# 判断url路线有效性，并在本地生成tvbox_json文件
def process_json(url):
    # 发送 HTTP 请求并获取 JSON 响应
    response = requests.get(url,verify=False)
    # 检查响应是否成功
    if response.status_code == 200:
    # 解析 JSON 响应
        jsondata = json.loads(response.text)
        datas=jsondata['urls']
        x=len(datas)
        print('总线路条数：',x)
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
        y=len(datas) #成功路线
    print('成功路线条数：',y)

    def DelRepeat(data,key):
        new_data = [] # 用于存储去重后的list
        values = []   # 用于存储当前已有的值
        for d in data:
            if d[key] not in values:
                new_data.append(d)
                values.append(d[key])
        z=len(data)-len(new_data) #去重路线条数
        return new_data,z


    dict2={}
    dict2['urls']=DelRepeat(datas,'url')[0]
    print('去除线路：',DelRepeat(datas,'url')[1])
    print('总成功路线：',y-DelRepeat(datas,'url')[1])
    savejson2=json.dumps(dict2, indent=4, ensure_ascii=False)
    
    # print(type(savejson2))
    with open(filename, "w", encoding="utf-8") as file:
        file.write(savejson2)
        file.close()
    return savejson2,y-DelRepeat(datas,'url')[1]

# 获取当前时间 
# def get_time():
#     # time=time.ctime()
#     # print(timeformat)
#     return time # 获取当前时间并返回到函数调用处


def check_file_exist():
    folder_path = os.getcwd()
    file_list = os.listdir(folder_path)
    if filename in file_list:
        # print(file_list)
        file_path = os.path.join(folder_path,filename)
        # 获取tvboxjson文件的修改时间
        filelast_modified=os.path.getmtime(file_path)
        last_modified_time = datetime.fromtimestamp(filelast_modified)
        formatted_time = last_modified_time.strftime('%Y-%m-%d %H:%M:%S')
        print('文件存在，文件修改时间是：',formatted_time,"文件路径是：",file_path)
        return file_path
    else:
        print('文件不存在')
        # print(file_list)

def main():
    url = "https://gitee.com/jiangnandao/tvboxline/raw/master/tvbox_json"
    tvjson=process_json(url)
    # path=check_file_exist()
    # print(time.ctime())
    notify.send("tvbox路线失效验证", "最后成功的线路条数有："+str(tvjson[1]))
    check_file_exist()
    
if __name__ == '__main__':
    filename='tvbox_json'
    main()

    