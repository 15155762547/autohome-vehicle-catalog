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

2026-10-17的人类查表布局采用四张日常表：车系、配置、配置参数扩展、专属配置明细。正常参数按汽车之家顺序分在配置表（基本参数至主动安全，211项）和一张扩展表（其余289项）；932种选装包和133种特色配置改为按配置关联的明细行。首批明细3022条，保留原文和来源项目ID，同名不同ID不直接合并。model_id紧随名称，品牌、车系和配置ID靠前，提供品牌/车系分组以及在售、预售、选装包、特色配置筛选视图。

迁移先保存实时记录、字段、视图、版本和SHA256，再建立替代表；数值条件原文紧邻对应数值，完整数值原文另存隐藏的`参数原文存档`。逐格和双向关联验证后，旧六张参数表改名并移入历史备份文件夹，不删除记录。以下命令要求`migration-dir`中已有经过版本、数量和哈希核对的完整备份（`backup-verification.json`、`backup/*/records.json`、`tables-before.json`）：

```bash
uv run autohome-catalog restructure-parameters --run full-20261017 \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --migration-dir "$AUTOHOME_DATA_ROOT/human-layout-20261017"
```

迁移后的配置标记`layout_version: 2`。旧版`sync-full`及独立列写入会拒绝作用于新版布局，避免重新生成大量选装包列；下一批增量或全量写入须先审查新版字段预算和明细更新计划。下面的横向扩展说明和命令仅供尚未迁移的旧版实验Base使用。

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

旧版布局输出独立列CSV及飞书计划，原始JSON仅用于证据。旧版88个数值参数另有一一对应的“原文”文本列，保留原始短横杠、符号和条件；默认检查视图隐藏这些证据列，需要查错时可显示。参数列优先使用源站原名和单位，仅在重名时加上必要的分组或参数ID，完整来源分组仍保留在字典及字段说明中。新增字段输出审计清单，先审查字典和列预算，再用 `schema-sync --config ...` 仅追加缺失列，已有列类型或关联目标不符会拒绝；不静默丢字段。●显示为“标配（●）”，○显示为“选配（○）”，短横杠保留原符号待字段语义核对，不变成false或0。不计算气囊总数。只有明确纯数值才转数字，缺失留空，无法转换的数值进入审计。

运行目录包含原始响应、URL/时间/SHA256清单、目录包含/排除/待复核清单、逐配置单元格证据、三张独立列CSV、请求统计与异常。源站图片URL附来源页面和配置身份，且代码拒绝图片二进制请求。

旧版飞书采用车型主表、配置主表和一对一配置参数扩展表。主表粒度分别为来源车系ID和配置ID；厂商、尺寸、座位、能源、续航、充电等参数属于具体配置。旧表迁移与试验目标通过仓库外的本地配置提供。生产目标不能写入。所有写入先plan，再apply，再verify；复跑不应新增重复记录。命令细节见 `autohome-catalog --help`。本地目标配置留在仓库外，包含 `purpose: experiment`、显式 `profile`、`base_token`、`allowed_base_token`、`production_base_tokens`、`legacy_tables`、`tables`。`tables` 映射 `models/trims/equipment` 到新表ID；`legacy_tables` 仅保存旧两表ID。代码拒绝向旧表写记录，且不提供删除命令。

```bash
# 把配置放在数据盘上的 private-config.json，实际ID不得提交公开仓库。
uv run autohome-catalog bootstrap --config "$AUTOHOME_DATA_ROOT/private-config.json" --backup /path/on/data-volume/verified-backup
uv run autohome-catalog plan --config "$AUTOHOME_DATA_ROOT/private-config.json" --run tesla-sample
uv run autohome-catalog apply --config "$AUTOHOME_DATA_ROOT/private-config.json" --run tesla-sample
uv run autohome-catalog verify --config "$AUTOHOME_DATA_ROOT/private-config.json" --run tesla-sample
# 再次plan必须零新增、零更新，才能认为同一快照幂等。
```

发生访问限制会同时写入当前运行的 `PAUSED.json`、数据根目录的 `ACCESS_PAUSED.json` 和外接盘固定位置 `.autohome-catalog-control/ACCESS_PAUSED.json`；换运行目录或 `--data-root` 也会被阻止。人工检查原因并明确处理前，不删除暂停标记。

