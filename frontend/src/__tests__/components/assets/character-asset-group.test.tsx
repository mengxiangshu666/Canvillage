import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CharacterAssetGroup } from "@/components/assets/character-asset-group";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

describe("character asset group", () => {
  it("shows both images together without cropping the reference sheet", () => {
    render(<CharacterAssetGroup character={{ name: "hero", portrait_url: "/front.png", four_view_url: "/sheet.png" }} />);
    const front = screen.getByAltText("hero · characters.assetGroup.front");
    const sheet = screen.getByAltText("hero · characters.assetGroup.fourView");
    expect(front).toHaveAttribute("src", expect.stringContaining("front.png"));
    expect(sheet).toHaveAttribute("src", expect.stringContaining("sheet.png"));
    expect(sheet).toHaveClass("object-contain");
    expect(screen.queryByText("characters.assetGroup.missing")).not.toBeInTheDocument();
  });

  it("leaves an explicit missing sheet slot for a legacy single-photo character", () => {
    render(<CharacterAssetGroup character={{ name: "hero", portrait_url: "/front.png" }} />);
    expect(screen.getByAltText("hero · characters.assetGroup.front")).toBeInTheDocument();
    expect(screen.getByText("characters.assetGroup.fourView")).toBeInTheDocument();
    expect(screen.getByText("characters.assetGroup.missing")).toBeInTheDocument();
  });
});
