# 风格模板视觉研究与收录准则（2026-07）

本目录的预设不是同义词列表，而是可执行的视觉生产合同。每项必须同时固定：

1. `medium/material`：媒介或材料工艺；
2. `palette`：主色、辅色和禁止退化的色彩方向；
3. `lighting/rendering`：布光、印相或绘制机制；
4. `composition/optics`：透视、镜头、空间和叙事组织；
5. `negative boundary`：与相邻风格的明确分界。

只有能在以上维度形成稳定视觉指纹、并能进入角色/场景/道具/分镜/视频统一提示词编译的候选才进入内置库；“泛电影感”“泛动漫”“高清感”等不可检验标签不收录。

## 2026-07 扩库来源

本轮先对既有 40 项逐一排重，再参考电影机构、博物馆、大学和国家影像机构资料。研究事实用于确定媒介边界，最终 JSON 字段是面向 AIGC 生成的生产化转译。

| 研究方向 | 采用/映射预设 | 主要资料 |
|---|---|---|
| 铅黄电影的主观彩色空间 | `giallo_psychological` | [BFI：Dario Argento 的色彩运用](https://www.bfi.org.uk/features/hues-out-hell-how-dario-argento-uses-colour) |
| 三色分离与染料转印彩色电影 | `technicolor_musical` | [UCLA Film & Television Archive：The Dawn of Technicolor](https://cinema.ucla.edu/blog/the-dawn-of-technicolor-1915-1935/) |
| 实体金属针阵列动画 | `pinscreen_engraving` | [National Film Board of Canada：Pinscreen Animation](https://blog.nfb.ca/blog/2015/02/12/pinscreen-animation-3-keys/) |
| 逐帧手工材料动画 | `oil_painted_animation`、`felt_puppet_stopmotion` | [NFB：Hand-Crafted Cinema](https://www.nfb.ca/film/handcrafted_cinema/) |
| 纸/绢本横卷连续叙事 | `yamato_e_emaki` | [The Met：Japanese Illustrated Handscrolls](https://www.metmuseum.org/essays/japanese-illustrated-handscrolls) |
| 南亚宫廷细密画 | `indian_miniature` | [The Met：The Art of the Mughals after 1600](https://www.metmuseum.org/essays/the-art-of-the-mughals-after-1600) |
| 朝鲜民间多焦点绘画 | `korean_minhwa` | [The Museum of Korean Folk Art](https://mokfa.omeka.net/) |
| 蓝晒化学印相 | `cyanotype_photography` | [National Science and Media Museum：Cyanotype Process](https://blog.scienceandmediamuseum.org.uk/introduction-cyanotype-process/) |
| 中世纪现代有限动画的平面调度 | `upa_midcentury_modern`，并用于校准 `ligne_claire_european` 的负向边界 | [MoMA：UPA, Form in the Animated Cartoon](https://www.moma.org/calendar/exhibitions/2434) |
| 日本金地屏风与湿中湿矿彩 | 邻近研究用于排除 `yamato_e_emaki` 的琳派混入 | [The Met：Rinpa Painting Style](https://www.metmuseum.org/essays/rinpa-painting-style) |
| 早期彩色玻璃正片颗粒 | 邻近研究用于校准 `technicolor_musical` 的负向边界 | [The Met：Original Autochromes](https://www.metmuseum.org/perspectives/on-view-january-2530-original-autochromes-produced-using-the-first-color-photographic-process) |

## 本轮明确剔除的重复方向

- `Dogme 95`、`cinéma vérité`、`direct cinema`：与 `realistic`、`arthouse_naturalism`、`documentary_16mm` 的当前边界过近；
- 经典 `film noir`：已由 `neo_noir` 覆盖；
- 普通剪影/剪纸动画：与 `chinese_shadow_puppet`、`paper_cut_folk` 重叠，故改收录媒介机制完全不同的 `pinscreen_engraving`；
- 泛水墨、泛复古、泛动漫：缺少可验证的材料、光学或构图指纹；
- 普通木偶定格：与 `clay_stop_motion` 过近；仅保留明确限定针毡纤维和缝线工艺的 `felt_puppet_stopmotion`。

## 16:9 资产门禁

- 内置展示图的最终规格固定为 `768×432`；
- 上游响应必须是原生宽银幕；仅容许供应商边长量化造成的 2% 以内比例误差；
- 竖图、方图、3:2 等响应必须失败并重试，不得再用破坏性中心裁切伪装成横图；
- 人物头脸、完整动作、双人关系、产品顶部/底部等提示词主体不得被裁出画面；
- 每次替换预览后必须提升前端资源版本、生成联系表、人工视觉复核，并同步桌面运行版。
