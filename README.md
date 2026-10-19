# 汽车之家新能源汽车目录

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
│   ├── boolean-fields.json          # 历史布尔字段、原参数对应及跨表布局
│   ├── fields.json                  # 初始中文字段字典、来源ID和类型
│   ├── multi-select-fields.json      # 复合功能多选、颜色及尺寸列排除规则
│   └── single-select-fields.json     # 纯状态单选、规格及原始参数映射
├── src/
│   └── autohome_catalog/
│       ├── __init__.py              # Python包入口说明
│       ├── boolean_layout.py        # 原始功能拆分、选装证据及布尔字段预算
│       ├── boolean_migration.py     # 布尔表分阶段入库、逐格核验及归档切换
│       ├── cli.py                   # 采集、导出、写表和图片任务命令
│       ├── compact_display.py       # 参数短名称、符号换行与原值备份回读
│       ├── dealer_prices.py         # 按城市补采经销商报价、断点复用及单列回读
│       ├── discovery.py             # 发现新能源在售和预售车系清单
│       ├── export.py                # 参数字段映射、CSV导出及原文审计
│       ├── feishu.py                # lark CLI调用、目标保护及参数回读
│       ├── full_export.py           # 全量原始参数导出及字段顺序
│       ├── full_sync.py             # 旧版横向扩展分批同步（新版禁用）
│       ├── human_layout.py          # 两张参数表与专属明细的迁移及视图
│       ├── image_urls.py            # 官图优先，补采其他图库URL及车系封面
│       ├── image_merge.py           # 图片并入车型表、旧表归档与限定字段更新
│       ├── historical_images.py     # 封面车型补官图或授权普通图库，核对动力及年款
│       ├── image_sync.py            # 图片JSON读写、幂等更新与历史格式兼容
│       ├── images.py                # 最新年款官图解析、URL检查及断点续跑
│       ├── migration.py             # 历史实验表备份检查及新表初始化
│       ├── model_grain.py           # models按动力拆分、保留历史关联及逐条核验
│       ├── models.py                # Pydantic响应模型与数据契约
│       ├── multi_select.py          # 复合功能转彩色多选、隐藏原文备份及全量回读
│       ├── number_format.py         # 按真实数字设置显示精度并回读确认数值不变
│       ├── parse.py                 # 源站参数、选配符号及配置身份解析
│       ├── primary_titles.py        # 主字段展示车型全称、保留拆分原值及逐格核验
│       ├── repair.py                # 通过真实配置页补采失败参数
│       ├── single_select.py         # 从原始备份批量恢复紧凑单选并核验关联
│       ├── spider.py                # Scrapy参数采集、并发及证据中间件
│       └── storage.py               # 外接盘边界、原始快照及访问暂停
└── tests/
    ├── conftest.py                  # 隔离测试用挂载盘，避免依赖个人环境
    ├── test_discovery.py            # 目录分页、范围和重复车系检查
    ├── test_export.py               # 字段导出、动力编号及原文审计检查
    ├── test_feishu.py               # 实验表写入保护、差异及回读检查
    ├── test_boolean_layout.py       # 布尔语义、位置价格、空值与迁移边界检查
    ├── test_compact_display.py       # 精简后位置、价格、状态及原文保护检查
    ├── test_dealer_prices.py         # 报价、正常无报价、异常响应和更新范围检查
    ├── test_full_export.py          # 全量字段导出及缺漏检查
    ├── test_human_layout.py         # 人类查表布局、字段和明细迁移检查
    ├── test_historical_images.py    # 历史官图的年款、动力、去重及已删除字段保护检查
    ├── test_image_merge.py          # JSON格式、车型身份保护及合并后无重复写入
    ├── test_images.py               # 官图归属、限流暂停、链接及写表检查
    ├── test_model_grain.py           # 编号唯一、动力名称及错关联拒绝检查
    ├── test_multi_select.py          # 功能标签、选配价格、未知状态及尺寸列防恢复检查
    ├── test_number_format.py         # 整数、小数、空值和限定字段更新检查
    ├── test_parse.py                # 配置参数及源站符号解析检查
    ├── test_primary_titles.py       # 品牌前缀去重、缺失组件及主字段修改边界检查
    ├── test_single_select.py        # 三态与空值、LED规格和选配价格检查
    └── test_storage.py              # 外接盘、来源证据和访问限制检查
