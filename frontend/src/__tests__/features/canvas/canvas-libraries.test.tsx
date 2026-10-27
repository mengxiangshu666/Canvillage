// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CharacterStudioModal } from "@/features/canvas/ui/CharacterStudioModal";
import { CharacterCreateDialog } from "@/features/canvas/ui/CharacterCreateDialog";
import { CharacterStudioPanel } from "@/features/canvas/ui/CharacterStudioPanel";
import { StyleLibraryModal } from "@/features/canvas/ui/StyleLibraryModal";
import { EffectLibraryModal } from "@/features/canvas/ui/EffectLibraryModal";
import { useCanvasStore } from "@/stores/canvasStore";
import { CANVAS_NODE_TYPES, type CanvasNode } from "@/features/canvas/domain/canvasNodes";

const characters = [
  {
    name: "沈青禾",
    description: "二十二岁，建筑师",
    portrait_url: "/characters/shen.png",
    four_view_url: "/characters/shen-4view.png",
    is_main: true,
  },
];

const identities = [
  {
    identity_id: "沈青禾_常服",
    identity_name: "常服",
    appearance_details: "深灰长风衣",
    image_url: "/characters/shen-identity.png",
    portrait_image_url: null,
    costume_image_url: null,
  },
];

const characterMocks = vi.hoisted(() => ({
  createCharacter: vi.fn().mockImplementation(async (data: { name: string }) => {
    characters.push({
      name: data.name,
      description: "",
      portrait_url: "",
      four_view_url: "",
      is_main: false,
    });
    return { ok: true, data: { name: data.name } };
  }),
  createIdentity: vi.fn().mockResolvedValue({ ok: true }),
  uploadPortrait: vi.fn().mockResolvedValue({ ok: true }),
  uploadIdentityImage: vi.fn().mockResolvedValue({ ok: true }),
  updateCharacter: vi.fn().mockResolvedValue({ ok: true }),
  updateIdentity: vi.fn().mockResolvedValue({ ok: true }),
  generateIdentity: vi.fn().mockResolvedValue({ ok: true, data: { image_url: "/new.png" } }),
  parseProfile: vi.fn().mockResolvedValue({
    profile: {
      suggested_name: "沈青禾",
      brief: "二十二岁的建筑师",
      face_prompt: "鹅蛋脸，柳叶眉",
      appearance_details: "深灰长风衣",
      signature_features: [{ feature: "柳叶眉", priority: "primary" }],
    },
    character_fields: {
      gender: "女",
      age_group: "youth",
      description: "二十二岁的建筑师",
      face_prompt: "鹅蛋脸，柳叶眉。签名特征：柳叶眉",
      body_type: "纤细高挑",
    },
  }),
}));

vi.mock("@/lib/queries/characters", () => ({
  useCharacters: () => ({ data: { data: characters }, isLoading: false }),
  useCharacterIdentities: () => ({ data: { data: identities }, isLoading: false }),
  useCreateCharacter: () => ({
    mutateAsync: characterMocks.createCharacter,
    isPending: false,
  }),
  useCreateIdentity: () => ({
    mutateAsync: characterMocks.createIdentity,
    isPending: false,
  }),
  useUploadPortrait: () => ({
    mutateAsync: characterMocks.uploadPortrait,
    isPending: false,
  }),
  useUploadIdentityImage: () => ({
    mutateAsync: characterMocks.uploadIdentityImage,
    isPending: false,
  }),
  useUpdateCharacter: () => ({
    mutateAsync: characterMocks.updateCharacter,
    isPending: false,
  }),
  useUpdateIdentity: () => ({
    mutateAsync: characterMocks.updateIdentity,
    isPending: false,
  }),
  useGenerateIdentityImage: () => ({
    mutateAsync: characterMocks.generateIdentity,
    isPending: false,
  }),
  useParseCharacterProfile: () => ({
    mutateAsync: characterMocks.parseProfile,
    isPending: false,
  }),
  useIdentityAttempts: () => ({
    data: { ok: true, data: { image_attempts: 3, portrait_attempts: 1 } },
    isLoading: false,
  }),
}));

vi.mock("@/features/canvas/ui/AssetLibraryModal", () => ({
  AssetLibraryModal: () => null,
}));

vi.mock("@/features/canvas/ui/CharacterStudio3dCanvas", () => ({
  CharacterStudio3dCanvas: (props: {
    hair?: { style?: string };
    outfit?: { style?: string };
    body?: { height?: number };
  }) => (
    <div
      data-testid="studio-3d"
      data-hair={props.hair?.style ?? ""}
      data-outfit={props.outfit?.style ?? ""}
      data-height={String(props.body?.height ?? "")}
    />
  ),
}));

