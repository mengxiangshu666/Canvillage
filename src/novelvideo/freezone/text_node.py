"""Freezone 文本工具辅助逻辑。

当前包含：
- 中英文提示词互译
- 故事脚本生成
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, Mapping, Sequence
from uuid import uuid4

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.output import PromptedOutput

from novelvideo.official_defaults import (
    DEFAULT_FREEZONE_STORY_SCRIPT_MODEL,
    DEFAULT_FREEZONE_TRANSLATION_MODEL,
)
from novelvideo.freezone.script_contract import (
    MOTION_PROMPT_SEGMENT_COUNT,
    SHOT_PROMPT_SEGMENT_COUNT,
    split_prompt_segments,
)
from novelvideo.ports.story_script import FreezoneStoryKeyframePlan, ScriptContentIntent
from novelvideo.freezone.script_video_duration import SCRIPT_CONTENT_DURATION_GUIDANCE

FREEZONE_TRANSLATION_PROVIDER = "newapi"
FREEZONE_TRANSLATION_MODEL = DEFAULT_FREEZONE_TRANSLATION_MODEL
FREEZONE_STORY_SCRIPT_MODEL = {
    "id": DEFAULT_FREEZONE_STORY_SCRIPT_MODEL,
    "provider": "newapi",
    "model": DEFAULT_FREEZONE_STORY_SCRIPT_MODEL,
    "label": "Village Infinite Canvas API Story Script",
}
LEGACY_FREEZONE_STORY_SCRIPT_MODEL_IDS = {
    "newapi_gemini_flash",
    "openrouter_gemini_flash",
    "OpenRouter Gemini 2.5 Flash",
}

FREEZONE_TRANSLATION_SYSTEM_PROMPT = """# Freezone Prompt Translator

You translate prompting text between Simplified Chinese and English for creative nodes.

## Goal
- First determine the dominant natural language of the source text.
- If the dominant natural language is English, translate natural-language content into Simplified Chinese.
- If the dominant natural language is Simplified Chinese, translate natural-language content into English.
- Translate accurately while preserving prompting intent.
- Keep the output concise, directly usable as a prompt.
- Preserve cinematic, visual, audio, and motion terminology naturally.

## Rules
1. For mixed-language prompts, use the dominant natural language to decide the opposite target language.
2. Translate all natural-language content that should be user-readable into the target language.
3. Preserve line breaks, list structure, tags, and prompt segmentation when possible.
4. Keep IDs, asset markers, variable names, file names, model names, color codes, bracket tags, and technical tokens intact.
   Examples: [CM_6932], [YZSZ_974d], #00FFFF, 16:9, v2.0, fal.ai.
5. Do not add new details not present in the source.
6. For image/video/audio/text prompting, prefer natural creator-facing wording over literal textbook translation.
7. Only return the source directly when the detected source_language is exactly the same as the target_language.
8. If source_language and target_language differ, copying the original prose is a failure.
9. When translating English into Chinese, keep technical tokens intact but translate every English instruction sentence, rule sentence, heading, and description into Simplified Chinese.
10. When translating Chinese into English, keep technical tokens intact but translate every Chinese instruction sentence, rule sentence, heading, and description into English.
11. Return structured data matching the requested schema. Do not wrap with markdown.
"""

FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT = """# Freezone Story Script Generator

You develop a user's idea, creative brief or script excerpt into a structured story-script table.

## Goal
- Turn the source script into a complete, production-oriented story script.
- Output rows that are directly usable by downstream image and video nodes.
- Keep the result cinematic, concrete, and structured.

## Requirements
1. Break the story into clear numbered shots with sequential `shot_no`, starting from 1.
2. Each row must include all schema fields. Do not omit fields.
3. Prefer concrete visual language over vague abstraction. Every shot should feel filmable.
4. Dialogue should be short and only present when appropriate. If no dialogue is needed, output `无`.
5. For optional human-readable string fields with no meaningful content, use `无`.
   Preserve schema types: empty maps are {}, empty lists are [], numbers are numbers.
6. Use exact schema field names; keep natural-language string values in Simplified Chinese.
7. Return one JSON object matching the schema, without markdown. Never stringify nested
   objects or arrays: director_plan and visual_bible are objects; rows and sequences
   are arrays of objects; assumptions is an array of strings; shot_nos is an array
   of integers; scene_descriptions and prop_descriptions are name-to-string objects.
   Required creative planning must be filled, not replaced with `无`, null or a prose string.

## Table style target
- First infer the user's purpose, audience, genre, and each scene's function from the source.
  Set `content_intent` per scene: action / narrative / product / dialogue / lyrical /
  documentary / tutorial / other. Mixed works may use different intents across scenes.
  A physical verb or a weapon/product in frame alone does not make a scene an action film.
  Explicit user intent takes priority; use other when uncertain rather than inventing a genre.
- Adapt the language to that intent: product shots show design and use; dialogue preserves
  meaningful words and reactions; lyrical scenes allow stillness and long takes; documentary
  and tutorials preserve factual detail and explanation. Only requested action scenes need
  combat beats and impact cuts. Never add fighting, crises, or narration to meet a ratio.
- The result should resemble a production storyboard table, not a prose summary.
- `visual_description` should describe one clear beat of action or state, usually in one concise sentence.
- `shot` should use concise combinations like `近景 / 特写`, `中景 / 仰视`, `全景 / 俯视远景`.
- `emotion` should be compact and specific, often 2-3 short phrases joined by `、`.
- `scene_tags`, `lighting_mood`, `sound` should all be concrete and film-facing.
- `prop_tags` should list only props that carry story weight or recur across shots (顿号 separated, e.g. `玉佩、青铜钥匙`); leave it `无` when the shot has none. Do not repeat ordinary set dressing here — those belong to segment 5 of `shot_prompt`.
- 人物栏只放有生命的人物或生物。产品、器物、武器、家具、车辆和其它无生命主体必须放进 `prop_tags`；即使它是本片主角，也不要把它伪装成 `character_1`。需要持续保持外观的物件要在每个相关镜头复用同一个道具名。
- `scene_tags` 只放可复用的空间或地点（房间、街道、庭院、河面等）。光带、尘粒、倒影、材质纹理、留白、背景色、景别效果和单镜头动作结果不要单独列为场景资产，应写进 `shot_prompt` 第 5/6 段。
- Fill scene_descriptions and prop_descriptions as name-to-design maps, using the exact
  names in this row's scene_tags and prop_tags. For each named asset, write its reusable
  baseline design, not just its name: scenes need geography, landmarks, entrances,
  scale, fixed dressing and materials; props need silhouette, size, construction,
  material, color and working/contact parts. Reuse the same definition across rows.
  Scene geography uses a stable landmark-based map orientation, not a camera's screen
  left/right. Distinguish walkable passages from character travel and camera movement;
  staging_plan identifies whose path it is, its start/end landmarks and the camera's
  viewing direction separately. Do not invent an unlabeled directional arrow.
  Keep transient damage, opening, holding and action in the state fields. Respect supplied
  designs; for idea-only input record important invented design choices in assumptions.
  No tagged assets means an empty map; never invent assets merely to populate the fields.
- 对 `prop_tags` 中会跨镜出现的道具，填写 `prop_state_start`、`prop_state_end`、`prop_state_change`：分别说明本镜起始状态、结束状态和可见变化；静态道具写「保持基准状态」。不得让道具无原因地出现、消失、复原或改变朝向。
- `character_1` should prefer a stable role identifier if inferable, such as `沈昭昭_现代` or `沈昭昭_古装`.
- `character_description_1` should prefer bracketed character-card format, e.g. `[沈昭昭_现代: 28岁女性，面色苍白，神情疲惫，身穿现代简约职业装……]`.
- Character cards freeze identity and baseline design. Put each present character's actual
  costume, goggles, safety equipment and persistent appearance in character_state_start
  and character_state_end, keyed by that character's exact name. Unchanged states must
  be copied verbatim, not paraphrased; pose and emotion belong in start_state/end_state.
  Current states take priority over baseline clothing in character cards and references.
  Describe actual changes in the motion prompt; do not reset equipment at a camera cut.
  New locations and explicit time ellipses may use appropriate changed outfits.

## Duration and action-line guidance
""" + SCRIPT_CONTENT_DURATION_GUIDANCE + """
- Default to allReference with the necessary character, scene, prop and state references,
  including when mode metadata is missing. Identify each reference's responsibility.
  Respect explicitly declared provider limits; missing metadata alone is not a reason
  to recommend single-image generation. Do not invent internal-cut, multi-speaker,
  extension or duration capabilities.
- `keyframe_plan` is optional and belongs to this one video segment, not a reason to
  create more video rows. Add at most the few state changes that the viewer must read:
  contact/weight transfer, an irreversible action state, or the visible ending. Each
  item states the frozen image purpose and the action that connects it. Ordinary
  continuous motion with no identity or contact risk should leave the list empty.
  For contact-heavy or multi-phase action, evaluate which later visible states need
  their own image to preserve contact, support, changed equipment or spatial outcome.
  Do not default to just an opening pose for such segments. Each additional state
  must supply useful new visual information, not paraphrase the first frame or an
  already planned image. Compare each candidate against start_state AND the actual
  pose requested in shot_prompt before retaining it. A fist just before contact and
  the same fist at contact often produce an indistinguishable still: do not add an
  image solely for that tiny temporal distinction. The purpose must name a visible
  difference in contact, support, position, equipment or revealed spatial information.
  If the opening already covers the candidate, omit it or select a later necessary
  state already present in the script, such as separation after impact; do not invent
  an extra action, force an ending image, or require a fixed frame count.
  Describe each frozen state as readable physical geometry, not only temporal words
  such as about to or immediately after. Before-contact states show a clear gap and
  preparation pose; contact states name touching parts, force direction and support;
  separation states show released contact, changed spacing and balance. Keep these
  facts consistent in start_state, shot_prompt and keyframe_plan, including purpose:
  a released-contact state cannot ask for a pose still holding that contact.
  Evaluate each segment independently; examples are not frame-count recipes.
  Do not reset an
  action already underway just to make the first image static or easier to draw.
  Specify who, where, body side, contact/support and action
  phase. Distinguish character anatomical left/right from screen left/right.
- A reference image is guidance, not an exact timeline. In `video_motion_prompt`, name
  the ordered responsibility of the first frame, each planned state frame, assets, and
  any real previous tail. Preserve causal motion and camera timing in text; never ask
  a still reference to imply velocity by itself.
- Keep native video audio enabled; design dialogue, voice, ambience and action sound in
  the video prompt. Do not introduce an external dubbing or music-generation workflow.

## Directing intent
- Before writing rows, fill director_plan with the story promise, protagonist goal,
  core conflict, ending change, visual_bible, rhythm_curve, sound_plan, assumptions
  and sequences. Each sequence lists its shot_nos and purpose, resistance, escalation,
  turn and release, plus staging_plan and performance_plan. Map landmarks, relative
  positions, travel routes and eyelines across the sequence. Describe what characters
  notice, decide, do and react to before choosing cuts; retain meaningful pauses.
  Each row must list its sequence_ids matching the actual sequence content and shot_nos.
  After any merging, addition or reordering, recompute both sides; never leave old numbers.
  For non-character work describe the subject's presentation instead. Adapt these to the genre: works without conflict or a protagonist
  describe their subject and expressive progression instead of inventing drama.
- If the user only provides an idea, make reasonable creative choices and record them
  in assumptions. User intent and references take priority. Do not invent user approval.
- Derive each row's purpose, information reveal, viewpoint, style and cut from this plan.
  Techniques may span several rows; describe the scope in the corresponding sequence.
  This plan is creative intent, not evidence that generated media has passed QC.
- Fill shot_purpose with what the audience should notice or feel and why this framing
  serves it. Do not mechanically alternate shot sizes; same-scale reverse shots, held
  views, empty establishing shots and deliberate jumps can all be appropriate.
