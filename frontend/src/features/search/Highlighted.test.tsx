import { render } from "@testing-library/react";

import { Highlighted } from "./Highlighted";

function marks(text: string, ranges: [number, number][]): string[] {
  const { container } = render(
    <p>
      <Highlighted text={text} ranges={ranges} />
    </p>,
  );
  return [...container.querySelectorAll("mark")].map((mark) => mark.textContent);
}

describe("Highlighted", () => {
  it("marks the matched words, counted in code points like the API", () => {
    // « 🐝 » is one code point but two UTF-16 units: the ranges after it must not shift.
    expect(
      marks("🐝 abeille sur la lavande", [
        [2, 9],
        [17, 24],
      ]),
    ).toEqual(["abeille", "lavande"]);
  });

  it("never reads the snippet as HTML", () => {
    const { container } = render(
      <p>
        <Highlighted text="<img src=x onerror=alert(1)> riz" ranges={[[29, 32]]} />
      </p>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("<img src=x onerror=alert(1)> riz");
  });

  it("skips ranges that overlap or fall outside", () => {
    expect(
      marks("un deux trois", [
        [3, 7],
        [5, 9],
        [20, 30],
        [8, 13],
      ]),
    ).toEqual(["deux", "trois"]);
  });
});