```

使用 Python、Scrapy、Pydantic 采集汽车之家在售及已确认预售的新能源汽车。按车型（车系＋动力）保存身份、按具体配置保存参数；保留原始字段、位置差异、选配及来源证据。图片只保存URL，不下载文件、不上传附件；当前直接保存在models的“图片URL清单”文本列中，内容为纯URL的JSON数组字符串。

当前采用**紧凑布局**：配置主表＋一张参数扩展表，各6,586行；专属配置明细3,022行。纯状态为“标配／选配／无”单选，未披露留空；44个复合功能字段使用彩色多选，规格和价格保留。每张表只有默认视图。下方布尔及早期分行文字布局为历史记录，最新规则见文末“复合功能多选”。

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

`model_id` 使用 `AUT-车系ID-BEV/PHEV/EREV/FCEV` 格式，动力类型取自每条配置的能源类型；未识别时留空。FCEV用于源站氢燃料车型，本次为Mirai；[丰田官方说明其为燃料电池电动车](https://global.toyota/en/newsroom/toyota/33558148.html)。2026-10-18起车型表按车系＋动力拆成1,010行，一行一个唯一model_id；名称如“秦PLUS（BEV）”“秦PLUS（PHEV）”，原始车系名另列保留。来源车系ID仍只有897种，可重复，不能作为车型表唯一键；trim和配置扩展表每行对应一个具体配置及其model_id。编号列在默认视图紧随名称。

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



首次默认并发2、超时20秒、有限重试；可在小样本上测并发1/2/4，依据成功率、延迟和完整性选择。访问限制、验证码、意外跳转会停止后续请求；不使用代理、验证码破解或变更身份绕过访问控制。遇到问题先保留证据，不能把一次完成称为全站完整。

## 最新年款官图URL

图片任务按已验收配置中的 `model_id` 分组，分别选择最新年款。本次范围为1,010组、4,292个候选配置。基础官图入口仅取源站分类53“官图”，优先官图数量最多的同组配置，不足时补同年款、同动力的其他已验收配置；按图片ID及URL去重，每组最多15张。

2026-10-18确认放宽：对于目前只有车系封面的车型，允许补同动力的历史官图。补采入口按历史年款从新到旧检查，使用源站参数核对具体配置的能源类型；选定一个年款后，仅在该年款中补齐最多15张，不把不同历史年款混在一组。“图片年款”填写图片实际年款，“图片说明”同时交代当前车型最新年款、图片历史年款、配置ID与仅供展示参考。用户删除的“图片检查结果”不重建、不写入，状态原因写进已有备注。

```bash
# 只修复已有车型中的车系封面兜底记录，支持断点继续；数据保存在仓库外。
uv run python -m autohome_catalog.historical_images \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --run "$AUTOHOME_DATA_ROOT/historical-images-20261018" \
  --prior "$AUTOHOME_DATA_ROOT/image-urls-20261017" --apply
```

补采先备份当前记录，按model_id更新已有图片列并回读，保留其他值和关联；不会新建表、视图或字段。已核实的HTTP307跳转至通用图库页`/cars/imglist`仅记为该车型入口不可用，不跟随跳转，继续其他车型。其他意外跳转、401/403/429或验证页面仍暂停来源并保留未完成数量。每个URL只从源页面提取，不请求图片文件；源页面可读不等于图片URL已通过HTTP验证。已接受的历史替换保存在原图片运行目录的`historical-overrides/`，后续普通同步不会退回旧封面。

2026-10-19继续放宽：对上述31组官图入口不可用的车型，允许补普通图库中的外观、内饰等图片，备注说明来源并非官图专属。仍核对车系、具体配置、能源类型及实际年款，最多15张，不混年款或动力。此前3组动力待核实和1组无同动力官图的记录按用户要求保留原样，不在本批中处理。通用图库首页不能作为任意车型的图片来源，程序只读取指定车系、配置的图库页面。

```bash
uv run python -m autohome_catalog.historical_images \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --run "$AUTOHOME_DATA_ROOT/general-images-20261018" \
  --prior "$AUTOHOME_DATA_ROOT/image-urls-20261017" \
  --general-history "$AUTOHOME_DATA_ROOT/historical-images-20261018" --apply
