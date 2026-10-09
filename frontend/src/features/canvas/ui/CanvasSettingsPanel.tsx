// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ReactNode } from 'react';
import {
  Activity,
  BadgeDollarSign,
  Check,
  Grid3X3,
  Magnet,
  Map,
  Mouse,
  Palette,
  RotateCcw,
  Route,
  Settings2,
  X,
} from 'lucide-react';

import { useSettingsStore, type CanvasEdgeRoutingMode } from '@/stores/settingsStore';
import { useSnapAlignStore } from '../snap-align/snapAlignStore';
import { useTrackpadPanStore } from '../trackpad-pan/trackpadPanStore';
import { CANVAS_TOOL_SURFACE_CLASS } from './canvas-node-menu-shared';
import { isCanvasPerformanceDebugAvailable } from './CanvasFpsMeter';
import {
  CANVAS_EDGE_COLOR_PRESETS,
  CANVAS_THEME_PRESETS,
  type CanvasThemeId,
  useCanvasViewSettingsStore,
} from './canvasViewSettingsStore';

interface CanvasSettingsPanelProps {
  onClose: () => void;
}

function SectionLabel({
  icon: Icon,
  children,
}: {
  icon: typeof Settings2;
  children: string;
}) {
  return (
    <div className="flex items-center gap-1.5 px-1 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/34">
      <Icon className="h-3 w-3" aria-hidden="true" />
      {children}
    </div>
  );
}

function Divider() {
  return <div className="mx-1 my-2.5 h-px bg-white/[0.06]" />;
}

interface SettingSwitchProps {
  checked: boolean;
  label: string;
  description: string;
  icon: typeof Settings2;
  onChange: (checked: boolean) => void;
}

function SettingSwitch({
  checked,
  label,
  description,
  icon: Icon,
  onChange,
}: SettingSwitchProps) {
  return (
    <div className="group flex min-h-[50px] items-center gap-2.5 rounded-[10px] px-1.5 py-1.5 transition-colors hover:bg-white/[0.035]">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px] border border-white/[0.055] bg-white/[0.04] text-white/52 transition-colors group-hover:text-white/74">
        <Icon className="h-[15px] w-[15px]" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[12px] font-medium leading-4 text-white/82">{label}</div>
        <div className="mt-0.5 text-[10px] leading-[15px] text-white/38">{description}</div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        data-state={checked ? 'checked' : 'unchecked'}
        onClick={() => onChange(!checked)}
        className={`canvas-setting-switch relative h-5 w-9 shrink-0 rounded-full border transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/28 ${
          checked
            ? 'border-[#7df9ff]/45 bg-[#7df9ff]/25'
            : 'border-white/[0.12] bg-black/30 hover:border-white/24 hover:bg-white/[0.07]'
        }`}
      >
        <span
          className={`canvas-setting-switch__thumb absolute left-[2px] top-[2px] h-3.5 w-3.5 rounded-full ${
            checked ? 'bg-[#eafeff]' : 'bg-white/52'
          }`}
        />
      </button>
    </div>
  );
}

interface SegmentOption<T extends string | number> {
  label: string;
  value: T;
}

