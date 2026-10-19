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
│       ├── cli.py  # 采集、导出、写表和图片任务命令
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

Python + Scrapy + Pydantic：采集汽车之家“找新能源”目录的在售及预售车系，按具体配置保存参数，保留源页面、时间和响应哈希。图片只保存原始URL，不下载图片文件或上传附件。

当前四表：车型（models）、配置主表（trims）、配置参数扩展、专属配置明细。2026-10-17目录快照为859在售＋38预售，共897个车系；按动力拆成1010个唯一model_id。主表和扩展各6586行，专属明细3022行。数量是已验收快照，不是实时全站数量。

| 想做什么 | 主要文件 |
|---|---|
| 找到在售、预售范围并采集参数 | discovery.py、spider.py、models.py、parse.py |
| 补采失败配置、导出完整参数 | repair.py、export.py、full_export.py |
| 飞书读取、写入保护和回读 | feishu.py、base_utils.py |
| 图片URL采集和同步 | images.py、image_urls.py、historical_images.py、image_sync.py、image_merge.py |
| 单列补经销商报价 | dealer_prices.py |
| 多选显示及数字精度维护 | multi_select.py、number_format.py |
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

这些命令不等于已向现行四表完成增量同步。现有layout_version=2会拒绝旧版通用写表；下一批参数入表必须使用审查过的当前字段映射，不能重新执行初始化或历史迁移来更新数据。

### 图片

按model_id分组，优先当前年款官图，最多15张；同组按图片ID和URL去重。经确认缺图时可使用同动力历史官图或普通图库，但必须备注实际年款、类别及来源，不混年款或动力。普通图库不能随意取其他车型的图片。

```bash
uv run autohome-catalog images-prepare --run images \
  --source "$AUTOHOME_DATA_ROOT/verified-trims.json"
uv run autohome-catalog images-collect --run images --model-id AUT-5346-BEV
uv run autohome-catalog images-sync --run images \
  --config "$AUTOHOME_DATA_ROOT/control/feishu.json"
```

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
  --run "$AUTOHOME_DATA_ROOT/dealer-quotes" --apply
```

按已存在的配置ID查询、只更新经销商报价。当前查询地区为北京（110100），正数显示“XX万起”，正常零报价显示“暂无报价”；错误响应不能写成暂无报价。报价与地区和时间有关，不代表成交价。备份、响应哈希和回读结果在运行目录中。

## 人怎么使用四张表

1. 在models按品牌、车系和动力找到车型，使用model_id定位，打开“配置版本”。
2. 在trims选择具体的车型全称，查看价格、电池、续航等；通过“参数扩展”进入座椅、灯光和智能配置。
3. 专属配置明细按具体配置关联，一个配置可以对应多条选装包/特色项目。
4. 跨表核对来源配置ID和关联，不用行号对应；品牌、车系、配置名称保留用于独立筛选。

车型model_id格式为`AUT-车系ID-BEV/PHEV/EREV/FCEV`，一行一个。来源车系ID可以重复，不能代替model_id。配置主表与扩展按来源配置ID一一对应。

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
