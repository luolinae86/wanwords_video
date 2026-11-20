# wanwords_video · 万字诀投放内容工厂

与产品仓 [wandwords](https://github.com/luolinae86/wandwords) 解耦：本仓只产出抖音 / 小红书 / 视频号的宣传片、数字人口播与发布文案。

## 品牌锁死

| 项 | 值 |
|---|---|
| 口号 | 听一句，打一句，练到会用 |
| CTA | wanwords.com · 前两课免费试学 |
| 主色 | `#0f766e` |
| 数字人 | 原创「万字诀英语教练」成年女教师（见 `brand/`） |
| 直播 | **禁止无人 AI 互动**；Phase3 仅真人周播 + 数字人片头/录播 |

## 目录

```
brand/          定妆、音色、海报、口号
promo/          产品演示片脚本与成片
avatar/         数字人口播集
publish/        三平台文案与封面
live/           合规边界与真人直播话术
matrix/         Phase2 周更选题与产能表
scripts/        合成 / TTS / Retalk / 烧录脚本
```

## Phase 流水线

### 主成片路线（desk · 对标句乐部）

电脑+键盘背景，显示器里真实练习；右下角圆形女主解说。

```bash
# 当前主推（外国女主 E + 手机首页开场 + 左侧字幕 + 键盘声）
python3 scripts/make_promo_desk_v5.py
```

成片：`promo/renders/desk_v5_vertical.mp4`  
角色：`brand/characters/female_en/`（外国女主 E）；中国女主保留 `female_cn/`

```bash
# 旧版
python3 scripts/make_promo_desk_v4.py
python3 scripts/make_promo_desk_v2.py
```

### 旧管线（次要）

```bash
python3 scripts/gen_brand_assets.py          # 定妆（女A/男D 已锁定）
python3 scripts/make_promo_guide_v1.py       # 旧幻灯式演示
python3 scripts/make_avatar_ep.py --ep=ep001 # 数字人口播（非主推）
```

### Phase 2 · 周更矩阵

见 [`matrix/WEEKLY.md`](matrix/WEEKLY.md)：每周 3 口播 + 2 名言/故事 + 1 H3 情景钩子（168 夜间错峰）。

### Phase 3 · 直播试点

见 [`live/COMPLIANCE.md`](live/COMPLIANCE.md)：真人周播 1–2 场；数字人只做开场/录播段。

## 环境

从 `../douyin/.env` 或 `../wandwords/.env` 读取：

- `DASHSCOPE_API_KEY`
- `ALIYUN_ACCESS_KEY_ID` / `ALIYUN_ACCESS_KEY_SECRET`（OSS 上传供 VideoRetalk）
- `OSS_BUCKET_NAME`（默认 `wanwords`）

算力约定：口播/演示/名言片优先阿里云；`192.168.1.168` 夜间跑 H3；不与现有小豆/PEP 长期抢卡。

## 复用边界

- VideoRetalk 分段 TTS↔字卡规则参考 douyin `kouyu_girl`，**定妆与音色全新，禁止拷贝 Leo**。
- 产品演示旁白落地 `wandwords/script/dev/guide_video_prompt.md`。
