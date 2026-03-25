# Auto Scheduler 启动说明

当前项目已创建虚拟环境：`.venv`

## 前台运行

```bash
source .venv/bin/activate
python auto_scheduler.py
```

或者直接：

```bash
.venv/bin/python auto_scheduler.py
```

## 后台运行

```bash
nohup .venv/bin/python auto_scheduler.py > auto_scheduler.log 2>&1 &
```

## 说明

- 启动前先修改项目配置。
- 日志输出文件为 `auto_scheduler.log`。
