# CircuitScout汉化网页

保存已部署汉化网页的源码快照、依赖清单和既有测试，保留上游MIT许可证及原版权声明。

当前页面提供KiCad导入、网络/焊盘选点、串口连接和探针命令入口。正式ADC测量、悟净PL计算、机械到位/接触及报告尚未在页面中统一联动。

```bash
python -m pip install -r requirements.lock.txt
python -m flask --app interface run --host 127.0.0.1 --port 8001
```

在本目录运行。部署时通过环境变量`FDW_WEB_SECRET`提供会话密钥，实例配置与实际上传文件由运行环境单独保存。串口入口面向受控的本地仪器网络。

原项目简介见[UPSTREAM_README.md](UPSTREAM_README.md)，许可证见[UPSTREAM_LICENSE.md](UPSTREAM_LICENSE.md)。