- Make spatial information serve viewer understanding: when an action depends on a
  route, obstacle, height, gap or landing target, stage the relevant relationship so
  the audience can read the cause and consequence. Carry that relationship from
  staging_plan into the actual image/motion prompts and visible start/end states;
  a landmark list in the plan alone is insufficient. A close detail can emphasize
  contact or reaction after the surrounding space is understood. An uninterrupted
  view can reveal it through blocking, camera or depth instead of an obligatory
  establishing shot. Preserve intentionally withheld information and reveal it at
  the planned moment; never show a hidden threat early merely to fill geography.
- Give each phase a readable attention target. Use silhouette, separation, eyelines,
  occlusion/reveal, focus or light where supported by the chosen style; avoid equally
  prominent background spectacle competing with a crucial contact or reaction.
  Blocking and camera may transfer attention within one shot, so each new attention
  target does not require a cut. Describe what becomes visible and why the view changes,
  not an abstract command to the audience. Deliberate visual complexity is valid when
  it serves the scene; simplify only competing information that obscures its purpose.
- film_language is open, combinable language, not a fixed menu: narration order,
  subjective/objective viewpoint, blocking, deep staging, long takes, reaction shots,
  crosscutting, ellipsis, montage, graphic/action/eyeline matches, sound bridges,
  silence and color/light progression are examples, not an exhaustive list.
- Choose only techniques that serve this work. Do not turn lyrical, documentary,
  dialogue or product work into an action sequence or impose a universal beat formula.
- cut_reason describes the OUTGOING boundary: why cut now, what the next view adds,
  and whether time/space/action continues or is deliberately omitted. A hard cut can
  still match action; montage can use hard cuts. Do not conflate edit intent with
  first/last-frame generation capability. Last row may state the ending/hold intention.
- start_state and end_state describe visible instantaneous poses, locations, direction,
  action phase and prop state. The end is not a summary of the entire action.
  Put start_state in the image prompt and carry the start-to-end action in the motion
  prompt. Never put abstract editing instructions into dialogue or on-screen text.
- Check the physical bridge between one end_state and the next start_state: getting
  off a board, releasing or taking a prop, changing support/contact and reaching a
  new stance are actions, not consequences of a camera cut. For intended continuous
  action, place the needed bridge in either shot's actual motion and allow time for
  its readable result. If an ordinary action can be inferred and omission serves
  the scene, state the ellipsis in cut_reason/transition_plan; do not claim exact
  action continuity while silently skipping a crucial recovery or consequence.
  Do not force every routine action onto screen or add a shot for every state change.
- Sound bridges and multi-shot techniques are plans, not claims that a video model
  executes them. reference_requirements must state needed coverage or references.
  When model capability is unknown, do not promise a duration, tail-frame lock or
  uninterrupted long take that the downstream generator may not support.

## Prompt formatting rules
Before finalizing generation or revision, review the proposed storyboard as a viewing
experience, not a checklist of camera techniques:
- For character-led work, make the character's specific desire, choice and consequence
  readable through acting and relationships. Do not substitute a generic emotion label
  or a succession of hazards for character development. Other genres follow their own intent.
- When the story calls for character change, distinguish the consciously pursued want
  from a need the character may resist or discover. In protagonist_goal/core_conflict,
  explain their relationship, not just a destination or victory. Design external obstacles
  to test that particular character's internal difficulty; connect sequence resistance,
  turn and release to a visible choice and its consequence. Carry the changed behavior
  into performance_plan and later shots, and make ending_change describe what the character
  now does differently, rather than announcing a moral in dialogue. Wants can evolve;
  do not require want-versus-need conflict or a growth arc in every genre, invent trauma,
  or overwrite an explicitly intended unchanged character or unresolved ending.
- A largely unchanged protagonist may transform other people, relationships or the
  surrounding world. In that case, make ending_change identify whose behavior or
  circumstances change, and carry the protagonist's choice and the affected subject's
  visible response into sequence turn/release, performance_plan and the relevant rows.
  Do not invent personal growth to fill a field. When a changing strategy is intended,
  contrast an earlier response to pressure with a later choice and its consequences;
  establish the connection through action, reaction or changed staging rather than
  a speech announcing the lesson. A repeated situation can reveal that difference,
  but repetition, escalating danger and a resolved victory are not mandatory.
- Design character identity and movement together: the body, silhouette, props and surface
  state should make the intended action possible and readable. Allow an appearance or prop
  state to change only when the story, action or environment causes it, and carry that
  visible change into the next relevant shot; do not freeze a character into an identical
  image when a motivated progression is part of the scene.
- For character-led scenes, use performance_plan to identify what each person wants
  from the other or the situation, what they notice, and the visible tactic they try.
  Put the resulting playable behavior into character_action and motion segment 2;
  a theme or emotion label is not the actor's physical task. Keep inner motives in
  the director plan, not as abstract commands for the video model to visualize.
- In interactions, distinguish the initiating action, what the listener perceives,
  and the listener's response. Let response timing follow attention and the situation;
  avoid identical synchronized faces unless synchrony is intentional. Listening,
  withholding a gesture, a changed distance or an interrupted task may be the beat.
- Match acting to what the framing can reveal: in wide views use readable silhouette,
  weight, orientation, contact and spacing; closer views can show gaze, expression
  onset and release or small hand changes. Select useful channels rather than packing
  every facial muscle and bodily cue into every shot. Preserve subtlety and stillness;
  do not invent tremors, gasps or distress to make a character appear alive.
- Check what the audience knows before and after each meaningful beat; preserve intended
  mystery and deliberate omission. Reveal, reaction, silence and held views can carry story.
- Guide attention with staging, composition, perspective, shape, color and light. Select
  the framing and camera movement that make the intended information readable, without
  imposing a shot-size pattern or a compulsory camera move.
- Treat technique as story grammar: a held pose, sudden contrast, simplified background,
  painterly emphasis, sound drop or accelerated rhythm is valid only when it externalizes
  a character state, reveal or change in audience knowledge. Keep the underlying style
  bible recognizable while allowing motivated local treatment changes.
- Treat feedback as evidence of a viewing problem, not automatically as the right solution.
  When revising, connect the requested change to its audience effect and preserve working
  material. Respect an explicit user solution; if the chosen scope cannot fix the cause,
  do not pretend a local cosmetic change resolves the whole story.
- Revise contradictions before output and keep story intent, performance, shot timing,
  cut purpose and prompts consistent. Do not claim that this internal review or completed
  generation proves audience response, model adherence or cinematic quality.

`shot_prompt` must be image-generation friendly and must be written as a chained bracket structure using ` + ` separators.

Preferred order for `shot_prompt`:
1. `[画面构图：景别、机位、视角、构图关系]`
2. `[角色卡/主体描述：如果存在角色1，必须**逐字照抄** character_description_1 的整段文字（保留方括号与角色 ID）；角色2 同理照抄 character_description_2。多个角色时两段都写进来，不要只留一个，也不要改写、压缩或换称呼。如果没有角色，则写主体/核心对象描述]`
3. `[主体/人物空间与互动关系：谁在前景、谁在中景、谁与什么环境或道具发生关系]`
4. `[极具体的微表情、主体状态或关键视觉信息：人物写眼神、嘴角、服饰与视线；产品写材质、结构、表面与使用状态；空镜写可见细节，不强加人物或伤痕]`
5. `[明确的场景环境元素与前景/背景道具：办公室、宫殿、屏风、龙椅、电脑蓝光、飞尘、门缝光等]`
6. `[光影几何与大气效果：主光方向、冷暖色温、边缘光、雾气、逆光、顶光、体积光等]`
7. `[视觉风格/质感：可辨认的画风媒介、轮廓与细节处理、材质响应、稳定色彩语言；不能只写电影感或高级感]`
8. `[技术参数：镜头焦段、光圈、景深、快门感、干净成像与解析度特征；这一段尽量不要省略，不主动添加胶片颗粒]`

`shot_prompt` 顶层必须**正好 8 段**，只能由 ` + ` 分隔。多个角色时，所有角色卡
都放进第 2 段这**一个**方括号里，用 `；` 连接，不得把每张角色卡拆成额外的 ` + ` 段。
The storyboard image is the shot's initial visible instant, not a summary collage
of the whole action. Align segments 3-5 with start_state and prop_state_start:
position, pose, eyeline, contact and action phase must describe the same moment.
Keep later changes and end_state in the motion prompt, not prematurely completed
in the starting image. A shot can begin mid-motion; do not force a neutral still
pose. Do not draw chronological panels, alternate poses or motion arrows unless
the user explicitly asks for that presentation as the actual image content.

## Cross-row consistency (hard requirement)
- The character card of a given role must be **byte-identical** in every row that role appears in.
  Do not re-describe the character, do not abbreviate, do not add per-shot wardrobe or mood
  variations to the card itself — put those in segments 3-5 instead.
- Keep segment 7 (`视觉风格/质感`) identical throughout the script for a shared visual style.
- Choose segment 8 (`技术参数`) per shot: focal length, aperture and depth of field serve
  the viewing purpose, framing and spatial relationships. Keep or vary them deliberately;
  do not force every shot to copy the first shot's optics.
- Rows may differ in composition, blocking, expression, environment, light geometry and optics.
- The shared style is a baseline, not identical visual treatment in every shot. Put
  story-motivated local contrasts in segments 3-6 and the motion prompt, while keeping
  segment 7 as the common medium/design anchor. A heightened action passage may hold
  readable extreme poses or use graphic rhythm; fear, intimacy or observation may slow
  the body, simplify the frame and let a reaction register. Explain the reason in
  film_language, lighting_mood, duration_reason and the sequence's performance plan.
  Do not copy a studio name or fashionable effect as a recipe, impose contrast on work
  that needs consistency, or promise stepped frame rates without model capability.
  Describe observable pose, timing and emphasis instead of unsupported output parameters.

Example style for `shot_prompt`:
下面是同一选择时刻的两种可选拍法，不是要求先后生成两镜；角色卡与共同材质保持一致。
- 固定观察，让双手、信封与撤回落点同时可读：
  `[画面构图：中近景，桌前略俯，双手与信封同框，桌沿留有撤回空间] + [角色卡/主体描述：[沈昭昭_现代: 28岁女性，面色苍白，神情疲惫，身穿现代简约职业装]] + [主体/人物空间与互动关系：她坐在桌后，双手放在红信封两侧，信封位于桌面中央] + [极具体的微表情、主体状态或关键视觉信息：右手尚未接触信封，左手靠近桌沿，信封封口完整] + [明确的场景环境元素与前景/背景道具：深夜办公室，灰绿桌面，后墙窗框，桌上的红信封] + [光影几何与大气效果：后墙窗光斜照桌面，纸面明暗可辨，红信封与灰绿桌面分离] + [视觉风格/质感：自然比例写实造型，哑光织物与清晰纸纤维，灰绿环境衬托红纸，肤色自然，高光纯净，暗部保留层次] + [技术参数：按桌面取景选择焦段，景深同时保留双手、封口与撤回落点，曝光稳定]`
- 有触发的移动，先读手的选择，再显露未被拿取的信封与人物距离：
  `[画面构图：中景，桌前右侧略俯，人物双手与信封同框，左侧桌沿可见] + [角色卡/主体描述：[沈昭昭_现代: 28岁女性，面色苍白，神情疲惫，身穿现代简约职业装]] + [主体/人物空间与互动关系：她坐在桌后，双手放在红信封两侧，信封位于桌面中央] + [极具体的微表情、主体状态或关键视觉信息：右手尚未接触信封，左手靠近桌沿，信封封口完整] + [明确的场景环境元素与前景/背景道具：深夜办公室，灰绿桌面，后墙窗框，桌上的红信封] + [光影几何与大气效果：后墙窗光斜照桌面，纸面明暗可辨，红信封与灰绿桌面分离] + [视觉风格/质感：自然比例写实造型，哑光织物与清晰纸纤维，灰绿环境衬托红纸，肤色自然，高光纯净，暗部保留层次] + [技术参数：按桌侧取景选择焦段，移动中双手和信封共同清晰，保持纸面细节与背景层次]`

