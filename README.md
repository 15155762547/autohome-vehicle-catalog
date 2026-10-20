# 汽车之家新能源汽车目录

## 文件布局

当前全部Git跟踪文件及中文用途。日常功能位于主包，历史迁移统一放在`legacy/`；运行数据、登录信息、备份和虚拟环境不提交仓库。

```text
autohome-vehicle-catalog/
├── .gitignore  # 排除缓存、运行数据和本地秘密文件
├── README.md  # 安装、命令、数据含义及文件导航
├── pyproject.toml  # Python依赖、命令入口及检查配置
├── uv.lock  # 固定依赖版本，便于同事复现环境
├── docs/
│   ├── history.md  # 合并的早期方案、结构变更、验收及迁移记录
│   └── request-measurements.md  # 采集请求耗时及并发测量记录
├── schema/
│   ├── fields.json  # 初始中文字段字典、来源ID和类型
│   ├── multi-select-fields.json  # 复合功能多选、颜色及尺寸列排除规则
│   └── single-select-fields.json  # 纯状态单选、规格及原始参数映射
├── src/
│   └── autohome_catalog/
│       ├── __init__.py  # Python包入口说明
│       ├── base_utils.py  # 公共分页读取、限量写入、异步等待和回读校验
│       ├── brochure_cover.py  # 每车型下载并上传一张代表封面、附件回读与断点续跑
│       ├── category_tables.py  # 按汽车之家分类复制参数、验证关联及归档旧宽表
│       ├── cli.py  # 采集、导出、写表和图片任务命令
│       ├── color_sync.py  # 颜色明细备份、图库抽样、迁移、容量回退与关联核验
│       ├── colors.py  # Pydantic颜色解析、稳定编号、价格与基础色归类
│       ├── dealer_prices.py  # 按城市补采经销商报价、断点复用及单列回读
│       ├── discovery.py  # 发现新能源在售和预售车系清单
│       ├── export.py  # 参数字段映射、CSV导出及原文审计
│       ├── feishu.py  # lark CLI调用、目标保护及参数回读
│       ├── full_export.py  # 全量原始参数导出及字段顺序
│       ├── historical_images.py  # 封面车型补官图或授权普通图库，核对动力及年款
│       ├── image_merge.py  # 图片并入车型表、旧表归档与限定字段更新
│       ├── image_sync.py  # 图片JSON读写、幂等更新与历史格式兼容
│       ├── image_urls.py  # 官图优先，补采其他图库URL及车系封面
│       ├── images.py  # 最新年款官图解析、URL检查及断点续跑
│       ├── models.py  # Pydantic响应模型与数据契约
│       ├── multi_select.py  # 复合功能转彩色多选、隐藏原文备份及全量回读
│       ├── number_format.py  # 按真实数字设置显示精度并回读确认数值不变
│       ├── parse.py  # 参数、功能分类、车型全称和原始符号规则
│       ├── repair.py  # 通过真实配置页补采失败参数
│       ├── series_merge.py  # 按源车系合并动力行、保留图片与配置并核验全表
│       ├── spider.py  # Scrapy参数采集、并发及证据中间件
│       ├── storage.py  # 外接盘边界、原始快照及访问暂停
│       └── legacy/  # 一次性历史迁移，和日常功能分开
│           ├── __init__.py  # 历史迁移工具包，日常运行不需要执行
│           ├── boolean_layout.py  # 历史布尔布局编译与选装证据整理
│           ├── boolean_migration.py  # 历史布尔迁移及完整回读流程
│           ├── compact_display.py  # 历史参数名称和换行显示整理
│           ├── full_sync.py  # 历史多扩展表批次编译及写表流程
│           ├── human_layout.py  # 历史四表布局迁移及归档切换
│           ├── migration.py  # 历史实验表初始化与旧表改名
│           ├── model_grain.py  # 历史车型按动力拆分和关联迁移
│           ├── primary_titles.py  # 历史首列车型全称迁移
│           └── single_select.py  # 历史布尔表恢复三态单选
└── tests/
    ├── conftest.py  # 隔离测试用挂载盘，避免依赖个人环境
    ├── test_brochure_cover.py  # 单张封面、图片签名、缓存哈希和原字段保护检查
    ├── test_category_tables.py  # 分类字段类型、数值格式及迁移写入边界检查
    ├── test_colors.py  # 颜色身份、价格、双拼、无资料、分页及迁移边界检查
    ├── test_dealer_prices.py  # 报价、正常无报价、异常响应和更新范围检查
    ├── test_discovery.py  # 目录分页、范围和重复车系检查
    ├── test_export.py  # 字段导出、动力编号及原文审计检查
    ├── test_feishu.py  # 实验表写入保护、差异及回读检查
    ├── test_full_export.py  # 全量字段导出及缺漏检查
    ├── test_historical_images.py  # 历史官图的年款、动力、去重及已删除字段保护检查
    ├── test_image_merge.py  # JSON格式、车型身份保护及合并后无重复写入
    ├── test_images.py  # 官图归属、限流暂停、链接及写表检查
    ├── test_multi_select.py  # 功能标签、选配价格、未知状态及尺寸列防恢复检查
    ├── test_number_format.py  # 整数、小数、空值和限定字段更新检查
    ├── test_parse.py  # 配置参数及源站符号解析检查
    ├── test_series_merge.py  # 车系合并、动力分组图片、错关联及删除边界检查
    ├── test_storage.py  # 外接盘、来源证据和访问限制检查
    └── legacy/
        ├── test_boolean_layout.py  # 布尔语义、位置价格、空值与迁移边界检查
        ├── test_compact_display.py  # 精简后位置、价格、状态及原文保护检查
        ├── test_human_layout.py  # 人类查表布局、字段和明细迁移检查
        ├── test_model_grain.py  # 编号唯一、动力名称及错关联拒绝检查
        ├── test_primary_titles.py  # 品牌前缀去重、缺失组件及主字段修改边界检查
        └── test_single_select.py  # 三态与空值、LED规格和选配价格检查
```

