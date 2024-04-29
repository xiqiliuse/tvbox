#!/usr/bin/env python3
# _*_ coding:utf-8 _*_
import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.parse
# import smtplib
# from email.mime.text import MIMEText
# from email.header import Header
# from email.utils import formataddr

import requests

# 原先的 print 函数和主线程的锁
_print = print
mutex = threading.Lock()


# 定义新的 print 函数
def print(text, *args, **kw):
    """
    使输出有序进行，不出现多线程同一时间输出导致错乱的问题。
    """
    with mutex:
        _print(text, *args, **kw)


# 通知服务
# fmt: off
push_config = {
    'HITOKOTO': True,                  # 启用一言（随机句子）

    'BARK_PUSH': '',                    # bark IP 或设备码，例：https://api.day.app/DxHcxxxxxRxxxxxxcm/
    'BARK_ARCHIVE': '',                 # bark 推送是否存档
    'BARK_GROUP': '',                   # bark 推送分组
    'BARK_SOUND': '',                   # bark 推送声音
    'BARK_ICON': '',                    # bark 推送图标
    'BARK_LEVEL': '',                   # bark 推送时效性
    'BARK_URL': '',                     # bark 推送跳转URL

    'CONSOLE': True,                    # 控制台输出

    'QYWX_KEY':''                     # 企业微信机器人--------
    }
notify_function = []
# fmt: on

# 首先读取 面板变量 或者 github action 运行变量
for k in push_config:
    if os.getenv(k):
        v = os.getenv(k)
        push_config[k] = v
        # print(v)

def console(title: str, content: str) -> None:
    """
    使用 控制台 推送消息。
    """
    print(f"{title}\n\n{content}")


def wecom_bot(title: str, content: str) -> None:
    """
    通过 企业微信机器人 推送消息。
    """
    if not push_config.get("QYWX_KEY"):
        print("企业微信机器人 服务的 QYWX_KEY 未设置!!\n取消推送")
        return
    print("企业微信机器人服务启动")

    origin = "https://qyapi.weixin.qq.com"
    if push_config.get("QYWX_ORIGIN"):
        origin = push_config.get("QYWX_ORIGIN")

    url = f"{origin}/cgi-bin/webhook/send?key={push_config.get('QYWX_KEY')}"
    headers = {"Content-Type": "application/json;charset=utf-8"}
    data = {"msgtype": "text", "text": {"content": f"{title}\n\n{content}"}}
    response = requests.post(url=url, data=json.dumps(data), headers=headers, timeout=15).json()

    if response["errcode"] == 0:
        print("企业微信机器人推送成功！")
    else:
        print("企业微信机器人推送失败！")


def one() -> str:
    """
    获取一条一言。
    :return:
    """
    url = "https://v1.hitokoto.cn/"
    res = requests.get(url).json()
    return res["hitokoto"] + "    ----" + res["from"]


if push_config.get("QYWX_KEY"):
    notify_function.append(wecom_bot)


def send(title: str, content: str) -> None:
    if not content:
        print(f"{title} 推送内容为空！")
        return

    # 根据标题跳过一些消息推送，环境变量：SKIP_PUSH_TITLE 用回车分隔
    skipTitle = os.getenv("SKIP_PUSH_TITLE")
    if skipTitle:
        if title in re.split("\n", skipTitle):
            print(f"{title} 在SKIP_PUSH_TITLE环境变量内，跳过推送！")
            return

    hitokoto = push_config.get("HITOKOTO")

    text = one() if hitokoto else ""
    content += "\n\n" + text

    ts = [
        threading.Thread(target=mode, args=(title, content), name=mode.__name__)
        for mode in notify_function
        ]
    [t.start() for t in ts]
    [t.join() for t in ts]


def main():
    send("title", "content")
    print("\n",one())
    print(push_config.get("QYWX_KEY"))

if __name__ == "__main__":
    main()
    