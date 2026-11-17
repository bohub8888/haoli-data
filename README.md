# 蚝历 · 数据层

**公开仓库：[`bohub8888/haoli-data`](https://github.com/bohub8888/haoli-data)**（MIT）。
2026-11-17 域名备案通过并切换完成后开放。

这是「蚝历」节假日日历的**数据层开源仓**：节日唯一来源表、内容准入规则、生成脚本、
校验脚本与全部公开数据产物。Android App、网站部署与分发渠道不在这个仓里。

数据面向中文用户：中国内地法定假日与调休、24 节气、农历传统节日、港澳台公众假期、
全球 60+ 国家/地区节假日、联合国官方国际日，以及经权威来源逐条核过的中国历史时刻。
同一天的多个节日会合并、去重、分类（中国 / 国际 / 其他国家），再输出为 JSON 与 ICS。

## 这个仓里有什么

| 路径 | 作用 |
|---|---|
| `observances.py` | 节日唯一来源表（固定日 / 浮动日 / 说明） |
| `cn_official.py` | 国务院放假安排与调休上班日，按年维护 |
| `curated.py` | 中国重大历史时刻策展库，逐条附 A 级来源与首发年份 |
| `conferences.py` | 会议 / 展会展期与观察名单 |
| `taxonomy.py` | 渠道分级（C1–C4）、正交分类、内容红线、生成期准入闸门 |
| `build.py` | 生成 all/widget/meta/cn JSON、events.json、234 份 ICS、官网页面 |
| `build_state.py` | 数据版本自动推导 + 幂等写入（重跑零差异） |
| `check_official_notice.py` | 抓取中国政府网放假通知，与 `cn_official.py` 逐日比对 |
| `check_*.py` / `test_*.py` | 四出口一致性、关键日期、翻译、解析与版本单测 |
| `data/` `2026/` `2027/` `ics/` | 全部公开数据产物（JSON + ICS + 公告原文缓存） |

## 数据准入

新增条目必须能追到权威来源，规则写在 `taxonomy.py`：

- **C1 / A 级**：国务院办公厅通知、全国人大常委会决定、UN 官方决议等；
- **C4 / B 级**：联合国官方纪念日清单、权威百科线索（只作选题，不直接入库）；
- 每条都要有 channel / source / level / verified，`validate()` 是构建期的硬闸门；
- 内容红线按条目点名（战犯侵略、主权表述等），中国池整层豁免，命中即构建失败；
- 史实记录 **不等于** 纪念日。历史时刻要进库，必须有央媒按日固定栏目或逢十周年回顾，
  名称照录官方表述，不自造「XX 纪念日」。

`curated.py` 里每条都带 `public`（对外可见说明）、`note`（内部考据）与 `anchor`
（事件首发年份，周年数由此自动计算），不接受无来源投稿。

## 如何贡献

1. 改对应的来源表（新增国际日 → `observances.py`；历史时刻 → `curated.py`；
   次年调休 → `cn_official.py`），不要直接改 `data/` 下的产物。
2. 本地跑：`python -m unittest test_official_notice test_build_state -v`
   和 `python check_official_notice.py --offline`。
3. PR 里写清官方来源链接与日期依据。CI 会自动核对公告、跑单测、校验
   `manifest.json` 里每份文件的 sha256 是否与实际产物一致。
4. 合并后由维护者执行构建与数据热更新；**数据更新不需要发新 APK**，
   App 会通过 `data/manifest.json` 的版本号自动拉取。

## 自动化：只在数据真变化时发布

每日 CI 抓中国政府网通知，与本地调休表逐日比对；发现差异只报错、生成报告，
**不会自动改写源表**——公告文本解析存在歧义，采纳变化仍需人工核源。

构建侧由 `build_state.py` 给数据定义文件算指纹：指纹没变就沿用旧版本号、
产物逐字不写（包括 ICS 的 DTSTAMP）；指纹变了才自动把数据版本 +1。
因此「重跑一次构建」不会产生任何假变更，更新清单只列出真正变化的文件。

## 许可与免责

代码与数据按 MIT 许可发布（见 `LICENSE`）。

节假日安排可能随官方公告调整，出差、排班、薪酬、签证等场景请以
中国政府网及各地官方公告为准；农历与节气按历法计算，伊斯兰节日按天文推算，
可能与当地最终公布相差一天。

## 本地运行

```bash
pip install -r requirements.txt
python -m unittest test_official_notice test_build_state -v
python check_official_notice.py --offline
```

`build.py` 还会用到 `template.html` 生成官网页面，站点地址由环境变量
`HAOLI_SITE_ORIGIN` 提供（见 `site_config.py`）。完整构建与发布流水线在维护者的
私有主仓里，本仓只包含数据层。
