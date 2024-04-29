#!/bin/sh
# /usr/bin/python3 tvboxgitee.py
# ls
echo "--------"
echo "Gitee更新json文件"
echo "--------"
# ssh -T git@gitee.com

# git branch -M master
# # git checkout

# # # git init

git add .
git commit -m "$(date +'%Y-%m-%d-%s')"
# git remote rm origin
git remote add origin git@gitee.com:jiangnandao/tvboxline.git
git pull origin master 
# git push origin master
git push -u origin master



# git remote show origin

echo "--------"
echo "已完成"
# git log
python -c 'import notify; print(notify.send("tvbox路线上传成功", "success"))'