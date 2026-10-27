// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 画布特效库的预设清单。
 *
 * 口径（参考依据见 `docs/ai/specs/effect-library-expansion-20261027.md`）：
 * - 平台「特效库」的 25 个标签只有名字，电影/电商那 6 个连占位提示词都没有；
 * - 真正可用的写法在 libtv 技能库的特效语料里（仙侠粒子、武侠兵器、科幻机甲等）；
 * - 这里只放能直接写进分镜/视频提示词的短句，不复制整段带时间轴的长提示词。
 *
 * 每一条 `promptFragment` 都是独立可读的画面描述，写进节点提示词后用户还能继续改。
 * 运镜不在这里重复登记：它由 `cameraMovementPresets.ts` 和后端目录提供，弹窗里合并展示。
 */

export const EFFECT_CATEGORIES = [
  { id: 'cinematic', label: '电影特效' },
  { id: 'lighting', label: '光效氛围' },
  { id: 'particles', label: '粒子特效' },
  { id: 'elements', label: '元素法术' },
  { id: 'impact', label: '打击反馈' },
  { id: 'commerce', label: '电商展示' },
  { id: 'weather', label: '天气环境' },
] as const;

export type EffectCategoryId = (typeof EFFECT_CATEGORIES)[number]['id'];

/** `video` 表示只有时间维度才成立（顿帧、变速、升格），图片节点不应套用。 */
export type EffectScope = 'both' | 'video';

export interface EffectPreset {
  id: string;
  label: string;
  category: EffectCategoryId;
  promptFragment: string;
  keywords: readonly string[];
  scope: EffectScope;
}