vi.mock("@/lib/queries/styles", () => ({
  useStyles: () => ({
    data: {
      data: [
        { id: "afrofuturism", name: "非洲未来史诗", type: "preset" },
        { id: "libtv_arcade_pixel", name: "街机像素风", type: "preset", tags: ["街机像素风", "libtv", "像素复古"] },
        { id: "custom-1", name: "我的风格", type: "custom" },
      ],
    },
    isLoading: false,
  }),
}));

vi.mock("@/features/canvas/hooks/useFreezoneVideoCameraTemplates", () => ({
  useFreezoneVideoCameraTemplates: () => ({ templates: [], isLoading: false, error: null }),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));

function imageNode(): CanvasNode {
  return {
    id: "image-1",
    type: CANVAS_NODE_TYPES.imageGen,
    position: { x: 0, y: 0 },
    selected: true,
    data: { displayName: "封面", prompt: "雨夜街头", imageUrl: "/a.png" },
  } as unknown as CanvasNode;
}

describe("画布素材库三件套", () => {
  beforeEach(() => {
    useCanvasStore.setState({
      nodes: [imageNode()],
      selectedNodeId: "image-1",
    });
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("角色造型室列出角色与造型，并把造型图放进画布", () => {
    const addNode = vi.fn(() => "new-node");
    useCanvasStore.setState({ addNode, setSelectedNode: vi.fn() });

    render(<CharacterStudioModal open project="p1" onClose={vi.fn()} />);

    expect(screen.getByText("角色造型室")).toBeTruthy();
    expect(screen.getAllByText("沈青禾").length).toBeGreaterThan(0);
    expect(screen.getByText("常服")).toBeTruthy();

    fireEvent.click(screen.getByText("放进画布"));

    expect(addNode).toHaveBeenCalledWith(
      CANVAS_NODE_TYPES.upload,
      expect.any(Object),
      expect.objectContaining({ imageUrl: "/characters/shen-identity.png" }),
      { avoidCollisions: true },
    );
  });

  it("风格库把风格写到当前选中节点", () => {
    render(<StyleLibraryModal open project="p1" onClose={vi.fn()} />);

    fireEvent.click(screen.getByText("非洲未来史诗"));

    const node = useCanvasStore.getState().nodes.find((item) => item.id === "image-1");
    expect(node?.data.styleTemplateId).toBe("afrofuturism");
  });

  it("风格广场分类只显示 libtv 风格并能套用", () => {
    render(<StyleLibraryModal open project="p1" onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /^风格广场/ }));

    expect(screen.queryByText("非洲未来史诗")).toBeNull();
    fireEvent.click(screen.getByText("街机像素风"));

    const node = useCanvasStore.getState().nodes.find((item) => item.id === "image-1");
    expect(node?.data.styleTemplateId).toBe("libtv_arcade_pixel");
  });

  it("官方角色库：卡片筛选 + 应用到造型室建档", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("officialCharacters.json")) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                items: [
                  {
                    id: "official-trend-01",
                    name: "都市霓裳",
                    categoryCode: "fashion_role",
                    categoryLabel: "时尚感角色",
                    gender: "女",
                    ageStage: "青年",
                    region: "东亚",
                    era: "现代",
                    temperament: "时尚",
                    shortDescription: "现代都市时尚女性。",
                    appearanceDescription: "高挑身形，锁骨短发。",
                    personalityDescription: "毒舌但护短。",
                    sceneHint: "都市短剧",
                  },
                  {
                    id: "official-lead-m01",
                    name: "谈判桌上的猎手",
                    categoryCode: "male_lead",
                    categoryLabel: "男主",
                    gender: "男",
                    ageStage: "青年",
                    region: "东亚",
                    era: "现代",
                    temperament: "清冷",
                    shortDescription: "并购操盘手男主。",
                    appearanceDescription: "深灰西装不打领带。",
                    personalityDescription: "理性到近乎冷酷。",
                    sceneHint: "都市商战短剧",
                  },
                ],
              }),
          });
        }
        if (url.includes("stylingCatalog.json")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve({ items: [] }) });
        }
        if (url.includes("voiceLibrary.json")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve({ items: [] }) });
        }
        return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
      }),
    );
    render(<CharacterStudioModal open project="p1" onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "官方角色库" }));
    await screen.findByText("都市霓裳");
    const cards = screen.getAllByRole("button", { name: "应用到造型室" });
    expect(cards).toHaveLength(2);
    expect(screen.getByText("谈判桌上的猎手")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "欧美" }));
    expect(screen.queryByText("都市霓裳")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "东亚" }));
    expect(screen.getByText("都市霓裳")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "应用到造型室" })[0]);
    await waitFor(() => {
      expect(characterMocks.createCharacter).toHaveBeenCalledWith({
        name: "都市霓裳",
        description: "现代都市时尚女性。性格：毒舌但护短。",
        role: "时尚感角色",
        gender: "女",
        age_group: "青年",
        face_prompt: "高挑身形，锁骨短发。",
      });
    });
    expect(screen.getByRole("button", { name: "保存" })).toBeTruthy();
  });

  it("造型室按录屏版式排布：全幅 3D + 左悬浮工具栏 + 右悬浮快捷生成，生成前先保存", async () => {
    characterMocks.updateCharacter.mockClear();
    characterMocks.updateIdentity.mockClear();
    characterMocks.generateIdentity.mockClear();

    render(<CharacterStudioPanel project="p1" character={characters[0] as never} />);

    for (const label of ["皮肤", "身材", "妆造", "换装", "姿态", "音色"]) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    }
    expect(screen.getByRole("button", { name: "3D 试衣" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "造型图" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认生成" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "保存" })).toBeTruthy();
    expect(screen.getByText(/造型图 3/)).toBeTruthy();
    expect(screen.getByPlaceholderText("服装、发型、妆容、配饰")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "确认生成" }));

    await waitFor(() => {
      expect(characterMocks.updateCharacter).toHaveBeenCalled();
      expect(characterMocks.updateIdentity).toHaveBeenCalled();
      expect(characterMocks.generateIdentity).toHaveBeenCalledWith(
        expect.objectContaining({ identityId: "沈青禾_常服" }),
      );
    });
  });

  it("一句话解析角色档案，创建时带上性别/年龄段/体型/面部锚点", async () => {
    characterMocks.parseProfile.mockClear();
    characterMocks.createCharacter.mockClear();

    render(
      <CharacterCreateDialog mode="character" open project="p1" onClose={vi.fn()} />,
    );

    const parseButton = screen.getByRole("button", { name: /AI 解析档案/ });
    expect(parseButton.hasAttribute("disabled")).toBe(true);

    fireEvent.change(
      screen.getByPlaceholderText("年龄、气质、外形要点；也可以直接描述这张参考图"),
      { target: { value: "二十二岁的女建筑师，气质克制" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /AI 解析档案/ }));

    await waitFor(() => {
      expect(characterMocks.parseProfile).toHaveBeenCalledWith({
        description: "二十二岁的女建筑师，气质克制",
        imageUrl: undefined,
      });
    });
    expect(screen.getByLabelText("解析出的角色档案")).toBeTruthy();

    // 建议名与简介自动填入，用户仍可修改
    await waitFor(() => {
      expect(
        (screen.getByPlaceholderText("例如：孟祥树") as HTMLInputElement).value,
      ).toBe("沈青禾");
    });

    fireEvent.click(screen.getByRole("button", { name: "创建" }));

    await waitFor(() => {
      expect(characterMocks.createCharacter).toHaveBeenCalledWith({
        name: "沈青禾",
        description: "二十二岁的女建筑师，气质克制",
        gender: "女",
        age_group: "youth",
        face_prompt: "鹅蛋脸，柳叶眉。签名特征：柳叶眉",
        body_type: "纤细高挑",
      });
    });
  });

  it("造型室按 r76 分组：目录卡与 ± 滑杆写进对应字段，再点一次/调回移除", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("stylingCatalog.json")) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                items: [
                  {
                    kind: "outfit",
                    style: "现代",
                    gender: "女",
                    name: "旗袍",
                    key: "qipao",
                    enum: "QipaoDress1",
                  },
                  {
                    kind: "hair",
                    style: "现代",
                    gender: "女",
                    name: "长直发",
                    key: "changzhifa",
                    enum: "LongStraight",
                  },
                ],
              }),
          });
        }
        if (url.includes("voiceLibrary.json")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve({ items: [] }) });
        }
        return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
      }),
    );
    render(<CharacterStudioPanel project="p1" character={characters[0] as never} />);

    fireEvent.click(screen.getByRole("button", { name: "换装" }));
    fireEvent.click(screen.getByRole("button", { name: "服饰" }));
    const qipao = await screen.findByRole("button", { name: "旗袍" });
    fireEvent.click(qipao);
    const appearance = screen.getByPlaceholderText(
      "服装、发型、妆容、配饰",
    ) as HTMLTextAreaElement;
    expect(appearance.value).toContain("服饰：旗袍");
    expect(screen.getByTestId("studio-3d").getAttribute("data-outfit")).toBe("long_dress");

    fireEvent.click(qipao);
    expect(appearance.value).not.toContain("服饰：旗袍");
    expect(screen.getByTestId("studio-3d").getAttribute("data-outfit")).toBe("");

    fireEvent.click(screen.getByRole("button", { name: "身材" }));
    fireEvent.change(screen.getByLabelText("AI 增高"), {
      target: { value: "100" },
    });
    const body = screen.getByPlaceholderText("纤细 / 健壮") as HTMLInputElement;
    expect(body.value).toContain("身形高挑100%");
    expect(screen.getByTestId("studio-3d").getAttribute("data-height")).toBe("1.05");

    fireEvent.change(screen.getByLabelText("AI 增高"), {
      target: { value: "0" },
    });
    expect(body.value).not.toContain("身形高挑");
    expect(screen.getByTestId("studio-3d").getAttribute("data-height")).toBe("1");
  });

  it("妆造色板与滑杆写进面部锚点，重复选择只保留一段妆造", () => {
    render(<CharacterStudioPanel project="p1" character={characters[0] as never} />);

    fireEvent.click(screen.getByRole("button", { name: "妆造" }));
    fireEvent.click(screen.getByRole("button", { name: "肤色瓷白" }));
    fireEvent.click(screen.getByRole("button", { name: "唇色正红" }));
    fireEvent.change(screen.getByLabelText("眉妆浓度"), { target: { value: "70" } });

    const face = screen.getByPlaceholderText("骨相、五官、皮肤质感") as HTMLTextAreaElement;
    expect(face.value).toContain("肤色瓷白");
    expect(face.value).toContain("唇色正红");
    expect(face.value).toContain("眉妆70%");
    expect(face.value.match(/妆造/g)?.length).toBe(1);
  });

  it("造型室里点击不会冒泡到角色库弹窗，把整个界面关掉", () => {
    const outer = vi.fn();
    render(
      <div onClick={outer}>
        <CharacterStudioPanel
          project="p1"
          character={characters[0] as never}
          onClose={vi.fn()}
        />
      </div>,
    );

    fireEvent.click(screen.getByRole("button", { name: "皮肤" }));
    fireEvent.click(screen.getByText("皮肤纹理"));

    expect(outer).not.toHaveBeenCalled();
  });

  it("新建角色浮层点遮罩只关自己，不会连带关闭角色库", () => {
    const outer = vi.fn();
    const onClose = vi.fn();
    render(
      <div onClick={outer}>
        <CharacterCreateDialog
          mode="character"
          open
          project="p1"
          onClose={onClose}
        />
      </div>,
    );

    fireEvent.click(screen.getByRole("dialog", { name: "创建你的新角色" }));

    expect(onClose).toHaveBeenCalled();
    expect(outer).not.toHaveBeenCalled();
  });

  it("新建造型可以带参考图：先建造型，再把图传到这个造型上", async () => {
    characterMocks.createIdentity.mockClear();
    characterMocks.uploadIdentityImage.mockClear();
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:preview"), revokeObjectURL: vi.fn() });

    render(
      <CharacterCreateDialog
        mode="identity"
        open
        project="p1"
        characterName="沈青禾"
        onClose={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("例如：唐代常服"), {
      target: { value: "常服" },
    });
    const file = new File(["x"], "ref.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("从本地上传参考图"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));

    await waitFor(() => {
      expect(characterMocks.createIdentity).toHaveBeenCalledWith({
        identity_name: "常服",
        appearance_details: undefined,
      });
    });
    expect(characterMocks.uploadIdentityImage).toHaveBeenCalledWith({
      identityName: "常服",
      file,
    });
  });

  it("特效库把运镜段写进当前节点提示词", () => {
    render(<EffectLibraryModal open project="p1" onClose={vi.fn()} />);

    fireEvent.click(screen.getAllByText("固定镜头")[0].closest("button") as HTMLElement);

    const node = useCanvasStore.getState().nodes.find((item) => item.id === "image-1");
    expect(String(node?.data.prompt)).toContain("运镜轨迹");
  });

  it("特效库把画面特效写进当前节点提示词", () => {
    render(<EffectLibraryModal open project="p1" onClose={vi.fn()} />);

    fireEvent.click(screen.getByText("火焰缠身").closest("button") as HTMLElement);

    const node = useCanvasStore.getState().nodes.find((item) => item.id === "image-1");
    const prompt = String(node?.data.prompt);
    expect(prompt).toContain("赤红烈焰");
    expect(prompt).toContain("[特效]");
  });

  it("镜头模板把带预览的效果写进当前节点提示词", () => {
    render(<EffectLibraryModal open project="p1" onClose={vi.fn()} />);

    fireEvent.click(screen.getByText("机甲变身").closest("button") as HTMLElement);

    const node = useCanvasStore.getState().nodes.find((item) => item.id === "image-1");
    expect(String(node?.data.prompt)).toContain("装甲从四肢向躯干逐层咬合");
  });
});