## 当前工程做什么

Python + Scrapy + Pydantic：采集汽车之家“找新能源”目录的在售及预售车系，按具体配置保存参数，保留源页面、时间和响应哈希。图库保存原始URL；另按用户确认，把每车型一张列表封面下载并上传为brochure_cover附件，供飞书画廊或应用展示。

分类布局：车型（models）→ 配置索引 → 28张参数分类表（27个源分类，颜色因单表容量限制分为外观、内饰两表）。2026-10-17目录快照为859在售＋38预售，共897个车系；现有6586款具体配置。车型表调整为一源站车系一行（897行），动力类型为BEV／PHEV／EREV／FCEV多选。2026-10-20重新核对目录为859在售＋39预售，新增Ray7尚待补采；不能把已有897行称为当前898车系已全部更新。数量均有快照时间。

| 想做什么 | 主要文件 |
|---|---|
| 找到在售、预售范围并采集参数 | discovery.py、spider.py、models.py、parse.py |
| 补采失败配置、导出完整参数 | repair.py、export.py、full_export.py |
| 飞书读取、写入保护和回读 | feishu.py、base_utils.py |
| 图片URL采集和同步 | images.py、image_urls.py、historical_images.py、image_sync.py、image_merge.py |
| 单列补经销商报价 | dealer_prices.py |
| 多选显示及数字精度维护 | multi_select.py、number_format.py |
| 按汽车之家分类拆表、核对和切换入口 | category_tables.py |
| 按配置查询颜色、加价和对应图库 | colors.py、color_sync.py |
| 查阅以前如何迁移表格 | docs/history.md、legacy/；日常不执行 |

## 安装与数据目录

需要Python 3.12+、uv；写入飞书另需已授权的lark-cli。依赖版本由uv.lock固定。

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run autohome-catalog --help
```

设置`AUTOHOME_EXTERNAL_MOUNT`为实际挂载的数据盘，`AUTOHOME_DATA_ROOT`为盘内运行目录。数据盘未挂载时程序拒绝创建替代目录。飞书配置保存在运行目录外层的`control/feishu.json`，包含目标白名单和显式profile，不能提交GitHub。

## 日常命令

先发现范围，再采集、补失败项和导出。下面的859/38是已验收快照示例，后续运行应核对源站当时数量，不要为了通过校验硬填旧数量。

```bash
uv run autohome-catalog discover --expected-on-sale 859 --expected-presale 38 --run directory
uv run autohome-catalog crawl --manifest "$AUTOHOME_DATA_ROOT/directory/manifest.json" --run catalog
uv run autohome-catalog repair --run catalog
uv run autohome-catalog export-full --run catalog
```

这些命令不等于已向现行分类表完成增量同步。layout_version>=2会拒绝旧版通用写表；下一批参数入表必须使用审查过的当前分类字段映射，不能重新执行初始化或历史迁移来更新数据。

### 图片

采集阶段按车系、动力和年款分组，每组优先当前年款官图、最多15张；同组按图片ID和URL去重。当前车型表按来源车系ID汇总，图库分组JSON保留各组身份。经确认缺图时可使用同动力历史官图或普通图库，但必须备注实际年款、类别及来源，不混年款或动力。普通图库不能随意取其他车型的图片。

```bash
uv run autohome-catalog images-prepare --run images \
  --source "$AUTOHOME_DATA_ROOT/verified-trims.json"