`video_motion_prompt` describes what changes or deliberately remains still over the shot,
using the chained bracket structure for transport. These slots organize information;
they do not prescribe a recurring dramatic pattern, action count or camera recipe.

Preferred order for `video_motion_prompt`:
1. `[明确的摄影机运镜轨迹与速度：按观看目的选择固定观察、景深调度、单一运动或有动机的连续复合运动；写清起始机位与取景、相对主体的路线、速度与变化触发、揭示时点和结束构图，固定时明确机位稳定。不要为填段强加运镜]`
2. `[主体极其具体的物理动作细节或状态变化：写可观察的动作、反应或有意义的保持；等待、克制、停顿及静态主体展示均可，不为填段捏造微动作，也不只写“情绪变化”]`
3. `[环境物理动态：本镜可见光色、材质表现及实际变化或稳定状态；不为每镜强加风、尘土、屏幕闪烁等效果]`
4. `[音效与氛围描述：环境声、器物声、呼吸声、脚步声、雷声等]`
5. `[对话台词与语气：有对白写具体台词与语气，没有就写无]`
6. `[时长：本镜按内容和模型能力确认的秒数]`

Hard rules for `video_motion_prompt`（这六段会被编译成送进视频模型的镜头文档，
段名本身不进画面，内容按段归位）：
- 第 1 段区分固定构图与固定世界机位，固定景别的稳定跟拍可以成立。
  只有确实覆盖全镜才写全程或始终；先跟随后停写清移动阶段与停止触发，
  不要同时要求全程移动和人物开始移动后摄影机不再移动；主体停住后跟拍随之停住可以成立。
- 第 2 段保留 t=0 起点、实际发生的因果变化和末拍状态；只在确有动作先后时用自然语言承接，
  不强制每镜塞满起初/随后/接着/末拍，不编号、不写秒数。无动作变化时具体描述姿态、视线或主体状态怎样保持，
  避免用抖动、喘气、眨眼等装饰性动作填时长。禁止写摄影机/流程的说明句（“保持当前可见主体”“形成可直接剪辑的
  明确切点”“运动在约 N 秒内沿同一方向延续”），这些句子会被模型念出来或画成字幕。
- 第 3 段落实本镜光色、材质及看得见的环境变化或保持，与分镜第6/7段同源；抽象情绪词（悲伤/绝望）和非视觉感官（气味/温度）
  不写。
- 第 4 段只写现场声音的名词清单。禁止出现「人声 / 说话 / 台词 / 对白 / 旁白 / 配音 /
  低语 / 呢喃」，也不要写「无音乐」——音乐由收尾约束统一处理。
- 第 5 段只写说话人和引号里的原文；没有台词整段写「无」。潜台词、语气说明不写进引号；
  语气最多在引号外留一两个短词。台词原文不要同时出现在第 2 段。
- 第 6 段的秒数必须与这一镜的节点时长一致，只写一次。

Example style for `video_motion_prompt`:
沿用上面的可选拍法；示例秒数只展示格式，实际按本镜自然表演、观看节奏与所选模型能力确认，不照抄例子的秒数、动作或镜数。
- `[明确的摄影机运镜轨迹与速度：固定在桌前略俯机位，全程保持双手、信封和桌沿同框，让接近与撤回可同时比较，结束仍保留未被拿取的信封] + [主体极其具体的物理动作细节或状态变化：沈昭昭右手从信封旁接近封口，尚未接触便停住，随后撤回桌沿；左手保持原位，信封始终留在桌面中央] + [环境物理动态：窗光稳定照在桌面，红纸与哑光织物保持各自材质，纸面不随人物撤手改变颜色] + [音效与氛围描述：衣袖摩擦、办公室通风声] + [对话台词与语气：无] + [时长：8.0s]`
- `[明确的摄影机运镜轨迹与速度：起始固定在桌前右侧略俯，先看清右手接近与停住；以右手开始撤回为触发，沿桌前同一观察侧缓慢向左横移，让信封与双手的距离逐渐显露，移动中保持三者同框；结束构图保留桌中央信封和桌沿双手，不越过人物与信封的互动轴] + [主体极其具体的物理动作细节或状态变化：沈昭昭右手从信封旁接近封口，尚未接触便停住，随后撤回桌沿；左手保持原位，信封始终留在桌面中央] + [环境物理动态：后墙窗框随机位产生连续视差，窗光仍来自同一位置，红纸与哑光织物保持各自材质，纸面不随移动改变颜色] + [音效与氛围描述：衣袖摩擦、办公室通风声] + [对话台词与语气：无] + [时长：7.0s]`

## Quality bar
- Describe observable image quality in the shared visual style: clean highlights, readable
  shadows, smooth color gradients and clear material boundaries. All character, scene,
  prop and keyframe assets require noise-free imaging: no random speckles, chroma noise,
  added film grain, compression blocks or moire. Preserve intended brushwork, pores, fur,
  fabric weave and story-required wear or dirt; do not replace texture with waxy smoothing.
- Enrich prompts with relevant facts, not adjective piles or invented specifications.
  Define silhouette, proportions, construction and material response from the design;
  define spatial landmarks, routes, light source and contact shadows from the scene.
  Do not use '8K', 'masterpiece' or studio names as substitutes for those descriptions.
- Motion follows the performance and physical cause: preparation, weight shift, contact,
  force, follow-through and settling where relevant. Show gaze and reaction before a
  decision when the story needs it. Respect the subject's anatomy and the actual duration;
  preserve useful stillness, avoid adding compulsory motion or a fixed action count.
  Keep contact points, occlusions, screen direction, material detail and exposure coherent
  over time; describe motivated light changes rather than arbitrary flicker.
  Surface texture stays attached to the moving object; stationary areas remain stable
  across frames. Exclude random temporal speckles, crawling texture, edge shimmer and
  compression blocks. Preserve natural motion blur, motivated lighting changes and
  intentional dirt; do not denoise by changing identity, shape, style or erasing detail.
- Avoid generic outputs like `人物站着`, `镜头推进`, `情绪复杂`.
- Prefer highly specific physical action, facial detail, scene detail, and camera-language wording.
- Preserve story logic and character-state progression across rows.
"""

FREEZONE_VISION_STORY_SCRIPT_SYSTEM_PROMPT = FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT + """

## Reference-grounded generation
Images may be attached to this request. Treat them as source evidence, not decoration.

### Video-keyframe mode
- The ordered images are keyframes from one reference video. Describe the actual subject, setting,
  wardrobe/species, action and visual style shown in those frames; do not invent an unrelated story.
- Group adjacent frames into coherent shots. Every row must set `keyframe_index` to the 1-based
  index of the attached frame that best represents that shot.
- Keep total row duration close to the supplied video duration when present.

### Character-reference mode
- The attached images are character references in the exact order named in the task.
- Character appearance, costume, era and visual traits must come from those images. Reuse the
  provided character names exactly so the backend can bind the correct asset URL.
- This mode has no video frames: set `keyframe_index` to 0 in every row.

