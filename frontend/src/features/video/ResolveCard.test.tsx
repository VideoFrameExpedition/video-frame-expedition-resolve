import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";

import type { Schemas } from "@/api/client";

import { ResolveCard } from "./ResolveCard";

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    search,
    className,
    title,
  }: {
    children: ReactNode;
    search: { timeline: string };
    className?: string;
    title?: string;
  }) => (
    <a href={`/library?timeline=${search.timeline}`} className={className} title={title}>
      {children}
    </a>
  ),
}));

const clipUse = (extra: Partial<Schemas["ResolveUseOut"]>): Schemas["ResolveUseOut"] => ({
  track_type: "video",
  track: 1,
  track_name: "Video 1",
  track_enabled: true,
  enabled: true,
  nested_in: null,
  record_in_tc: "01:00:03:12",
  record_out_tc: "01:00:07:02",
  record_in_s: 3.4,
  record_out_s: 7.07,
  source_in_s: 2.5,
  source_out_s: 6.8,
  media_pool_item_id: "m1",
  timeline_item_id: "i1",
  ...extra,
});

const LINK: Schemas["ResolveLinkOut"] = {
  bin_id: "b1",
  bin_label: "Montage",
  database: { type: "Disk", name: "Local Database" },
  project: { id: "p1", name: "cats 2026" },
  timeline: {
    id: "t1",
    name: "Timeline 1",
    fps: 29.97,
    drop_frame: false,
    start_timecode: "01:00:00:00",
    duration_s: 185,
  },
  synced_at: "2026-09-28T12:31:00Z",
  uses: [
    clipUse({}),
    clipUse({
      track: 2,
      enabled: false,
      nested_in: "Composé 1",
      record_in_tc: "01:01:00:00",
      record_in_s: 60,
      timeline_item_id: "i2",
    }),
    clipUse({ track_type: "audio", track: 1, timeline_item_id: "i3" }), // its linked sound
  ],
};

describe("ResolveCard", () => {
  it("shows nothing for a video no timeline of the library uses", () => {
    const { container } = render(<ResolveCard links={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("links each timeline to its videos in the library, with where the video is used", () => {
    render(<ResolveCard links={[LINK]} />);
    expect(screen.getByText("DaVinci Resolve")).toBeVisible();
    const link = screen.getByRole("link", { name: "cats 2026 › Timeline 1" });
    expect(link).toHaveAttribute("href", "/library?timeline=b1");
    expect(link).toHaveAttribute("title", "Voir les vidéos de la timeline « Montage »");

    const uses = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(uses).toHaveLength(2); // the picture's uses; the linked sound is only counted
    expect(uses[0]).toHaveTextContent("V101:00:03:12 → 01:00:07:02source 2,50 → 6,80 s");
    expect(uses[0]).not.toHaveTextContent("désactivé");
    expect(uses[1]).toHaveTextContent("V2");
    expect(uses[1]).toHaveTextContent("dans « Composé 1 »");
    expect(uses[1]).toHaveTextContent("désactivé");
    expect(screen.getByText("+ 1 emploi sur une piste audio")).toBeVisible();
    expect(
      screen.getByText(/^Positions relevées le .+ : la timeline a pu changer depuis\.$/),
    ).toBeVisible();
  });
});
