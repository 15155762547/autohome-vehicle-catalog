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

公司要求采集汽车之家“找新能源”目录的在售与预售，排除停售及其他未售，不按进口身份额外筛选。正式全量范围来自该页面两个状态分页接口的实际成员，不使用品牌接口的模糊“未售”状态推断预售，也不依赖搜索响应中不可靠的 `newenergy` 标记。

2026-10-17 实时目录快照核对为859在售＋38预售，897个不重复车系、6586个不重复配置。赛博越野旅行车属于源站预售目录；纳入依据是公司的统一范围要求与源站目录归属，不是个人指定例外。数字是该时点快照，后续运行必须重新核对。

```bash
uv run autohome-catalog discover --expected-on-sale 859 --expected-presale 38 --run full-directory
uv run autohome-catalog crawl --manifest "$AUTOHOME_DATA_ROOT/full-directory/manifest.json" --run full-catalog
# 若首轮报告缺失，按官网配置页请求补采失败项；不重爬已完成车系。
uv run autohome-catalog repair --run full-catalog
uv run autohome-catalog export-full --run full-catalog
```

全量导出为`完整车型.csv`、`完整配置.csv`和`字段目录.csv`，完整文本保留区间、多个取值、位置、标配／选装符号及条件。数字筛选列不强转这些条件，飞书保留必要的原文对照。全量源站字段先归并，再按300列预算准备扩展批次；新增的品牌专属参数横向展开，只有实际有本批参数的配置才创建对应扩展记录。

```bash
uv run autohome-catalog prepare-full-sync --run full-catalog
# 显式实验Base配置包含profile和允许写入的目标ID；新扩展表ID及批次配置只保存在数据盘。
uv run autohome-catalog sync-full --config "$AUTOHOME_DATA_ROOT/private-config.json" --run full-catalog
```

全量同步复用已有的plan/apply/verify，以来源车系／配置ID去重；同一快照重复计划必须零新增、零更新。现有参数列和类型不改动；新增参数保持源站原文。各表内按源站分组及字段顺序排列，来源和关联列后置，model_id紧随名称。扩展表按配置ID与主表一一关联。旧表及生产表不写入。

目录分页总数、页内数量、重复ID或源站成员变化会报错；清单保留每页URL、时间、哈希及原始响应。采集验证清单证据后，仅取对应车系及配置ID，并再次核对配置在售／预售状态。目录证据随采集运行一起保存；`progress.json` 可查看实时完成数量，结束时 `status.json` 列出覆盖与失败。没有默认全量或后台定时任务。

小样本或品牌专项仍可使用 `crawl --brand ... --series ...`。历史 `--presale-series` 仅用于有独立证据的显式样本，不能把所有状态10或0当作预售。同一个运行目录只允许一次联网采集；重放不联网。

## 参数与输出

`schema/fields.json` 是五品牌137配置、25个历史响应的427项初始中文参数字典（实时特斯拉样本补审后为434项），保留源站大组/分组/原名/单位/原始参数ID。该字典不是全站永久全集。每份响应独立建立titleid映射，按specid匹配具体配置；不能跨年份复用列号。

`model_id` 使用 `AUT-车系ID-BEV/PHEV/EREV/FCEV` 格式，动力类型取自每条配置的能源类型；未识别时留空。FCEV用于源站氢燃料车型，本次为Mirai；[丰田官方说明其为燃料电池电动车](https://global.toyota/en/newsroom/toyota/33558148.html)。车型表保持一车系一行，多动力编号换行列出；trim和配置扩展表每行对应一个编号。编号列在默认视图紧随名称。

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
- `discovery.py`：新能源在售／预售分页目录与证据校验。
- `repair.py`：按网页真实spec页请求补采缺失参数，按源字段身份重映射每个响应的titleid；保持每个配置实际来源。
- `full_export.py` / `full_sync.py`：源站完整CSV导出、300列横向扩展批次和已授权实验Base同步。
- `cli.py`：显式范围的命令行入口。

首次默认并发2、超时20秒、有限重试；可在小样本上测并发1/2/4，依据成功率、延迟和完整性选择。访问限制、验证码、意外跳转会停止后续请求；不使用代理、验证码破解或变更身份绕过访问控制。遇到问题先保留证据，不能把一次完成称为全站完整。
