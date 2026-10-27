// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 角色造型室的 3D 白模试衣（PlayCanvas）。
 *
 * 底座：仓库已随包分发的 Quaternius UAL2 白模（CC0，导演台同一套替身，
 * 见 `frontend/public/viewer-kit/quaternius/ual2/License.txt`）。
 * 参考语料里的 `midwam-human.glb` 是平台素材，不下载、不分发。
 *
 * 衣服、发型是低模图元做的**替身**：点选后即时改变形状和颜色，用来判断
 * 比例与配色；真正出图仍走造型图生成，不把这里当生产级服装资产。
 *
 * 与导演台的区别：这里是单角色静态 T-pose + 轨道相机，不需要世界坐标、
 * 碰撞与动画时间轴，所以单独一个小引擎，不复用 2600 行的 viewerApp。
 */

import * as pc from 'playcanvas';

import type {
  StudioBodyProportions,
  StudioHairSpec,
  StudioOutfitSpec,
} from '@/features/canvas/domain/characterStudio3dSpecs';

const MODEL_URL = '/viewer-kit/quaternius/ual2/UAL2_Standard.glb';
const MANNEQUIN_SKIN = '#e8e4dc';
const MANNEQUIN_JOINT = '#b8b2a7';

export interface CharacterStudioLook {
  hair: StudioHairSpec | null;
  outfit: StudioOutfitSpec | null;
  body: StudioBodyProportions;
  autoRotate: boolean;
}

export interface CharacterStudioApp {
  update(look: CharacterStudioLook): void;
  resetView(): void;
  dispose(): void;
}

export interface CharacterStudioAppOptions {
  canvas: HTMLCanvasElement;
  look: CharacterStudioLook;
  onReady?: () => void;
  onError?: (error: Error) => void;
}

interface Rig {
  head: pc.Entity;
  neck: pc.Entity;
  spine1: pc.Entity;
  spine2: pc.Entity;
  pelvis: pc.Entity;
  clavicleL: pc.Entity;
  clavicleR: pc.Entity;
  upperArmL: pc.Entity;
  upperArmR: pc.Entity;
  lowerArmL: pc.Entity;
  lowerArmR: pc.Entity;
  handL: pc.Entity;
  handR: pc.Entity;
  thighL: pc.Entity;
  thighR: pc.Entity;
  calfL: pc.Entity;
  calfR: pc.Entity;
  footL: pc.Entity;
  footR: pc.Entity;
}

interface Point {
  x: number;
  y: number;
  z: number;
}

function modelLocal(entity: pc.Entity, model: pc.Entity): Point {
  const world = entity.getPosition();
  const local = model.getWorldTransform().clone().invert().transformPoint(world);
  return { x: local.x, y: local.y, z: local.z };
}

function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
}

function midpoint(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, z: (a.z + b.z) / 2 };
}

function toVec3(p: Point): pc.Vec3 {
  return new pc.Vec3(p.x, p.y, p.z);
}

function parseColor(hex: string): pc.Color {
  const clean = hex.replace('#', '');
  const value = Number.parseInt(clean, 16);
  if (!Number.isFinite(value)) return new pc.Color(1, 1, 1);
  return new pc.Color(
    ((value >> 16) & 255) / 255,
    ((value >> 8) & 255) / 255,
    (value & 255) / 255,
  );
}

function ambientMaterial(hex: string): pc.StandardMaterial {
  const material = new pc.StandardMaterial();
  material.diffuse = parseColor(hex);
  material.gloss = 0.38;
  material.metalness = 0;
  material.useMetalness = false;
  material.update();
  return material;
}

