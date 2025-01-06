# #!/bin/sh
# # /usr/bin/python3 tvboxgitee.py
# # ls

# echo "Gitee更新json文件"
# cat ~/.ssh/id_ed25519.pub

# # # git remote add origin git@gitee.com:jiangnandao/tvboxline.git

# git rm giteeup.swap.sh
# git remote rm origin 
# git remote add origin git@gitee.com:jiangnandao/tvboxline.git
# git fetch --all 
# git reset --hard origin/master

echo "--------"
# ##git add tvbox.json
git add .
git commit -m "$(date +%Y-%m-%d\ %H:%M:%S)" 
git remote add origin git@gitee.com:jiangnandao/tvboxline.git
git push origin "master"

# # echo $(date +%Y-%m-%d\ %H:%M:%S)

# log=$(git log --after="1 day ago")
log=$(git show HEAD)
echo $log
## python -c 'import notify; notify.send("tvbox路线上传成功", "success")'

python -c "
import notify,sys
var1='''$log'''
print(notify.send('tvbox路线上传成功', var1))
"
echo "--------"


# echo "已完成"
# git remote add origin git@gitee.com:jiangnandao/metest.git
# git push origin "master"


# git checkout master #切换分支
# git branch
# git remote -v
# git remote show origin
# git remote remove origin
# git remote add origin git@gitee.com:jiangnandao/tvboxline.git

# echo "--------"
# git add tvbox.json
# git add .
# git commit -m "$(date +'%Y-%m-%d-%s')" 
# # git remote add origin git@gitee.com:jiangnandao/tvboxline.git

# git branch -d master
# git push origin master
# git fetch origin master

# ssh -T git@gitee.com
# git config --list 
# git remote remove origin 
# git remote add origin git@gitee.com:jiangnandao/tvboxline.git
