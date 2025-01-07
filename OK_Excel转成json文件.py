#2025-01-07更新，完成
# 把Excel表中的单独线路整合成json文件
import json
import openpyxl

def main():
    i=1
    # excel_file = r'D:\OneDrive\Learn\pythonLearn\TvBox\tvbox\excel_xianlu.xlsx' #家里路径
    excel_file = r'D:\lxd\learn\py\tvbox\excel_0103.xlsx' #公司路径
    workbook = openpyxl.load_workbook(filename=excel_file)
    sheet1= workbook['0107']
    dict2={} #创建一个字典,存储Urls
    listdict=[]
    data_dict = {}
    for row in sheet1.iter_rows(values_only=True):
        if row[0] is not None and row[1] is not None:
            data_dict["url"] = row[1]
            data_dict["name"] = row[0]
            listdict.append(data_dict.copy())
            print(row[0],row[1])
        else:
            break
        i=i+1
    dict2['urls']=listdict
    with open('output.json', 'w', encoding='utf-8') as json_file:
        json.dump(dict2, json_file, ensure_ascii=False, indent=4)

if __name__ == '__main__':
    main()