function findRig(model: pc.Entity): Rig | null {
  const need = [
    'Head',
    'neck_01',
    'spine_01',
    'spine_02',
    'pelvis',
    'clavicle_l',
    'clavicle_r',
    'upperarm_l',
    'upperarm_r',
    'lowerarm_l',
    'lowerarm_r',
    'hand_l',
    'hand_r',
    'thigh_l',
    'thigh_r',
    'calf_l',
    'calf_r',
    'foot_l',
    'foot_r',
  ];
  const found: Record<string, pc.Entity | null> = {};
  for (const name of need) {
    found[name] = (model.findByName(name) as pc.Entity | null) ?? null;
  }
  if (Object.values(found).some((value) => !value)) return null;
  return {
    head: found.Head as pc.Entity,
    neck: found.neck_01 as pc.Entity,
    spine1: found.spine_01 as pc.Entity,
    spine2: found.spine_02 as pc.Entity,
    pelvis: found.pelvis as pc.Entity,
    clavicleL: found.clavicle_l as pc.Entity,
    clavicleR: found.clavicle_r as pc.Entity,
    upperArmL: found.upperarm_l as pc.Entity,
    upperArmR: found.upperarm_r as pc.Entity,
    lowerArmL: found.lowerarm_l as pc.Entity,
    lowerArmR: found.lowerarm_r as pc.Entity,
    handL: found.hand_l as pc.Entity,
    handR: found.hand_r as pc.Entity,
    thighL: found.thigh_l as pc.Entity,
    thighR: found.thigh_r as pc.Entity,
    calfL: found.calf_l as pc.Entity,
    calfR: found.calf_r as pc.Entity,
    footL: found.foot_l as pc.Entity,
    footR: found.foot_r as pc.Entity,
  };
}

