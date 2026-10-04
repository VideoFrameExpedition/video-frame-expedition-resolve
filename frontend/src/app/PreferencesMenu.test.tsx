import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import i18n from "@/i18n";

import { PreferencesMenu } from "./PreferencesMenu";

beforeEach(async () => {
  window.localStorage.clear();
  await i18n.changeLanguage("fr");
});

describe("PreferencesMenu", () => {
  it("changes the interface language, remembered in this browser", async () => {
    const user = userEvent.setup();
    render(<PreferencesMenu />);
    await user.click(screen.getByRole("button", { name: "Thème" }));
    await user.click(screen.getByRole("menuitem", { name: "English" }));
    expect(i18n.language).toBe("en");
    expect(window.localStorage.getItem("vfe.language")).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });
});