Never fabricate reference URLs. Leave character image and reference fields empty: the backend fills
them from project assets after validating the generated character names and frame indexes.
"""

FREEZONE_NODE_TYPE_LABELS: dict[str, str] = {
    "generic": "通用提示词",
    "image": "图片节点提示词",
    "video": "视频节点提示词",
    "audio": "音频节点提示词",
    "text": "文本节点提示词",
}

_translation_agents: dict[str, Agent] = {}
_story_script_agents: dict[str, Agent] = {}
_shot_rewrite_agents: dict[str, Agent] = {}
_vision_story_script_agents: dict[str, Agent] = {}


class FreezoneTranslationResult(BaseModel):
    """Structured translation result produced by the LLM."""

    translated_text: str = Field(description="Translated prompt text.")
    source_language: Literal["zh", "en"] = Field(
        description="Dominant natural language detected from the source text."
    )
    target_language: Literal["zh", "en"] = Field(
        description="Opposite target language used for translation."
    )


class FreezoneShotRewriteRow(BaseModel):
    """单镜重写的结构化结果。

    刻意只包含**一个镜头自己的字段**：整表重发会让模型顺手「润色」其它行，
    一次局部修改就变成一次全篇重写。任务要求保留角色卡、全片风格和本镜技术参数；
    结果再经共享合同检查与机械修复。
    """

    content_intent: ScriptContentIntent = Field(
        default="other", description="本场创作意图，结合用户原意判断；不可为消除提醒而改变类型"
    )
    duration: float = Field(gt=0, allow_inf_nan=False, description="这一镜的时长，单位秒，可为小数")
    duration_policy: Literal["", "single_action_line", "multi_beat", "fixed_timing"] = ""
    duration_reason: str | None = Field(default=None, max_length=1000, description="本镜动作、对白与停留的时间安排及切点依据；时长改变时同步更新，未修改则保留")
    visual_description: str = Field(description="画面描述，围绕明确观看目的，可含连续表演与多个节拍")
    shot_purpose: str | None = Field(default=None, max_length=600, description="本镜观看目的及取景理由")
    film_language: str | None = Field(default=None, max_length=1000, description="服务表达目的的开放组合手法")
    cut_reason: str | None = Field(default=None, max_length=600, description="通向下一镜的切镜理由")
    start_state: str | None = Field(default=None, max_length=1000, description="首帧可见姿态、空间与道具状态")
    end_state: str | None = Field(default=None, max_length=1000, description="切点可见姿态、空间与道具状态")
    character_state_start: dict[str, Annotated[str, Field(max_length=1000)]] | None = Field(default=None, max_length=2, description="角色名到本镜起始服装装备状态；与连续前镜结束逐字一致，未改则保留")
    character_state_end: dict[str, Annotated[str, Field(max_length=1000)]] | None = Field(default=None, max_length=2, description="角色名到本镜结束服装装备状态；未变化照抄起始，改变时同步真实动作；未改则保留")
    transition_plan: str | None = Field(default=None, max_length=1000, description="本镜通向下一镜的开放衔接计划；未修改则保留")
    generation_mode: str | None = Field(default=None, max_length=100, description="推荐生成方式：all_reference优先；按模型能力选择image_to_video / first_last_frame / text_to_video；未修改则保留")
    reference_requirements: str | None = Field(default=None, max_length=1000, description="本镜生成前需要的角色、场景、尾帧等参考；未修改则保留")
    keyframe_plan: list[FreezoneStoryKeyframePlan] | None = Field(default=None, max_length=4, description="本镜内可选状态关键画面计划；不是拆成更多视频节点")
    prop_state_start: str | None = Field(default=None, max_length=1000, description="本镜开始时关键道具状态；未修改则保留")
    prop_state_end: str | None = Field(default=None, max_length=1000, description="本镜结束时关键道具状态；与动作结果一致")
    prop_state_change: str | None = Field(default=None, max_length=1000, description="本镜道具的可见变化；静态时说明保持")
    shot: str = Field(description="景别，例如「中近景 / 平视」")
    character_action: str = Field(description="可见的角色动作或主体状态；允许有意义的静态展示")
    emotion: str = Field(description="情绪，2-3 个短词组，顿号分隔")
    scene_tags: str = Field(default="", description="场景标签")
    scene_descriptions: dict[str, Annotated[str, Field(max_length=1600)]] | None = Field(default=None, max_length=30, description="场景名到基准空间设定的映射；未改则省略，保留已有设计")
    prop_descriptions: dict[str, Annotated[str, Field(max_length=1600)]] | None = Field(default=None, max_length=30, description="道具名到基准外观设定的映射；未改则省略，保留已有设计")
    prop_tags: str = Field(default="", description="道具标签，没有写「无」")
    lighting_mood: str = Field(default="", description="光影氛围")
    sound: str = Field(default="", description="音效")
    dialogue: str = Field(default="", description="对白，没有写「无」")
    shot_prompt: str = Field(description="8 段式分镜提示词，段间用 ` + ` 连接")
    video_motion_prompt: str = Field(description="6 段式视频运动提示词，段间用 ` + ` 连接")


def _direct_or_newapi_text_model(
    *,
    kind: Literal["text", "vision"],
    model_ref: str | None,
    model_env: str,
    default_model: str,
    timeout_seconds: float | None = None,
):
    """Resolve the selected server-owned direct model before legacy NewAPI."""

    from novelvideo.generators.direct_models import (
        get_direct_pydantic_model,
        is_direct_model_ref,
        resolve_direct_model,
    )

    direct = resolve_direct_model(kind, model_ref)
    if direct is not None:
        runtime_model = get_direct_pydantic_model(
            kind,
            direct.catalog_id,
            timeout_seconds=timeout_seconds or 120.0,
        )
        if runtime_model is None:
            raise RuntimeError(f"所选直连{kind}模型已停用或不存在")
        return runtime_model, direct.catalog_id
    if is_direct_model_ref(model_ref):
        raise ValueError(f"所选直连{kind}模型已停用或不存在")

    from novelvideo.config import get_newapi_text_pydantic_model
    from novelvideo.model_gateway_settings import normalize_direct_model_id

    resolved_model_ref = normalize_direct_model_id(kind, model_ref)
    resolved_default = normalize_direct_model_id(kind, default_model)
    resolved_override = resolved_model_ref or None
    if not resolved_override and not resolved_default:
        raise ValueError(f"尚未配置可用的直连{kind}模型，请先在模型中心配置并检测。")

    return (
        get_newapi_text_pydantic_model(
            model_env,
            resolved_default,
            model_name_override=resolved_override,
            timeout_seconds_override=timeout_seconds,
        ),
        resolved_model_ref or resolved_default,
    )


def _text_model_cache_key(
    *,
    kind: Literal["text", "vision"],
    model_ref: str | None,
    default_model: str,
) -> str:
    """Derive a cache key without constructing an unused provider client."""

    from novelvideo.generators.direct_models import is_direct_model_ref, resolve_direct_model

    direct = resolve_direct_model(kind, model_ref)
    if direct is not None:
        # A stable registry id can be edited in-place. Keep the client cache in
        # lockstep with upstream URL/model/key changes without storing a raw key.
        fingerprint = hashlib.sha256(
            "\0".join(
                (direct.catalog_id, direct.upstream_model, direct.base_url, direct.api_key)
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"{direct.catalog_id}@{fingerprint}"
    if is_direct_model_ref(model_ref):
        raise ValueError(f"所选直连{kind}模型已停用或不存在")
    from novelvideo.model_gateway_settings import normalize_direct_model_id

    return normalize_direct_model_id(kind, model_ref) or normalize_direct_model_id(
        kind,
        default_model,
    )


def create_freezone_translation_agent(model: str | None = None) -> Agent:
    """创建 Freezone 中英互译 Agent。"""
    runtime_model, _resolved_id = _direct_or_newapi_text_model(
        kind="text",
        model_ref=model,
        model_env="FREEZONE_TRANSLATION_MODEL",
        default_model=FREEZONE_TRANSLATION_MODEL,
    )
    return Agent(
        runtime_model,
        system_prompt=FREEZONE_TRANSLATION_SYSTEM_PROMPT,
        output_type=FreezoneTranslationResult,
        name="Freezone Prompt Translator",
    )


def get_freezone_translation_agent(model: str | None = None) -> Agent:
    """获取翻译 Agent 单例。"""
    cache_key = _text_model_cache_key(
        kind="text",
        model_ref=model,
        default_model=FREEZONE_TRANSLATION_MODEL,
    )
    if cache_key not in _translation_agents:
        _translation_agents[cache_key] = create_freezone_translation_agent(model)
    return _translation_agents[cache_key]


def resolve_freezone_story_script_model(model: str | None) -> dict[str, str]:
    model_text = str(model or "").strip()
    from novelvideo.generators.direct_models import is_direct_model_ref, resolve_direct_model

    direct = resolve_direct_model("text", model_text or None)
    if direct is not None:
        return {
            "id": direct.catalog_id,
            "provider": "direct",
            "model": direct.upstream_model,
            "label": direct.label,
        }
    if is_direct_model_ref(model_text):
        raise ValueError("所选直连文字模型已停用或不存在")
    if not model_text:
        direct_default = resolve_direct_model("text", None)
        if direct_default is not None:
            return {
                "id": direct_default.catalog_id,
                "provider": "direct",
                "model": direct_default.upstream_model,
                "label": direct_default.label,
            }
        if not FREEZONE_STORY_SCRIPT_MODEL["model"]:
            raise ValueError("尚未配置可用的直连文字模型，请先在模型中心配置并检测。")
        return dict(FREEZONE_STORY_SCRIPT_MODEL)
    if model_text == FREEZONE_STORY_SCRIPT_MODEL["id"]:
        return dict(FREEZONE_STORY_SCRIPT_MODEL)
    if model_text.casefold() == FREEZONE_STORY_SCRIPT_MODEL["label"].casefold():
        return dict(FREEZONE_STORY_SCRIPT_MODEL)
    if model_text in LEGACY_FREEZONE_STORY_SCRIPT_MODEL_IDS:
        return dict(FREEZONE_STORY_SCRIPT_MODEL)
    return {
        "id": model_text,
        "provider": "newapi",
        "model": model_text,
        "label": model_text,
    }


def _shot_rewrite_character_segment(
    rows: Sequence[Mapping[str, Any]], target_index: int,
) -> str:
    from novelvideo.freezone.script_contract import (
        SHOT_SEGMENT_ORDER, classify_segments, repair_script_rows, split_prompt_segments,
    )

    # Reuse per-character canonicalization; never borrow another shot's cast.
    prompt = str(repair_script_rows(rows).rows[target_index].get("shot_prompt") or "")
    roles = classify_segments(prompt, SHOT_SEGMENT_ORDER)
    return split_prompt_segments(prompt)[roles.index("character_card")] if "character_card" in roles else ""


def build_freezone_shot_rewrite_task(
    *,
    rows: Sequence[Mapping[str, Any]],
    target_index: int,
    instruction: str,
    source_text: str = "",
    director_plan: Mapping[str, Any] | None = None,
) -> str:
    """构建「只改这一镜」的任务。

    三件事必须交给模型以外的机制保证，所以这里用**明确的冻结清单**替代「请保持」这类请求：
    角色卡按本镜人物各自首次出现取基准，第 7 段取首行风格；技术参数按本镜目的调整。
    返回后服务端检查角色、风格与结构，不把各镜技术参数统一为首行。
    """

    from novelvideo.freezone.script_contract import (
        MOTION_SEGMENT_LABELS_ZH,
        MOTION_SEGMENT_ORDER,
        SHOT_SEGMENT_LABELS_ZH,
        SHOT_SEGMENT_ORDER,
        classify_segments,
        split_prompt_segments,
    )

    if not rows:
        raise ValueError("rows is required")
    if not 0 <= target_index < len(rows):
        raise ValueError("target_index out of range")

    table = [dict(row) for row in rows]
    target = table[target_index]

    first_prompt = str(table[0].get("shot_prompt") or "")
    first_segments = split_prompt_segments(first_prompt)
    first_roles = classify_segments(first_prompt, SHOT_SEGMENT_ORDER)

    def _first_segment(role: str) -> str:
        if role in first_roles:
            return first_segments[first_roles.index(role)]
        return ""

    canonical_style = _first_segment("style")

    # 目标行的角色卡可能不是规范形态；以本篇首次出现为准，并把它作为唯一合法卡片下发。
    target_prompt = str(target.get("shot_prompt") or "")
    frozen_card = _shot_rewrite_character_segment(table, target_index)

    table_lines: list[str] = []
    for position, row in enumerate(table):
        shot_no = str(row.get("display_shot_no") or row.get("shot_no") or position + 1)
        marker = " ← 本次要改的就是这一镜" if position == target_index else ""
        table_lines.append(
            f"第 {position + 1} 行（镜号 {shot_no}）："
            f"{str(row.get('visual_description') or '').strip()}｜"
            f"景别 {str(row.get('shot') or '').strip()}｜"
            f"观看目的 {str(row.get('shot_purpose') or '').strip()}｜"
            f"拍法 {str(row.get('film_language') or '').strip()}｜"
            f"首帧 {str(row.get('start_state') or '').strip()}｜"
            f"切点 {str(row.get('end_state') or '').strip()}｜"
            f"服装装备起始 {json.dumps(row.get('character_state_start') or {}, ensure_ascii=False)}｜"
            f"服装装备结束 {json.dumps(row.get('character_state_end') or {}, ensure_ascii=False)}｜"
            f"切镜理由 {str(row.get('cut_reason') or '').strip()}｜"
            f"通向下一镜 {str(row.get('transition_plan') or '').strip()}｜"
            f"道具起始 {str(row.get('prop_state_start') or '').strip()}｜"
            f"道具变化 {str(row.get('prop_state_change') or '').strip()}｜"
            f"道具结束 {str(row.get('prop_state_end') or '').strip()}｜"
            f"时长 {str(row.get('duration') or '').strip()}s｜"
            f"时长依据 {str(row.get('duration_reason') or '').strip()}｜"
            f"对白 {str(row.get('dialogue') or '').strip() or '无'}{marker}"
        )

    parts = [
        "下面是一份已经定稿的分镜脚本表。只重写**标注为「本次要改的」那一行**，"
        "其余行的内容不得出现在你的输出里，也不得被暗示改动。",
        "整表（只作为上下文，用来保证与前后镜衔接）：",
        "\n".join(table_lines),
        "本次要改的这一镜，现有内容：",
        "\n".join(
            f"- {field}：{str(target.get(field) or '').strip() or '（空）'}"
            for field in (
                "duration",
                "duration_reason",
                "visual_description",
                "shot",
                "character_action",
                "content_intent",
                "shot_purpose",
                "film_language",
                "cut_reason",
                "start_state",
                "end_state",
                "character_state_start",
                "character_state_end",
                "transition_plan",
                "generation_mode",
                "reference_requirements",
                "emotion",
                "scene_tags",
                "scene_descriptions",
                "prop_descriptions",
                "prop_tags",
                "prop_state_start",
                "prop_state_change",
                "prop_state_end",
                "lighting_mood",
                "sound",
                "dialogue",
            )
        ),
        f"- 分镜提示词（8 段）：{target_prompt or '（空）'}",
        f"- 视频运动提示词（6 段）：{str(target.get('video_motion_prompt') or '').strip() or '（空）'}",
        f"- 状态关键画面计划：{json.dumps(target.get('keyframe_plan') or [], ensure_ascii=False)}",
        "如果改变时长或表演过程，同步更新 duration_reason 与视频运动提示词中的时间安排。"
        + SCRIPT_CONTENT_DURATION_GUIDANCE,
        "动作或关键道具变化时，同步更新本镜 prop_state_start / prop_state_change / prop_state_end、"
        "人物character_state_start / character_state_end；角色卡与参考图只锁身份和基准设计，当前服装装备以本镜状态为准；"
        "首帧与切点状态以及图像/运动提示词；不要保留互相矛盾的旧状态。"
        "只在前镜通向本镜、或本镜通向后镜明确为连续动作时检查动作接续；"
        "其他切法保留有意省略与换视点。拍法变化时同步更新本镜 transition_plan、"
        "generation_mode 与 reference_requirements，不得把生成方式推荐写成模型已具备的能力。"
        "关键画面计划只保留真正需要冻结的接触、重心、不可逆状态或结束状态，"
        "不要把每个动作节拍都拆成静帧，也不要因此新增视频镜头。邻镜只作上下文，不改写；"
        "未修改的道具状态和生成计划字段可以省略。",
    ]

    frozen: list[str] = [
        "冻结清单（以下整段必须原样出现在你的输出里，逐字相同，不许改写、压缩、重排）："
    ]
    if frozen_card:
        frozen.append(f"- 第 2 段：[{frozen_card}]")
    if canonical_style:
        frozen.append(f"- 第 7 段：[{canonical_style}]")
    parts.append(
        "第 8 段技术参数不是冻结项：按本镜观看目的与用户修改选择焦段、光圈、景深，"
        "与本镜景别、机位和空间关系一致；无改动理由时保留本镜原参数，不照抄第一镜。"
    )
    frozen.append(
        "- 角色卡里出现过的角色 ID 不得改名、不得换称呼；本镜的服装或状态变化写在第 3–5 段里。"
    )
    parts.append("\n".join(frozen))

    parts.append(
        "输出要求：\n"
        "- 分镜提示词必须仍是 8 段，段序固定为："
        + " → ".join(SHOT_SEGMENT_LABELS_ZH[role] for role, _ in SHOT_SEGMENT_ORDER)
        + "，段间用 ` + ` 连接；\n"
        "- 视频运动提示词必须仍是 6 段，段序固定为："
        + " → ".join(MOTION_SEGMENT_LABELS_ZH[role] for role, _ in MOTION_SEGMENT_ORDER)
        + "，段间用 ` + ` 连接；\n"
        "- 视频运动提示词第 6 段写的时长必须与 duration 字段一致；\n"
        "- 只输出这一镜的字段，不要输出其它行的任何内容；\n"
        "- 不要输出 markdown 包裹，不要解释。"
    )

    if source_text.strip():
        parts.append(
            "源剧本（用于确认这一镜在故事里的位置，不要照抄进输出）：\n"
            + source_text.strip()
        )
    if director_plan:
        parts.append("全片导演总图（保持创作方向，不得擅自改写）：\n" + json.dumps(director_plan, ensure_ascii=False))
    parts.append(_story_script_craft_block())
    parts.append(
        f"当前任务：只改第 {target_index + 1} 行，落实以下用户修改要求：\n"
        + (instruction.strip() or "让这一镜更清楚、更可拍")
        + "\n把故事资料用于理解这一镜，按冻结清单保留身份与风格；同步本镜动作、表演、"
        "首末状态、关键画面用途和提示词中的相关变化，保留必要反应、停顿与结果。"
        "只输出这一镜的规定字段。"
    )
    return "\n\n".join(parts)


async def generate_freezone_shot_rewrite(
    *,
    rows: Sequence[Mapping[str, Any]],
    target_index: int,
    instruction: str,
    source_text: str = "",
    director_plan: Mapping[str, Any] | None = None,
    model: str | None = None,
    video_model: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """重写一镜并把结果拼回整表，返回（新表, 合同报告）。

    拼接之后重跑修复器以保持角色卡和全片风格；各镜技术参数不互相覆盖。
    """

    from novelvideo.freezone.script_contract import enforce_story_script_contract

    task = build_freezone_shot_rewrite_task(
        rows=rows,
        target_index=target_index,
        instruction=instruction,
        source_text=source_text,
        director_plan=director_plan,
    )
    from novelvideo.freezone.script_video_duration import run_duration_planned_script

    response = await run_duration_planned_script(get_freezone_shot_rewrite_agent(model), task, video_model)
    rewritten = response.output

    table = [dict(row) for row in rows]
    table[target_index] = merge_freezone_shot_rewrite(rows, target_index, rewritten)
    data = {"rows": table}
    report = enforce_story_script_contract(data, target_index=target_index)
    return data["rows"], report


def merge_freezone_shot_rewrite(
    rows: Sequence[Mapping[str, Any]], target_index: int, rewritten: FreezoneShotRewriteRow,
) -> dict[str, Any]:
    from novelvideo.freezone.script_contract import (
        SHOT_SEGMENT_ORDER, classify_segments, split_prompt_segments,
    )

    frozen_card = _shot_rewrite_character_segment(rows, target_index)
    rewritten_prompt = getattr(rewritten, "shot_prompt", None)
    if frozen_card and rewritten_prompt is not None:
        roles = classify_segments(rewritten_prompt, SHOT_SEGMENT_ORDER)
        if "character_card" not in roles:
            raise ValueError("重写结果缺少本镜角色卡/主体描述，请重试")
        segments = split_prompt_segments(rewritten_prompt)
        segments[roles.index("character_card")] = frozen_card
        rewritten_prompt = " + ".join(f"[{segment}]" for segment in segments)

    target = dict(rows[target_index])
    for field in (
        "duration",
        "duration_policy",
        "duration_reason",
        "visual_description",
        "shot_purpose",
        "film_language",
        "cut_reason",
        "start_state",
        "end_state",
        "character_state_start",
        "character_state_end",
        "transition_plan",
        "generation_mode",
        "reference_requirements",
        "keyframe_plan",
        "content_intent",
        "shot",
        "character_action",
        "emotion",
        "scene_tags",
        "scene_descriptions",
        "prop_descriptions",
        "prop_tags",
        "prop_state_start",
        "prop_state_change",
        "prop_state_end",
        "lighting_mood",
        "sound",
        "dialogue",
        "shot_prompt",
        "video_motion_prompt",
    ):
        value = getattr(rewritten, field, None)
        if field == "shot_prompt":
            value = rewritten_prompt
        if field == "keyframe_plan" and value is not None:
            value = [item.model_dump() if hasattr(item, "model_dump") else item for item in value]
        if value is None:
            continue
        if field == "content_intent" and target.get(field) not in (None, "", "other"):
            continue
        target[field] = value

    return target


async def generate_freezone_script_contract_repair(
    *,
    rows: Sequence[Mapping[str, Any]],
    issues: Sequence[Mapping[str, Any]],
    source_text: str = "",
    director_plan: Mapping[str, Any] | None = None,
    model: str | None = None,
    video_model: str | None = None,
    max_passes: int = 1,
    on_target: Callable[[int, int, Any], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Diagnose and repair authored causal scopes, preserving partial progress.

    A single row that the text model cannot rewrite must not throw away the rows that
    already succeeded: the caller pays for real model calls, so partial progress is
    kept and reported. ``on_target(position, total, target)`` is invoked before each
    rewrite so the task layer can report live progress instead of a frozen percentage.
    """

    from novelvideo.freezone.script_contract import repair_script_rows
    from novelvideo.freezone.script_repair import (
        is_model_repairable_issue,
        plan_script_contract_repairs,
    )

    table = [dict(row) for row in rows]
    from novelvideo.freezone.script_video_duration import explicit_script_duration_target

    target_duration = explicit_script_duration_target(source_text)
    sequence_mode = bool(director_plan and director_plan.get("sequences"))
    if sequence_mode:
        from novelvideo.ports.story_script import FreezoneStoryDirectorPlan

        director_plan = FreezoneStoryDirectorPlan.model_validate(director_plan).model_dump()
    accepted_report = repair_script_rows(table, director_plan=director_plan, target_duration_seconds=target_duration)
    keyframe_fixes = [issue.as_dict() for issue in accepted_report.issues if issue.fixed and issue.rule_id == "script.keyframe.duplicate_plan.v1"]
    table = accepted_report.rows
    passes = max(1, min(int(max_passes), 3))
    active_issues = accepted_report.as_dict()["issues"] if sequence_mode else list(issues)
    planned_rules: set[str] = set()
    reported_repairable_rules = {
        str(issue.get("rule_id") or "").strip()
        for issue in active_issues
        if isinstance(issue, Mapping) and is_model_repairable_issue(issue)
    }
    total = 0
    applied = 0
    rejected = 0
    failures: list[dict[str, Any]] = []
    passes_run = 0
    attempted: set[tuple[int, str]] = set()
    target_results: list[dict[str, Any]] = []
    for pass_number in range(1, passes + 1):
        targets = plan_script_contract_repairs(table, active_issues, attempted=attempted, director_plan=director_plan)
        if not targets:
            break
        passes_run = pass_number
        pass_start = [dict(row) for row in table]
        total += len(targets)
        planned_rules.update(rule_id for target in targets for rule_id in target.rule_ids)
        for position, target in enumerate(targets, start=1):
            scope = target.row_indices or (target.row_index,)
            attempted.update((index, rule_id) for index in scope for rule_id in target.rule_ids)
            receipt = {"row_index": target.row_index, "row_indices": list(scope), "sequence_ids": list(target.sequence_ids), "rule_ids": list(target.rule_ids), "pass": pass_number}
            if on_target is not None:
                on_target(position, len(targets), target)
            try:
                candidate_plan = director_plan
                if target.sequence_ids:
                    from novelvideo.freezone.sequence_rewrite import generate_sequence_rewrite

                    candidate, _report = await generate_sequence_rewrite(
                        rows=table, sequence_id=target.sequence_ids[0], related_sequence_ids=target.sequence_ids[1:],
                        director_plan=director_plan, instruction=target.instruction, source_text=source_text,
                        model=model, video_model=video_model, preserve_structure=True,
                    )
                    sequence_receipt = _report["sequence_rewrite"]
                    candidate_plan = sequence_receipt["director_plan"]
                    receipt["diagnosis"] = sequence_receipt["diagnosis"]
                else:
                    candidate, _report = await generate_freezone_shot_rewrite(
                        rows=table, target_index=target.row_index, instruction=target.instruction,
                        source_text=source_text,
                        **({"director_plan": director_plan} if director_plan is not None else {}),
                        model=model,
                        **({"video_model": video_model} if video_model is not None else {}),
                    )
            except Exception as exc:  # noqa: BLE001 - one bad row must not lose the others
                target_results.append({**receipt, "outcome": "failed"})
                failures.append(
                    {
                        "row_index": target.row_index,
                        "rule_ids": list(target.rule_ids),
                        "error": str(exc)[:300],
                        "pass": pass_number,
                    }
                )
                continue
            if len(candidate) != len(table) or (sequence_mode and any(
                row.get("shot_id") != table[index].get("shot_id") or (index not in scope and row != table[index])
                for index, row in enumerate(candidate)
            )):
                rejected += 1
                target_results.append({**receipt, "outcome": "rejected", "reason": "scope_changed"})
                continue
            candidate_report = repair_script_rows(candidate, director_plan=candidate_plan, target_duration_seconds=target_duration)
            asset_baseline_issues = [
                issue for issue in accepted_report.issues
                if issue.rule_id == "script.assets.definition_consistency.v1"
                and issue.row_index in scope
            ]
            removed_definition = any(
                not isinstance(candidate_report.rows[issue.row_index].get(issue.field), Mapping)
                or not str(candidate_report.rows[issue.row_index][issue.field].get(issue.detail.get("asset")) or "").strip()
                or issue.detail.get("asset") not in {
                    tag.strip() for tag in re.split(r"[、，,；;\n]+", str(candidate_report.rows[issue.row_index].get(
                        "scene_tags" if issue.field == "scene_descriptions" else "prop_tags"
                    ) or ""))
                }
                for issue in asset_baseline_issues
            )
            previous_blockers = {
                (issue.rule_id, issue.row_index, issue.field)
                for issue in accepted_report.blocking
            }
            new_blockers = {
                (issue.rule_id, issue.row_index, issue.field)
                for issue in candidate_report.blocking
            }
            if (
                removed_definition
                or (sequence_mode and any(row != table[index] for index, row in enumerate(candidate_report.rows) if index not in scope))
                or new_blockers - previous_blockers
                or len(candidate_report.blocking) + len(candidate_report.advisory)
                > len(accepted_report.blocking) + len(accepted_report.advisory)
            ):
                rejected += 1
                target_results.append({**receipt, "outcome": "rejected", "reason": "contract_regression"})
                continue
            changed = candidate_report.rows != table or candidate_plan != director_plan
            keyframe_fixes.extend(issue for issue in _report.get("issues", []) if issue.get("fixed") and issue.get("rule_id") == "script.keyframe.duplicate_plan.v1")
            keyframe_fixes.extend(issue.as_dict() for issue in candidate_report.issues if issue.fixed and issue.rule_id == "script.keyframe.duplicate_plan.v1")
            table = candidate_report.rows
            director_plan = candidate_plan
            accepted_report = candidate_report
            applied += int(changed)
            target_results.append({**receipt, "outcome": "applied" if changed else "unchanged"})
        active_issues = repair_script_rows(table, director_plan=director_plan, target_duration_seconds=target_duration).as_dict().get("issues", [])
        active_issues = [
            issue for issue in active_issues
            if isinstance(issue, Mapping) and is_model_repairable_issue(issue)
        ]
        if table == pass_start and not plan_script_contract_repairs(table, active_issues, attempted=attempted, director_plan=director_plan):
            break

    if total and len(failures) == total:
        first_error = failures[0]["error"] if failures else "unknown error"
        raise ValueError(f"一键优化：所有目标镜头都改写失败（{total} 镜）：{first_error}")

    # Always return a fresh report, including the no-op case where the only remaining
    # issues were deterministic fixes or rules the row-rewrite contract cannot change.
    report = repair_script_rows(table, director_plan=director_plan, target_duration_seconds=target_duration)
    payload = report.as_dict()
    for issue in keyframe_fixes:
        if issue not in payload["issues"]:
            payload["issues"].append(issue)
            payload["fixed_count"] += 1
            payload["issue_count"] += 1
    remaining_model_issues = [
        issue for issue in payload.get("issues", [])
        if isinstance(issue, Mapping) and is_model_repairable_issue(issue)
    ]
    payload["repair"] = {
        # Non-local blockers must remain visible even when no row rewrite can fix them.
        "schema": "village_script_contract_repair.v1",
        "targets": total,
        "applied": applied,
        "rejected": rejected,
        "failed": len(failures),
        "passes": passes_run,
        "max_passes": passes,
        "remaining_issue_count": len(remaining_model_issues) + sum(1 for issue in report.blocking if not is_model_repairable_issue(issue.as_dict())),
        "needs_more_repair": bool(remaining_model_issues or report.blocking),
        "scope": "sequence" if sequence_mode else "shot",
        "director_plan": director_plan,
        "stop_reason": (
            "complete" if not remaining_model_issues and not report.blocking else
            "budget_exhausted" if plan_script_contract_repairs(table, remaining_model_issues, attempted=attempted, director_plan=director_plan) else
            "review_required"
        ),
        "target_results": target_results,
        "failures": failures,
        "rule_ids": sorted(planned_rules),
        # 两类问题会落在这里：行级硬伤占满目标后被挤下的全片问题，以及报告里没能定位到
        # 具体镜头、本轮无从下手的行级问题。两种都要如实报出来，界面对用户讲清楚
        # 「再点一次继续」或「这条要自己处理」，而不是让用户以为点了没反应。
        "deferred_rule_ids": sorted(reported_repairable_rules - planned_rules),
    }
    return report.rows, payload


