# 汽车之家新能源汽车目录

使用 Python、Scrapy、Pydantic 采集汽车之家在售及已确认预售的新能源汽车。按车系保存身份、按具体配置保存参数；保留原始字段、位置差异、选配及来源证据。图片只保存 URL，不下载文件、不上传附件。

## 安装

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/)，写入飞书另需已授权的 `lark-cli`。`uv.lock` 固定本次依赖版本。

运行目录必须位于已挂载的数据盘，不能在盘缺失时创建同名本机目录。本机示例：

```bash
export AUTOHOME_EXTERNAL_MOUNT='/Volumes/奥睿科1TB'
export AUTOHOME_DATA_ROOT='/Volumes/奥睿科1TB/代码程序/autohome-vehicle-catalog-data'
export UV_CACHE_DIR='/Volumes/奥睿科1TB/代码程序/.autohome-build-cache'
export TMPDIR='/Volumes/奥睿科1TB/代码程序/.autohome-build-tmp'
```

同事在其他机器运行时将这四个路径改为自己的已挂载数据盘；确认挂载后创建缓存及临时目录，再安装和测试。代码不含账号Cookie、飞书目标或内部数据。单元测试模拟挂载边界，不依赖本机盘名或飞书账号。

```bash
# 仅在上面的数据盘已真实挂载、缓存与临时目录已创建后运行。
uv sync --extra dev
uv run pytest
```

## 范围与状态

目录从汽车之家品牌接口发现，并保存筛选选项及每个车系的原始状态；不把新能源搜索页发现数当全站完整性证明，也不按厂商是否进口筛选。

源站把部分状态10和0均归为“未售”。状态10不能直接全部当预售。`--presale-series` 是用户明确确认的预售范围，源站状态及用户指定依据分别保留。其他模糊未售进入复核清单，不进入配置表。`--presale-series` 不能使停售车系重新纳入。

本项目首次验收使用特斯拉四车系：Model 3、Model Y、Model Y L、赛博越野旅行车。其中赛博越野旅行车由用户明确指定纳入，记录这个依据，不宣称源站接口已经标明正式预售价。

```bash
# brand ID 和 series ID 均为汽车之家公开来源编号。
uv run autohome-catalog crawl --brand 133 --presale-series 5535 --run tesla-sample
# 同一个运行目录只允许一次联网采集；重放不联网。
uv run autohome-catalog export --run tesla-sample
```

采集只针对显式给出的品牌；`--series` 可进一步限定本轮车系。没有隐式全站任务或后台定时任务。

## 参数与输出

`schema/fields.json` 是五品牌137配置、25个历史响应的427项初始中文参数字典（实时特斯拉样本补审后为434项），保留源站大组/分组/原名/单位/原始参数ID。该字典不是全站永久全集。每份响应独立建立titleid映射，按specid匹配具体配置；不能跨年份复用列号。

`model_id` 使用 `AUT-车系ID-BEV/PHEV/EREV` 格式，动力类型取自每条配置的能源类型；未识别时留空。车型表保持一车系一行，多动力编号换行列出；trim和配置扩展表每行对应一个编号。编号列在默认视图紧随名称。

配置输出独立列CSV及飞书计划，原始JSON仅用于证据。88个数值参数另有一一对应的“原文”文本列，保留原始短横杠、符号和条件；默认检查视图隐藏这些证据列，需要查错时可显示。参数列优先使用源站原名和单位，仅在重名时加上必要的分组或参数ID，完整来源分组仍保留在字典及字段说明中。新增字段输出审计清单，先审查字典和列预算，再用 `schema-sync --config ...` 仅追加缺失列，已有列类型或关联目标不符会拒绝；不静默丢字段。●显示为“标配（●）”，○显示为“选配（○）”，短横杠保留原符号待字段语义核对，不变成false或0。不计算气囊总数。只有明确纯数值才转数字，缺失留空，无法转换的数值进入审计。

运行目录包含原始响应、URL/时间/SHA256清单、目录包含/排除/待复核清单、逐配置单元格证据、三张独立列CSV、请求统计与异常。源站图片URL附来源页面和配置身份，且代码拒绝图片二进制请求。

飞书采用车型主表、配置主表和一对一配置参数扩展表。主表粒度分别为来源车系ID和配置ID；厂商、尺寸、座位、能源、续航、充电等参数属于具体配置。旧表迁移与试验目标通过仓库外的本地配置提供。生产目标不能写入。所有写入先plan，再apply，再verify；复跑不应新增重复记录。命令细节见 `autohome-catalog --help`。本地目标配置留在仓库外，包含 `purpose: experiment`、显式 `profile`、`base_token`、`allowed_base_token`、`production_base_tokens`、`legacy_tables`、`tables`。`tables` 映射 `models/trims/equipment` 到新表ID；`legacy_tables` 仅保存旧两表ID。代码拒绝向旧表写记录，且不提供删除命令。

```bash
# 把配置放在数据盘上的 private-config.json，实际ID不得提交公开仓库。
uv run autohome-catalog bootstrap --config "$AUTOHOME_DATA_ROOT/private-config.json" --backup /path/on/data-volume/verified-backup
uv run autohome-catalog plan --config "$AUTOHOME_DATA_ROOT/private-config.json" --run tesla-sample
uv run autohome-catalog apply --config "$AUTOHOME_DATA_ROOT/private-config.json" --run tesla-sample
uv run autohome-catalog verify --config "$AUTOHOME_DATA_ROOT/private-config.json" --run tesla-sample
# 再次plan必须零新增、零更新，才能认为同一快照幂等。
```

发生访问限制会同时写入当前运行的 `PAUSED.json`、数据根目录的 `ACCESS_PAUSED.json` 和外接盘固定位置 `.autohome-catalog-control/ACCESS_PAUSED.json`；换运行目录或 `--data-root` 也会被阻止。人工检查原因并明确处理前，不删除暂停标记。

## 工程职责

- `models.py`：Pydantic响应与身份契约。
- `parse.py`：目录范围和参数单元格解析。
- `spider.py`、`storage.py`：Scrapy请求、外接盘证据、限制暂停。
- `export.py`：中文字典、参数展开及审计。
- `feishu.py`、`migration.py`：指定试验表的备份核验、旧表改名、新表创建、计划、写入和回读。
- `cli.py`：显式范围的命令行入口。

首次默认并发2、超时20秒、有限重试；可在小样本上测并发1/2/4，依据成功率、延迟和完整性选择。访问限制、验证码、意外跳转会停止后续请求；不使用代理、验证码破解或变更身份绕过访问控制。遇到问题先保留证据，不能把一次完成称为全站完整。