export const EFFECT_PRESETS: ReadonlyArray<EffectPreset> = [
  // 电影特效：平台特效库的 6 个空壳标签，补上可直接使用的画面描述。
  {
    id: 'flame-on',
    label: '火焰缠身',
    category: 'cinematic',
    promptFragment:
      '赤红烈焰从主体边缘燃起并向上翻卷，火星四散，火光映亮周围环境，热浪让背景轻微扭曲',
    keywords: ['火焰', 'flame', '燃烧', '烈焰', '火光'],
    scope: 'both',
  },
  {
    id: 'money-rain',
    label: '金钱雨',
    category: 'cinematic',
    promptFragment:
      '成片金色硬币与纸币从画面上方倾泻而下，边下落边翻转反光，落地弹跳并逐渐堆积',
    keywords: ['金钱', 'money', '金币', '钞票', '下雨'],
    scope: 'both',
  },
  {
    id: 'portal',
    label: '传送门',
    category: 'cinematic',
    promptFragment:
      '主体前方裂开一道环形能量传送门，门内漩涡状光流旋转，边缘电弧跳跃，主体被光流缓缓吸入',
    keywords: ['传送门', 'portal', '虫洞', '时空', '裂缝'],
    scope: 'both',
  },
  {
    id: 'particle-dissolve',
    label: '粒子消散',
    category: 'cinematic',
    promptFragment:
      '主体从边缘开始解体为密集发光粒子，粒子沿上升气流飘散并逐渐熄灭，最后只留一圈轮廓微光',
    keywords: ['消散', 'dissolve', '沙化', '灰飞烟灭', '粒子'],
    scope: 'both',
  },
  {
    id: 'time-freeze',
    label: '时间静止',
    category: 'cinematic',
    promptFragment:
      '画面在动作最高点骤然定格，飞溅的碎片与水滴悬停在空中，只有主体能继续缓慢移动',
    keywords: ['定格', '时间静止', 'freeze', '悬停', '静止'],
    scope: 'video',
  },
  {
    id: 'slow-explosion',
    label: '升格爆炸',
    category: 'cinematic',
    promptFragment:
      '爆炸火球在极慢动作中向外膨胀，碎片与火星悬浮飞散，冲击波以可见波纹推开周围空气',
    keywords: ['爆炸', 'explosion', '升格', '慢动作', '冲击波'],
    scope: 'video',
  },

  // 光效氛围
  {
    id: 'volumetric-light',
    label: '体积光',
    category: 'lighting',
    promptFragment:
      '强光束从画面侧上方穿过空气中的尘埃与薄雾，形成清晰的丁达尔光束，主体边缘被逆光勾亮',
    keywords: ['体积光', '丁达尔', '光束', 'god ray', '逆光'],
    scope: 'both',
  },
  {
    id: 'neon-sweep',
    label: '霓虹扫光',
    category: 'lighting',
    promptFragment:
      '一道高饱和霓虹光带沿主体表面快速扫过，扫过处亮起荧光轮廓，暗部保留冷色反光',
    keywords: ['霓虹', 'neon', '扫光', '荧光', '赛博'],
    scope: 'both',
  },
  {
    id: 'lens-flare',
    label: '镜头光晕',
    category: 'lighting',
    promptFragment:
      '光源附近出现横向拉长的镜头光晕与彩虹色眩光，光斑缓慢漂移，画面边缘保持干净',
    keywords: ['光晕', 'flare', '眩光', '光斑', '逆光'],
    scope: 'both',
  },
  {
    id: 'glow-orbs',
    label: '光斑呼吸',
    category: 'lighting',
    promptFragment:
      '背景漂浮三团大面积柔焦光斑，光斑缓慢有机漂移，靠近主体时主体边缘泛起柔和辉光',
    keywords: ['光斑', '呼吸光', 'bokeh', '柔焦', '辉光'],
    scope: 'both',
  },
  {
    id: 'scan-line',
    label: '扫描光带',
    category: 'lighting',
    promptFragment:
      '一条细亮扫描线自下而上扫过主体，线过之处浮现半透明数据网格与刻度，随后逐渐淡出',
    keywords: ['扫描', 'scan', '科技感', 'HUD', '数据'],
    scope: 'both',
  },
  {
    id: 'lightning',
    label: '闪电',
    category: 'lighting',
    promptFragment:
      '云层中连续劈下数道分叉闪电，强光把主体照成高对比剪影，雷声震动让画面轻微抖动',
    keywords: ['闪电', 'lightning', '雷电', '雷雨', '剪影'],
    scope: 'both',
  },
  {
    id: 'energy-shield',
    label: '能量护盾',
    category: 'lighting',
    promptFragment:
      '主体周身展开半透明六边形能量护盾，护盾表面流动光纹，受击处炸开一圈金色涟漪',
    keywords: ['护盾', 'shield', '能量罩', '防护', '六边形'],
    scope: 'both',
  },
  {
    id: 'water-caustics',
    label: '水光焦散',
    category: 'lighting',
    promptFragment:
      '水面反射的波光在主体和背景上流动，光斑边缘锐利、亮度起伏，并带轻微折射扭曲',
    keywords: ['水光', '焦散', 'caustics', '波光', '折射'],
    scope: 'both',
  },

  // 粒子特效
  {
    id: 'xianxia-qi',
    label: '仙侠灵气',
    category: 'particles',
    promptFragment:
      '青金色灵力粒子从地面升起，沿主体轮廓盘旋汇聚，拖出细长光尾，周围浮现古文字符虚影',
    keywords: ['灵气', '仙侠', '灵力', '符文', '光尾'],
    scope: 'both',
  },
  {
    id: 'gold-dust',
    label: '金芒粒子',
    category: 'particles',
    promptFragment:
      '细密金色粒子在空中悬浮聚拢，沿主体轮廓逐段勾勒，最后炸裂成放射状金芒',
    keywords: ['金芒', '金色粒子', '金光', '聚拢', '炸裂'],
    scope: 'both',
  },
  {
    id: 'sparks',
    label: '火星飞溅',
    category: 'particles',
    promptFragment:
      '撞击点炸开密集橙红火星，火星沿抛物线飞散并逐渐熄灭，留下短促的发光轨迹',
    keywords: ['火星', 'sparks', '火花', '撞击', '飞溅'],
    scope: 'both',
  },
  {
    id: 'star-dust',
    label: '星尘',
    category: 'particles',
    promptFragment:
      '无数冷白星尘从画面上方缓缓飘落，粒子带轻微闪烁，靠近镜头处形成柔焦光点',
    keywords: ['星尘', '星点', 'star dust', '闪烁', '飘落'],
    scope: 'both',
  },
  {
    id: 'smoke',
    label: '烟雾',
    category: 'particles',
    promptFragment:
      '浓淡不均的烟雾从地面翻涌升起，被侧光切成层次，边缘缓慢消散并掠过主体脚下',
    keywords: ['烟雾', 'smoke', '雾气', '翻涌', '侧光'],
    scope: 'both',
  },
  {
    id: 'rain-freeze',
    label: '雨滴悬停',
    category: 'particles',
    promptFragment:
      '密集雨滴在画面中骤然悬停，每颗水滴清晰可辨并折射环境光，随后同时炸裂成水雾',
    keywords: ['雨滴', '悬停', 'rain', '水雾', '折射'],
    scope: 'video',
  },
  {
    id: 'debris',
    label: '碎屑飞溅',
    category: 'particles',
    promptFragment:
      '主体表面崩解出大小不一的碎片，碎片带旋转与拖影向外飞散，细屑在光中闪烁',
    keywords: ['碎屑', 'debris', '碎片', '崩解', '拖影'],
    scope: 'both',
  },
  {
    id: 'energy-converge',
    label: '能量汇聚',
    category: 'particles',
    promptFragment:
      '光点从画面四周向主体掌心汇聚，汇成旋转的能量球，球体表面电弧跳动，亮度逐级升高',
    keywords: ['能量', '汇聚', '光球', '蓄力', '电弧'],
    scope: 'both',
  },

  // 元素法术：仙侠语料里的金木水火土与兵器光效，压成单镜可用的短句。
  {
    id: 'fire-lotus',
    label: '火莲绽放',
    category: 'elements',
    promptFragment:
      '赤红火莲在主体脚下层层绽放，莲瓣由流动火焰构成，火舌向上卷起，空气因高温扭曲',
    keywords: ['火莲', '火焰', '仙侠', '绽放', '热浪'],
    scope: 'both',
  },
  {
    id: 'water-dragon',
    label: '水龙',
    category: 'elements',
    promptFragment:
      '高压水流凝聚成一条水龙，龙身由旋转水纹构成，咆哮冲向前方，撞击处炸成海啸般巨浪',
    keywords: ['水龙', '水系', '巨浪', '水流', '仙侠'],
    scope: 'both',
  },
  {
    id: 'ice-prison',
    label: '冰封',
    category: 'elements',
    promptFragment:
      '寒气从主体脚下向外蔓延，地面迅速结出六角冰晶，冰锥自下而上把目标封进整块幽蓝巨冰',
    keywords: ['冰封', '冰晶', '寒冰', '冻结', '霜'],
    scope: 'both',
  },
  {
    id: 'thunder-strike',
    label: '劫火神雷',
    category: 'elements',
    promptFragment:
      '暗红色火焰雷电从云层劈下，雷柱呈扭曲蛇形，落地炸开火环与冲击波，残留焦灼符文',
    keywords: ['雷', '闪电', '天劫', '雷柱', '符文'],
    scope: 'both',
  },
  {
    id: 'wood-prison',
    label: '万木囚笼',
    category: 'elements',
    promptFragment:
      '翠绿法阵在地面扩散，带刺古木巨柱破土升起，柱身缠绕发光藤蔓并不断抽出新枝',
    keywords: ['木系', '藤蔓', '囚笼', '法阵', '巨木'],
    scope: 'both',
  },
  {
    id: 'earth-spikes',
    label: '地刺',
    category: 'elements',
    promptFragment:
      '地面炸开环形裂纹，尖锐石笋与地刺交替破土升起，碎石与尘土呈环形掀起',
    keywords: ['地刺', '石笋', '土系', '地裂', '碎石'],
    scope: 'both',
  },
  {
    id: 'metal-swords',
    label: '万剑诀',
    category: 'elements',
    promptFragment:
      '亿万金色光点凝聚成悬浮光剑，剑身刻满流动暗金符文，剑阵呈球形展开后如暴雨齐射',
    keywords: ['万剑', '飞剑', '金系', '剑阵', '齐射'],
    scope: 'both',
  },

  // 打击反馈
  {
    id: 'shockwave',
    label: '冲击波',
    category: 'impact',
    promptFragment:
      '命中瞬间炸开环形冲击波，空气被推开形成可见波纹，尘土与碎片呈放射状飞散',
    keywords: ['冲击波', 'shockwave', '气浪', '命中', '波纹'],
    scope: 'both',
  },
  {
    id: 'hit-freeze',
    label: '顿帧',
    category: 'impact',
    promptFragment:
      '命中帧画面骤然停顿两帧，背景与主体一起冻结，随后以更快的速度恢复动作',
    keywords: ['顿帧', 'hit stop', '卡帧', '冻结', '打击感'],
    scope: 'video',
  },
  {
    id: 'screen-shake',
    label: '震屏',
    category: 'impact',
    promptFragment:
      '重击落地时镜头剧烈震动并伴随轻微倾斜，震动幅度在数帧内指数衰减',
    keywords: ['震屏', 'shake', '镜头震动', '重击', '落地'],
    scope: 'video',
  },
  {
    id: 'anime-impact',
    label: '动漫打击帧',
    category: 'impact',
    promptFragment:
      '撞击瞬间插入一帧高对比负片画面，放射状集中线与红青色散叠加，下一帧全部撤除',
    keywords: ['打击帧', '负片', '集中线', '动漫', '色散'],
    scope: 'video',
  },
  {
    id: 'slow-motion',
    label: '升格慢放',
    category: 'impact',
    promptFragment:
      '关键动作进入极慢动作，飞溅的碎屑与衣摆悬停般缓慢移动，背景轻微虚化',
    keywords: ['慢动作', '升格', 'slow motion', '悬停', '虚化'],
    scope: 'video',
  },
  {
    id: 'speed-ramp',
    label: '变速',
    category: 'impact',
    promptFragment:
      '动作先以慢速蓄力，命中瞬间骤然加速，随后再次降速定格在最高张力的一帧',
    keywords: ['变速', 'speed ramp', '加速', '蓄力', '定格'],
    scope: 'video',
  },

  // 电商展示
  {
    id: 'rotate-3d',
    label: '3D 旋转',
    category: 'commerce',
    promptFragment:
      '主体置于纯色无缝背景中匀速旋转一周，棚拍柔光随角度变化，产品边缘保持锐利',
    keywords: ['3D旋转', '转台', '产品', '电商', '环绕'],
    scope: 'both',
  },
  {
    id: 'outfit-change',
    label: '瞬间换装',
    category: 'commerce',
    promptFragment:
      '主体在转身瞬间完成换装，旧衣物化作光点散去，新装从肩部向下逐层生成并贴合身体',
    keywords: ['换装', '变装', '服装', '电商', '光点'],
    scope: 'both',
  },
  {
    id: 'product-sweep',
    label: '产品扫光',
    category: 'commerce',
    promptFragment:
      '一道锐利高光沿产品边缘快速扫过，金属与玻璃表面依次亮起，背景保持干净暗场',
    keywords: ['产品', '扫光', '高光', '电商', '暗场'],
    scope: 'both',
  },
  {
    id: 'floating-in',
    label: '悬浮缓入',
    category: 'commerce',
    promptFragment:
      '产品从画面深处缓慢悬浮推近，轻微旋转后落到画面中心，背景光晕随距离收拢',
    keywords: ['悬浮', '缓入', '产品', '推近', '光晕'],
    scope: 'both',
  },

  // 天气环境
  {
    id: 'rain',
    label: '暴雨',
    category: 'weather',
    promptFragment:
      '密集雨丝斜向砸落，地面溅起水花，雨幕让远处背景朦胧，主体表面挂满水珠',
    keywords: ['雨', 'rain', '暴雨', '水花', '雨幕'],
    scope: 'both',
  },
  {
    id: 'snow',
    label: '落雪',
    category: 'weather',
    promptFragment:
      '大颗雪花在画面中缓慢飘落，近处雪花失焦成光斑，地面积雪随脚步扬起',
    keywords: ['雪', 'snow', '雪花', '积雪', '飘落'],
    scope: 'both',
  },
  {
    id: 'fog',
    label: '浓雾',
    category: 'weather',
    promptFragment:
      '低空浓雾缓慢流动，吞没背景层次，主体轮廓在雾中若隐若现，光线被雾散射成柔光',
    keywords: ['雾', 'fog', '雾气', '朦胧', '散射'],
    scope: 'both',
  },
  {
    id: 'sandstorm',
    label: '沙暴',
    category: 'weather',
    promptFragment:
      '黄沙遮天蔽日，沙粒贴地高速掠过并向一侧倾斜，远处景物被沙幕吞没',
    keywords: ['沙暴', '沙尘', 'sandstorm', '黄沙', '荒漠'],
    scope: 'both',
  },
  {
    id: 'strong-wind',
    label: '强风',
    category: 'weather',
    promptFragment:
      '强风把衣摆、头发与地面碎屑统一吹向一侧，布料高频抖动，空气中有可见尘线',
    keywords: ['风', 'wind', '强风', '衣摆', '尘线'],
    scope: 'both',
  },
];