def _story_script_shot_numbers(source_text: str) -> list[int]:
    return [
        int(match)
        for match in re.findall(
            r"(?m)^\s*(?:\*\*)?镜\s*(\d+)(?=[^0-9]|$)",
            str(source_text or ""),
        )
    ]


def _incomplete_story_script_rows(
    data: Any,
) -> list[str]:
    """Return generated rows that still lack executable shot prompts."""

    missing: list[str] = []
    for index, row in enumerate(data.rows, start=1):
        if not str(row.shot_prompt or "").strip():
            missing.append(f"第 {index} 镜缺少分镜提示词")
        else:
            shot_segments = split_prompt_segments(row.shot_prompt)
            if len(shot_segments) != SHOT_PROMPT_SEGMENT_COUNT:
                missing.append(
                    f"第 {index} 镜分镜提示词有 {len(shot_segments)} 段，"
                    f"必须正好 {SHOT_PROMPT_SEGMENT_COUNT} 段"
                )
        if not str(row.video_motion_prompt or "").strip():
            missing.append(f"第 {index} 镜缺少视频运动提示词")
        else:
            motion_segments = split_prompt_segments(row.video_motion_prompt)
            if len(motion_segments) != MOTION_PROMPT_SEGMENT_COUNT:
                missing.append(
                    f"第 {index} 镜视频运动提示词有 {len(motion_segments)} 段，"
                    f"必须正好 {MOTION_PROMPT_SEGMENT_COUNT} 段"
                )
    return missing


