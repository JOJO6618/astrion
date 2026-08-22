# 自定义工具开发指南（非常详细｜三层架构版）

适用对象：**从零开始、第一次上手** 的管理员。  
当前能力：**仅支持 Python**，且必须在容器内执行；自定义工具仅管理员可见/可用。  

> 本项目默认不做严格校验（私有化客制化场景）。这让你更自由，但也更容易踩坑。本文用大量例子/反例把坑提前说明。

---
## 0. 快速上手（5 分钟 checklist）

1) 创建工具文件夹：`data/custom_tools/<tool_id>/`  
2) 写 3 个文件：
   - `definition.json`（定义层）
   - `execution.py`（执行层）
   - `return.json`（返回层，可选但强烈建议先写一个简单截断）
3) 刷新管理端：
   - 打开 `admin/policy`：工具应该出现在 `custom` 分类的可选列表里
4) 用管理员账号在聊天中调用工具（让模型调用或你手动触发）

如果第 3 步看不到工具：先看本文的“常见问题/排查”章节。

---
## 1. 三层架构：你在写什么？

每个自定义工具由三层组成，各层之间通过“工具运行时”串起来：

### 1.1 定义层：`definition.json`
你告诉系统：
- 工具 ID（也是模型调用的函数名）
- 工具用途描述（给模型看，决定它会不会用、怎么用）
- 参数 schema（模型按它组织参数）
- 分类/图标/超时等元信息

### 1.2 执行层：`execution.py`（Python 模板）
你提供“低代码逻辑”：
- 文件内容会先被做一次 **字符串格式化**（把 `{参数名}` 替换成实际入参）
- 然后在容器内作为 Python 脚本运行

### 1.3 返回层：`return.json`（后处理）
对执行结果二次处理，例如：
- 限制返回长度（避免输出爆炸）
- 用模板把结果包装成更友好的消息

---
## 2. 目录结构规范（必须遵守）

每个工具一个独立目录：`data/custom_tools/<tool_id>/`

标准文件名：
```
definition.json   # 定义层（必需）
execution.py      # 执行层（必需）
return.json       # 返回层（可选）
meta.json         # 备注/展示层（可选）
```

只要某个目录同时存在 `definition.json` + `execution.py`，系统就会把它加载为“自定义工具”。

> 文件编码建议统一 UTF-8。

---
## 3. 运行时规则（你需要牢记的“真实行为”）

### 3.1 谁能看到/调用？
- **只有管理员账号**能看到/调用自定义工具
- 普通用户看不到工具，也无法在聊天中调用

### 3.2 工具如何出现在 `admin/policy`？
- 后端会自动注入一个分类 `custom`（名称“自定义工具”）
- 并把扫描到的自定义工具 ID 填入该分类的工具列表
- 所以 admin/policy 的“工具可选列表”会出现这些工具名，从而可分配到别的分类

### 3.3 执行层为什么“像模板”？
执行层会先做一次 Python `str.format` 类似的格式化：
- `{a}`、`{op}`、`{b}` 这样的占位符会被替换
- 这会导致一个大坑：**Python 字典/集合的花括号 `{}` 会被误认为占位符**（下文详解）

---
## 4. 定义层 `definition.json`：字段规范 + 大量示例

### 4.1 推荐字段（强烈建议都写）
```json
{
  "id": "calc",
  "description": "简单两数运算（加减乘除），仅管理员可见",
  "parameters": {
    "type": "object",
    "properties": {
      "a": { "type": "number", "description": "第一个数字" },
      "op": { "type": "string", "enum": ["+", "-", "*", "/"], "description": "运算符" },
      "b": { "type": "number", "description": "第二个数字" }
    },
    "required": ["a", "op", "b"]
  },
  "category": "custom",
  "icon": "brain.svg",
  "timeout": 15
}
```