const CATEGORY_LABELS = new Map(
  EFFECT_CATEGORIES.map((category) => [category.id, category.label]),
);

export function effectCategoryLabel(id: EffectCategoryId): string {
  return CATEGORY_LABELS.get(id) ?? id;
}

/**
 * 按关键词过滤。搜索词命中标签、提示词正文、分类名或英文别名都算命中；
 * 多个词用空格分隔时要求全部命中，便于「仙侠 火」这种组合。
 */
export function filterEffectPresets(
  presets: ReadonlyArray<EffectPreset>,
  query: string,
): EffectPreset[] {
  const terms = query
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  if (terms.length === 0) return [...presets];
  return presets.filter((preset) => {
    const haystack = [
      preset.label,
      preset.promptFragment,
      effectCategoryLabel(preset.category),
      ...preset.keywords,
    ]
      .join(' ')
      .toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}

export function findEffectPreset(
  presets: ReadonlyArray<EffectPreset>,
  id: string | null | undefined,
): EffectPreset | null {
  if (!id) return null;
  return presets.find((preset) => preset.id === id) ?? null;
}

/**
 * 每条画面特效挂一张「示意预览」：复用镜头模板里语义最接近的封面和动图。
 *
 * 平台没有为这 44 条提示词型特效提供官方演示，这里不假装它们是实拍成片；
 * UI 上会标「示意」。键是 `EffectPreset.id`，值是 `EFFECT_TEMPLATES` 的 id。
 */
export const EFFECT_PREVIEW_TEMPLATE_IDS: Readonly<Record<string, string>> = {
  'flame-on': '23030908',
  'money-rain': '23030907',
  portal: '23030906',
  'particle-dissolve': '23043926',
  'time-freeze': '23064310',
  'slow-explosion': '23043925',

  'volumetric-light': '23043918',
  'neon-sweep': '23043916',
  'lens-flare': '23030907',
  'glow-orbs': '23064308',
  'scan-line': '23064309',
  lightning: '23043921',
  'energy-shield': '23053556',
  'water-caustics': '23030896',

  'xianxia-qi': '23043921',
  'gold-dust': '23053561',
  sparks: '23030908',
  'star-dust': '23064308',
  smoke: '23043918',
  'rain-freeze': '23043923',
  debris: '23043926',
  'energy-converge': '23030906',

  'fire-lotus': '23030908',
  'water-dragon': '23053559',
  'ice-prison': '23053564',
  'thunder-strike': '23043921',
  'wood-prison': '23053563',
  'earth-spikes': '23064304',
  'metal-swords': '23043921',

  shockwave: '23043925',
  'hit-freeze': '23064310',
  'screen-shake': '23053558',
  'anime-impact': '23053558',
  'slow-motion': '23030896',
  'speed-ramp': '23053558',

  'rotate-3d': '23043915',
  'outfit-change': '23053562',
  'product-sweep': '23043916',
  'floating-in': '23053565',

  rain: '23043923',
  snow: '23053564',
  fog: '23043918',
  sandstorm: '23064304',
  'strong-wind': '23053563',
};

export function effectPreviewTemplateId(id: string): string | null {
  return EFFECT_PREVIEW_TEMPLATE_IDS[id] ?? null;
}