def _require_complete_directed_script(data: Any) -> Any:
    from novelvideo.freezone.script_director_validation import require_script_director_plan

    require_script_director_plan(data)
    missing = _incomplete_story_script_rows(data)
    if missing:
        raise ModelRetry("请补齐可执行提示词并返回完整结果：" + "；".join(missing[:12]))
    return data


def create_freezone_story_script_agent(
    model: str | None = None,
    *,
    expected_row_count: int | None = None,
) -> Agent:
    """创建故事脚本生成 Agent。"""
    from novelvideo.ports.story_script import FreezoneStoryScriptGenerateData

    resolved = resolve_freezone_story_script_model(model)
    llm_model, _resolved_id = _direct_or_newapi_text_model(
        kind="text",
        model_ref=resolved["id"] if resolved["provider"] == "direct" else resolved["model"],
        model_env="FREEZONE_STORY_SCRIPT_MODEL",
        default_model=resolved["model"],
        timeout_seconds=300.0,
    )
    agent = Agent(
        llm_model,
        system_prompt=FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT,
        # Keep the full schema in JSON instructions for channels with unreliable tool output.
        output_type=PromptedOutput(FreezoneStoryScriptGenerateData),
        model_settings={"temperature": 0.4},
        # 结构化脚本表字段多，模型偶尔会把时长写成 "2-5"/"3秒" 之类而过不了校验。默认
        # output_retries=1 只给一次纠正机会不够，
        # 抛 "Exceeded maximum output retries (1)"。对齐本仓其它复杂结构化 agent
        # (episode_planner / content_rewriter)提到 3，让模型按回喂的校验错误自我修正。
        output_retries=3,
        name="Freezone Story Script Generator",
    )
    agent.output_validator(_require_complete_directed_script)
    from novelvideo.freezone.script_video_duration import validate_script_video_duration

    agent.output_validator(validate_script_video_duration)
    if expected_row_count:

        @agent.output_validator
        def require_complete_source_coverage(
            data: FreezoneStoryScriptGenerateData,
        ) -> FreezoneStoryScriptGenerateData:
            if len(data.rows) != expected_row_count:
                raise ModelRetry(
                    "源剧本明确标注了 "
                    f"{expected_row_count} 个镜头，rows 必须完整覆盖全部镜头；"
                    f"当前只有 {len(data.rows)} 行。请补齐全部 {expected_row_count} 行，"
                    "不得只返回前几场或摘要。"
                )
            return data

    return agent


def get_freezone_story_script_agent(
    model: str | None = None,
    *,
    expected_row_count: int | None = None,
) -> Agent:
    """获取故事脚本生成 Agent 单例。"""
    resolved = resolve_freezone_story_script_model(model)
    cache_key = (
        _text_model_cache_key(
            kind="text",
            model_ref=resolved["id"],
            default_model=resolved["model"],
        )
        if resolved["provider"] == "direct"
        else resolved["id"]
    )
    if expected_row_count:
        cache_key = f"{cache_key}|rows={int(expected_row_count)}"
    if cache_key not in _story_script_agents:
        _story_script_agents[cache_key] = create_freezone_story_script_agent(
            resolved["id"],
            expected_row_count=expected_row_count,
        )
    return _story_script_agents[cache_key]


def create_freezone_shot_rewrite_agent(model: str | None = None) -> Agent:
    """创建单镜重写 Agent。

    与整表生成共用同一个系统提示词：段序、角色卡逐字、全片风格与逐镜技术参数是同一套，
    换一个系统提示词就等于放两套标准进同一个产品。
    """

    resolved = resolve_freezone_story_script_model(model)
    llm_model, _resolved_id = _direct_or_newapi_text_model(
        kind="text",
        model_ref=resolved["id"] if resolved["provider"] == "direct" else resolved["model"],
        model_env="FREEZONE_STORY_SCRIPT_MODEL",
        default_model=resolved["model"],
    )
    agent = Agent(
        llm_model,
        system_prompt=FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT,
        output_type=PromptedOutput(FreezoneShotRewriteRow),
        output_retries=3,
        name="Freezone Shot Rewriter",
    )
    from novelvideo.freezone.script_video_duration import validate_script_video_duration

    agent.output_validator(validate_script_video_duration)
    return agent


def get_freezone_shot_rewrite_agent(model: str | None = None) -> Agent:
    """获取单镜重写 Agent 单例（按解析后的模型缓存）。"""

    resolved = resolve_freezone_story_script_model(model)
    cache_key = (
        _text_model_cache_key(
            kind="text",
            model_ref=resolved["id"],
            default_model=resolved["model"],
        )
        if resolved["provider"] == "direct"
        else resolved["id"]
    )
    if cache_key not in _shot_rewrite_agents:
        _shot_rewrite_agents[cache_key] = create_freezone_shot_rewrite_agent(resolved["id"])
    return _shot_rewrite_agents[cache_key]


def build_freezone_translation_task(
    *,
    text: str,
    node_type: Literal["generic", "image", "video", "audio", "text"],
) -> str:
    """构建翻译任务。"""
    node_label = FREEZONE_NODE_TYPE_LABELS[node_type]

    parts = [
        f"Translate the following {node_label}.",
        "You must decide whether the dominant natural language is Simplified Chinese or English.",
        "If dominant language is English, translate into Simplified Chinese.",
        "If dominant language is Simplified Chinese, translate into English.",
        "Do not copy the original prose when translating between different languages.",
        "Preserve IDs, file names, bracket tags, color codes, ratios, and model names exactly, but translate the surrounding natural-language instructions.",
        "Keep it directly usable as a creative prompt.",
    ]
    parts.append(f"Source text:\n{text.strip()}")
    return "\n\n".join(parts)


async def translate_freezone_text(
    *,
    text: str,
    node_type: Literal["generic", "image", "video", "audio", "text"] = "generic",
    model: str | None = None,
) -> tuple[str, Literal["zh", "en"], Literal["zh", "en"]]:
    """执行 Freezone 中英互译。"""
    if not text or not text.strip():
        return "", "zh", "en"

    task = build_freezone_translation_task(
        text=text,
        node_type=node_type,
    )
    agent = (
        get_freezone_translation_agent(model)
        if str(model or "").strip()
        else get_freezone_translation_agent()
    )
    response = await agent.run(task)
    result = response.output
    target_language: Literal["zh", "en"] = result.target_language
    if target_language == result.source_language:
        target_language = "zh" if result.source_language == "en" else "en"
    return (
        result.translated_text.strip(),
        result.source_language,
        target_language,
    )