字段说明：
- `id`（必填）：工具 ID，建议与文件夹名一致（例如文件夹 `calc/`，id 也叫 `calc`）
- `description`（强烈建议）：越清楚越好（模型会读它决定是否调用）
- `parameters`（建议）：JSON Schema 风格
  - `type`：固定 `"object"`
  - `properties`：参数字典
  - `required`：必填参数数组
- `category`（可选，默认 custom）：用于管理端分类
- `icon`（可选）：SVG 文件名（来自 `static/icons/`）
- `timeout`（可选，默认 30）：执行超时秒数

> 注意：当前实现里 `required` 以 `parameters.required` 为准；另外顶层 `required` 字段也会被识别，但建议统一写在 `parameters.required`。

---
### 4.2 定义层示例 A：最小可用“回声工具”
文件：`data/custom_tools/echo/definition.json`
```json
{
  "id": "echo",
  "description": "回声：原样返回 text",
  "parameters": {
    "type": "object",
    "properties": {
      "text": { "type": "string", "description": "要回显的文本" }
    },
    "required": ["text"]
  },
  "category": "custom",
  "timeout": 10
}
```

---
### 4.3 定义层示例 B：可选参数（optional）怎么写？
```json
{
  "id": "greet",
  "description": "打招呼：name 可选",
  "parameters": {
    "type": "object",
    "properties": {
      "name": { "type": "string", "description": "名字（可选）" }
    },
    "required": []
  }
}
```
执行层里要记得处理 name 缺失的情况（见执行层示例）。

---
### 4.4 反例：参数 schema 写错导致模型不会传参
反例（`type` 写成 string）：
```json
{
  "id": "bad_schema",
  "parameters": {
    "type": "string",
    "properties": {
      "a": { "type": "number" }
    }
  }
}
```
后果：模型端可能无法正确构造对象参数，调用失败或入参为空。

---
## 5. 执行层 `execution.py`：核心坑点、写法规范、正反例

### 5.1 执行层“模板替换”规则（最关键）
执行层在运行前会做一次模板替换：
- `{a}` → 入参 a 的值
- `{op}` → 入参 op 的值

因此：
- **你写的不是纯 Python，而是 Python + 占位符**
- 任何你写的 `{...}` 都可能被当成占位符解析

---
### 5.2 大坑：字典/集合花括号必须双写 `{{` `}}`

你之前遇到的报错：
```json
{
  "success": false,
  "error": "缺少必填参数: \"'+'\""
}
```
根因就是模板引擎把这段当成“需要一个叫 `+'` 的占位符”：
```py
ops = {'+': operator.add, ...}
```

**正确写法：把字典的 `{}` 变成 `{{}}`**  
示例（正确）：
```py
ops = {{'+': operator.add, '-': operator.sub, '*': operator.mul, '/': operator.truediv}}
```

反例（错误）：
```py
ops = {'+': operator.add}
```

同理：集合 `{1,2,3}`、字典推导式 `{k:v for ...}` 都需要转义，否则会被误当占位符。

---
### 5.3 参数是字符串时，必须自己加引号（或安全编码）

如果参数是字符串：
- `{name}` 替换后会变成裸文本，导致 Python 语法错误

正确（加引号）：
```py
name = '{name}'
```

反例（没引号，容易 SyntaxError）：
```py
name = {name}
```

更稳健的写法（推荐）：用 JSON 序列化来安全注入字符串（避免引号/换行破坏代码）。
```py
import json
name = json.loads({name_json})
```
但这需要你在定义层把参数设计成 `name_json`，且调用时传入 JSON 字符串；新手不建议第一天就这么做。

---
### 5.4 强烈建议：执行层先做“输入校验”，再计算
因为没有强校验，入参可能是空、类型不对、字符串里有奇怪字符。

示例：对 calc 的 op 做严格白名单检查：
```py
a = {a}
op = '{op}'
b = {b}
if op not in ['+','-','*','/']:
    raise ValueError('op must be one of + - * /')
```

