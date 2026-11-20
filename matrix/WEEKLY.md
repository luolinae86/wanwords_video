# Phase 2 · 周更矩阵

产能目标（可按 Phase1 数据加减）：

| 类型 | 产能 | 命令 / 路径 | 算力 |
|------|------|-------------|------|
| 口播 | 3/周 | `python3 scripts/make_avatar_ep.py --ep=ep0XX` | 阿里云 TTS + VideoRetalk |
| 名言/故事 | 2/周 | 复用 douyin `quote_video` 思路；成片进 `matrix/renders/` | DashScope wan3.0 |
| H3 情景钩子 | 1/周 | 168 夜间：`ssh root@192.168.1.168` 错峰跑 H3 | 本地 96GB |
| 产品演示 | 按版本 | `python3 scripts/make_promo_guide_v1.py` | 本机 ffmpeg + TTS |

## 选题池（口播）

1. 听得懂却写不出（ep001 已做）
2. 打错立刻提醒 vs 背完就忘
3. 晾衣绳动物卡怎么帮你拆句
4. 人教版 / 新概念一句示范
5. 错词本：专治反复错
6. 三端同步：地铁上手机、回家电脑
7. 动画课「观看 ⇄ 练习」
8. 四六级长难句拆一句打一句

## 数据反馈栏

发完 Phase1 后填写，决定下周权重（考试党 vs 家长）：

| 平台 | 作品 | 完播 | 主页访问 | 结论 |
|------|------|------|----------|------|
| 抖音 guide_v1 |  |  |  |  |
| 抖音 ep001 |  |  |  |  |
| 小红书 guide_v1 |  |  |  |  |
| 小红书 ep001 |  |  |  |  |
| 视频号 guide_v1 |  |  |  |  |
| 视频号 ep001 |  |  |  |  |

## H3 错峰

- 机器：`192.168.1.168`（外网跳板见小豆 skill）
- 避开白天小豆/PEP/反字墓占卡；夜间提交 1 条情景听写钩子
- **本阶段不部署 InfiniteTalk**

## 名言片备注

quote-video 管线若在 douyin 仓，成片复制到 `matrix/renders/quote_<id>.mp4`，文案用 `publish/` 模板改 CTA 为万字诀。
