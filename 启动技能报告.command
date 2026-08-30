#!/bin/bash
# 双击启动 skill-keeper 交互报告:自动重扫 → 起本地服务 → 打开浏览器(关闭本终端窗口即退出)。
# 等价于命令行: python3 scripts/report.py --serve
# 注意:本文件需与 scripts/ 同级(项目根目录);整个项目文件夹迁移时一起搬走即可。
DIR="$(cd "$(dirname "$0")" && pwd)"
exec /usr/bin/env python3 "$DIR/scripts/report.py" --serve "$@"