def _story_script_craft_block() -> str:
    """把产品已有的工艺规则与受限词表写进脚本生成任务。

    三件东西本来只服务于图像 / 视频节点的提示词优化器，脚本生成这一步拿不到：
    `filmcraft_kb` 的八条规则、`video_node` 的 23 条运镜词表、参考角色的九种声明。
    而脚本生成的产出正是这些节点的输入——不在这里约束，就得等到出图之后才发现
    「一镜两个运镜」「角色卡被改写」，那时已经花过钱了。
    """

    from novelvideo.production.filmcraft_kb import inject_filmcraft_rules

    rules = inject_filmcraft_rules(node_type="video", params={"shot_count": 2})
    rule_lines = "\n".join(
        f"- [{item['rule_id']}] {item['instruction']}\n  禁忌：{item['avoid']}"
        for item in rules
        if item["rule_id"] not in {
            "craft.framing_distance_arc.v1", "craft.dialogue_ratio_budget.v1",
            "craft.standoff_ceiling.v1", "craft.beat_cadence.v1",
            "craft.single_primary_action.v1", "craft.continuity_handoff.v1",
        }
    )

    camera_names: list[str] = []
    try:
        from novelvideo.freezone.video_node import VIDEO_CAMERA_TEMPLATES

        camera_names = [str(item.get("name") or "") for item in VIDEO_CAMERA_TEMPLATES]
    except Exception:  # pragma: no cover - 词表取不到时退化成不约束，不阻断生成
        camera_names = []
    camera_line = (
        "视频运动提示词第 1 段优先写清一个主要摄影运动，常用参考共 %d 条：%s。"
        "这些不是封闭名单；固定观察、景深调度和有理由的连续复合运镜也可用，"
        "必须写清可见轨迹、速度和终点，复杂方案按模型能力与实测拆分。"
        % (len(camera_names), "、".join(name for name in camera_names if name))
        if camera_names
        else "视频运动提示词第 1 段写清主要摄影运动；固定观察和有理由的连续复合运镜也可用。"
    )

    roles_line = ""
    try:
        from novelvideo.freezone.reference_manifest import REFERENCE_ROLE_LABELS

        roles_line = (
            "参考素材按作用分九种："
            + "、".join(f"{key}（{label}）" for key, label in REFERENCE_ROLE_LABELS.items())
            + "。每张参考图只承担一项，写清它提供什么、不得提供什么——"
            "图片上的标签不算声明，模型不会自己推断。"
        )
    except Exception:  # pragma: no cover
        roles_line = ""

    parts = [
        "工程规则（与图像/视频节点的编译器同源，不是风格建议）：",
        "先识别用户要做的内容与本场目的；只有动作戏采用打戏节拍，不把展示、对白或抒情改成打斗。",
        "一镜围绕明确的观看目的；连续表演可包含观察、判断、行动与反应，不必拆成独立短镜。静态展示和有意义的停留也成立，不必强加身体动作。",
        "连续动作镜才要求结束状态接续起始状态；自然切镜按叙事与空间逻辑衔接。",
        "必须：角色身份、服装、发型、关键特征跨镜沿用，只有剧本明说换装时才改变；",
        "必须：同场景的几何、关键道具状态与世界光源跨镜保持，变化必须写清可见原因；"
        "光源以固定地标为基准，例如东墙窗向桌面照明，屏幕左右随本镜观察方向改变。"
        "换场分别落实本场光色，不把全片硬套成同一来光方向或色温；同场变化交代何时开灯、遮光或改变光源。",
        "涉及身体碰撞的动作才按发力 → 接触 → 受力 → 重心变化 → 结果反馈表达。",
        "视频提示词围绕可见过程写：从本镜起始状态出发，交代谁移动、相对什么、方向与速度、"
        "接触或支撑如何变化，以及最后落在哪里。人物坐在艇里就按座位、脚撑和船体关系写动作，"
        "不要无依据写成站立屈膝；悬空时不要把空气当支撑。不要用‘完成挑战’替代实际动作。",
        "例如滑板起步可写：左脚踩在板面，右脚蹬地带动板轮向前，右脚收回板上，"
        "重心随滑行稳定；摄影机沿同一方向侧跟，让脚与板轮的接触可见。"
        "这只是因果表达示例，不要求每镜照搬动作数、拍法、节拍或时长。",
        "连续表演写清观察与反应发生在什么动作之后；停顿、听对方说话或静止也可成为镜头内容。"
        "镜头变化服务于要看清的接触、关系或揭示，不靠随意推拉转圈制造动感。",
        "摄影先回答本镜要看清什么、什么信息暂不揭示，再选固定观察或运动。"
        "运动稿摄影段写清起始机位/取景、摄影机相对主体或地标的路线、观看方向、速度，"
        "以及何种动作或信息触发转向、加减速或停留，结束时保留什么构图。"
        "人物路线与摄影机路线分别写；焦点转移或主体靠近不等于摄影机推轨。"
        "连续复合运镜按实际先后衔接，转向有可见动机；不要求固定动作数、运动次数或一定停稳。"
        "首图只画摄影起点，切点状态写结束瞬间；跨镜核对视线、观察侧和屏幕方向，"
        "越轴或改观察侧有意为之时在film_language/cut_reason说明，不强制每镜换角度。",
        "全片美术用可见设计定风格：画风媒介、轮廓与细节处理、材料对光的响应及色彩语言，"
        "不能只堆‘电影感、高级感’。visual_bible的camera_language写摄影习惯与适用理由，"
        "逐镜起点/路线/揭示落实到实际摄影段；lighting/color_progression的全片变化"
        "只在对应镜头落实，不把以后才发生的变化放到本镜。"
        "共同画风和材质写入第7段并沿用；本镜光源方向、色彩关系、曝光及剧情引起的变化"
        "写入分镜第6段与lighting_mood，并同步运动稿环境段。"
        "允许有理由的局部光色变化，保留整体设计；去噪不能抹掉笔触、织物、皮肤或剧情磨损。"
        "优化摄影或光色时同步首图、运动稿、首末状态与时长依据，不保留旧的矛盾安排。",
        "全能参考默认同时使用本镜关键帧与所需资产：关键帧锁起始构图和状态，角色图锁身份，"
        "场景图锁空间，道具图锁结构；当前服装装备继承前镜实际状态，不能被资产基准服装覆盖。"
        "不把资产设定表的拼版和标注拍成真实场景；不重复堆满静态外观和泛泛质量形容词。",
        camera_line,
    ]
    if roles_line:
        parts.append(roles_line)
    if rule_lines:
        parts.append("完整规则集（rule_id 供后续归因引用）：\n" + rule_lines)
    return "\n".join(parts)


