# 导入上传流 500 与工作区菜单目标

真实日志 `debug_stream.log` 记录 `AttributeError: 'SpooledTemporaryFile' object has no attribute 'seekable'`。旧 Python 临时上传流已有 read/seek/tell，但 ZipFile 打开条目时需要 seekable。此前使用新解释器和 BytesIO 的测试未覆盖这一输入边界。

本次回归全部使用临时数据和合成 ZIP，不读真实对话和凭据，不重启主服务：

```bash
set -o pipefail
ASTRION_IGNORE_DOTENV=1 \
ASTRION_DATA_ROOT="$PWD/cache/conversation-transfer-upload-test" \
/opt/homebrew/bin/python3.11 -B -m unittest discover \
  -s test/2026-10-27_对话迁移_02 -p 'test_*.py'

/usr/bin/python3 -B test/2026-10-27_对话迁移_02/native_stream.py
node test/2026-10-27_对话迁移_02/import_target.cjs
```

实测结果：

- HTTP 回归先复现同一 500，修复后通过：真实 Flask multipart 解析使用缺少 seekable 的上传流，导入后历史前缀不变。
- 本机原生 Python 3.9.6 临时上传文件读取通过；该单测直接加载生产流适配器类，只有标准库依赖，不复制适配器实现。系统 Python 3.9 未安装 Flask，完整 Flask 测试运行于已有依赖的 Python 3.11。
- 实际 Dialog setup + 真实 transferRequest：菜单目标 B 在文件选择期间切换当前工作区 C 后仍向 B 导入；非分组入口捕获原当前工作区，Chat 保持账号作用域。无浏览器自动化。
- 原迁移 31 项全部通过；改动 SFC 的 ESLint、TypeScript 检查及生产构建通过。

菜单显示与真实服务端导入仍需用户实测。遇到未预期异常，现在完整堆栈会写入后端 debug_stream.log；不把只返回统一提示等同于未发生后端异常。