uv run autohome-catalog images-collect --run images --model-id AUT-5346-BEV
uv run autohome-catalog images-sync --run images \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json"
```

上面的动力分组采集命令保留用于原始素材采集；当前车系布局拒绝直接执行旧 `images-sync`，不能将单动力结果覆盖到整车系。

`images-collect-urls`用于从源页面提取各类图片URL；`historical_images`用于有明确授权范围的缺图补采，两者都支持保留运行证据及断点结果。具体参数可用以下帮助查询：

```bash
uv run python -m autohome_catalog.historical_images --help
```

图片直接保存在models中，内容是JSON字符串数组，一格一组，图片数应等于数组长度。兼容读取函数`image_sync.url_lines`处理JSON、历史换行和Markdown链接包装；普通`json.loads`只负责JSON解析，不会清理Markdown包装。

已知兼容事项：现场图片列已改名为“图片URL清单（json字符串）”，部分现有写入代码仍使用旧名“图片URL清单”。下一次图片同步前需完成列名/field_id映射，不应把列名改回或新建重复列。本次目录整理不改这个独立问题，不运行图片写表。用户删除的“图片检查结果”不重建。

历史及普通图库替换保存在原图片运行目录的`historical-overrides/`，普通同步会保留已接受的替换。说明中的时间和验证方式以实际记录为准；源页面可读不等于图片URL已通过HTTP验证，也不保证永久可访问。

### 经销商报价

```bash
uv run python -m autohome_catalog.dealer_prices \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --root "$AUTOHOME_DATA_ROOT/dealer-quotes" --apply
```

按已存在的配置ID查询、只更新经销商报价。分类布局自动写入“基本参数”，不把报价写入配置索引或历史表。当前查询地区为北京（110100），正数显示“XX万起”，正常零报价显示“暂无报价”；错误响应不能写成暂无报价。报价与地区和时间有关，不代表成交价。备份、响应哈希和回读结果在运行目录中。

## 人怎么使用分类表

1. 在models按品牌、车系和动力找到车型，使用来源车系ID定位，打开“配置版本”。
2. 配置索引一行一个具体配置，打开“基本参数”“座椅配置”“车外灯光”等分类入口；也可以直接进入对应分类表，筛选车型全称、品牌、车系或来源车系ID。
3. 24张普通分类表各6586行，按照相同配置ID对应；特色配置、选装包按项目保存，两表合计3022条明细。外观颜色18954行、内饰颜色8589行，一款配置可以对应多项颜色；没有颜色资料的配置不生成虚构明细。
4. 跨表核对来源配置ID和关联，不用行号对应；品牌、车系、配置名称保留用于独立筛选。

车型表的来源车系ID一行一个，不重复；配置表允许同一车系ID出现多次，用来源配置ID区分具体配置。旧动力编号已从在用表移除，完整映射保存在本地备份。各分类通过“对应配置”关联配置索引；项目明细另以“明细ID”区分，同一配置ID重复属于正常的一对多关系。

分类名称与顺序：基本参数 → 车身 → 发动机 → 电动机 → 电池/充电 → 变速箱 → 底盘转向 → 车轮制动 → 被动安全 → 主动安全 → 驾驶操控 → 四驱/越野 → 驾驶硬件 → 驾驶功能 → 外观/防盗 → 车外灯光 → 天窗/玻璃 → 外后视镜 → 互联/智能化 → 方向盘/内后视镜 → 车内充电 → 座椅配置 → 音响/车内灯光 → 空调/冰箱 → 特色配置 → 外观颜色 → 内饰颜色 → 选装包。最后的选装包是源数据已有分类，保留以免遗漏。

飞书表名不允许半角斜杠，实际表名使用全角“／”，例如“电池／充电”；配置索引里的分类关联名仍保留源分类写法。

普通参数表以车型全称开头，颜色明细以颜色项目开头，保留独立身份字段用于筛选；参数按源站顺序排列，原始值和价格条件仍保留。每表只有一个默认视图。旧主表、扩展和专属明细在全部回读通过后移入“历史备份｜分类拆表20261019”，用于追溯，不作为日常编辑入口。

拆表是一次受限迁移，必须先准备现场备份、完整字段映射与选项定义，再运行；不应用它更新下一批采集数据：

```bash
uv run python -m autohome_catalog.category_tables \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --run "$AUTOHOME_DATA_ROOT/categories-20261019" --publish
```

迁移按稳定ID断点续跑，逐字段回读数据与类型；普通表保持相同配置集合，旧记录不删除。飞书不支持把已有单向关联改为双向，车型入口通过新建双向字段、填入并回读关联后切换，原单向字段隐藏留存。没有完成验证时，新表名称带“分类整理中”，原入口继续保留。运行证据和映射仅在外接盘，不提交业务记录到GitHub。

纯功能状态使用“标配／选配／无”单选；44个复合功能字段使用多选标签，选配条件与价格仍保留。当前字段规则见schema中的single-select-fields.json、multi-select-fields.json，初始解析字典fields.json仍供采集导出使用。单选与多选规则文件不是需要反复执行的迁移任务。

数字列按真实小数位显示，不给整数补零；长度、宽度、高度三个独立列已由用户删除，保留组合长宽高，程序禁止恢复它们。指导价和经销商报价目前仍是文本，不能把文本排序当作数值预算筛选。来源采集时间、报价采集日期和图片检查时间分别代表不同数据的时间。

## 历史工具与验证

旧布尔、旧多扩展表、拆车型、换主字段等流程已归档到legacy。不要为了更新普通参数再跑一次迁移。确需复现历史流程时，先阅读[历史记录](docs/history.md)，再查看显式历史入口：

```bash
uv run autohome-catalog --legacy --help
# 独立迁移模块的位置现在为autohome_catalog.legacy.<模块>
uv run python -m autohome_catalog.legacy.primary_titles --help
```

移动前后保留全部原有测试，增加默认与历史CLI隔离检查；包构建另验证全部模块可导入及字典可加载。测试不直接访问飞书或源站。历史测量见[请求测量](docs/request-measurements.md)，其小样本耗时不能推断当前全站性能。

原始响应、登录信息、实时表格记录、备份与日志不在仓库。采集遇401/403/429、验证页或意外跳转会暂停，不换身份或代理绕过。已核实的单车型图库跳通用首页仅记录该车型入口不可用，不把通用首页的其他车型图片采入。

## 颜色明细：按配置找颜色和加价

“外观颜色”“内饰颜色”一行对应一个具体配置的一项源颜色。从配置索引的对应颜色关联打开，可看到“外观｜快银”等短名称；也可用车型、部位、基础色筛选。品牌、车系名称、配置名称、来源配置ID在最右侧。只保留一个默认视图。

| 字段 | 含义 |
|---|---|
| 颜色项目／车型全称／来源车系ID | 原厂色名及其所属车型，前3列 |
| 部位／单色／双拼 | 外观、内饰及源色值的组成 |
| 基础色／分类状态 | 名称和色值能对应才归类；含混或冲突项标待核对 |
| 加价（元）／价格说明 | 仅正数显示整数加价；源价格0显示空白和未列加价，不代表免费或标配 |
| 图库入口／图库核验状态 | 源配置、部位、颜色的专属链接；无入口不代表该颜色不存在 |
| 颜色资料（配置索引） | 外观内饰都有、仅外观、仅内饰、未提供有效资料；没有资料时不造颜色行 |
| 对应配置 | 双向关联配置索引，完整核对颜色集合 |

完整色值、原厂名称、源颜色编号、原始顺序、单项原文JSON和来源时间默认隐藏，整份原文仍在归档原表。颜色明细以`颜色明细ID`为行键，不能用配置ID去重。链接来源ID不同的同名色分别保留；无来源ID时按名称与有序色值的稳定哈希识别。

```bash
# CONFIG和COLOR_RUN沿用外接盘配置及本轮运行目录；所有飞书请求显式使用shengxu。
python -m autohome_catalog.color_sync --config "$CONFIG" --root "$COLOR_RUN" --action prepare
python -m autohome_catalog.color_sync --config "$CONFIG" --root "$COLOR_RUN" --action sample
python -m autohome_catalog.color_sync --config "$CONFIG" --root "$COLOR_RUN" --action publish
```

`prepare`备份现场并对账原文、生成样例；`sample`只取图库HTML验证身份，不下载图片；`publish`暂存、逐项回读、核对双向关联后切换入口。原表归档，不删除。相同运行目录可中断续跑；按稳定ID新增或更新，值相同的行不重复写入。源表、父配置身份或其他参数发生变化时停止核对。

优先单表；只有源服务明确报告表内行数上限时才回退为“外观颜色”“内饰颜色”两表，索引分别关联。未确认的错误不当作容量问题，不购买扩容、不截断记录。发生401／403／429或验证页时停止该来源的页面抽样，并记录受阻；不更换身份重试。抽样结果仅适用于抽中的链接，其余保持“结构及身份匹配（未实测）”。

外接盘`colors-export.json`是可供程序读取的明细导出，`颜色明细验收.md`记录本轮统计、待分类数量和图库抽样结果：

```python
import json
from pathlib import Path