def build_freezone_story_script_task(
    *,
    source_text: str,
    prompt: str,
    character_refs: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """构建故事脚本生成任务。"""
    parts = [
        "根据以下上传剧本内容生成一个完整的故事脚本表。",
        "先填写 director_plan 导演总图，再按 sequences 的镜号拆 rows；观看目的、拍法、视觉风格与声音须服从总图，用户未指定的选择列入 assumptions。",
        "每段填写 staging_plan 空间调度与 performance_plan 表演推进，先安排注意、判断、行动、反应或主体展示过程，再选择机位和切点；不要拿情绪标签代替表演。" + SCRIPT_CONTENT_DURATION_GUIDANCE,
        "输出字段必须覆盖：镜号、时长、时长依据、画面描述、观看目的、拍法组合、切镜理由、首帧状态、切点状态、角色1、角色描述1、角色图1、参考、景别、角色动作、情绪、场景标签、道具标签、道具起始状态、道具结束状态、道具状态变化、光影氛围、音效、对白、分镜提示词、视频运动提示词、衔接方式、推荐生成方式、参考需求、状态关键画面计划。",
        "如果用户给了额外要求，也必须一起遵守。",
        "请严格按照影视制片表格思路输出，不要输出散文摘要。",
        "请让分镜提示词和视频运动提示词都采用括号分段 + 号连接的格式。",
        "缺失对白时写 `无`；角色图和参考字段一律留空，由后端绑定真实项目资产。",
        "分镜提示词必须像高质量图像生成提示词，视频运动提示词必须像高质量视频运动提示词，而不是简单一句概括。",
    ]
    source = source_text.strip()
    expected_rows = len(_story_script_shot_numbers(source))
    if expected_rows:
        parts.append(
            f"源剧本已经明确标注了 {expected_rows} 个镜头。rows 必须逐镜覆盖全部 "
            f"{expected_rows} 个镜号，禁止只生成开头、第一场或摘要；输出 rows 的长度"
            f"必须正好是 {expected_rows}。"
        )
        parts.append(
            "对白必须服从画面可看性：如果源剧本台词很密，只把推进剧情、反转或情绪落点"
            "所必需的台词留在 dialogue / 视频运动提示词第 5 段，其余镜头写 `无`，"
            "把信息交给动作、空间和视觉细节。对白戏和教程保留理解所需的原文，"
            "不要为了满足固定无对白比例删掉必要内容。"
        )
    reference_block = _story_script_character_reference_block(character_refs)
    if reference_block:
        parts.append(reference_block)
    parts.append(
        "参考风格要点：\n"
        "- 镜号连续递增\n"
        "- 时长按实际动作、对白、观察与有意义的停留决定，不设短镜默认范围，不按固定长短排列；只有想法时镜数按表达需要决定\n"
        "- 景别写法类似 `近景 / 特写`、`中景 / 仰视`\n"
        "- 景别可使用大远景、远景、全景、中远景、中景、近景、特写；按观看目的选择，同景别正反打、换主体和有意保持可以成立，不强制跨两档\n"
        "- 角色描述尽量写成 `[角色ID: ...]` 形式\n"
        "- 道具标签只写有戏剧功能或跨镜反复出现的道具（顿号分隔，如 `玉佩、青铜钥匙`），没有就写 `无`；普通陈设不写在这里，它们属于分镜提示词第 5 段\n"
        "- 分镜提示词最好严格按 8 段写：构图、角色卡/主体描述、空间关系、微表情/状态、环境与道具、光影几何、视觉风格、技术参数\n"
        "- 分镜提示词的顶层 ` + ` 分隔必须正好 8 段；多个角色卡全部放进第 2 段，用 `；` 连接，不能把第二张角色卡当作第 3 段\n"
        "- 如果存在角色1，分镜提示词第二段必须**逐字照抄**角色描述1 的整段文字（含方括号），不要改写、不要压缩、不要换成模糊代称，也不要补自己的描写\n"
        "- 同一份脚本里「视觉风格」段必须全片统一：其余每行逐字照抄第一行的风格段，保持质感与调色方向\n"
        "- 「技术参数」段按每镜观看目的选择焦段、光圈与景深，配合景别、机位和空间关系；可以保持也可以变化，不强制全片照抄第一镜，不用参数堆砌代替可见画面设计\n"
        "- 视频运动提示词最好严格按 6 段写：运镜轨迹、主体动作、环境动态、音效氛围、对白语气、时长\n"
        "- 视频运动提示词第 6 段写的时长必须与该行的时长列一致（两者会一起送进视频模型，不一致就是自相矛盾）\n"
        "- 视频运动提示词里的主体动作必须是可见物理动作，不要只写情绪变化\n"
        "- 视频提示词按 MCSLA 写：先写模型可执行的摄影机（景别、机位、主要轨迹与有理由的变化），再写主体、风格和按时间发生的连续表演；避免互相矛盾的同时指令，但不强制一个动作或单一运镜；图生视频只写会动的内容，不重复静态参考图已经确定的身份与场景"
    )
    parts.append(_story_script_craft_block())
    parts.append(f"源剧本内容：\n{source}")
    parts.append(
        "当前创作任务：\n"
        + (prompt.strip() or "把以上故事转成可执行的完整导演规划和分镜脚本。")
        + "\n围绕这个任务组织导演规划和全部镜头，资料和示例只提供依据，不照搬镜数、"
        "时长或关键帧数量。保留故事中的必要因果、表演细节、反应、停顿和结果；"
        "检查每镜目标、可见状态与参考用途一致。按规定字段输出完整结构化结果。"
    )
    return "\n\n".join(parts)


async def generate_freezone_story_script(
    *,
    source_text: str,
    prompt: str = "",
    model: str | None = None,
    video_model: str | None = None,
    character_refs: Sequence[Mapping[str, Any]] | None = None,
):
    """执行故事脚本生成。"""
    from novelvideo.freezone.script_video_duration import explicit_script_duration_target

    target_duration = explicit_script_duration_target(source_text, prompt)
    if not source_text.strip():
        source_text, prompt = prompt.strip(), ""
    if not source_text:
        raise ValueError("请提供创作想法或剧本正文")

    task = build_freezone_story_script_task(
        source_text=source_text,
        prompt=prompt,
        character_refs=character_refs,
    )
    expected_rows = len(_story_script_shot_numbers(source_text))
    from novelvideo.freezone.script_video_duration import run_duration_planned_script

    response = await run_duration_planned_script(get_freezone_story_script_agent(
        model,
        expected_row_count=expected_rows or None,
    ), task, video_model, **({"target_duration_seconds": target_duration} if target_duration is not None else {}))
    return response.output


def _story_script_character_reference_block(
    character_refs: Sequence[Mapping[str, Any]] | None,
) -> str:
    """Render durable character labels for the structured-story prompt.

    URLs intentionally never enter the prompt: model output cannot be trusted to reproduce them,
    and ``bind_story_script_assets`` is the single owner of asset binding.
    """

    rows: list[str] = []
    for index, ref in enumerate(character_refs or (), start=1):
        name = str(ref.get("name") or "").strip() or f"角色{index}"
        description = str(ref.get("description") or "").strip()
        role = str(ref.get("role") or "").strip()
        detail = "，".join(item for item in (role, description) if item)
        rows.append(f"- {name}" + (f"：{detail}" if detail else ""))
    if not rows:
        return ""
    return (
        "已提供的角色参考图按以下顺序附在任务中：\n"
        + "\n".join(rows)
        + "\n生成行中的 character_1 / character_2 必须复用上述角色名；"
        "角色图 URL 不要自行填写，由后端回填。"
    )


def build_freezone_video_story_script_task(
    *,
    frame_count: int,
    prompt: str,
    duration_sec: float | None = None,
    character_refs: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    duration_hint = (
        f"参考视频总时长约 {duration_sec:.2f} 秒，所有镜头时长之和应接近该时长。"
        if duration_sec and duration_sec > 0
        else "参考视频总时长未知，请根据关键帧的时间顺序分配合理镜头时长。"
    )
    parts = [
        f"下面按时间顺序附了 {frame_count} 张从参考视频抽出的关键帧，请生成完整故事脚本表。",
        "这是视频拆解任务：必须如实描述关键帧里实际出现的主体、场景、动作与风格，禁止套用示例剧情。",
        duration_hint,
        f"每一行必须写 keyframe_index，范围为 1 到 {frame_count}，用于绑定该镜真实参考图。",
        "角色图和参考字段留空，由后端绑定项目资产。",
    ]
    if prompt.strip():
        parts.append(f"用户额外要求：\n{prompt.strip()}")
    reference_block = _story_script_character_reference_block(character_refs)
    if reference_block:
        parts.append(reference_block)
    return "\n\n".join(parts)


def build_freezone_character_story_script_task(
    *,
    image_count: int,
    prompt: str,
    source_text: str = "",
    character_refs: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    parts = [
        f"下面附了 {image_count} 张角色参考图，请生成一张完整故事脚本表。",
        "角色外貌、服装、年代与气质必须严格来自参考图；剧情来自用户要求与可选源剧本。",
        "所有行的 keyframe_index 必须为 0；角色图和参考 URL 留空，由后端绑定真实资产。",
    ]
    if prompt.strip():
        parts.append(f"用户要求：\n{prompt.strip()}")
    else:
        parts.append("围绕这些角色编排一段结构完整、可执行的短剧。")
    reference_block = _story_script_character_reference_block(character_refs)
    if reference_block:
        parts.append(reference_block)
    if source_text.strip():
        parts.append(f"源剧本内容：\n{source_text.strip()}")
    return "\n\n".join(parts)


def create_freezone_vision_story_script_agent(model: str | None = None) -> Agent:
    """Use the dedicated vision route for story scripts with image evidence."""

    from novelvideo.ports.story_script import FreezoneStoryScriptGenerateData

    runtime_model, _resolved_id = _direct_or_newapi_text_model(
        kind="vision",
        model_ref=model,
        model_env="",
        default_model="",
        timeout_seconds=300.0,
    )

    agent = Agent(
        runtime_model,
        system_prompt=FREEZONE_VISION_STORY_SCRIPT_SYSTEM_PROMPT,
        output_type=PromptedOutput(FreezoneStoryScriptGenerateData),
        output_retries=3,
        name="Freezone Reference Story Script Generator",
    )
    agent.output_validator(_require_complete_directed_script)
    from novelvideo.freezone.script_video_duration import validate_script_video_duration

    agent.output_validator(validate_script_video_duration)
    return agent


def get_freezone_vision_story_script_agent(model: str | None = None) -> Agent:
    cache_key = _text_model_cache_key(
        kind="vision",
        model_ref=model,
        default_model="",
    )
    if cache_key not in _vision_story_script_agents:
        _vision_story_script_agents[cache_key] = create_freezone_vision_story_script_agent(model)
    return _vision_story_script_agents[cache_key]


async def generate_freezone_story_script_with_vision(
    *,
    frame_paths: Sequence[str | Path] | None = None,
    character_image_paths: Sequence[str | Path] | None = None,
    source_text: str = "",
    prompt: str = "",
    duration_sec: float | None = None,
    character_refs: Sequence[Mapping[str, Any]] | None = None,
    model: str | None = None,
    video_model: str | None = None,
):
    """Generate a story script from verified local frame/character image evidence."""

    from pydantic_ai import BinaryContent

    from novelvideo.freezone.vision_gateway import image_media_type

    frames = [Path(path) for path in frame_paths or () if Path(path).is_file()]
    character_images = [
        Path(path) for path in character_image_paths or () if Path(path).is_file()
    ]
    if not frames and not character_images:
        raise ValueError("at least one video keyframe or character reference image is required")
    task = (
        build_freezone_video_story_script_task(
            frame_count=len(frames),
            prompt=prompt,
            duration_sec=duration_sec,
            character_refs=character_refs,
        )
        if frames
        else build_freezone_character_story_script_task(
            image_count=len(character_images),
            prompt=prompt,
            source_text=source_text,
            character_refs=character_refs,
        )
    )
    attachments = [
        BinaryContent(data=path.read_bytes(), media_type=image_media_type(str(path)))
        for path in (*frames, *character_images)
    ]
    from novelvideo.freezone.script_video_duration import run_duration_planned_script

    from novelvideo.freezone.script_video_duration import explicit_script_duration_target

    target_duration = explicit_script_duration_target(source_text, prompt)
    response = await run_duration_planned_script(get_freezone_vision_story_script_agent(model), [task, *attachments], video_model,
        **({"target_duration_seconds": target_duration} if target_duration is not None else {}))
    return response.output


def bind_story_script_assets(
    data: Any,
    *,
    frame_urls: Sequence[str] | None = None,
    character_refs: Sequence[Mapping[str, Any]] | None = None,
    preserve_existing: bool = False,
    target_indices: Sequence[int] | None = None,
) -> Any:
    """Bind only verified project assets into model-produced rows.

    The model chooses semantic character names and representative frame indexes; URL ownership
    remains server-side so a bad model completion cannot create stale or cross-project references.
    """

    frames = [str(url).strip() for url in frame_urls or () if str(url).strip()]
    by_name: dict[str, str] = {}
    ordered_images: list[str] = []
    for ref in character_refs or ():
        name = str(ref.get("name") or "").strip()
        image_url = str(ref.get("image_url") or "").strip()
        if image_url:
            ordered_images.append(image_url)
        if name and image_url:
            by_name[name.casefold()] = image_url

    def match_character(name: object) -> str:
        normalized = str(name or "").strip().casefold()
        if not normalized:
            return ""
        if normalized in by_name:
            return by_name[normalized]
        for candidate, image_url in by_name.items():
            if candidate in normalized or normalized in candidate:
                return image_url
        return ""

    scope = set(target_indices) if target_indices is not None else None
    for index, row in enumerate(getattr(data, "rows", ()) or ()):
        if scope is not None and index not in scope:
            continue
        # Identity is assigned server-side after structured output. Keeping it out
        # of the model schema prevents duplicate or reused IDs across regenerations.
        if not preserve_existing or not str(getattr(row, "shot_id", "") or "").strip():
            row.shot_id = f"shot_{uuid4().hex}"
        if not preserve_existing or not getattr(row, "shot_order", 0):
            row.shot_order = index + 1
        if not preserve_existing or not str(getattr(row, "display_shot_no", "") or "").strip():
            row.display_shot_no = str(getattr(row, "shot_no", "") or row.shot_order)
        frame_index = int(getattr(row, "keyframe_index", 0) or 0)
        if not 1 <= frame_index <= len(frames):
            frame_index = index + 1 if index < len(frames) else 0
        if frames or not preserve_existing:
            row.keyframe_index = frame_index
            row.reference = frames[frame_index - 1] if frame_index else ""
        row.character_image_1 = match_character(getattr(row, "character_1", "")) or (row.character_image_1 if preserve_existing else "")
        row.character_image_2 = match_character(getattr(row, "character_2", "")) or (row.character_image_2 if preserve_existing else "")
        if not row.character_image_1 and len(ordered_images) == 1:
            row.character_image_1 = ordered_images[0]
    _annotate_generation_plan(getattr(data, "rows", ()) or (), target_indices=scope)
    return data


def _annotate_generation_plan(rows: Sequence[Any], *, target_indices: set[int] | None = None) -> None:
    """Choose a conservative node plan; explicit user/model choices remain intact."""

    continuation_markers = ("连续动作", "延续上一镜动作", "不间断", "接上上一镜")
    for index, row in enumerate(rows):
        if target_indices is not None and index not in target_indices:
            continue
        scene = str(getattr(row, "scene_tags", "") or "").strip()
        motion = str(getattr(row, "video_motion_prompt", "") or "")
        next_row = rows[index + 1] if index + 1 < len(rows) else None
        next_scene = str(getattr(next_row, "scene_tags", "") or "").strip()
        next_motion = str(getattr(next_row, "video_motion_prompt", "") or "")
        requested = str(getattr(row, "transition_plan", "") or "").strip()
        if not requested:
            requested = (
                "continuous_action"
                if next_row is not None and any(marker in next_motion for marker in continuation_markers)
                else "scene_change"
                if next_row is not None and scene and next_scene and scene != next_scene
                else "direct_cut"
            )
        row.transition_plan = requested

        mode = str(getattr(row, "generation_mode", "") or "").strip()
        if mode.lower() in {"allreference", "all_reference"}:
            mode = "all_reference"
        if mode not in {"all_reference", "image_to_video", "first_last_frame", "text_to_video"}:
            mode = "first_last_frame" if requested == "first_last_frame" else (
                "image_to_video" if getattr(row, "reference", "") or getattr(row, "character_image_1", "") else "text_to_video"
            )
        row.generation_mode = mode

        requirements = str(getattr(row, "reference_requirements", "") or "").strip()
        if not requirements:
            needs: list[str] = []
            if getattr(row, "character_image_1", "") or getattr(row, "character_image_2", ""):
                needs.append("角色资产")
            if getattr(row, "reference", ""):
                needs.append("本镜关键帧")
            previous_transition = str(getattr(rows[index - 1], "transition_plan", "") or "") if index > 0 else ""
            if previous_transition in {"continuous_action", "first_last_frame"}:
                needs.append("上一镜尾帧状态")
            row.reference_requirements = "、".join(needs) if needs else "无额外参考"
        props = str(getattr(row, "prop_tags", "") or "").strip()
        if props and props not in {"无", "没有", "none", "n/a"}:
            action_text = " ".join((str(getattr(row, "character_action", "") or ""), motion, str(getattr(row, "visual_description", "") or "")))
            state_start = str(getattr(row, "prop_state_start", "") or "").strip()
            state_end = str(getattr(row, "prop_state_end", "") or "").strip()
            state_change = str(getattr(row, "prop_state_change", "") or "").strip()
            if not state_start:
                state_start = "保持道具资产基准状态"
            if not state_end:
                state_end = "保持道具资产基准状态"
            if not state_change:
                state_change = "无可见状态变化"
                if any(token in action_text for token in ("拿起", "握住", "握持", "展开", "打开", "旋转", "放下", "落回", "合上", "收拢")):
                    state_change = "按本镜动作完成道具状态变化，并在末拍保持结束状态"
            row.prop_state_start = state_start
            row.prop_state_end = state_end
            row.prop_state_change = state_change