反例（危险）：用 eval 解析运算符/表达式
```py
print(eval(f\"{a}{op}{b}\"))  # 不要这么写
```
原因：op 或其他参数可被注入恶意表达式。

---
### 5.5 执行层示例 1：echo（最简单）
文件：`data/custom_tools/echo/execution.py`
```py
text = '{text}'
print(text)
```

---
### 5.6 执行层示例 2：greet（可选参数）
文件：`data/custom_tools/greet/execution.py`
```py
name = '{name}'
name = (name or '').strip()
if not name:
    print('你好！')
else:
    print(f'你好，{name}！')
```
注意：这里用到了 f-string 的 `{name}`，**它是 Python 运行时的花括号，不会参与模板替换**，因为模板替换已经在执行前完成，且我们这里的 `{name}` 是在 Python 代码字符串里，不是模板语法（但仍要注意别在最外层写出新的 `{}` 占位符）。

---
### 5.7 执行层示例 3：calc（你要的三参数计算器）
文件：`data/custom_tools/calc/execution.py`
```py
a = {a}
op = '{op}'
b = {b}
import operator
ops = {{'+': operator.add, '-': operator.sub, '*': operator.mul, '/': operator.truediv}}
print(ops[op](a, b))
```

要点回顾：
- 数字：`a = {a}`、`b = {b}`
- 字符串：`op = '{op}'`
- 字典：`ops = {{...}}`（双花括号）

---
### 5.8 反例：输出过大导致工具失败/截断
反例：
```py
print('A' * 1000000)
```
后果：输出有全局上限（默认 10k 字符左右），你会看到截断或失败提示。  
建议：配合 return.json 的 `truncate`，或自行在执行层控制输出长度。

---
### 5.9 反例：死循环导致超时
```py
while True:
    pass
```
后果：到 `timeout` 后被中断，返回 timeout。

---
## 6. 返回层 `return.json`：怎么做二次处理（例子 + 反例）

返回层是可选的，但强烈建议至少写一个 `truncate`，避免工具输出污染上下文。

### 6.1 支持字段（当前版本）
```json
{
  "truncate": 2000,
  "template": "[tool_id={tool_id}] 输出: {output}"
}
```

- `truncate`：整数，输出超过该长度时截断到前 N 字符
- `template`：字符串模板，可用变量：
  - `{output}`：stdout+stderr 合并后的输出文本（已按 truncate 处理后的版本）
  - `{stderr}`：stderr（如果存在）
  - `{return_code}`：进程返回码
  - `{tool_id}`：工具 id

> `template` 只是影响 `message` 展示，不会改变 `output` 字段本身（除 truncate）。

---
### 6.2 返回层示例：calc 结果更友好
文件：`data/custom_tools/calc/return.json`
```json
{
  "truncate": 2000,
  "template": "[calc] 结果: {output}"
}
```

---
### 6.3 反例：template 写错占位符导致忽略
```json
{ "template": "结果={result}" }
```
因为 `{result}` 不是支持变量，格式化会失败，系统会回退到默认 message（你会看到输出但不按模板展示）。

---
## 7. 备注/展示层 `meta.json`：备注与图标

### 7.1 meta.json 适合放什么？
示例：
```json
{
  "notes": "这是一个示例工具，用于演示参数与模板写法",
  "icon": "calculator.svg",
  "owner": "admin",
  "created_at": "2026-01-05"
}
```

### 7.2 图标怎么选？
目前图标字段会被保存/返回（前端后续可用它显示工具卡片 SVG）。  
建议写成 `static/icons/` 下的文件名，例如：
- `brain.svg`
- `terminal.svg`
- `bot.svg`

你可以用命令查看有哪些图标：
```bash
ls static/icons
```

> 当前 UI 可能还没把 icon 真正渲染出来，但字段已经为未来对接准备好。

---
## 8. 如何创建一个新工具：从 0 到可用（完整示例）

下面示例创建一个工具 `text_len`：计算字符串长度，并在返回层截断。

### 8.1 创建目录
```
data/custom_tools/text_len/
```