payload = json.loads(Path("colors-export.json").read_text())
by_color_id = {row[payload["row_key"]]: row for row in payload["items"]}
# 同一个来源配置ID可以对应多个颜色明细ID，不能覆盖为一行。
```

本轮源数据共27543项：14067项提供专属图库链接，13476项未提供。全部提供的URL通过结构与配置／部位身份核对，14个代表链接另做真实HTML页面身份抽样；其余不声称实测。基础色归类有7663项待核对，原厂色名与完整色值均保留，基础色空白不表示缺失源颜色。

## 车型代表封面附件

models的`brochure_cover`是附件类型，每个来源车系ID恰好一张汽车之家车系列表封面，放在车系名称旁边。可供后续画廊或飞书应用选择为封面来源。车系合并保留封面附件，多图JSON和图片数按车系去重汇总，配置关联取原集合的并集。

封面按已有且已验收的列表URL下载，检查响应、文件签名、MIME和SHA256；同一URL只下载一次，不随机从多图里选内饰或局部图。封面用于车系展示，不能据此确认某年款、配置、动力或具体颜色。保留原水印和内容。

```bash
uv run python -m autohome_catalog.brochure_cover \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json" \
  --root "$AUTOHOME_DATA_ROOT/brochure-20261020" --roundtrip