## 文件布局

下面列出仓库全部Git跟踪文件（运行数据、虚拟环境和登录配置位于仓库外）：

```text
autohome-vehicle-catalog/
├── .gitignore                       # 排除缓存、运行数据和本地秘密文件
├── README.md                        # 安装、命令、数据含义及文件导航
├── pyproject.toml                   # Python依赖、命令入口及检查配置
├── uv.lock                          # 固定依赖版本，便于同事复现环境
├── docs/
│   ├── implementation-plan.md       # 重建工程的原始实施方案
│   ├── request-measurements.md      # 采集请求耗时及并发测量记录
│   ├── schema-changes.md            # 中文字段字典与结构变更说明
│   └── verified-sample.md           # 小样本参数验证结果及边界
├── schema/
│   └── fields.json                  # 初始中文字段字典、来源ID和类型
├── src/
│   └── autohome_catalog/
│       ├── __init__.py              # Python包入口说明
│       ├── cli.py                   # 采集、导出、写表和图片任务命令
│       ├── discovery.py             # 发现新能源在售和预售车系清单
│       ├── export.py                # 参数字段映射、CSV导出及原文审计
│       ├── feishu.py                # lark CLI调用、目标保护及参数回读
│       ├── full_export.py           # 全量原始参数导出及字段顺序
│       ├── full_sync.py             # 旧版横向扩展分批同步（新版禁用）
│       ├── human_layout.py          # 两张参数表与专属明细的迁移及视图
│       ├── image_urls.py            # 官图优先，补采其他图库URL及车系封面
│       ├── image_sync.py            # 图片表备份、幂等更新和关联核验
│       ├── images.py                # 最新年款官图解析、URL检查及断点续跑
│       ├── migration.py             # 历史实验表备份检查及新表初始化
│       ├── models.py                # Pydantic响应模型与数据契约
│       ├── parse.py                 # 源站参数、选配符号及配置身份解析
│       ├── repair.py                # 通过真实配置页补采失败参数
│       ├── spider.py                # Scrapy参数采集、并发及证据中间件
│       └── storage.py               # 外接盘边界、原始快照及访问暂停
└── tests/
    ├── conftest.py                  # 隔离测试用挂载盘，避免依赖个人环境
    ├── test_discovery.py            # 目录分页、范围和重复车系检查
    ├── test_export.py               # 字段导出、动力编号及原文审计检查
    ├── test_feishu.py               # 实验表写入保护、差异及回读检查
    ├── test_full_export.py          # 全量字段导出及缺漏检查
    ├── test_human_layout.py         # 人类查表布局、字段和明细迁移检查
    ├── test_images.py               # 官图归属、限流暂停、链接及写表检查
    ├── test_parse.py                # 配置参数及源站符号解析检查
    └── test_storage.py              # 外接盘、来源证据和访问限制检查
```

首次默认并发2、超时20秒、有限重试；可在小样本上测并发1/2/4，依据成功率、延迟和完整性选择。访问限制、验证码、意外跳转会停止后续请求；不使用代理、验证码破解或变更身份绕过访问控制。遇到问题先保留证据，不能把一次完成称为全站完整。

## 最新年款官图URL

图片任务按已验收配置中的 `model_id` 分组，分别选择最新年款。本次范围为1,010组、4,292个候选配置。仅取源站分类53“官图”，优先官图数量最多的同组配置，不足时补同年款、同动力的其他已验收配置；按图片ID及URL去重，每组最多15张。最新年款没有官图时留空，不混用老款或实拍。

```bash
# source 是已完成飞书回读核验的配置快照，位于仓库外。
uv run autohome-catalog images-prepare --run images-20261017 \
  --source "$AUTOHOME_DATA_ROOT/human-layout-20261017/verified-trims.json"
# 小样；同一目录重跑复用已完成组，失败组重新检查，暂停来源仍保持暂停。
uv run autohome-catalog images-collect --run images-20261017 \
  --model-id AUT-5346-BEV --model-id AUT-6762-BEV \
  --model-id AUT-8562-PHEV --model-id AUT-8100-EREV --model-id AUT-5535-BEV
uv run autohome-catalog images-sync --run images-20261017 \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json"
# 继续完整范围；完成后再同步全部结果。
uv run autohome-catalog images-collect --run images-20261017
uv run autohome-catalog images-sync --run images-20261017 \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json"
```

