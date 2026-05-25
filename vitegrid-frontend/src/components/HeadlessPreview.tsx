import { useEffect, useState } from "react";
import { LiveRender } from "./LiveRender";
import type { DocumentLayout } from "../types";

export default function HeadlessPreview() {
  const [layout, setLayout] = useState<DocumentLayout | null>(null);

  useEffect(() => {
    const rawData = localStorage.getItem("vitegrid_headless_layout");
    if (rawData) {
      try {
        setLayout(JSON.parse(rawData));
      } catch (err) {
        console.error("Failed to parse headless layout state:", err);
      }
    }
  }, []);

  if (!layout) {
    return <div className="bg-white min-h-screen" id="preview-loading" />;
  }

  return (
    <div
      className="bg-white inline-block"
      id="headless-render-canvas"
      style={{
        width: `${layout.page_width_px || 816}px`,
        height: `${layout.page_height_px || 1056}px`,
      }}
    >
      <LiveRender layout={layout} blocks={layout.blocks} />
    </div>
  );
}