```

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

以下为历史独立图片表的结构，当前已合并至models，见[图片并入车型表](#图片并入车型表2026-10-18)：

| 字段 | 用途 |
|---|---|
| 车型、model_id、品牌 | 人类查找与程序稳定标识 |
| 动力、年款 | 明确图片归属；混合动力车系分别保存 |
| 所属车型 | 按model_id关联当前车型表，反向入口为“图片组”；旧“所属车系”关联保留隐藏 |
| 图片URL清单 | 历史为换行文本；现为纯URL的JSON数组字符串 |
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

补采最初沿用独立“车型图片”表和model_id，原“官图URL清单”改名为“图片URL清单”，增加“图片类别”“验证方式”；当时每行一个URL。当前图片直接写入models，URL为JSON数组字符串，原独立表归档。图片来源和采集时间保存在JSON中，运行结果及暂停记录留在数据盘，完整采集后再回读全部分组并进行重复运行检查。

## models一行一个model_id（2026-10-18）

原models的897行按车系汇总，曾把同车系多个model_id换行放在一格。现行models为1,010行，唯一键为`model_id`，显示名称使用“原车系名称（动力代码）”，例如秦PLUS（BEV）和秦PLUS（PHEV）。`原车系名称`、`来源车系ID`保留源站车系身份；`动力类型`提供中文解释。来源车系ID可重复，关联和程序读取以model_id为准。

迁移先逐表备份记录、字段、视图、版本和SHA256，核对每条配置的能源类型、model_id、来源车系ID及图片分组。只在所有配置和图片都能唯一归组时创建新models。原897行表改名后移入历史备份文件夹，不删记录、不改变历史关联。当前配置和图片增加双向“所属车型”字段关联新models，旧“所属车系”保留隐藏；配置主记录ID、全部参数、图片URL、参数扩展和专属明细均保留。反向关联按每个车型的完整配置清单一次核对、补齐；迁移后逐字段核对保护值及全部正反向关联，再发布新表。

```bash
uv run autohome-catalog split-models --run model-grain-20261018 \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" --prepare-only
uv run autohome-catalog split-models --run model-grain-20261018 \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json"
```

运行目录的`plan.json`保存每个model_id的配置和图片记录映射，`model-record-map.json`保存新飞书记录ID，`verification.json`保存逐条核验结果。重复执行复用已建立的模型记录和关联，不重复建表或新增记录。运行配置切换`key_fields.models`为`model_id`，并指定`model_relation_field: 所属车型`与`models_image_field: 图片组`。图片同步按实时models的model_id解析父记录，不能把历史采集JSON里的旧model_record直接写回新关联。


## 参数显示精简（2026-10-18）

现有参数表保持行列数量、字段ID、类型、顺序和关联，仅修改中文显示名称与参数文本。`标配（●）`精简为`●`，`选配（○）`精简为`○`，同一格的多项功能分行显示；选装价格、适用位置、方向数和限制条件保留。原文只写标配时仍只显示●，不推断子项；空白和短横杠保持原值。来源字段、“原文”列和参数原文存档不修改。不增加主题表或分类视图。

短列名对应原字段的完整清单见`compact_display.py`中的`ALIASES`，原字段说明保留来源原名。运行目录的原始记录、字段定义、视图和SHA256用于回溯；`display-plan.json`记录完整转换结果，`verification.json`记录逐项回读。显示文本经过简化，不替代原始采集JSON证据。

下面的迁移命令仅接受已经完成记录、字段、视图备份及版本一致性校验的外接盘目录；不直接接受空目录。重复执行仅补写与备份或预期值一致的单元格，遇到其他编辑会停止。

```bash
uv run python -m autohome_catalog.compact_display \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --run "$AUTOHOME_DATA_ROOT/compact-display-20261018"
```


## 历史方案：真正布尔字段（2026-10-18）

此方案已由下方紧凑单选布局替代；本节保留用于解释历史文件，不要在当前Base重跑布尔迁移。

现行规范采用方案A：`true`表示标配，`false`表示选装或明确未配；未披露保留空值，不推断为false。选装项目、价格、方向数、位置和其他条件保存在关联明细。源站只给整体状态时只转换整体字段，具体子功能保持未披露。飞书字段类型为`checkbox`，界面显示复选框；JSON中的状态是布尔值，不是`"true"`/`"false"`文本，不使用option作为第三种状态。

复合功能按原始响应的子项拆列，品牌、尺寸和数值仍保留原类型。原始普通参数槽位按源站顺序对应到新列，映射见`schema/boolean-fields.json`。参数分成3张表，每张覆盖相同的6,586条配置，仅保留默认表格视图。通过来源配置ID逐行对应，model_id继续关联同一个车型；车型表的当前入口保持“配置版本”，历史文本链接改名为“历史配置版本”并隐藏。原来的文本参数表和专属明细完整归档，禁止直接把旧text列强转checkbox。

“专属配置明细”和“专属配置明细2”保留原特色配置、选装包和新增选装参数明细。实际写入触发单表记录数上限后，按完整model_id分成两张，每张低于18,000行，同一车型的明细集中在同一张表；`是否标配`为真正布尔字段，`选装价格`保留源站金额原文。`类别`是明细业务分类，不是布尔状态。采集原文、历史表、来源JSON和隐藏的原文证据不作为标准化状态字段。

7类同时包含状态和规格的参数保留规格文本，并增加相邻“标配”布尔列：雾灯、雨刷、遮阳帘、显示屏和氛围灯。总计409列功能状态，规格未披露时不编造尺寸或类型。

必须用`--format json`回读以保留checkbox的空值。本机CLI的NDJSON导出会把空checkbox规范化成false，不可用于区分未披露和明确未配。飞书“未勾选/等于false”筛选也可能同时包含空值；程序读取时用`value is False`与`value is None`区分。

```bash
uv run python -m autohome_catalog.boolean_migration \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --run "$AUTOHOME_DATA_ROOT/boolean-20261018" --prepare
uv run python -m autohome_catalog.boolean_migration \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --run "$AUTOHOME_DATA_ROOT/boolean-20261018"
```

此迁移使用已完成版本一致性校验的全量备份。`boolean-plan.json`保存子项状态和选装证据，`typed-tables.json`、`typed-details.json`保存目标值，`migration-state.json`支持未完成阶段续跑，`verification.json`仅在全部数据和正反向关联核验并切换配置后生成。写入失败先按稳定键回读，再补缺失记录，不能盲目重放新增请求。最终表回读为JSON字典，保留真正的布尔值及空值。程序读取参数需要合并当前主表和配置文件`boolean_extension_tables`列出的两张扩展表，不能只读取`tables.equipment`。明细需要遍历`boolean_detail_tables`，不能只读取`tables.special`。选装金额保留源站报价原文；同一来源项目的整体报价可能被多个拆分子项引用，不能直接把明细金额逐行相加。


同事读取本次合并交付文件（运行数据在外接盘，不提交Git）：

```python
import json
from pathlib import Path

