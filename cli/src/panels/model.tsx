// /model 面板：两级流程
//   第一级：上下选择模型，Enter 选中进入第二级
//   第二级：上下选择 快速/思考；停在「思考」（无需 Enter）时下方出现推理强度滑块（←→ 选档）；
//           Enter 保存 模型 + 模式 + 强度
import { RGBA, TextAttributes } from '@opentui/core';
import { T } from '../components';
import { PanelFrame } from './frame';
import { SelectorList } from '../selector';
import { EFFORT_LEVELS, EFFORT_META, MODEL_OPTIONS, type EffortLevel } from '../data';

const FG = RGBA.defaultForeground();
const DIM = TextAttributes.DIM;
const INVERSE = TextAttributes.INVERSE;

export type ModelStep = 'model' | 'mode';

export const MODE_ITEMS = [
  { value: 'fast', label: '快速', desc: '直接回答，不展开推理' },
  { value: 'thinking', label: '思考', desc: '先推理再回答，可调推理强度' },
];

/** 推理强度滑块：档位平铺（固定间距、不定宽——多语言文案长度不预设），当前档文字 INVERSE 反色；←→ 移动 */
function EffortSlider({ effort }: { effort: EffortLevel }) {
  return (
    <box flexDirection="row">
      <T fg={FG} attributes={DIM}>{'  └ 推理强度  '}</T>
      {EFFORT_LEVELS.map((lv, i) => (
        <box key={lv} flexDirection="row">
          {i > 0 ? <T fg={FG}>{'  '}</T> : null}
          <T fg={FG} attributes={lv === effort ? INVERSE : DIM}>{EFFORT_META[lv].label}</T>
        </box>
      ))}
    </box>
  );
}

export function ModelPanel({
  step,
  sel,
  width,
  model,
  pendingModel,
  thinking,
  effort,
}: {
  step: ModelStep;
  sel: number;
  width: number;
  /** 当前生效模型（第一级标 ●） */
  model: string;
  /** 第一级选中、待保存的模型 */
  pendingModel: string;
  /** 当前生效模式（第二级标 ●） */
  thinking: boolean;
  effort: EffortLevel;
}) {
  if (step === 'model') {
    return (
      <PanelFrame title="选择模型   ↑↓ 选择   Enter 确认   Esc 返回" width={width}>
        <SelectorList
          items={MODEL_OPTIONS.map((m) => ({ value: m.name, label: m.name, desc: m.meta }))}
          sel={sel}
          current={model}
          width={width}
        />
      </PanelFrame>
    );
  }
  return (
    <PanelFrame title={`${pendingModel}   选择模式   Enter 保存   Esc 返回上级`} width={width}>
      <SelectorList items={MODE_ITEMS} sel={sel} current={thinking ? 'thinking' : 'fast'} width={width} />
      {sel === 1 ? (
        <>
          <EffortSlider effort={effort} />
          <T fg={FG} attributes={DIM}>{`      ${EFFORT_META[effort].label}  ${EFFORT_META[effort].desc}`}</T>
        </>
      ) : null}
    </PanelFrame>
  );
}