“车型图片”每个model_id一行，字段如下：

| 字段 | 用途 |
|---|---|
| 车型、model_id、品牌 | 人类查找与程序稳定标识 |
| 动力、年款 | 明确图片归属；混合动力车系分别保存 |
| 所属车系 | 关联原车型表，反向入口为“官图组” |
| 图片URL清单 | 同一文本单元格，一行一个裸URL，便于复制 |
| 图片数 | 已提取URL数量，0至15；是否直连验证另看验证方式 |
| 图片类别、验证方式 | 区分官图、外观、内饰等，以及HTTP验证或仅源站提供URL |
| 检查结果、说明 | 满15张、不足15张、无匹配官图、失败或访问受阻及原因 |
| 来源页 | 实际配置官图页；无图时保留车系官图查询页 |
| 检查时间 | 此次源站检查时间（UTC） |

原车型表的列表封面字段保留原值，从默认视图隐藏，改用“官图组”入口。同步前备份车型、图片和视图，按model_id生成增改计划，不清空表；写入后回读URL逐行、数量和双向关联，并检查原车型字段未变化。只向显式配置的实验Base写入图片表，不写已验收参数表及生产表。

图片URL仅发HEAD请求，确认HTTP 200和`image/*`响应类型，不保存图片二进制。网页HTML与来源URL、时间和SHA256保存在外接盘的`sources/`。JSON中`images`只包含通过检查的URL；`problems`保留失败URL和原因。状态“无匹配官图”表示指定最新年款候选已逐一查询且没有匹配照片；解析失败、意外跳转、404、非图片响应不会冒充无图。401、403、429或验证页面会写`pauses/域名.json`并停止该来源的后续请求；暂停同时保存在外接盘固定控制目录`image-source-pauses/`，换运行目录也会加载；保留暂停标记，不能靠换目录或身份继续请求。同一运行可在根目录放`STOP`文件停止新请求，检查后由人工处理暂停。

第三方原始URL不能保证永久可访问。HEAD验证也不能证明飞书点击路径一定正常；当前任务仅通过CLI核对URL写入及回读，不进行浏览器或飞书界面操作。

运行目录的`groups/`保存逐组结果，`images.json`汇总所有已处理组，`summary.json`和`图片采集报告.md`列出满15张、不足、无匹配、失败、受阻及未处理数量。运行数据与登录配置不提交Git。

```python
import json
from pathlib import Path

rows = json.loads(Path('/数据盘/任务目录/images.json').read_text())
by_model = {row['target']['model_id']: row for row in rows}
urls = [photo['url'] for photo in by_model['AUT-5535-BEV']['images']]
# 每张还有 picture_id、spec_id、source_page、http_status、content_type、checked_at。
```


## 放宽后的图片URL补采（当前方式）

2026-10-17用户放宽图片类别要求：官图优先，不足时取汽车之家同一配置的外观、内饰、座椅等图片。仍按1,010个model_id分别保存，最多15个URL。图库配置优先匹配当前最新年款及动力；没有可提取图库时，保留有已验收目录原始快照及SHA256证明的车系封面。封面无法证明具体动力/年款，JSON标注`scope: series`，飞书年款留空、图片类别标注“车系封面”，不冒充对应年款官图。

此命令只请求汽车之家图库HTML，不请求图片域名，既不GET图片也不HEAD图片，保留原有图片域名403暂停标记。`verification: source_page_only`明确表示源站页面提供的URL，未做直连可用性验证；已有HTTP200及图片类型验证的结果保留其验证证据。HTTP200验证不等于永久可用。

```bash
uv run autohome-catalog images-collect-urls --run image-urls-20261017 \
  --official-run "$AUTOHOME_DATA_ROOT/images-20261017" \
  --source-run "$AUTOHOME_DATA_ROOT/full-20261017"
uv run autohome-catalog images-sync --run image-urls-20261017 \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json"
```

补采沿用原“车型图片”表和model_id，不另建重复表。原“官图URL清单”字段改名为“图片URL清单”，增加“图片类别”“验证方式”；每行一个URL，原单元格内容写前备份。图片来源和采集时间保存在JSON中，运行结果及暂停记录留在数据盘，完整采集后再回读全部分组并进行重复运行检查。
