# ChequeScript

本地小工具。客户 PDF 里往往既有打印文字，也有手写支票的照片。打印页原样保留，手写支票交给视觉模型读成可核对的文字记录，再生成一份新的 PDF。

支票记录只用于记账，页脚写明不是可流通支票。

## 能做什么

- 上传一份 PDF。打印页照抄；同一页上的支票照片会拆开。
- 一张图里如果有多张支票，中间有空白时会切开。读的时候如果模型认出不止一张，会各记一条。
- 核对金额、收款人、日期等字段，也可以改成普通文字页，或从文件里去掉。
- 生成新的 PDF 和 CSV。原有打印内容在前，支票文字记录在后。
- 只有点击 Read handwriting 时，支票图片才会发给你在设置里填写的模型。

## 安装

需要 Windows 上的 Python 3.11 或更新版本。

双击 `start.bat`。第一次运行会创建虚拟环境、安装依赖，并打开 http://127.0.0.1:8741 。

也可以手动启动：

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8741
```

在 Settings 里填写模型地址、模型名和 API key。程序只监听本机。Key 保存在本机的 `data/settings.json`，这个目录不会进入仓库。

## 测试

```bat
.venv\Scripts\python.exe -m pytest tests -q
```