payload = json.loads(Path("boolean-configurations.json").read_text())
for row in payload["configurations"]:
    for name in payload["boolean_fields"]:
        value = row[name]
        assert type(value) is bool or value is None
        # 用 is True / is False / is None 区分标配、非标配与未披露。
```

`boolean-option-details.json`通过`来源配置ID`关联同一配置，保留选装项目与价格；`delivery-verification.json`记录合并后的行列数量及布尔类型检查。


## 紧凑单选（2026-10-18）

使用布尔化以前的哈希验证备份及原始响应重新映射，不将旧false统一改成选配。186个纯功能字段使用单选，选项固定为标配、选配、无，未披露留空。驾驶模式、座椅调节、APP功能等复合字段恢复简短分行文字，保留每个子项状态和位置、方向数、价格；不再为每个子项展开一列。

9个状态与规格混用的字段增加相邻状态列。近光灯可以表示为“光源=LED、状态=标配、选配规格=激光（10000元）”；只有同时存在标配和选配规格时才建立必要的选配规格列。只有选配规格时主规格配合“选配”状态表示。纯状态字段的报价另存`选配价格说明`，不在单选选项中拼价格。数值条件原文仍保留在原文证据列，默认隐藏，需要时显示核对。

参数恢复为两张表，每张6,586行；510个原字段槽位均有映射，见`schema/single-select-fields.json`。普通参数的24,826条选配信息回到对应参数字段及原文证据中；专属配置表恢复原有3,022条选装包/特色配置明细，因此明细行数减少不代表删掉选配数据。旧布尔表归档保留，车型和图片表的记录身份保持。

```bash
uv run python -m autohome_catalog.single_select \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --run "$AUTOHOME_DATA_ROOT/single-select-20261018" --prepare
uv run python -m autohome_catalog.single_select \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --run "$AUTOHOME_DATA_ROOT/single-select-20261018"
```

批量输入为运行目录的`conversion.json`及`字段映射.json`。迁移只创建登记的新表，按稳定配置ID入库；逐格检查字段值和类型、核验正反向关联后切换车型入口。以表版本号完全一致为条件复用已核验备份，发生变化的表重新通过CLI读取，不重复全量回读未变化的历史表。断点状态保存在`migration-state.json`，已完成重跑直接返回核验结果。

当前配置读取`tables.trims`、`parameter_extension_tables`与`detail_tables`。飞书API中单选单元格是单项列表（例如`["标配"]`），交付JSON将这195个状态列规范为字符串或null；其他选项字段保持原类型。`single-select-configurations.json`保存配置，`single-select-details.json`保存3,022条独立明细，`source-coverage.json`记录全部选配项目及价格的对应核验。运行数据和登录配置不提交Git。


## 图片并入车型表（2026-10-18）

两表按唯一model_id严格一一对应后，将1,010组、12,956个图片URL及元数据写入现有models记录；不创建新车型，不复制车型名、品牌、动力，不改变配置关联或参数表。新增图片年款、图片URL清单、图片数、图片类别、图片验证方式、图片检查结果、图片说明、图片采集来源页、图片检查时间共9列。默认视图只把URL清单和图片数放在右侧，其余图片信息可按需显示；旧封面和图片组关联保留但隐藏。

`图片URL清单`仍是文本字段，但值为JSON数组字符串：`["https://car2.autoimg.cn/a.jpg", "https://car3.autoimg.cn/b.jpg"]`。元素必须是纯URL；解析时不按换行拆分。`url_lines`兼容历史换行、Markdown链接和JSON格式，写入统一使用JSON。图库类别和验证方式沿用源数据，没有重新访问图片域名。

```python
import json
urls = json.loads(model_row["图片URL清单"])
```

迁移命令（同一已完成运行目录重跑不会重复写入）：

```bash
uv run python -m autohome_catalog.image_merge \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --run "$AUTOHOME_DATA_ROOT/merge-images-20261018"
```

合并前备份两表记录、字段及配置；核对唯一model_id与原图片关联，新增图片列后批量更新，回读核验全部URL顺序、图片数和元数据，确认原车型记录ID及全部旧字段保留、参数表未变，再归档原图片表。备份保存在数据盘，不删除历史数据。当前配置为`image_storage: models_json`，`tables.images`移除，原图片表ID保存在`archived_images_table`。

原`images-sync`命令继续可用：合并模式只允许更新models中的9个图片字段，不能创建车型或独立图片表。按model_id定位既有记录；同步前后检查原车型字段及配置关联，图片未变化时不重复写入。图片表已归档，后续读取直接取`tables.models`，不再读取旧图片表。

## 数字显示精度

数字列按当前全列数据设置最少必要的小数位：全为整数设为0位，有真实小数则保留所需位数（飞书最多4位），全空列保持原设置。飞书按整列统一设置，因此混有整数与小数的列中，整数仍可能显示补零。此命令只更新数字字段的显示精度，不写记录、不转成文本；备份并回读核对全部数字及记录ID。

```bash
uv run python -m autohome_catalog.number_format \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --root "$AUTOHOME_DATA_ROOT/number-format-新时间戳" --apply
```

去掉`--apply`只检查并生成计划。每次使用新的运行目录保留备份，输出`数字显示调整.md`及JSON审计文件。

## 经销商报价补采

参数接口的“暂无报价”不能代表网页没有经销商报价。补采使用网页另行调用的`/web-main/car/spec/getspecinfo`接口，按已有配置ID查询`askprice.newsprice`，当前统一北京（cityid=110100），与单配置试采一致。正数显示为“14.58万起”，正常响应为0显示“暂无报价”；请求失败、缺少配置或无效价格不当作无报价，保留原值并记录问题。报价与地区、采集时间有关，不代表最终成交价。

```bash
uv run python -m autohome_catalog.dealer_prices \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --root "$AUTOHOME_DATA_ROOT/dealer-full-新时间戳" --apply
```

每批查询50个已有配置，串行限速。原始响应、地区、时间及SHA256留在外接盘；相同运行目录中断后可继续，复用前核对配置ID和响应哈希。只更新trim中已有的“经销商报价”字段，不新建记录、字段或视图；写后回读全部报价及配置身份。HTTP 401/403/429或验证页面会暂停来源。最终结果为`经销商报价交付说明.md`及JSON，运行数据不提交GitHub。

## 首列车型全称

配置、参数扩展和专属明细的主字段显示“车型全称”：品牌＋车系＋配置。若车系已以品牌开头，只保留车系原文，例如问界＋问界M9成为问界M9；特斯拉＋Model 3成为特斯拉 Model 3。源品牌的空格分隔别名也参与前缀判断，例如AITO 问界＋问界M9成为AITO 问界M9。只在显示名中去除明确重复的品牌，不在原始名称里删字。品牌、车系名称、配置名称均保留并显示。

CLI无法直接更换主字段或设置冻结区域，因此保留原主字段ID用于全称展示，将原主字段的数据完整保存到同名新增字段：配置/扩展为“配置名称”，专属明细为“品牌”。每表增加一列，不新增表。源字段语义和全部记录/关联ID保留，但这三个源字段的field_id改为新增列ID；直接按旧field_id读取的调用方应使用迁移目录`state.json`映射，不能将其误认为品牌/配置原值。按字段名读取不变。源站“车型名称”原文另行保留，不强行修正源站特殊命名。

执行前在外接盘备份三个表的完整记录、字段和默认视图到同一运行目录，包含`metadata-before.json`及`trims-before.json`、`equipment-before.json`、`special-before.json`；然后：

```bash
uv run python -m autohome_catalog.primary_titles \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --root "$AUTOHOME_DATA_ROOT/title-primary-20261018"
```

同目录支持按已有字段和实际值续跑。写入仅限全称及原主字段副本，完整回读确认原有字段值、记录ID和链接未变；专属明细把项目名称放全称后面，同一配置的多个项目仍以明细ID区分。冻结额外列、列宽和行高需在界面设置；命令不修改这些设置。

## 复合功能多选

44个复合功能字段改成原生彩色多选：配置主表13个、参数扩展31个。包括驾驶模式、主副驾调节、接口、语音控制、APP远程、手机互联、座椅功能、后视镜功能等；轮胎规格、数字、图片JSON及既有纯状态单选保持原类型。

- `运动 标配`变为`["运动"]`；`标准/舒适`保留为一个选项，不按标签内部斜杠拆分。
- `按摩 选配（3000元）`变为`["按摩（选配，3000元）"]`，不把可选装当作标配。
- 明确无显示`["无"]`；子项明确无保留`副（无）`等标记；未披露为空列表/空值。
- 源站只说支持或可选装、未列出子功能时，保留“支持（未列明功能）”或选配及价格标记，不编造具体功能。
- 整项`价格条件：…`独立保留为说明标签，不把组合价格分摊到某个功能。说明标签不是配备的功能。
- 同一选项去重，源站原次序、重复项和全部文字仍保存在备份。读取端应按数组处理，不再按换行文本拆分。

每张参数表只新增一个默认隐藏的“多选转换原文JSON”文本字段，按来源配置ID保存本表被转换字段的完整原文；先写备份并全量回读，再更换类型和写入标签。字段ID保持，默认参数顺序保持，不新建表或视图。最后对全部原字段及关联做回读，非目标参数保持原值。

先在外接盘备份两张参数表完整记录与默认视图，文件为`metadata-before.json`、`trims-before.json`、`equipment-before.json`；然后分检查和执行两步：

```bash
uv run python -m autohome_catalog.multi_select \
  --root "$AUTOHOME_DATA_ROOT/multi-select-20261018"
uv run python -m autohome_catalog.multi_select \
  --root "$AUTOHOME_DATA_ROOT/multi-select-20261018" \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" --apply
```

不能识别的原文保持不改，记录到`skipped.json`；同目录支持按备份和当前类型续跑，不重建记录。原文与标签对应关系在运行目录`plan.json`，公开字段规则在`schema/multi-select-fields.json`。

用户明确删除独立的“长度(mm)”“宽度(mm)”“高度(mm)”三列，保留“长*宽*高(mm)”合并项。代码不再生成这三列，CLI字段/表创建及字段改名也会拦截恢复它们；原始源站响应仍完整保存，不能把这三列的有意省略当作缺列而自动补回。