/** 纯文字分段控件：选项本身已经说清含义时用它（例如连线路径）。 */
function SegmentedSetting<T extends string | number>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="px-1.5 py-1">
      <div className="mb-1.5 px-1 text-[11px] font-medium text-white/56">{label}</div>
      <div className="grid grid-flow-col auto-cols-fr gap-0.5 rounded-[10px] border border-white/[0.06] bg-black/25 p-[3px]">
        {options.map((option) => {
          const active = option.value === value;
          return (
            <button
              key={String(option.value)}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(option.value)}
              className={`h-7 whitespace-nowrap rounded-[7px] px-2 text-[11px] font-medium transition-[background-color,color,box-shadow] duration-150 ${
                active
                  ? 'bg-white/[0.13] text-white/92 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]'
                  : 'text-white/40 hover:bg-white/[0.05] hover:text-white/72'
              }`}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * 带预览的分段控件。用于「密度」「点大小」这类光看名字分不清的档位：
 * 每个选项把参数直接画成点阵，选项之间拉开成四个等宽卡片，
 * 中文标签不再被挤成竖排。
 */
function PreviewTileGroup<T extends string | number>({
  label,
  options,
  value,
  onChange,
  renderPreview,
}: {
  label: string;
  options: readonly SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  renderPreview: (value: T) => ReactNode;
}) {
  return (
    <div className="px-1.5 py-1">
      <div className="mb-1.5 px-1 text-[11px] font-medium text-white/56">{label}</div>
      <div className="grid grid-cols-4 gap-1.5">
        {options.map((option) => {
          const active = option.value === value;
          return (
            <button
              key={String(option.value)}
              type="button"
              aria-pressed={active}
              aria-label={`${label}：${option.label}`}
              onClick={() => onChange(option.value)}
              className={`flex flex-col items-center gap-1 rounded-[9px] border px-1 pb-1.5 pt-2 transition-colors ${
                active
                  ? 'border-[#7df9ff]/40 bg-[#7df9ff]/[0.09]'
                  : 'border-white/[0.06] bg-white/[0.02] hover:border-white/[0.16] hover:bg-white/[0.05]'
              }`}
            >
              <span
                className="block h-6 w-full overflow-hidden rounded-[5px] bg-black/45"
                aria-hidden="true"
              >
                {renderPreview(option.value)}
              </span>
              <span
                className={`text-[10px] leading-none ${active ? 'text-white/90' : 'text-white/48'}`}
              >
                {option.label}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

const GRID_DENSITY_OPTIONS = [
  { label: '宽松', value: 32 },
  { label: '标准', value: 20 },
  { label: '紧凑', value: 12 },
  { label: '极密', value: 8 },
] as const;

const GRID_DOT_SIZE_OPTIONS = [
  { label: '细', value: 1.5 },
  { label: '标准', value: 2.4 },
  { label: '粗', value: 3.2 },
  { label: '特粗', value: 4 },
] as const;

const EDGE_ROUTING_OPTIONS: readonly SegmentOption<CanvasEdgeRoutingMode>[] = [
  { label: '自然曲线', value: 'spline' },
  { label: '清晰直角', value: 'orthogonal' },
  { label: '智能避让', value: 'smartOrthogonal' },
];

/**
 * 密度预览：点阵间距按档位缩放。预览盒只有 24px 高，所以按 1/4 比例缩小，
 * 保留「越密越紧」的相对关系，而不是照搬像素值。
 */
function GridDensityPreview({ gap }: { gap: number }) {
  const step = Math.max(3.5, gap / 4);
  const radius = Math.min(1.1, Math.max(0.7, step / 6));
  return (
    <span
      className="block h-full w-full"
      style={{
        backgroundImage: `radial-gradient(circle, rgba(255,255,255,0.5) ${radius}px, transparent ${radius}px)`,
        backgroundSize: `${step}px ${step}px`,
        backgroundPosition: 'center',
      }}
    />
  );
}

/** 点大小预览：间距固定 10px，只变点径，档位差别一眼能比出来。 */
function GridDotSizePreview({ size }: { size: number }) {
  // 下限抬到 0.85px：最小的「细」档如果按真实半径画，在 24px 预览盒里几乎看不见。
  const radius = 0.85 + (size - 1.5) * 0.42;
  return (
    <span
      className="block h-full w-full"
      style={{
        backgroundImage: `radial-gradient(circle, rgba(255,255,255,0.62) ${radius}px, transparent ${radius}px)`,
        backgroundSize: '10px 10px',
        backgroundPosition: 'center',
      }}
    />
  );
}

function CanvasAppearanceSettings({
  canvasTheme,
  customCanvasColor,
  edgeColor,
  onCanvasThemeChange,
  onCustomCanvasColorChange,
  onEdgeColorChange,
}: {
  canvasTheme: string;
  customCanvasColor: string;
  edgeColor: string;
  onCanvasThemeChange: (value: CanvasThemeId) => void;
  onCustomCanvasColorChange: (value: string) => void;
  onEdgeColorChange: (value: string) => void;
}) {
  return (
    <div className="px-1.5 py-1">
      <div className="mb-1.5 px-1 text-[11px] font-medium text-white/56">画布主题</div>
      <div className="grid grid-cols-3 gap-1.5">
        {CANVAS_THEME_PRESETS.map((theme) => {
          const active = theme.id === canvasTheme;
          return (
            <button
              key={theme.id}
              type="button"
              aria-label={`画布主题：${theme.label}`}
              aria-pressed={active}
              onClick={() => onCanvasThemeChange(theme.id)}
              className={`group relative rounded-[9px] border p-1 text-left transition-colors ${
                active
                  ? 'border-[#7df9ff]/38 bg-white/[0.07]'
                  : 'border-white/[0.06] bg-white/[0.02] hover:border-white/[0.16] hover:bg-white/[0.05]'
              }`}
            >
              <span
                className="block h-9 rounded-[6px] border border-white/[0.06]"
                style={{
                  backgroundColor: theme.background,
                  backgroundImage: `radial-gradient(circle, ${theme.grid} 1px, transparent 1px)`,
                  backgroundSize: '8px 8px',
                }}
              />
              <span className="mt-1 flex items-center justify-between px-0.5">
                <span className={`text-[10px] ${active ? 'text-white/88' : 'text-white/44'}`}>
                  {theme.label}
                </span>
                {active && (
                  <Check
                    className="h-3 w-3 text-[#7df9ff]"
                    aria-hidden="true"
                  />
                )}
              </span>
            </button>
          );
        })}
      </div>
      <label
        className={`mt-1.5 flex h-9 cursor-pointer items-center justify-between rounded-[9px] border px-2.5 text-[10px] transition-colors ${
          canvasTheme === 'custom'
            ? 'border-[#7df9ff]/38 bg-white/[0.07] text-white/88'
            : 'border-white/[0.06] bg-white/[0.02] text-white/44 hover:border-white/[0.16] hover:bg-white/[0.05] hover:text-white/72'
        }`}
      >
        自定义画布颜色
        <input
          type="color"
          aria-label="自定义画布颜色"
          value={customCanvasColor}
          onChange={(event) => onCustomCanvasColorChange(event.target.value)}
          className="h-5 w-7 cursor-pointer rounded border-0 bg-transparent p-0"
        />
      </label>

      <div className="mb-1.5 mt-3 px-1 text-[11px] font-medium text-white/56">连线色彩</div>
      <div className="grid grid-cols-5 gap-1.5">
        {CANVAS_EDGE_COLOR_PRESETS.map((preset) => {
          const active = preset.value === edgeColor;
          return (
            <button
              key={preset.value}
              type="button"
              title={preset.label}
              aria-label={`连线色彩：${preset.label}`}
              aria-pressed={active}
              onClick={() => onEdgeColorChange(preset.value)}
              className={`flex h-8 items-center justify-center rounded-[8px] border transition-colors ${
                active
                  ? 'border-white/32 bg-white/[0.09]'
                  : 'border-white/[0.06] bg-white/[0.02] hover:border-white/[0.16] hover:bg-white/[0.05]'
              }`}
            >
              <span
                className="h-[3px] w-7 rounded-full"
                style={{
                  backgroundColor: preset.value,
                  boxShadow: `0 0 8px ${preset.value}`,
                }}
              />
            </button>
          );
        })}
      </div>
      <label className="mt-1.5 flex h-8 cursor-pointer items-center justify-between rounded-[8px] border border-white/[0.06] bg-white/[0.02] px-2.5 text-[10px] text-white/44 transition-colors hover:border-white/[0.16] hover:bg-white/[0.05] hover:text-white/72">
        自定义连线色彩
        <input
          type="color"
          aria-label="自定义连线色彩"
          value={edgeColor}
          onChange={(event) => onEdgeColorChange(event.target.value)}
          className="h-5 w-7 cursor-pointer rounded border-0 bg-transparent p-0"
        />
      </label>
    </div>
  );
}

export function CanvasSettingsPanel({ onClose }: CanvasSettingsPanelProps) {
  const viewSettings = useCanvasViewSettingsStore();
  const snapAlignEnabled = useSnapAlignStore((state) => state.enabled);
  const toggleSnapAlign = useSnapAlignStore((state) => state.toggle);
  const trackpadPanEnabled = useTrackpadPanStore((state) => state.enabled);
  const toggleTrackpadPan = useTrackpadPanStore((state) => state.toggle);
  const showNodePrice = useSettingsStore((state) => state.showNodePrice);
  const setShowNodePrice = useSettingsStore((state) => state.setShowNodePrice);
  const edgeRoutingMode = useSettingsStore((state) => state.canvasEdgeRoutingMode);
  const setEdgeRoutingMode = useSettingsStore((state) => state.setCanvasEdgeRoutingMode);

  const reset = () => {
    viewSettings.reset();
    if (snapAlignEnabled) toggleSnapAlign();
    if (!trackpadPanEnabled) toggleTrackpadPan();
    setShowNodePrice(true);
    setEdgeRoutingMode('spline');
  };

  return (
    <section
      aria-label="画布设置"
      className={`nopan nowheel ${CANVAS_TOOL_SURFACE_CLASS} relative flex max-h-[min(74vh,620px)] w-[372px] max-w-[calc(100vw-24px)] flex-col overflow-hidden rounded-[16px] text-white`}
    >
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/[0.14] to-transparent"
      />
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-white/[0.07] px-4 py-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[9px] border border-white/[0.07] bg-white/[0.05] text-white/74">
            <Settings2 className="h-[15px] w-[15px]" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h2 className="text-[13px] font-semibold tracking-[0.005em] text-white/92">画布设置</h2>
            <p className="mt-0.5 text-[10px] leading-4 text-white/38">
              调整后立即生效，并自动记住你的选择
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭画布设置"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[8px] text-white/38 transition-colors hover:bg-white/[0.075] hover:text-white/84"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </header>

      <div className="ui-scrollbar min-h-0 flex-1 overflow-x-hidden overflow-y-auto px-2 py-2.5">
        <SectionLabel icon={Palette}>画布外观</SectionLabel>
        <CanvasAppearanceSettings
          canvasTheme={viewSettings.canvasTheme}
          customCanvasColor={viewSettings.customCanvasColor}
          edgeColor={viewSettings.edgeColor}
          onCanvasThemeChange={viewSettings.setCanvasTheme}
          onCustomCanvasColorChange={viewSettings.setCustomCanvasColor}
          onEdgeColorChange={viewSettings.setEdgeColor}
        />

        <Divider />
        <SectionLabel icon={Mouse}>交互操作</SectionLabel>
        <SettingSwitch
          icon={Mouse}
          label="触控板平移"
          description="开启后双指平移；关闭后滚轮直接缩放"
          checked={trackpadPanEnabled}
          onChange={() => toggleTrackpadPan()}
        />
        <SettingSwitch
          icon={Magnet}
          label="智能对齐"
          description="拖动节点时贴齐其他节点并显示辅助线"
          checked={snapAlignEnabled}
          onChange={() => toggleSnapAlign()}
        />
        <SettingSwitch
          icon={Grid3X3}
          label="吸附到网格"
          description="拖动节点时按当前网格间距整齐落位"
          checked={viewSettings.snapToGrid}
          onChange={viewSettings.setSnapToGrid}
        />

        <Divider />
        <SectionLabel icon={Grid3X3}>网格与显示</SectionLabel>
        <SettingSwitch
          icon={Grid3X3}
          label="网格底纹"
          description="显示低对比点阵，不干扰素材预览"
          checked={viewSettings.showGrid}
          onChange={viewSettings.setShowGrid}
        />
        {viewSettings.showGrid && (
          <>
            <PreviewTileGroup
              label="网格密度"
              options={GRID_DENSITY_OPTIONS}
              value={viewSettings.gridGap}
              onChange={viewSettings.setGridGap}
              renderPreview={(gap) => <GridDensityPreview gap={gap} />}
            />
            <PreviewTileGroup
              label="网格点"
              options={GRID_DOT_SIZE_OPTIONS}
              value={viewSettings.gridDotSize}
              onChange={viewSettings.setGridDotSize}
              renderPreview={(size) => <GridDotSizePreview size={size} />}
            />
          </>
        )}
        <SettingSwitch
          icon={Map}
          label="导航小地图"
          description="常驻显示全局缩略图，快速定位节点"
          checked={viewSettings.minimapPinned}
          onChange={viewSettings.setMinimapPinned}
        />
        <SettingSwitch
          icon={BadgeDollarSign}
          label="节点价格"
          description="在支持的生成节点上显示费用估算"
          checked={showNodePrice}
          onChange={setShowNodePrice}
        />
        {isCanvasPerformanceDebugAvailable() && (
          <SettingSwitch
            icon={Activity}
            label="性能数据"
            description="显示 FPS、P95 帧耗时、掉帧和长任务"
            checked={viewSettings.performanceHudEnabled}
            onChange={viewSettings.setPerformanceHudEnabled}
          />
        )}

        <Divider />
        <SectionLabel icon={Route}>连线设置</SectionLabel>
        <SegmentedSetting
          label="连线路径"
          options={EDGE_ROUTING_OPTIONS}
          value={edgeRoutingMode}
          onChange={setEdgeRoutingMode}
        />
      </div>

      <footer className="flex shrink-0 items-center justify-between border-t border-white/[0.07] px-4 py-2.5">
        <span className="text-[10px] text-white/30">仅调整画布体验，不会修改节点内容</span>
        <button
          type="button"
          onClick={reset}
          className="flex h-7 items-center gap-1.5 rounded-[8px] px-2.5 text-[11px] text-white/50 transition-colors hover:bg-white/[0.065] hover:text-white/82"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          恢复默认
        </button>
      </footer>
    </section>
  );
}