### 8.2 definition.json
```json
{
  "id": "text_len",
  "description": "计算文本长度（字符数）",
  "parameters": {
    "type": "object",
    "properties": {
      "text": { "type": "string", "description": "输入文本" }
    },
    "required": ["text"]
  },
  "category": "custom",
  "timeout": 10
}
```

### 8.3 execution.py
```py
text = '{text}'
print(len(text))
```

### 8.4 return.json（可选）
```json
{
  "truncate": 200,
  "template": "[text_len] 字符数: {output}"
}
```

### 8.5 验证
1) 管理端 `admin/policy`：应看到 `text_len`  
2) 管理员聊天：让模型调用 `text_len`，或提示它“用 text_len 计算这段话长度”

---
## 9. 用 API 管理工具（可选）

如果你不想手工改文件，也可以调用后端接口（管理员权限）：

### 9.1 列表
`GET /api/admin/custom-tools`

### 9.2 新增/更新
`POST /api/admin/custom-tools`

请求 JSON 推荐结构（字段越全越好）：
```json
{
  "id": "calc",
  "description": "简单两数运算",
  "parameters": { "type": "object", "properties": {}, "required": [] },
  "required": [],
  "category": "custom",
  "icon": "brain.svg",
  "timeout": 15,
  "execution_code": "print('hello')\n",
  "return": { "truncate": 2000, "template": "输出: {output}" },
  "meta": { "notes": "通过 API 创建" }
}
```

### 9.3 删除
`DELETE /api/admin/custom-tools?id=calc`

---
## 10. 常见问题与排查（非常实用）

### 10.1 admin/policy 看不到工具
逐条检查：
1) 你是否用 **管理员账号** 登录？
2) 工具目录是否存在：`data/custom_tools/<tool_id>/`
3) 是否同时存在两个文件：
   - `data/custom_tools/<tool_id>/definition.json`
   - `data/custom_tools/<tool_id>/execution.py`
4) `definition.json` 是否是合法 JSON（多一个逗号都会解析失败）
5) 刷新页面（强刷），必要时重启后端服务

### 10.2 调用时报 “缺少必填参数: \"'+'\"” 或类似奇怪 key
几乎可以确定是：**执行层里有未转义的字典/集合花括号**。  
把 `{` 改成 `{{`，把 `}` 改成 `}}`（只针对“字典/集合字面量部分”）。

### 10.3 调用时报 SyntaxError（语法错误）
常见原因：
- 字符串参数没加引号：`name = {name}`（错误）
- 参数中包含换行/引号导致代码被截断（建议先做简单输入，或改用更稳健的注入方式）

### 10.4 调用成功但输出很乱/太长
解决：
- 在 `return.json` 里加 `truncate`
- 或执行层自行控制输出

### 10.5 工具能执行但模型不愿意用/总用错
解决：
- 优化 `description`：写清楚输入输出、限制、例子
- `parameters.properties.*.description` 写清楚含义
- `enum` 尽量给明确可选值（例如运算符）

---
## 11. 规范建议（团队协作必看）

### 11.1 命名规范
- 工具 id：小写 + 下划线（例如 `calc`, `text_len`, `db_query`）
- 目录名与 `definition.json.id` 保持一致

### 11.2 输出规范
建议输出尽量“短而确定”：
- 优先输出一个结果值或一段简短摘要
- 需要结构化时输出 JSON（但注意长度）

### 11.3 安全与边界
虽然是私有化环境，也强烈建议：
- 不要在执行层写破坏性命令/删除文件
- 不要 `eval` 用户输入
- 给工具合理的 `timeout`
- 给返回层加 `truncate`

---
## 12. 当前版本限制（请先接受这些现实）
- 仅支持 Python（没有 NodeJS 执行）
- 不提供强校验与沙箱策略自定义（依赖现有容器执行与全局限制）
- 图标字段已预留（`icon`），但前端是否渲染取决于后续 UI 接入