export async function createCharacterStudioApp(
  options: CharacterStudioAppOptions,
): Promise<CharacterStudioApp> {
  const { canvas } = options;
  let look = options.look;

  if (typeof pc.Application !== 'function') {
    throw new Error('3D 引擎未加载');
  }

  const app = new pc.Application(canvas, {
    graphicsDeviceOptions: {
      antialias: true,
      alpha: true,
      preserveDrawingBuffer: true,
      powerPreference: 'high-performance',
    },
  });
  app.setCanvasFillMode(pc.FILLMODE_NONE);
  app.setCanvasResolution(pc.RESOLUTION_AUTO);
  app.graphicsDevice.maxPixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  app.scene.exposure = 1.05;
  app.scene.ambientLight = new pc.Color(0.42, 0.42, 0.46);

  const camera = new pc.Entity('studio_camera');
  camera.addComponent('camera', {
    clearColor: new pc.Color(0, 0, 0, 0),
    fov: 30,
    nearClip: 0.05,
    farClip: 60,
    toneMapping: pc.TONEMAP_ACES2,
  });
  app.root.addChild(camera);

  const keyLight = new pc.Entity('studio_key_light');
  keyLight.addComponent('light', {
    type: 'directional',
    color: new pc.Color(1, 0.96, 0.9),
    intensity: 1.35,
  });
  keyLight.setEulerAngles(38, 35, 0);
  app.root.addChild(keyLight);

  const fillLight = new pc.Entity('studio_fill_light');
  fillLight.addComponent('light', {
    type: 'directional',
    color: new pc.Color(0.62, 0.72, 0.95),
    intensity: 0.55,
  });
  fillLight.setEulerAngles(12, -140, 0);
  app.root.addChild(fillLight);

  const turntable = new pc.Entity('studio_turntable');
  app.root.addChild(turntable);

  const ground = new pc.Entity('studio_ground');
  const groundMesh = pc.createCylinder(app.graphicsDevice, {
    radius: 1.6,
    height: 0.02,
    heightSegments: 1,
    capSegments: 64,
  });
  ground.addComponent('render', {
    meshInstances: [new pc.MeshInstance(groundMesh, ambientMaterial('#171a20'))],
    castShadows: false,
    receiveShadows: false,
  });
  ground.setLocalPosition(0, -0.01, 0);
  app.root.addChild(ground);

  // ———— 轨道相机 ————
  let azimuth = 0;
  let elevation = 8;
  let cameraDistance = 2.4;
  let targetY = 0.9;
  let dragging = false;
  let lastX = 0;
  let lastY = 0;

  const applyCamera = () => {
    const az = (azimuth * Math.PI) / 180;
    const el = (elevation * Math.PI) / 180;
    const x = Math.sin(az) * Math.cos(el) * cameraDistance;
    const y = targetY + Math.sin(el) * cameraDistance;
    const z = Math.cos(az) * Math.cos(el) * cameraDistance;
    camera.setPosition(x, y, z);
    camera.lookAt(0, targetY, 0);
  };
  applyCamera();

  const onPointerDown = (event: PointerEvent) => {
    dragging = true;
    lastX = event.clientX;
    lastY = event.clientY;
    canvas.setPointerCapture?.(event.pointerId);
  };
  const onPointerMove = (event: PointerEvent) => {
    if (!dragging) return;
    azimuth -= (event.clientX - lastX) * 0.4;
    elevation = Math.max(-25, Math.min(70, elevation + (event.clientY - lastY) * 0.3));
    lastX = event.clientX;
    lastY = event.clientY;
    applyCamera();
  };
  const onPointerUp = (event: PointerEvent) => {
    dragging = false;
    canvas.releasePointerCapture?.(event.pointerId);
  };
  const onWheel = (event: WheelEvent) => {
    event.preventDefault();
    cameraDistance = Math.max(1.2, Math.min(6, cameraDistance + event.deltaY * 0.0012));
    applyCamera();
  };
  canvas.addEventListener('pointerdown', onPointerDown);
  canvas.addEventListener('pointermove', onPointerMove);
  canvas.addEventListener('pointerup', onPointerUp);
  canvas.addEventListener('pointercancel', onPointerUp);
  canvas.addEventListener('wheel', onWheel, { passive: false });

  let lastWidth = 0;
  let lastHeight = 0;
  const resize = () => {
    const rect = canvas.parentElement?.getBoundingClientRect();
    if (!rect || rect.width < 2 || rect.height < 2) return;
    const width = Math.round(rect.width);
    const height = Math.round(rect.height);
    if (width === lastWidth && height === lastHeight) return;
    lastWidth = width;
    lastHeight = height;
    app.resizeCanvas(width, height);
  };
  // 不用 ResizeObserver：面板里 canvas 的 CSS 尺寸由 flex 决定，写成
  // width/height 属性后再观察父容器会反复触发，实测能把主线程卡死。
  requestAnimationFrame(resize);
  window.addEventListener('resize', resize);

  let disposed = false;
  let model: pc.Entity | null = null;
  let rig: Rig | null = null;
  let bodyMeshes: Array<{ meshInstance: pc.MeshInstance; part: 'skin' | 'joint' }> = [];
  let hairRoot: pc.Entity | null = null;
  let outfitRoot: pc.Entity | null = null;
  let appliedHairKey = '';
  let appliedOutfitKey = '';
  let footOffset = 0;

  const skinMaterial = ambientMaterial(MANNEQUIN_SKIN);
  const jointMaterial = ambientMaterial(MANNEQUIN_JOINT);

  const setMaterialOnModel = () => {
    for (const { meshInstance, part } of bodyMeshes) {
      meshInstance.material = part === 'skin' ? skinMaterial : jointMaterial;
    }
  };

  const applyBody = (body: StudioBodyProportions) => {
    if (!model || !rig) return;
    model.setLocalScale(body.build, body.height, body.build);
    model.setLocalPosition(0, -footOffset * body.height, 0);
    rig.head.setLocalScale(body.head, body.head, body.head);
    if (hairRoot) hairRoot.setLocalScale(body.head, body.head, body.head);
    for (const bone of [rig.thighL, rig.thighR, rig.calfL, rig.calfR]) {
      bone.setLocalScale(1, body.legs, 1);
    }
    targetY = Math.max(0.6, cameraDistance * 0.32);
    applyCamera();
  };

  const destroyTree = (root: pc.Entity | null) => {
    if (!root) return;
    for (const render of root.findComponents('render') as pc.RenderComponent[]) {
      for (const meshInstance of render.meshInstances ?? []) {
        meshInstance.mesh?.destroy();
        meshInstance.material?.destroy();
      }
    }
    root.destroy();
  };

  const meshEntity = (
    name: string,
    mesh: pc.Mesh,
    material: pc.StandardMaterial,
  ): pc.Entity => {
    const entity = new pc.Entity(name);
    entity.addComponent('render', {
      meshInstances: [new pc.MeshInstance(mesh, material)],
      castShadows: false,
      receiveShadows: false,
    });
    return entity;
  };

  const frustumBetween = (
    name: string,
    from: Point,
    to: Point,
    topRadius: number,
    bottomRadius: number,
    hex: string,
  ): pc.Entity => {
    const height = Math.max(0.02, distance(from, to));
    const mesh = pc.createCone(app.graphicsDevice, {
      baseRadius: bottomRadius,
      peakRadius: topRadius,
      height,
      heightSegments: 1,
      capSegments: 24,
    });
    const entity = meshEntity(name, mesh, ambientMaterial(hex));
    entity.setPosition(toVec3(midpoint(from, to)));
    const dir = toVec3(to).sub(toVec3(from)).normalize();
    entity.setRotation(new pc.Quat().setFromDirections(pc.Vec3.UP, dir));
    return entity;
  };

  const sleeveBetween = (
    name: string,
    shoulder: Point,
    wrist: Point,
    radius: number,
    hex: string,
  ): pc.Entity => {
    const height = Math.max(0.02, distance(shoulder, wrist));
    const mesh = pc.createCylinder(app.graphicsDevice, {
      radius,
      height,
      heightSegments: 1,
      capSegments: 16,
    });
    const entity = meshEntity(name, mesh, ambientMaterial(hex));
    entity.setPosition(toVec3(midpoint(shoulder, wrist)));
    const dir = toVec3(wrist).sub(toVec3(shoulder)).normalize();
    entity.setRotation(new pc.Quat().setFromDirections(pc.Vec3.UP, dir));
    return entity;
  };

  const buildHair = (hair: StudioHairSpec) => {
    destroyTree(hairRoot);
    hairRoot = null;
    if (!rig) return;
    const root = new pc.Entity(`studio_hair_${hair.style}`);
    const headRadius = Math.max(0.06, distance(modelLocal(rig.head, model as pc.Entity), modelLocal(rig.neck, model as pc.Entity)) * 1.55);
    const R = headRadius;
    const head = modelLocal(rig.head, model as pc.Entity);
    // 头骨的局部轴不一定朝上（glTF/UE 两套约定都见过）；发型挂在白模根上，
    // 用模型空间定位，保证“头顶/脑后”与画面一致。
    root.setLocalPosition(head.x, head.y + R * 0.7, head.z);
    const addSphere = (name: string, pos: Point, scale: [number, number, number]) => {
      const mesh = pc.createSphere(app.graphicsDevice, {
        radius: R,
        latitudeBands: 10,
        longitudeBands: 14,
      });
      const entity = meshEntity(name, mesh, ambientMaterial(hair.color));
      entity.setLocalPosition(pos.x, pos.y, pos.z);
      entity.setLocalScale(scale[0], scale[1], scale[2]);
      root.addChild(entity);
    };
    const addCapsule = (
      name: string,
      pos: Point,
      radius: number,
      height: number,
      euler: [number, number, number],
    ) => {
      const mesh = pc.createCapsule(app.graphicsDevice, {
        radius,
        height,
        heightSegments: 2,
        sides: 12,
      });
      const entity = meshEntity(name, mesh, ambientMaterial(hair.color));
      entity.setLocalPosition(pos.x, pos.y, pos.z);
      entity.setLocalEulerAngles(euler[0], euler[1], euler[2]);
      root.addChild(entity);
    };

    switch (hair.style) {
      case 'buzz':
        addSphere('cap', { x: 0, y: 0.42 * R, z: -0.02 * R }, [1.06, 0.7, 1.06]);
        break;
      case 'bob':
        addSphere('cap', { x: 0, y: 0.46 * R, z: 0 }, [1.08, 0.94, 1.08]);
        addCapsule('back', { x: 0, y: -0.4 * R, z: -0.7 * R }, 0.72 * R, 1.7 * R, [6, 0, 0]);
        break;
      case 'bun':
        addSphere('cap', { x: 0, y: 0.46 * R, z: 0 }, [1.08, 0.94, 1.08]);
        addSphere('bun', { x: 0, y: 0.5 * R, z: -0.75 * R }, [0.45, 0.45, 0.45]);
        break;
      case 'high_pony':
        addSphere('cap', { x: 0, y: 0.46 * R, z: 0 }, [1.08, 0.94, 1.08]);
        addCapsule(
          'pony',
          { x: 0, y: -hair.length * 0.34 * R, z: -0.72 * R },
          0.3 * R,
          hair.length * R,
          [22, 0, 0],
        );
        break;
      case 'twin_tail':
        addSphere('cap', { x: 0, y: 0.46 * R, z: 0 }, [1.08, 0.94, 1.08]);
        addCapsule(
          'tail_l',
          { x: -0.72 * R, y: -hair.length * 0.3 * R, z: -0.35 * R },
          0.26 * R,
          hair.length * R,
          [10, 0, -14],
        );
        addCapsule(
          'tail_r',
          { x: 0.72 * R, y: -hair.length * 0.3 * R, z: -0.35 * R },
          0.26 * R,
          hair.length * R,
          [10, 0, 14],
        );
        break;
      case 'waves':
        addSphere('cap', { x: 0, y: 0.46 * R, z: 0 }, [1.08, 0.94, 1.08]);
        addSphere('wave_1', { x: -0.35 * R, y: -0.5 * R, z: -0.72 * R }, [0.55, 0.6, 0.5]);
        addSphere('wave_2', { x: 0.3 * R, y: -1.1 * R, z: -0.78 * R }, [0.58, 0.65, 0.52]);
        addSphere('wave_3', { x: -0.2 * R, y: -1.7 * R, z: -0.7 * R }, [0.5, 0.6, 0.48]);
        break;
      case 'sleek_shoulder':
        addSphere('cap', { x: 0, y: 0.46 * R, z: 0 }, [1.08, 0.94, 1.08]);
        addCapsule(
          'back',
          { x: 0, y: -hair.length * 0.34 * R, z: -0.7 * R },
          0.42 * R,
          hair.length * R,
          [6, 0, 0],
        );
        break;
      case 'long_straight':
      default:
        addSphere('cap', { x: 0, y: 0.38 * R, z: 0 }, [1.04, 0.96, 1.05]);
        addCapsule(
          'back',
          { x: 0, y: -(hair.length * 0.36) * R, z: -0.75 * R },
          0.42 * R,
          hair.length * R,
          [4, 0, 0],
        );
        break;
    }
    (model as pc.Entity).addChild(root);
    hairRoot = root;
  };

  const buildOutfit = (outfit: StudioOutfitSpec) => {
    destroyTree(outfitRoot);
    outfitRoot = null;
    if (!model || !rig) return;
    const root = new pc.Entity(`studio_outfit_${outfit.style}`);
    const skin = model;
    const neck = modelLocal(rig.neck, skin);
    const spine1 = modelLocal(rig.spine1, skin);
    const pelvis = modelLocal(rig.pelvis, skin);
    const kneeL = modelLocal(rig.calfL, skin);
    const kneeR = modelLocal(rig.calfR, skin);
    const ankleL = modelLocal(rig.footL, skin);
    const ankleR = modelLocal(rig.footR, skin);
    const shoulderL = modelLocal(rig.upperArmL, skin);
    const shoulderR = modelLocal(rig.upperArmR, skin);
    const wristL = modelLocal(rig.handL, skin);
    const wristR = modelLocal(rig.handR, skin);
    const shoulderWidth = Math.max(0.16, distance(shoulderL, shoulderR));
    const hipWidth = Math.max(0.12, distance(modelLocal(rig.thighL, skin), modelLocal(rig.thighR, skin)));
    const torsoTop = { x: neck.x, y: neck.y - shoulderWidth * 0.02, z: neck.z };
    const torsoBottom = spine1;
    const color = outfit.color;
    const add = (entity: pc.Entity) => root.addChild(entity);

    const wantsTop = outfit.style !== 'long_robe' || outfit.bottom === 'long_skirt';
    if (wantsTop) {
      add(
        frustumBetween(
          'top',
          torsoBottom,
          torsoTop,
          shoulderWidth * 0.46,
          shoulderWidth * 0.64,
          color,
        ),
      );
    }

    if (outfit.sleeve !== 'none') {
      const sleeveColor = color;
      if (outfit.sleeve === 'wide') {
        add(sleeveBetween('sleeve_l', shoulderL, wristL, shoulderWidth * 0.19, sleeveColor));
        add(sleeveBetween('sleeve_r', shoulderR, wristR, shoulderWidth * 0.19, sleeveColor));
      } else if (outfit.sleeve === 'long') {
        add(sleeveBetween('sleeve_l', shoulderL, wristL, shoulderWidth * 0.135, sleeveColor));
        add(sleeveBetween('sleeve_r', shoulderR, wristR, shoulderWidth * 0.135, sleeveColor));
      } else {
        const shortL = midpoint(shoulderL, wristL);
        const shortR = midpoint(shoulderR, wristR);
        add(
          sleeveBetween(
            'sleeve_l',
            shoulderL,
            { x: shoulderL.x + (shortL.x - shoulderL.x) * 0.8, y: shoulderL.y + (shortL.y - shoulderL.y) * 0.8, z: shoulderL.z + (shortL.z - shoulderL.z) * 0.8 },
            shoulderWidth * 0.15,
            sleeveColor,
          ),
        );
        add(
          sleeveBetween(
            'sleeve_r',
            shoulderR,
            { x: shoulderR.x + (shortR.x - shoulderR.x) * 0.8, y: shoulderR.y + (shortR.y - shoulderR.y) * 0.8, z: shoulderR.z + (shortR.z - shoulderR.z) * 0.8 },
            shoulderWidth * 0.15,
            sleeveColor,
          ),
        );
      }
    }

    if (outfit.style === 'flared_dress') {
      add(frustumBetween('skirt', kneeL.y < pelvis.y ? { x: 0, y: kneeL.y, z: 0 } : spine1, spine1, Math.max(hipWidth * 0.8, 0.17), Math.max(hipWidth * 1.35, 0.28), color));
    }

    switch (outfit.bottom) {
      case 'pants':
        add(
          frustumBetween(
            'pants_l',
            { x: ankleL.x * 0.9, y: ankleL.y, z: ankleL.z * 0.9 },
            { x: (ankleL.x + pelvis.x) / 2, y: pelvis.y, z: (ankleL.z + pelvis.z) / 2 },
            hipWidth * 0.26,
            hipWidth * 0.34,
            color,
          ),
        );
        add(
          frustumBetween(
            'pants_r',
            { x: ankleR.x * 0.9, y: ankleR.y, z: ankleR.z * 0.9 },
            { x: (ankleR.x + pelvis.x) / 2, y: pelvis.y, z: (ankleR.z + pelvis.z) / 2 },
            hipWidth * 0.26,
            hipWidth * 0.34,
            color,
          ),
        );
        break;
      case 'shorts':
        add(frustumBetween('shorts_l', kneeL, { x: (kneeL.x + pelvis.x) / 2, y: pelvis.y, z: (kneeL.z + pelvis.z) / 2 }, hipWidth * 0.3, hipWidth * 0.38, color));
        add(frustumBetween('shorts_r', kneeR, { x: (kneeR.x + pelvis.x) / 2, y: pelvis.y, z: (kneeR.z + pelvis.z) / 2 }, hipWidth * 0.3, hipWidth * 0.38, color));
        break;
      case 'long_skirt':
        add(
          frustumBetween(
            'skirt',
            { x: 0, y: ankleL.y + (pelvis.y - ankleL.y) * 0.12, z: 0 },
            spine1,
            Math.max(hipWidth * 0.8, 0.16),
            Math.max(hipWidth * 1.25, 0.26),
            color,
          ),
        );
        break;
      case 'skirt':
        add(frustumBetween('skirt', kneeL, spine1, Math.max(hipWidth * 0.8, 0.16), Math.max(hipWidth * 1.2, 0.24), color));
        break;
      case 'none':
      default:
        break;
    }

    // 直接挂到白模上：白模整体的身高/胖瘦倍率与旋转都会带上衣服。
    model.addChild(root);
    outfitRoot = root;
  };

  const applyLook = (next: CharacterStudioLook) => {
    look = next;
    if (!model || !rig) return;
    const hairKey = next.hair
      ? `${next.hair.style}:${next.hair.color}:${next.hair.length}`
      : 'none';
    if (hairKey !== appliedHairKey) {
      appliedHairKey = hairKey;
      if (next.hair) buildHair(next.hair);
      else {
        destroyTree(hairRoot);
        hairRoot = null;
      }
    }
    const outfitKey = next.outfit
      ? `${next.outfit.style}:${next.outfit.color}:${next.outfit.sleeve}:${next.outfit.bottom}`
      : 'none';
    if (outfitKey !== appliedOutfitKey) {
      appliedOutfitKey = outfitKey;
      if (next.outfit) buildOutfit(next.outfit);
      else {
        destroyTree(outfitRoot);
        outfitRoot = null;
      }
    }
    applyBody(next.body);
  };

  const loadModel = async (): Promise<void> => {
    const asset = new pc.Asset('character_studio_mannequin', 'container', {
      url: MODEL_URL,
    });
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        reject(new Error('白模加载超时：本地服务连接可能已被其它实时连接占满'));
      }, 15000);
      const done = () => window.clearTimeout(timer);
      asset.once('load', () => {
        done();
        resolve();
      });
      asset.once('error', (error: unknown) => {
        done();
        const message =
          typeof error === 'string'
            ? error
            : (error as { message?: string } | null)?.message ?? '未知错误';
        reject(new Error(`白模加载失败：${message}`));
      });
      app.assets.add(asset);
      app.assets.load(asset);
    });
    if (disposed) return;
    const resource = asset.resource as {
      instantiateRenderEntity?: (options?: { castShadows?: boolean }) => pc.Entity;
    } | null;
    const instance = resource?.instantiateRenderEntity?.({ castShadows: false });
    if (!instance) throw new Error('白模资源为空');

    instance.name = 'studio_mannequin';
    instance.setLocalEulerAngles(0, 0, 0);
    turntable.addChild(instance);
    model = instance;
    rig = findRig(instance);
    if (!rig) throw new Error('白模骨骼不完整，无法试衣');

    bodyMeshes = [];
    const collect = (component: pc.RenderComponent | pc.ModelComponent) => {
      for (const meshInstance of component.meshInstances ?? []) {
        const materialName = String(meshInstance.material?.name ?? '');
        const part = /joint/i.test(materialName) ? 'joint' : 'skin';
        meshInstance.castShadow = false;
        meshInstance.receiveShadow = false;
        bodyMeshes.push({ meshInstance, part });
      }
    };
    for (const render of instance.findComponents('render') as pc.RenderComponent[]) collect(render);
    for (const modelComponent of instance.findComponents('model') as pc.ModelComponent[]) collect(modelComponent);
    setMaterialOnModel();

    app.root.syncHierarchy();
    footOffset = Math.min(
      modelLocal(rig.footL, instance).y,
      modelLocal(rig.footR, instance).y,
    );
    const headTop = modelLocal(rig.head, instance).y + 0.12;
    const height = Math.max(0.8, headTop - footOffset);
    targetY = height * 0.45;
    cameraDistance = height * 2.55;
    applyCamera();
    applyLook(look);
    options.onReady?.();
  };

  let last = performance.now();
  const onUpdate = () => {
    if (disposed) return;
    const now = performance.now();
    const delta = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (look.autoRotate) {
      turntable.setLocalEulerAngles(0, turntable.getLocalEulerAngles().y + delta * 18, 0);
    }
  };
  app.on('update', onUpdate);
  app.start();

  try {
    await loadModel();
  } catch (error) {
    disposed = true;
    app.destroy();
    throw error instanceof Error ? error : new Error(String(error));
  }

  return {
    update(next: CharacterStudioLook) {
      if (disposed) return;
      applyLook(next);
    },
    resetView() {
      if (disposed) return;
      azimuth = 0;
      elevation = 8;
      if (model && rig) {
        const headTop = modelLocal(rig.head, model).y + 0.12;
        const height = Math.max(0.8, headTop - footOffset);
        targetY = height * 0.45;
        cameraDistance = height * 2.55;
      }
      turntable.setLocalEulerAngles(0, 0, 0);
      applyCamera();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      window.removeEventListener('resize', resize);
      canvas.removeEventListener('pointerdown', onPointerDown);
      canvas.removeEventListener('pointermove', onPointerMove);
      canvas.removeEventListener('pointerup', onPointerUp);
      canvas.removeEventListener('pointercancel', onPointerUp);
      canvas.removeEventListener('wheel', onWheel);
      app.destroy();
    },
  };
}
