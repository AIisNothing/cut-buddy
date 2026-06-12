# 掉秤搭子 cut-buddy · 看板设计方案

> 设计方向:**暖陪跑风**(Nature Distilled)——暖土色系、自然质感、柔和阴影。
> 由 ui-ux-pro-max 设计引擎生成,经人工调整:风格从引擎原始推荐的 Vibrant & Block-based 改为 Nature Distilled(更贴合"不焦虑陪跑"的产品调性),字体从 Lora/Raleway 改为 Varela Round/Nunito Sans(更圆润友好)。

---

## 1. 设计原则(从 PRD 调性翻译而来)

1. **温暖不刺激**:暖土色打底,高饱和色只用于强调(趋势、CTA),永不用红色警示体重数据
2. **看趋势的视觉层级**:7 日均线永远比单日散点更醒目——视觉上就在告诉用户"看这条,别看那个点"
3. **正反馈醒目**:夸夸卡片、里程碑是看板的情绪核心,给最暖的色和最大的留白
4. **庆祝可关**:烟花等大动效必须响应 `prefers-reduced-motion`

## 2. 色彩令牌

| 令牌 | 色值 | 用途 |
|---|---|---|
| `--terracotta` | `#C67B5C` | 主色:标题强调、图表主线、进度条 |
| `--warm-clay` | `#B5651D` | 主色加深:hover、当前里程碑 |
| `--sand-beige` | `#D4C4A8` | 次要元素:分隔、未达成里程碑、图表网格 |
| `--soft-cream` | `#F5F0E1` | 页面背景 |
| `--card-bg` | `#FFFCF5` | 卡片背景(比背景略亮,形成柔和层次) |
| `--olive-green` | `#6B7B3C` | 正向反馈:掉秤趋势、达标徽章、训练日 |
| `--text-main` | `#3D2E22` | 正文(深咖,对 cream 背景对比度 ≈ 10:1) |
| `--text-muted` | `#6E5C4B` | 次要文字(对比度 ≈ 5.2:1,达标) |
| `--praise-bg` | `#FAE8D4` | 夸夸卡片专属暖底 |

**禁用**:纯黑 `#000`、警示红(体重涨了用中性灰棕表述,不用红色制造焦虑)、AI 紫粉渐变。

## 3. 字体

| 用途 | 字体 | 回退链 |
|---|---|---|
| 标题/数字 | Varela Round | `'Varela Round', 'PingFang SC', 'Hiragino Sans GB', sans-serif` |
| 正文 | Nunito Sans | `'Nunito Sans', 'PingFang SC', 'Hiragino Sans GB', sans-serif` |

- ⚠️ **离线约束**(PRD 第 8 节):正式看板要么把 woff2 字体文件随看板本地存放,要么直接用回退链(系统中文字体本身够圆润)。**不要依赖 Google Fonts CDN**。Varela Round/Nunito Sans 只覆盖拉丁字符与数字——中文自动走 PingFang,数字(体重值)走圆体,效果协调。
- 字号:正文 ≥16px;大数字(今日体重)32-40px;行高 1.6

## 4. 布局与间距

- 卡片圆角 `16px`,内边距 `20-24px`,卡片间距 `16px`
- 柔和阴影:`0 2px 12px rgba(91, 64, 38, 0.08)`(暖棕色调阴影,不用冷灰)
- 响应式:375px 单列 / 768px 两列 / 1024px+ 三列网格,趋势图始终通栏
- 最大宽度 `max-width: 1080px` 居中

## 5. 图表规范(Chart.js)

| 项 | 规范 |
|---|---|
| 体重趋势 | 单日散点:sand-beige 小点(弱化);7 日均线:terracotta 实线 2.5px(主角) |
| 区域填充 | 均线下方 terracotta 12% 透明度 |
| 配额对比 | 横向条:实际摄入 terracotta,配额区间用 sand-beige 底带;达标徽章 olive-green |
| 身体成分 | 脂肪量 warm-clay 线、瘦体重 olive-green 线,加图例 |
| 无障碍 | 双线必须线型区分(实线/虚线),不能只靠颜色;canvas 配 aria-label |

## 6. 动效

- 微交互 200ms,`ease-out` 进 `ease-in` 出;hover 用颜色/阴影变化,**不用 scale**(防布局抖动)
- 每屏最多 1-2 个动效元素;数字变化可做 600ms 计数动画
- 烟花彩蛋:`@media (prefers-reduced-motion: reduce)` 下降级为静态祝贺卡片
- 夸夸卡片可做一次性轻微进场(淡入上移 8px),不循环

## 7. 反面清单(这个产品尤其要避开)

- ❌ 红色/警告色表达体重上涨(制造焦虑,违背产品理念)
- ❌ emoji 当图标(用内联 SVG;文案里的 emoji 可以保留)
- ❌ 死板的纯数据表格风(PRD 反面模式:Static design / No gamification)
- ❌ 深色模式优先(暖陪跑风以浅暖底为主;深色模式可作后续选项)
- ❌ 可点元素没有 cursor-pointer / 无 hover 反馈

## 8. 交付前检查清单

- [ ] 文字对比度 ≥4.5:1(本方案 text-main/muted 均达标)
- [ ] prefers-reduced-motion 已处理(尤其烟花)
- [ ] 375/768/1024/1440 四档无横向滚动
- [ ] 图标统一内联 SVG(Lucide 风格,24×24 viewBox)
- [ ] 字体离线可用(本地 woff2 或回退链)
- [ ] 图表色盲友好(线型+图例区分)
