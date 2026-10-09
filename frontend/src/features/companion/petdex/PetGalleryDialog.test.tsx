import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps, PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PetGalleryDialog } from "@/features/companion/petdex/PetGalleryDialog";
import { loadImportedPets, savePetRecord } from "@/features/companion/petdex/petdex-storage";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({ children, ...props }: ComponentProps<"button">) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
}));

vi.mock("@/stores/app-store", () => ({
  useAppStore: (
    selector: (state: {
      companionHidden: boolean;
      setCompanionHidden: (hidden: boolean) => void;
    }) => unknown,
  ) => selector({ companionHidden: false, setCompanionHidden: vi.fn() }),
}));

vi.mock("@/features/companion/petdex/SpritePetCompanion", () => ({
  SpritePetCompanion: () => <div data-testid="sprite-pet" />,
}));

vi.mock("@/features/companion/petdex/PetSpriteThumbnail", () => ({
  PetSpriteThumbnail: ({ children }: PropsWithChildren) => <div>{children}</div>,
}));

vi.mock("@/features/companion/petdex/petdex-pets", () => ({
  fetchLocalPets: vi.fn().mockResolvedValue([]),
  PETDEX_GRID_COLS: 8,
  PETDEX_GRID_ROWS: 9,
  DEFAULT_BUILTIN_PET_SLUG: "zhizhi",
}));

vi.mock("@/features/companion/petdex/petdex-storage", () => ({
  deletePetRecord: vi.fn(),
  loadImportedPets: vi.fn().mockResolvedValue([]),
  savePetRecord: vi.fn(),
}));

describe("PetGalleryDialog import controls", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(loadImportedPets).mockResolvedValue([]);
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:pet-test"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("keeps Import clickable with metadata only and explains the missing spritesheet", async () => {
    render(
      <PetGalleryDialog
        open
        onOpenChange={vi.fn()}
        currentKind="zhizhi"
        currentPet={null}
        onConfirm={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "myBuddy.companion.importCta" }));

    const fileInputs = document.querySelectorAll<HTMLInputElement>('input[type="file"]');
    expect(fileInputs).toHaveLength(2);
    fireEvent.change(fileInputs[1], {
      target: {
        files: [new File(["{}"], "pet.json", { type: "application/json" })],
      },
    });

    const importButton = screen.getByRole("button", { name: "myBuddy.import.confirm" });
    expect(importButton).toBeEnabled();
    expect(importButton).toHaveAttribute("data-pet-import-ready", "false");
    expect(
      screen.getByText("myBuddy.import.missingSpritesheet"),
    ).toBeInTheDocument();

    const chooseSprite = vi.fn();
    fileInputs[0].click = chooseSprite;
    fireEvent.click(importButton);
    await waitFor(() => {
      expect(
        screen.getByText("myBuddy.import.missingSpritesheet"),
      ).toBeInTheDocument();
    });
    expect(chooseSprite).toHaveBeenCalledOnce();
  });

  it("persists a complete pet and shows it selected in the gallery immediately", async () => {
    const importedPet = {
      slug: "user-import-pet",
      displayName: "用户导入的宠物",
      spritesheetUrl: "blob:user-import",
      imported: true as const,
    };
    vi.mocked(loadImportedPets)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([importedPet]);

    render(
      <PetGalleryDialog
        open
        onOpenChange={vi.fn()}
        currentKind="zhizhi"
        currentPet={null}
        onConfirm={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "myBuddy.companion.importCta" }));
    const fileInputs = document.querySelectorAll<HTMLInputElement>('input[type="file"]');
    fireEvent.change(fileInputs[0], {
      target: {
        files: [new File(["sprite"], "spritesheet.webp", { type: "image/webp" })],
      },
    });
    fireEvent.change(fileInputs[1], {
      target: {
        files: [
          new File(
            [JSON.stringify({ meta: { name: "user_import_pet", displayName: "用户导入的宠物" } })],
            "pet.json",
            { type: "application/json" },
          ),
        ],
      },
    });

    fireEvent.click(screen.getByRole("button", { name: "myBuddy.import.confirm" }));

    await waitFor(() => {
      expect(savePetRecord).toHaveBeenCalledWith(
        expect.objectContaining({
          slug: "user-import-pet",
          displayName: "用户导入的宠物",
          blob: expect.any(File),
        }),
      );
    });
    expect(await screen.findByText("用户导入的宠物")).toBeInTheDocument();
  });
});