```

车系布局可重复传`--series-id 5346`先跑小样，使用新的运行目录，不能混用旧动力粒度的备份。每次只向空封面单元格上传一个附件；重跑对照文件名中的源图哈希、文件大小和token，已有匹配附件即跳过，不重复追加。发现不同附件则停止，不覆盖人工内容。401／403／429或验证页暂停源站请求，不更换身份绕过。

上传使用飞书原生附件CLI，同表串行。下载缓存、附件下载回验、原表备份、运行JSON与Markdown验收报告都留在外接盘，不提交GitHub。文件缓存放在运行配置同级的`brochure-20261020-files/`目录，满足CLI相对路径要求。

## 车系身份与动力合并

- 车型以“来源车系ID”唯一识别，不因动力重复建立车型。配置以“来源配置ID”唯一识别，参数、颜色仍通过具体配置关联。
- 日常表中不再维护旧 `AUT-车系ID-动力` 编号；完整旧编号和记录映射保存在外接盘迁移备份。参数分类的来源车系ID从对应配置自动取得，导出时仍能取得该值。
- 单张 `brochure_cover` 保留原附件；车系图库为各动力图片URL去重并集，图片数可能超过15。隐藏字段“图库分组JSON”保留动力、年款和来源说明，便于后续按动力展示。
- 迁移先备份所有受影响表，再调整配置关联，回读通过后移除113条重复车型；保留全部原配置记录。当前布局禁止重放旧动力拆分、分类初始化、图片并表或颜色迁移任务。

迁移工具（只对已审查的旧1010行快照使用；完成后同一运行目录重复调用不再写入）：

```bash
python -m autohome_catalog.series_merge --config "$FEISHU_CONFIG" --root "$MERGE_RUN"
```

封面命令在车系布局下按“来源车系ID”读取；指定单车可用 `--series-id`。报价及数字显示维护也按来源车系ID读取车型，不恢复已移除的动力编号。
