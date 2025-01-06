#2025-01-06更新，未完成
import json,openpyxl



def main():
    i=1
    # excel_file = r'D:\OneDrive\Learn\pythonLearn\TvBox\tvbox\excel_xianlu.xlsx' #家里路径
    excel_file = r'D:\lxd\learn\py\tvbox\excel_0103.xlsx' #公司路径
    workbook = openpyxl.load_workbook(filename=excel_file)
    sheet1= workbook['0103']
    data_dict = {}
    for row in sheet1.iter_rows(values_only=True):
        if row[0] is not None and row[1] is not None:
            data_dict[row[0]] = row[1]
            print(row[0],row[1])
        else:
            break
        i=i+1
    with open('output.json', 'w', encoding='utf-8') as json_file:
        json.dump(data_dict, json_file, ensure_ascii=False, indent=4)

if __name__ == '__main__':
    main()