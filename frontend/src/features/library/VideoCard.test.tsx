import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

import type { Video } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";

import { VideoCard } from "./VideoCard";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, className }: { children: ReactNode; className?: string }) => (
    <a href="/videos/v1" className={className}>
      {children}
    </a>
  ),
}));

const video = {
  id: "v1",
  filename: "20200726_113722.mp4",
  title: null,
  summary: null,
  status: "ready",
  orientation: "horizontal",
  width: 3840,
  height: 2160,
  duration_s: 21,
  poster_url: null,
  captured_at: null,
  captured_at_source: null,
  place: null,
  weather: null,
  light: null,
  timeline_bins: [],
} as unknown as Video;

describe("VideoCard", () => {
  it("ticks without opening the video, and reports a shift-click", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const { rerender } = render(
      <TooltipProvider>
        <VideoCard video={video} onSelect={onSelect} />
      </TooltipProvider>,
    );
    const box = screen.getByRole("checkbox", { name: "Sélectionner 20200726_113722.mp4" });
    expect(box.closest("a")).toBeNull(); // not inside the link
    await user.click(box);
    expect(onSelect).toHaveBeenLastCalledWith(true, false);

    rerender(
      <TooltipProvider>
        <VideoCard video={video} selected onSelect={onSelect} />
      </TooltipProvider>,
    );
    await user.keyboard("{Shift>}");
    await user.click(box);
    await user.keyboard("{/Shift}");
    expect(onSelect).toHaveBeenLastCalledWith(false, true);
  });

  it("lets a file name break after its separators only", () => {
    const { container } = render(
      <TooltipProvider>
        <VideoCard video={video} />
      </TooltipProvider>,
    );
    expect(container.querySelectorAll("h3 wbr")).toHaveLength(1);
    expect(screen.getByRole("heading")).toHaveTextContent("20200726_113722.mp4");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument(); // no selection offered
  });

  it("says in how many Resolve timelines the video is used", () => {
    const { rerender } = render(
      <TooltipProvider>
        <VideoCard video={video} />
      </TooltipProvider>,
    );
    expect(screen.queryByRole("img", { name: /timeline/ })).not.toBeInTheDocument();
    rerender(
      <TooltipProvider>
        <VideoCard video={{ ...video, timeline_bins: ["b1", "b2"] }} />
      </TooltipProvider>,
    );
    expect(screen.getByRole("img", { name: "Dans 2 timelines Resolve" })).toHaveTextContent("2");
  });
});
