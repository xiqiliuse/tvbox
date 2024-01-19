import requests
import json
import warnings 
warnings.filterwarnings("ignore")

with open("D:\\lxd\\learn\\py\\TvBoxShare\\tvbox.json", "r", encoding="utf-8") as file:
    dataa = file.read()
    json_data=json.loads(dataa)
    print(type(json_data))
    
    datas=json_data['urls']

    for data in datas:
            # print(data['url'])
            # print(data['name'])
            testurl=data['url']
            try:
                rnse = requests.get(testurl,verify=False)
                if rnse.status_code==200:
                    print(data['name'],data['url'],"成功")
                else:
                    print(data['name'],data['url'],"失败")
            except Exception as e:
                print(data['name'],'error')

