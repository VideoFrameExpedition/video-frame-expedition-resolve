import { attachmentName } from "@/api/queries";

import { saveBlob } from "./download";

describe("downloads", () => {
  it("reads the file name a response gives, the UTF-8 one first", () => {
    const named = (value: string | null) =>
      new Response("x", value ? { headers: { "Content-Disposition": value } } : {});
    expect(
      attachmentName(
        named(
          `attachment; filename="vid_os.csv"; filename*=UTF-8''vid%C3%A9os%20%C3%A9t%C3%A9.csv`,
        ),
        "f.csv",
      ),
    ).toBe("vidéos été.csv");
    expect(attachmentName(named('attachment; filename="plans.csv"'), "f.csv")).toBe("plans.csv");
    expect(attachmentName(named("attachment; filename*=UTF-8''%E0%A4"), "f.csv")).toBe("f.csv");
    expect(attachmentName(named(null), "f.csv")).toBe("f.csv");
  });

  it("hands a blob to the browser through a temporary link", () => {
    vi.useFakeTimers();
    const create = vi.fn(() => "blob:vfe/1");
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      expect(this.download).toBe("vidéos.csv");
      expect(this.getAttribute("href")).toBe("blob:vfe/1");
      expect(document.body.contains(this)).toBe(true);
    });
    saveBlob(new Blob(["a;b"]), "vidéos.csv");
    expect(click).toHaveBeenCalledOnce();
    expect(document.querySelector("a[download]")).toBeNull();
    expect(revoke).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith("blob:vfe/1");
    click.mockRestore();
    vi.useRealTimers();
  });
});
